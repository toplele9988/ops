"""交叉核对阿里私有备份，只输出计数与脱敏摘要；不连接生产、不请求模型。"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from contextlib import ExitStack, closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import tempfile


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def sha(value):
    return hashlib.sha256(value if isinstance(value, bytes) else value.encode()).hexdigest()


def file_sha(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def need(ok):
    if not ok:
        raise ValueError('来源、响应或落库证据不一致，请重新准备同一审阅时点的副本')


def private_snapshot(path):
    path = Path(path).absolute()
    need('..' not in path.parts and path.is_relative_to('/tmp')
         and not any(p.is_symlink() for p in (path, *path.parents)))
    info = path.stat()
    need(stat.S_ISDIR(info.st_mode) and info.st_uid == os.geteuid()
         and stat.S_IMODE(info.st_mode) == 0o700)
    names = ('analysis.sqlite3', 'inbound.sqlite3', 'ledger.sqlite3', 'catalog.sqlite3',
             'source-freeze.json', 'usage.json')
    for name in names:
        info = (path / name).lstat()
        need(stat.S_ISREG(info.st_mode) and info.st_nlink == 1
             and info.st_uid == os.geteuid() and stat.S_IMODE(info.st_mode) == 0o600)
    return path


def covered(ranges, length):
    end = 0
    for start, stop in sorted(ranges):
        if start > end:
            return False
        end = max(end, stop)
    return end >= length


def classify_source(key, record, complete, responses, requested, unknown):
    """跨版本按原来源及完整字符覆盖判定；未知优先于任何已完成状态。"""
    length = len(record['body'])
    if key in unknown:
        return 'unknown'
    if covered(complete.get(key, ()), length):
        return 'complete'
    if covered(responses.get(key, ()), length):
        return 'local_resume'
    if not record.get('sent_at'):
        return 'time_unconfirmed'
    if not requested.get(key):
        return 'never_requested'
    return 'requested_incomplete'


def analyze(snapshot):
    snapshot = private_snapshot(snapshot)
    with ExitStack() as stack:
        databases = {}
        for name in ('analysis', 'inbound', 'ledger', 'catalog'):
            db = stack.enter_context(closing(sqlite3.connect(
                (snapshot / (name + '.sqlite3')).as_uri() + '?mode=ro', uri=True)))
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA query_only=ON')
            db.execute('BEGIN')
            databases[name] = db
        db, inbound, ledger, catalog = (databases[n] for n in ('analysis', 'inbound', 'ledger', 'catalog'))
        # Known column names were checked against production code and sqlite_master.
        current = {r[0] for r in db.execute('SELECT analysis_version FROM current_conversations')}
        analyses = {r['version']: dict(r) for r in db.execute('SELECT * FROM analyses')}
        windows = [dict(r) for r in db.execute('SELECT * FROM windows')]
        attempts = defaultdict(list)
        for row in db.execute('SELECT * FROM model_attempts'):
            attempts[(row['version'], row['window_index'])].append(dict(row))
        bindings = {(r['version'], r['window_index']): dict(r)
                    for r in db.execute('SELECT * FROM validated_executions')}
        executions = {(r['version'], r['window_index'], r['attempt']): dict(r)
                      for r in db.execute('SELECT * FROM execution_attempts')}
        freeze = json.loads((snapshot / 'source-freeze.json').read_text())
        need(freeze['schema'] == 'icbu_frozen_source_versions_v1')
        frozen = {(e['conversation_key'], e['source_version']) for e in freeze['entries']}
        need(len(freeze['entries']) == len(frozen) == 471)
        freeze_audit = db.execute("SELECT payload_json,event_sha256 FROM routing_audit WHERE kind='quota_pool_source_freeze'").fetchall()
        need(len(freeze_audit) == 1 and sha(freeze_audit[0]['payload_json']) == freeze_audit[0]['event_sha256'])
        saved_freeze = json.loads(freeze_audit[0]['payload_json'])['payload']
        need(saved_freeze['manifest'] == freeze and saved_freeze['manifest_sha256'] == sha(canonical(freeze)))

        records = {}
        for version in current:
            a = analyses[version]
            g = json.loads(a['group_json'])
            need(g['source_version'] == a['source_version'] and g['key'] == a['conversation_key'])
            for r in g['records']:
                key = (r['inbound_id'], r['source_sha256'])
                need(key not in records)
                records[key] = (r, a)

        # Every current source must still match the archive and successful history index.
        for key, (r, a) in records.items():
            row = inbound.execute('SELECT id,payload,fingerprint,state,created_at FROM inbound WHERE id=?',
                                  (r['inbound_id'],)).fetchone()
            saved = ledger.execute('SELECT * FROM history_index WHERE inbound_id=?',
                                   (r['inbound_id'],)).fetchone()
            need(row is not None and saved is not None)
            payload = json.loads(row['payload'])
            need(row['state'] == 'archived' and payload.get('history') is True
                 and sha(row['payload']) == r['source_sha256']
                 and payload.get('content') == r['body']
                 and all(payload.get(k) == r[k] for k in ('account', 'conversation_id', 'role'))
                 and row['fingerprint'] == r['receiver_fingerprint'])
            need(saved['status'] == 'indexed' and saved['payload_sha256'] == r['source_sha256']
                 and saved['owner'] == a['owner'] and saved['source_scope'] == r['scope']
                 and saved['account'] == r['account'] and saved['conversation_id'] == r['conversation_id']
                 and saved['model_version'] == a['embedding_model'] and saved['asset_id'])
        need(ledger.execute("SELECT count(*) FROM history_index WHERE status='indexed'").fetchone()[0]
             == len(records))

        unknown_windows = set()
        timeout_attempts = {}
        for row in db.execute("SELECT payload_json,event_sha256 FROM routing_audit WHERE kind='quota_pool_failed'"):
            need(sha(row['payload_json']) == row['event_sha256'])
            payload = json.loads(row['payload_json'])['payload']
            if payload.get('matrix_error_code') == 'api_timeout':
                unknown_windows.add((payload['version'], payload['window_index']))
                timeout_attempts[(payload['version'], payload['window_index'])] = payload['attempt']
        for key, rows in attempts.items():
            if any(r['status'] in ('processing', 'interrupted') for r in rows):
                unknown_windows.add(key)

        requested, complete, responses = defaultdict(list), defaultdict(list), defaultdict(list)
        unknown = set()
        checkpoint_ids = defaultdict(set)
        verified_assets = set()
        counts = Counter()
        current_counts = Counter()
        pending_sources = set()
        pending_mixed = pending_untouched = 0
        unbound_old_responses = 0
        for w in windows:
            a = analyses[w['version']]
            key = (w['version'], w['window_index'])
            plan = json.loads(w['plan_json'])
            need(plan['window_sha256'] == w['window_sha256'] and plan['index'] == w['window_index'])
            if w['version'] in current:
                current_counts[w['state']] += 1
            else:
                counts[('frozen_' if (a['conversation_key'], a['source_version']) in frozen else 'unfrozen_')
                       + w['state']] += 1
            for s in plan['segments']:
                source = (s['inbound_id'], s['source_sha256'])
                if source in records:
                    r = records[source][0]
                    need(r['body'][s['char_start']:s['char_end']] == s['text'])
                if attempts[key]:
                    requested[source].append((s['char_start'], s['char_end']))
                if key in unknown_windows:
                    unknown.add(source)
                if w['version'] in current and w['state'] == 'pending':
                    pending_sources.add(source)
            if w['state'] != 'validated':
                continue
            result = json.loads(w['result_json'])
            b = bindings.get(key)
            if b is None and w['version'] not in current:
                # Old result flags alone never authorize reuse or another request.
                unbound_old_responses += 1
                continue
            need(b is not None and b['source_version'] == a['source_version']
                 and b['window_sha256'] == w['window_sha256']
                 and b['registry_sha256'] == plan['registry_sha256']
                 and result['window_sha256'] == w['window_sha256']
                 and result['registry_sha256'] == plan['registry_sha256']
                 and result['source_version'] == a['source_version']
                 and b['result_sha256'] == sha(canonical(result)))
            t = [r for r in attempts[key] if r['attempt'] == b['attempt']]
            need(len(t) == 1 and t[0]['status'] == 'validated'
                 and t[0]['response_sha256'] == b['response_sha256'] and t[0]['finished_at'] is not None)
            e = executions.get((*key, b['attempt']))
            need(e is None and b['origin'] == 'legacy_v4_success_audit' or e is not None
                 and e['requested_model'] == b['requested_model']
                 and e['source_version'] == a['source_version'] and e['window_sha256'] == w['window_sha256']
                 and e['registry_sha256'] == plan['registry_sha256'])
            local_done = a['state'] == 'no_supported_claims' and not result['claims']
            if a['state'] == 'indexed':
                asset = catalog.execute('SELECT * FROM multimedia_assets WHERE asset_id=?',
                                        (a['asset_id'],)).fetchone()
                need(asset is not None and asset['source_id'] == 'icbu-conversation-analysis'
                     and asset['source_type'] == 'icbu_conversation_analysis_candidate'
                     and asset['source_sha256'] == a['asset_sha256'] and asset['owner'] == a['owner'])
                document = json.loads(asset['extraction_snapshot_json'])
                need(document['analysis_version'] == a['version']
                     and document['source_version'] == a['source_version'] and document['claims']
                     and sha(canonical(document)) == asset['source_sha256'])
                chunks = catalog.execute('SELECT owner,source_scope,evidence_json FROM knowledge_chunks WHERE asset_id=?',
                                         (a['asset_id'],)).fetchall()
                need(chunks and all(c['owner'] == asset['owner'] and c['source_scope'] == asset['source_scope']
                                    for c in chunks))
                verified_assets.add(a['asset_id'])
                local_done = True
            checkpoint = sha(canonical([w['version'], w['window_index'], b['result_sha256'], b['response_sha256']]))
            for s in plan['segments']:
                source = (s['inbound_id'], s['source_sha256'])
                responses[source].append((s['char_start'], s['char_end']))
                checkpoint_ids[source].add(checkpoint)
                if local_done:
                    complete[source].append((s['char_start'], s['char_end']))

        classes = defaultdict(list)
        entries = []
        backlog_entries = []
        time_ranges = defaultdict(list)
        labels = {'complete': '已完成', 'local_resume': '可靠响应但本地未完成', 'never_requested': '从未请求',
                  'unknown': '结果未知', 'time_unconfirmed': '来源时间待确认',
                  'requested_incomplete': '有请求审计但缺完整可靠响应，需人工核对'}
        for key, (r, a) in sorted(records.items()):
            status = classify_source(key, r, complete, responses, requested, unknown)
            identifier = sha(canonical(key))
            classes[status].append('A-' + identifier[:12])
            if r.get('sent_at'):
                time_ranges[status].append(r['sent_at'])
            entries.append({'source_key_sha256': identifier, 'fingerprint': sha(canonical(r)), 'classification': status,
                            'checkpoint_sha256': sorted(checkpoint_ids[key]),
                            'source_created_at': r.get('sent_at'), 'received_at': r['received_at'],
                            'source_version': a['source_version']})
            if status == 'never_requested':
                backlog_entries.append({
                    'channel': 'alibaba', 'source_id': r['inbound_id'], 'fingerprint': sha(canonical(r)),
                    'source_sha256': r['source_sha256'],
                    'content_sha256': sha(r['body']), 'prompt_sha256': None,
                    'source_at': datetime.fromisoformat(r['sent_at']).timestamp(),
                    'admitted_at': datetime.fromisoformat(r['received_at']).timestamp(),
                    'reason': 'never_requested', 'checkpoints': [], 'completed_steps': []})
        for w in windows:
            if w['version'] not in current or w['state'] != 'pending':
                continue
            sources = {(s['inbound_id'], s['source_sha256']) for s in json.loads(w['plan_json'])['segments']}
            if any(requested[k] for k in sources):
                pending_mixed += 1
            else:
                pending_untouched += 1
        usage = json.loads((snapshot / 'usage.json').read_text())
        totals = Counter()
        for r in usage['models'].values():
            for name in ('calls', 'completed_calls', 'actual_total_tokens', 'unknown_reserved_tokens'):
                need(type(r[name]) is int and r[name] >= 0)
                totals[name] += r[name]
        report = {'口径': '当前来源按原始消息ID与payload指纹去重，并跨全部历史版本核对响应及落库覆盖；不按pending整窗重跑',
                  '当前窗口': dict(current_counts), '旧版本窗口': dict(counts), '当前来源数': len(records),
                  '分类': {label: {'数量': len(classes[code]), '代表': classes[code][:3]}
                           for code, label in labels.items()},
                  '当前待处理窗口含来源数': len(pending_sources),
                  '当前待处理窗口中已完成来源数': sum(covered(complete[k], len(records[k][0]['body'])) for k in pending_sources),
                  '当前待处理窗口与未知来源交集': len(pending_sources & unknown),
                  '当前待处理窗口含旧请求数': pending_mixed, '当前待处理窗口全部从未请求数': pending_untouched,
                  '已核对知识资产数_含历史': len(verified_assets), '冻结来源版本数': len(frozen),
                  '旧响应无执行绑定_未用于完成判定': unbound_old_responses,
                  '来源时间范围': {labels[k]: {'最早': min(v), '最晚': max(v)} for k, v in time_ranges.items() if v},
                  '本地模型账本统计': dict(totals), '云端剩余额度': '未知', '实际单价与费用': '未知',
                  '新增付费预算': '未知', '生产写入': 0, '模型请求': 0}
        ali_holds = []
        for key, attempt in sorted(timeout_attempts.items()):
            rows = [r for r in attempts[key] if r['attempt'] == attempt]
            execution = executions.get((*key, attempt))
            need(len(rows) == 1 and rows[0]['response_sha256'] is None and execution is not None)
            w = next(w for w in windows if (w['version'], w['window_index']) == key)
            a = analyses[w['version']]
            originals = {r['inbound_id']: r for r in json.loads(a['group_json'])['records']}
            source_ids = {s['inbound_id'] for s in json.loads(w['plan_json'])['segments']}
            ali_holds.append({
                'channel': 'alibaba',
                'request_sha256': sha(canonical({'attempt': rows[0], 'execution': execution})),
                'source_keys': sorted(sha(identifier) for identifier in source_ids),
                'source_sha256': sorted({originals[identifier]['source_sha256'] for identifier in source_ids}),
                'content_sha256': sorted({sha(originals[identifier]['body']) for identifier in source_ids}),
                'prompt_sha256': execution['prompt_sha256']})
        need(len(ali_holds) == 1)
        snapshots = {name: file_sha(snapshot / (name + '.sqlite3')) for name in databases}
        return report, {'schema': 'kb-alibaba-gap-review-v1', 'entries': entries,
                        'ali_holds': ali_holds,
                        'backlog_review_template': {
                            'schema': 'kb-backlog-review-v1', 'after': None,
                            'snapshots': snapshots,
                            'entries': backlog_entries},
                        '待补项': '老板批准固定恢复边界后填写after；稳定新证据适配器核对授权及原提示词后填写prompt_sha256。当前草案不能生成执行清单。',
                        'snapshot_sha256': {name + '.sqlite3': value for name, value in snapshots.items()}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot', required=True, type=Path, help='/tmp 中0700目录，包含六个0600副本文件')
    parser.add_argument('--private-review', action='store_true', help='在新私有临时目录输出不含正文的来源审核输入')
    args = parser.parse_args()
    report, review = analyze(args.snapshot)
    if args.private_review:
        output = Path(tempfile.mkdtemp(prefix='kb-alibaba-gap-review-', dir='/tmp'))
        fd = os.open(output / 'review.json', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'w') as stream:
            json.dump(review, stream, ensure_ascii=False, sort_keys=True, indent=2)
            stream.write('\n')
        report['私有审核输入目录'] = str(output)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error):
        raise SystemExit('盘点未完成，请核对私有副本权限、真实表结构与来源审计；未请求模型') from None
