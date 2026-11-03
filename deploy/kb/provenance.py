"""只读私有副本核对四条未知请求、文档尾部和阿里单来源计划；不授予请求权限。"""
from __future__ import annotations

import argparse
import ast
from contextlib import closing
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace

import inventory_alibaba as ali
import inventory_wechat as wechat
from source_gate import verify as verify_quarantine

CORE = Path('/opt/imgw/scripts/icbu-conversation-analysis/analysis_core.py')
CORE_SHA = '023433809d08e917b1cc11ef6154e9521f1a602c9a830596b3913db36943f82a'


def need(ok):
    if not ok:
        raise ValueError('溯源证据不一致，请保留隔离并重新准备私有副本')


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def sha(value):
    return hashlib.sha256(value if isinstance(value, bytes) else value.encode()).hexdigest()


def alibaba_algorithms():
    raw = CORE.read_bytes()
    need(sha(raw) == CORE_SHA)
    names = {'Rejected', 'require', 'canonical', 'sha', 'utc', 'group_key', 'normalize_record',
             'record_version', 'plan_windows', 'validate_coverage', 'prompt_for', 'window_registry'}
    constants = {'PROMPT_RULES'}
    selected = [node for node in ast.parse(raw).body
                if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in names
                or isinstance(node, ast.Assign) and any(
                    isinstance(target, ast.Name) and target.id in constants for target in node.targets)]
    namespace = {'hashlib': hashlib, 'json': json, 'datetime': datetime, 'timezone': timezone,
                 'defaultdict': __import__('collections').defaultdict, 're': __import__('re')}
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(CORE), 'exec'), namespace)
    return SimpleNamespace(**namespace)


def source_baseline():
    baseline = json.loads((Path(__file__).parent / 'baseline.json').read_text())
    for name, expected in baseline['sha256'].items():
        path = Path(name)
        need(not path.is_absolute() and '..' not in path.parts)
        source = Path(baseline['base']) / path
        need(not any(p.is_symlink() for p in (source, *source.parents)))
        need(sha(source.read_bytes()) == expected)
    return baseline['sha256']


def alibaba_single_source(group, record, *, core=None):
    """沿用原计划/提示词/全字符覆盖算法；只接受恰一窗，不代替原来源授权。"""
    core = core or alibaba_algorithms()
    r = core.normalize_record(record)
    need(r['account'] == group['account'] and r['conversation_id'] == group['conversation_id'])
    need(group['key'] == core.group_key(group['owner'], r['account'], r['conversation_id']))
    singleton = {key: group[key] for key in ('key', 'owner', 'account', 'conversation_id')}
    singleton.update(records=[r], source_version=core.record_version([r]))
    plans = core.plan_windows(singleton)
    need(len(plans) == 1)
    prompt = core.prompt_for(singleton, plans[0])
    # Source identity and prompt stay independent of later conversation arrivals.
    return {'unit_id': 'KA:' + sha(canonical([group['key'], r['inbound_id'], r['source_sha256']])),
            'group': singleton, 'window': plans[0], 'prompt': prompt, 'prompt_sha256': sha(prompt)}


def document_completeness(row, *, algorithms=None):
    """只提高原分块上限作完整性对照，原十二块字节不变；不生成尾部请求。"""
    ns = algorithms or wechat.algorithms()
    original_limit = ns['DOC_CHUNKS']
    capped = ns['_document_units'](row)
    try:
        ns['DOC_CHUNKS'] = max(original_limit + 1, len(row['body_text']) + 1)
        full = ns['_document_units'](row)
    finally:
        ns['DOC_CHUNKS'] = original_limit
    need(full[:len(capped)] == capped)
    return {'required_chunks': len(full), 'existing_limit_chunks': len(capped),
            'omitted_chunks': len(full) - len(capped),
            'omitted_characters': sum(len(r['body']) for u in full[len(capped):] for r in u['rows']),
            'decision': 'exclude_entire_document' if len(full) > len(capped) else 'complete_within_limit'}


