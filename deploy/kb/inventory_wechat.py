"""只盘点 /tmp 私有副本，重建历史窗口；不读凭据，不生成调用授权。"""
from __future__ import annotations

import argparse
import ast
from collections import Counter, defaultdict
from contextlib import closing
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import stat
import tempfile
from types import SimpleNamespace

EXTRACT = Path('/opt/imgw/wechat-kb-dual-live-v1/video_knowledge/kb_extract.py')
EXTRACT_SHA = 'f6999348a2b8eea6a5c367a58b7ddc0fc45f7e79b053c10d1881d1121c044842'
HISTORY = Path('/opt/imgw/wechat/video_knowledge/history_backfill.py')
HISTORY_SHA = 'fa574fe3b936977ab5d286e256b32230fe07eafe6ee7667fd803c9dd5d877d74'


def sha(value):
    return hashlib.sha256(value if isinstance(value, bytes) else value.encode()).hexdigest()


def private(path):
    path = Path(path).absolute()
    if '..' in path.parts or not path.is_relative_to('/tmp') or any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('仅接受 /tmp 内无符号链接的私有副本')
    info = path.stat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o700):
        raise ValueError('副本目录须由当前用户持有且权限为 0700')
    for name in ('catalog.sqlite3', 'calls.sqlite3', 'state.sqlite3'):
        item = (path / name).lstat()
        if (not stat.S_ISREG(item.st_mode) or item.st_nlink != 1
                or item.st_uid != os.geteuid() or stat.S_IMODE(item.st_mode) != 0o600):
            raise ValueError('副本文件须为当前用户持有的 0600 普通文件且无硬链接')
    return path


def algorithms():
    # Only source-bound pure functions are loaded; provider/config imports are absent.
    raw = EXTRACT.read_bytes()
    if sha(raw) != EXTRACT_SHA:
        raise ValueError('提取源码基线已变化，请重新核对盘点算法')
    tree = ast.parse(raw)
    constants = {'UNIT_CHARS', 'UNIT_MESSAGES', 'DOC_LINES', 'DOC_CHUNKS', 'PROMPT'}
    functions = {'_eligible', '_make_unit', '_document_units', 'units'}
    selected = [node for node in tree.body
                if isinstance(node, ast.FunctionDef) and node.name in functions
                or isinstance(node, ast.Assign) and any(
                    isinstance(target, ast.Name) and target.id in constants
                    for target in node.targets)]
    namespace = {'hashlib': hashlib, 'cpq_windows': SimpleNamespace(
        _one_line=lambda value: re.sub(r'\s+', ' ', value).strip(),
        _time=lambda row: row['source_created_at'] if row['source_created_at'] is not None else row['received_at'],
        WXID=re.compile(r'wxid_[A-Za-z0-9_-]+'),
        PHONE=re.compile(r'(?<!\d)1[3-9]\d{9}(?!\d)'))}
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(EXTRACT), 'exec'), namespace)
    history_raw = HISTORY.read_bytes()
    if sha(history_raw) != HISTORY_SHA:
        raise ValueError('历史提示词基线已变化，请重新核对盘点算法')
    history_tree = ast.parse(history_raw)
    learning = next(ast.literal_eval(node.value) for node in history_tree.body
                    if isinstance(node, ast.Assign) and any(
                        isinstance(target, ast.Name) and target.id == 'LEARNING_PROMPT'
                        for target in node.targets))
    namespace['LEARNING_PROMPT'] = learning
    return namespace


def open_copy(path):
    db = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)
    db.execute('PRAGMA query_only=ON')
    db.row_factory = sqlite3.Row
    return db