def checked_schema(db, table, expected):
    # Tables are fixed internal literals; no input is interpolated into SQL.
    actual = [r[1] for r in db.execute('PRAGMA table_info(' + table + ')')]
    need(actual == expected)


def wechat_unknowns(snapshot, review):
    with closing(wechat.open_copy(snapshot / 'calls.sqlite3')) as db:
        checked_schema(db, 'calls', ['cache_key', 'attempt', 'prompt_sha256', 'model', 'state',
            'error_code', 'day', 'reserved_tokens', 'response_json', 'response_sha256', 'created_at', 'updated_at'])
        checked_schema(db, 'scope', ['key', 'value'])
        rows = db.execute("SELECT * FROM calls WHERE state IN ('hold','provider_started') ORDER BY created_at").fetchall()
        need(len(rows) == 3 and [tuple(r) for r in db.execute('SELECT key,value FROM scope ORDER BY key')]
             == [('hold', 'api_timeout')])
        by_checkpoint = {h['request_sha256']: h for h in review['wechat_holds']}
        need(len(by_checkpoint) == 3)
        checklist = []
        for r in rows:
            checkpoint = sha(canonical(tuple(r)))
            h = by_checkpoint.pop(checkpoint, None)
            need(h is not None and h['prompt_sha256'] == r['prompt_sha256'] and r['state'] == 'hold'
                 and r['error_code'] == 'api_timeout' and r['response_json'] is None
                 and r['response_sha256'] is None and r['reserved_tokens'] > 0)
            checklist.append({'channel': 'wechat', 'request_sha256': checkpoint,
                'prompt_sha256': r['prompt_sha256'], 'cache_key_sha256': sha(r['cache_key']),
                'attempt': r['attempt'], 'model': r['model'], 'reserved_tokens': r['reserved_tokens'],
                'source_count': len(h['source_keys']), 'source_set_sha256': sha(canonical(h)),
                'state': '永久隔离'})
        need(not by_checkpoint)
        return checklist, dict(db.execute('SELECT state,count(*) FROM calls GROUP BY state'))


def document_checks(snapshot):
    ns = wechat.algorithms()
    results = []
    with closing(wechat.open_copy(snapshot / 'catalog.sqlite3')) as db:
        for source in db.execute('SELECT * FROM channel_business_evidence'):
            row = dict(source)
            if not isinstance(row.get('body_text'), str) or len(ns['cpq_windows']._one_line(row['body_text'])) <= 600:
                continue
            row['chatroom_id'] = 'source-only-completeness-check'
            if len(ns['_document_units'](row)) < ns['DOC_CHUNKS']:
                continue
            result = document_completeness(row, algorithms=ns)
            result['source_key_sha256'] = sha(row['evidence_id'])
            result['source_sha256'] = row['source_sha256']
            results.append(result)
    return results


def alibaba_units(snapshot, review):
    core = alibaba_algorithms()
    wanted = {i['source_id']: i for i in review['backlog_review_template']['entries']}
    result, excluded = [], []
    with closing(wechat.open_copy(snapshot / 'analysis.sqlite3')) as db:
        need({'group_json', 'version'} <= {r[1] for r in db.execute('PRAGMA table_info(analyses)')})
        need('analysis_version' in {r[1] for r in db.execute('PRAGMA table_info(current_conversations)')})
        for row in db.execute('SELECT group_json FROM analyses WHERE version IN (SELECT analysis_version FROM current_conversations)'):
            group = json.loads(row['group_json'])
            for record in group['records']:
                source = wanted.pop(record['inbound_id'], None)
                if source is None:
                    continue
                need(source['source_sha256'] == record['source_sha256'] and source['content_sha256'] == sha(record['body']))
                try:
                    unit = alibaba_single_source(group, record, core=core)
                    repeat = alibaba_single_source(deepcopy(group), deepcopy(record), core=core)
                    need(unit == repeat)
                except (ValueError, core.Rejected):
                    excluded.append({'source_key_sha256': sha(record['inbound_id']), 'reason': '无法证明恰一个完整稳定单元，排除'})
                    continue
                result.append({'source_key_sha256': sha(record['inbound_id']),
                    'source_sha256': record['source_sha256'], 'unit_id_sha256': sha(unit['unit_id']),
                    'prompt_sha256': unit['prompt_sha256'], 'window_sha256': unit['window']['window_sha256'],
                    'registry_sha256': unit['window']['registry_sha256'], 'characters': len(record['body']),
                    'segments': len(unit['window']['segments']), 'evidence_spans': len(unit['window']['evidence_spans'])})
    need(not wanted)
    return result, excluded


def analyze(wechat_snapshot, alibaba_snapshot):
    pins = source_baseline()
    ws = wechat.private(wechat_snapshot)
    als = ali.private_snapshot(alibaba_snapshot)
    wr = {}
    wc = wechat.inventory(ws, wr)
    ac, ar = ali.analyze(als)
    checklist, calls = wechat_unknowns(ws, wr)
    quarantine = verify_quarantine({'schema': 'kb-source-quarantine-v1', 'holds': wr['wechat_holds'] + ar['ali_holds']})
    ah = ar['ali_holds'][0]
    checklist.append({'channel': 'alibaba', 'request_sha256': ah['request_sha256'],
        'prompt_sha256': ah['prompt_sha256'], 'source_count': len(ah['source_keys']),
        'source_set_sha256': sha(canonical(ah)), 'state': '永久隔离'})
    documents = document_checks(ws)
    units, excluded = alibaba_units(als, ar)
    summary = {'结果': '已完成只读副本核对，未授予模型请求权限', 'calls状态数量': calls,
        '微信精确溯源请求数': wc['精确溯源请求数'], '微信旧请求仍未能精确溯源数': wc['未能精确溯源请求数'],
        '未知请求逐条核对数': len(checklist),
        '微信未知请求直接来源数': [r['source_count'] for r in checklist if r['channel'] == 'wechat'],
        '阿里未知请求直接来源数': [r['source_count'] for r in checklist if r['channel'] == 'alibaba'],
        '文档上限命中数': len(documents), '文档已确认截断数': sum(r['omitted_chunks'] > 0 for r in documents),
        '文档尾部处理': '命中截断的整份文档及其已有块暂时排除，不补发尾部、不宣称全文完成',
        '阿里从未请求来源数': len(ar['backlog_review_template']['entries']),
        '阿里恰一稳定单源窗口数': len(units), '阿里无法确认而排除数': len(excluded),
        '动态授权': '已核对现行源码判断；未加载含凭据配置，真实当前来源准入必须由原函数在每轮及请求前重新判断',
        '来源快照': 'R3-C 已存在的私有只读副本；不是本次生产启动时点的原子快照', '模型请求': 0, '生产写入': 0}
    private = {'schema': 'kb-r5a-provenance-v1', 'summary': summary,
        'wechat_source_baseline': pins, 'alibaba_core_sha256': CORE_SHA,
        'snapshot_sha256': {'wechat_' + n: ali.file_sha(ws / (n + '.sqlite3')) for n in ('catalog', 'calls', 'state')},
        'unknown_checklist': checklist, 'quarantine': quarantine,
        'document_checks': documents, 'alibaba_units': units, 'alibaba_excluded': excluded}
    private['snapshot_sha256'].update({'alibaba_' + n: v for n, v in ar['snapshot_sha256'].items()})
    return summary, private


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wechat-snapshot', required=True, type=Path)
    parser.add_argument('--alibaba-snapshot', required=True, type=Path)
    args = parser.parse_args()
    summary, private = analyze(args.wechat_snapshot, args.alibaba_snapshot)
    output = Path(tempfile.mkdtemp(prefix='kb-r5a-provenance-', dir='/tmp'))
    fd = os.open(output / 'checklist.json', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as stream:
        stream.write(canonical(private) + '\n')
    summary['私有校验清单'] = str(output / 'checklist.json')
    summary['清单sha256'] = ali.file_sha(output / 'checklist.json')
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, TypeError, KeyError, OSError, sqlite3.Error):
        raise SystemExit('溯源核对未完成，请检查私有副本、源码基线和四条未知记录；未请求模型') from None