def inventory(snapshot, private_review=None):
    snapshot = private(snapshot)
    ns = algorithms()
    with closing(open_copy(snapshot / 'catalog.sqlite3')) as catalog, \
            closing(open_copy(snapshot / 'calls.sqlite3')) as calls, \
            closing(open_copy(snapshot / 'state.sqlite3')) as state:
        # Validate schema before naming business fields in queries.
        expected = {
            'channel_business_evidence': {'evidence_id', 'classification_id', 'source_created_at', 'created_at', 'body_text'},
            'channel_role_registry': {'classification_id', 'owner', 'source_scope', 'channel_role', 'channel_id_sha256'},
            'kb_segment': {'unit_id', 'unit_sha256', 'status', 'created_at'},
            'kb_learning_segment': {'unit_id', 'unit_sha256', 'status', 'created_at'}}
        for table, fields in expected.items():
            if not fields <= {row[1] for row in catalog.execute('PRAGMA table_info(' + table + ')')}:
                raise ValueError('副本结构不匹配，请核对版本')
        observed_roles = {}
        for table, role in (('kb_segment', 'supplier'), ('kb_learning_segment', 'learning')):
            for room in catalog.execute('SELECT DISTINCT chatroom_id FROM ' + table):
                hashed = sha(room[0])
                if hashed in observed_roles and observed_roles[hashed] != role:
                    raise ValueError('历史提取角色冲突，请先人工核对')
                observed_roles[hashed] = role
        learning_classes = {row[0] for row in state.execute(
            "SELECT DISTINCT json_extract(observation_json,'$.classification_id') "
            "FROM channel_role_intake WHERE json_extract(observation_json,'$.registered_business_role')='learning'")}
        rows = []
        excluded_role_candidates = 0
        for item in catalog.execute('''SELECT e.*,r.channel_role,r.channel_id_sha256,r.account_sha256
            FROM channel_business_evidence e JOIN channel_role_registry r USING(classification_id)
            WHERE e.owner=r.owner AND e.source_scope=r.source_scope AND r.channel_kind='group'
              AND r.channel_role IN ('CHANNEL_ROLE_SUPPLIER','CHANNEL_ROLE_NEUTRAL')'''):
            row = dict(item, chatroom_id=item['channel_id_sha256'])
            row['role'] = 'supplier' if item['channel_role'] == 'CHANNEL_ROLE_SUPPLIER' else 'learning'
            if (observed_roles.get(row['chatroom_id']) != row['role']
                    or row['role'] == 'learning' and row['classification_id'] not in learning_classes):
                excluded_role_candidates += 1
                continue
            if (not ns['_eligible'](row) or row['role'] == 'learning' and not (
                    row['source_kind'] in ('message', 'historical_message')
                    and row['is_cost_price'] == 0 and row['price_kind'] == 'not_price')):
                continue
            rows.append(row)
        by_id = {row['evidence_id']: row for row in rows}
        by_room = defaultdict(list)
        for row in rows:
            by_room[row['chatroom_id']].append(row)
        learning_rooms = {row['chatroom_id']: True for row in rows if row['role'] == 'learning'}
        ctx = {'_learning': learning_rooms}
        grouped_cache, indexed_at, prompt_cache, unit_cache = {}, {}, {}, {}
        def units_at(at):
            # Admission time is monotone, while source time may be historical.
            for room, sources in by_room.items():
                selected = [row for row in sources if row['created_at'] <= at]
                marker = len(selected)
                if indexed_at.get(room) != marker:
                    ns['kb_authorized'] = lambda db, context: selected
                    grouped_cache[room] = ns['units'](None, ctx, [room])
                    prompt_cache[room] = {sha((ns['LEARNING_PROMPT']
                        if unit['business_role'] == 'learning' else ns['PROMPT']) + unit['text']): unit
                        for unit in grouped_cache[room]}
                    unit_cache[room] = {(unit['business_role'], unit['unit_id'], unit['unit_sha256']): unit
                                        for unit in grouped_cache[room]}
                    indexed_at[room] = marker
            return prompt_cache, unit_cache
        events = []
        for table in ('kb_segment', 'kb_learning_segment'):
            role = 'learning' if table == 'kb_learning_segment' else 'supplier'
            for row in catalog.execute('SELECT * FROM ' + table):
                events.append((float(row['created_at']), 'segment', role, dict(row)))
        for row in catalog.execute('SELECT original_table,original_row_json FROM kb_segment_audit'):
            if row['original_table'] not in ('kb_segment', 'kb_learning_segment'):
                raise ValueError('审计表类型不匹配，请核对副本')
            original = json.loads(row['original_row_json'])
            events.append((float(original['created_at']), 'audit',
                           'learning' if row['original_table'] == 'kb_learning_segment' else 'supplier', original))
        call_rows = list(calls.execute('SELECT * FROM calls ORDER BY created_at'))
        for row in call_rows:
            events.append((row['created_at'], 'call', '', dict(row)))
        coverage, responses, unknown, requested = defaultdict(set), defaultdict(set), set(), set()
        linked_calls = set()
        unit_records, unmatched_segments, invalid_response = {}, 0, 0
        unknown_sources = []
        proven_units = defaultdict(dict)
        for at, kind, role, record in sorted(events, key=lambda item: (item[1] == 'call', item[0], item[1])):
            prompt_index, unit_index = units_at(at)
            if kind != 'call':
                key = (role, record['unit_id'], record['unit_sha256'])
                matched = [group[key] for group in unit_index.values() if key in group]
                if not matched and record['unit_id'].startswith('KC:'):
                    # Older authorization boundaries can start a window midway through
                    # today's source order. Recover the exact recorded anchor and size;
                    # only its original full unit SHA may establish completion.
                    anchor, count = record['unit_id'][3:].rsplit(':', 1)
                    if count.isdigit() and int(count) <= ns['UNIT_MESSAGES']:
                        source = by_id.get(anchor)
                        if source is not None and source['role'] == role:
                            selected = sorted((r for r in by_room[source['chatroom_id']]
                                if r['created_at'] <= at),
                                key=lambda r: (ns['cpq_windows']._time(r), r['evidence_id']))
                            offset = next((i for i, r in enumerate(selected) if r['evidence_id'] == anchor), None)
                            if offset is not None:
                                selected = selected[offset:offset + int(count)]
                                rebuilt = ns['_make_unit'](record['unit_id'], source['chatroom_id'], [
                                    {'evidence_id': r['evidence_id'], 'body': ns['cpq_windows']._one_line(r['body_text']),
                                     'at': ns['cpq_windows']._time(r)} for r in selected])
                                rebuilt['business_role'] = role
                                rebuilt['unit_sha256'] = sha(role + '\0' + rebuilt['unit_sha256'])
                                if rebuilt['unit_sha256'] == record['unit_sha256']:
                                    matched = [rebuilt]
                if not matched:
                    unmatched_segments += 1
                    continue
                for unit in matched:
                    prompt_sha = sha((ns['LEARNING_PROMPT'] if role == 'learning' else ns['PROMPT']) + unit['text'])
                    proven_units[prompt_sha][unit['unit_sha256']] = unit
                if record['status'] in ('done', 'no_facts'):
                    for unit in matched:
                        for source in {row['evidence_id'] for row in unit['rows']}:
                            coverage[source].add(unit['unit_id'])
                continue
            matched = [group[record['prompt_sha256']] for group in prompt_index.values()
                       if record['prompt_sha256'] in group]
            if not matched:
                matched = [unit for unit in proven_units.get(record['prompt_sha256'], {}).values()
                           if all(by_id[r['evidence_id']]['created_at'] <= at for r in unit['rows'])]
            if len(matched) != 1:
                continue
            unit = matched[0]
            key = (record['cache_key'], record['attempt'])
            linked_calls.add(key)
            source_ids = {item['evidence_id'] for item in unit['rows']}
            requested.update(source_ids)
            unit_records.setdefault(record['prompt_sha256'], {'unit': unit, 'complete': set(), 'unknown': False})
            entry = unit_records[record['prompt_sha256']]
            if record['state'] in ('hold', 'provider_started'):
                unknown.update(source_ids)
                entry['unknown'] = True
                unknown_sources.append((record['prompt_sha256'], source_ids))
                if private_review is not None:
                    private_review.setdefault('wechat_holds', []).append({
                        'channel': 'wechat',
                        'request_sha256': sha(json.dumps(tuple(record.values()), ensure_ascii=False,
                            sort_keys=True, separators=(',', ':'), allow_nan=False)),
                        'prompt_sha256': record['prompt_sha256'],
                        'source_keys': sorted(sha(source) for source in source_ids),
                        'content_sha256': sorted({sha(by_id[source]['body_text']) for source in source_ids}),
                        'source_sha256': sorted({by_id[source]['source_sha256'] for source in source_ids})})
            elif record['state'] == 'complete':
                raw = record['response_json']
                if not isinstance(raw, str) or sha(raw) != record['response_sha256']:
                    invalid_response += 1
                    continue
                # Valid JSON + byte hash is a reliable checkpoint, not fact acceptance.
                try:
                    json.loads(raw)
                except (TypeError, ValueError):
                    invalid_response += 1
                    continue
                entry['complete'].add(record['model'])
                for source in source_ids:
                    responses[source].add(record['model'])
        last_request_started = max((row['created_at'] for row in call_rows), default=None)
        classes = defaultdict(list)
        complete_ids = set()
        for row in rows:
            source = row['evidence_id']
            required = len(ns['_document_units'](row)) if len(ns['cpq_windows']._one_line(row['body_text'])) > 600 else 1
            complete = len(coverage[source]) >= required
            if source in unknown:
                category = '结果未知'
            elif required >= ns['DOC_CHUNKS']:
                category = '分块完整性待确认'
            elif complete:
                category = '已完成'
                complete_ids.add(source)
            elif type(row['source_created_at']) not in (int, float) or not math.isfinite(row['source_created_at']) or row['source_created_at'] <= 0:
                category = '来源时间待确认'
            elif source in responses:
                category = '有可靠响应但本地未完成'
            elif source not in requested and last_request_started is not None and row['created_at'] > last_request_started:
                category = '从未请求'
            elif source not in requested:
                category = '请求覆盖待确认'
            else:
                category = '已请求但响应待核对'
            classes[category].append(source)
        last_success = calls.execute("SELECT MAX(updated_at) FROM calls WHERE state='complete'").fetchone()[0]
        last_unknown = calls.execute("SELECT MAX(updated_at) FROM calls WHERE state='hold'").fetchone()[0]
        for category in ('已完成', '有可靠响应但本地未完成', '从未请求', '结果未知', '来源时间待确认'):
            classes[category]
        categories = {category: {'证据数': len(ids), '代表标识': ['E-' + sha(source)[:12] for source in sorted(ids)[:3]]}
                      for category, ids in sorted(classes.items())}
        # Keep a source-bound private list in memory for caller review; do not emit IDs or body hashes.
        role_counts = Counter(row['role'] for row in rows)
        unknown_origins = {by_id[source].get('source_sha256') for source in unknown
                           if isinstance(by_id[source].get('source_sha256'), str)}
        unknown_bodies = {sha(by_id[source]['body_text']) for source in unknown}
        def crosses_unknown(row):
            return (row['evidence_id'] in unknown
                    or row.get('source_sha256') in unknown_origins
                    or sha(row['body_text']) in unknown_bodies)
        def touches_unknown(entry):
            return any(crosses_unknown(by_id[row['evidence_id']]) for row in entry['unit']['rows'])
        local_only = [entry for entry in unit_records.values() if not entry['unknown'] and not touches_unknown(entry)
                      and len(entry['complete']) >= 2
                      and any(row['evidence_id'] not in complete_ids for row in entry['unit']['rows'])]
        partial = [entry for entry in unit_records.values() if not entry['unknown'] and not touches_unknown(entry)
                   and len(entry['complete']) == 1
                   and any(row['evidence_id'] not in complete_ids for row in entry['unit']['rows'])]
        after = [row for row in rows if max(row['source_created_at'] or row['received_at'], row['created_at']) > last_unknown]
        admitted_callbacks = {row[0] for row in catalog.execute('SELECT callback_sha256 FROM channel_business_evidence')}
        registry = {(row['account_sha256'], 'receive:' + row['account_sha256'] + ':group:' + row['channel_id_sha256'])
                    for row in rows}
        archive_after, unadmitted = 0, 0
        for row in state.execute('SELECT account_sha256,source_scope,callback_sha256,source_created_at,received_at,message_type FROM received_callback_archive'):
            if (row['account_sha256'], row['source_scope']) in registry and row['received_at'] > last_unknown:
                archive_after += 1
                unadmitted += row['callback_sha256'] not in admitted_callbacks
        facts = {}
        for table in ('kb_fact', 'kb_learning_fact'):
            facts[table] = catalog.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0]
        never_units = 0
        capped_documents = 0
        never_crossed = 0
        never_crossed_units = 0
        for row in rows:
            if len(ns['cpq_windows']._one_line(row['body_text'])) > 600:
                chunks = len(ns['_document_units'](row))
                capped_documents += chunks >= ns['DOC_CHUNKS']
            else:
                chunks = 1
            if row['evidence_id'] in classes['从未请求']:
                never_units += chunks
                if crosses_unknown(row):
                    never_crossed += 1
                    never_crossed_units += chunks
        report = {'口径': '角色登记与来源范围相符的已入Catalog证据候选；当前动态授权未核验，不是执行清单',
                  '已观察提取范围数': len(observed_roles), '不在既有提取范围角色候选数': excluded_role_candidates,
                  '候选证据数': len(rows), '角色证据数': dict(role_counts), '分类': categories,
                  '从未请求候选命中未知原始来源或正文数': never_crossed,
                  '从未请求排除未知交叉后证据数': len(classes['从未请求']) - never_crossed,
                  '从未请求排除未知交叉后单元数': never_units - never_crossed_units,
                  '从未请求排除未知交叉后双读者请求数上界': 2 * (never_units - never_crossed_units),
                  '从未请求稳定证据单元数': never_units, '从未请求双读者请求数上界': 2 * never_units,
                  'DOC_CHUNKS上限命中文档数': capped_documents,
                  '分块完整性': '未核验12块之外尾部；命中上限的文档不能据此宣布全文完成',
                  '来源窗口请求数': len(call_rows), '精确溯源请求数': len(linked_calls),
                  '未能精确溯源请求数': len(call_rows) - len(linked_calls),
                  '重建请求单元数': len(unit_records), '双响应仅续本地候选单元数': len(local_only),
                  '单响应尚缺第二读者单元数': len(partial), '未知请求数': len(unknown_sources),
                  '未知源证据数': len(unknown), '未匹配片段审计数': unmatched_segments,
                  '响应校验失败数': invalid_response, '已落库事实数': facts,
                  '最后成功检查点时间': last_success, '最后未知终止时间': last_unknown,
                  '最后未知之后已入库候选证据数': len(after),
                  '最后未知之后来源归档数': archive_after,
                  '其中未入业务证据归档数': unadmitted,
                  '自然增量边界': '未指定；由老板批准启动窗口时固定，不由盘点工具代填'}
        if private_review is not None:
            private_review['schema'] = 'kb-wechat-gap-review-v1'
        return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot', type=Path, required=True, help='/tmp 私有副本目录')
    parser.add_argument('--private-review', action='store_true', help='在新私有临时目录输出不含正文的未知来源审核输入')
    args = parser.parse_args()
    review = {} if args.private_review else None
    report = inventory(args.snapshot, review)
    if review is not None:
        if len(review.get('wechat_holds', [])) != 3:
            raise ValueError('三条未知来源未全部匹配，请先补齐证据')
        output = Path(tempfile.mkdtemp(prefix='kb-wechat-gap-review-', dir='/tmp'))
        fd = os.open(output / 'review.json', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'w') as stream:
            json.dump(review, stream, ensure_ascii=False, indent=2)
        report['私有审核目录'] = str(output)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error):
        raise SystemExit('无法盘点，请核对私有副本、源码基线和库结构；未生成执行清单') from None
