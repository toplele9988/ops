"""只统计 /tmp 私有副本的提示词长度；不导入模型、不生成请求授权或正文报告。"""
from __future__ import annotations

import argparse
import ast
from collections import Counter
from contextlib import closing
import json
import math
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import inventory_alibaba as ali
import inventory_wechat as wechat
import provenance
from official_deepseek import reservation_tokens

CLIENT = Path('/opt/imgw/wechat/domestic_digest/client.py')
CLIENT_SHA = 'a3171dd827223f14dfbf300b78683ce222bf950a411353cb04bd7ba13b1fb6cc'
POOL = Path('/opt/imgw/scripts/icbu-conversation-analysis/analysis_quota_pool.py')
POOL_SHA = 'beb98bf2de43de4e4599c3e8aaf3ec23bc3be80c354304a8cb3274fac22391ec'


def need(ok):
    if not ok:
        raise ValueError('估算输入不完整，请重新核对私有副本和来源排除范围')


def distribution(values):
    values = sorted(values)
    need(values and all(type(value) is int and value >= 0 for value in values))
    return {'count': len(values), 'sum': sum(values), 'min': values[0],
            'p50': values[math.ceil(len(values) * .50) - 1],
            'p90': values[math.ceil(len(values) * .90) - 1],
            'p95': values[math.ceil(len(values) * .95) - 1],
            'p99': values[math.ceil(len(values) * .99) - 1], 'max': values[-1],
            'mean': round(sum(values) / len(values), 2)}


def request_contracts():
    """只加载已绑定源码中的纯函数和 schema 字面量，不导入客户端或凭据模块。"""
    raw = CLIENT.read_bytes()
    need(wechat.sha(raw) == CLIENT_SHA)
    selected = [node for node in ast.parse(raw).body
                if isinstance(node, ast.FunctionDef) and node.name == 'request_payload']
    need(len(selected) == 1)
    namespace = {'json': json, 'MAX_PROMPT_CHARS': 120000, 'DigestAPIError': ValueError}
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(CLIENT), 'exec'), namespace)
    core_raw = provenance.CORE.read_bytes()
    need(wechat.sha(core_raw) == provenance.CORE_SHA)
    core_values = {}
    for node in ast.parse(core_raw).body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in {'SCHEMA', 'CATEGORIES', 'STANCES'}:
                    core_values[target.id] = ast.literal_eval(node.value)
    need(set(core_values) == {'SCHEMA', 'CATEGORIES', 'STANCES'})
    schemas = []
    for path, expected, names, context in (
            (wechat.EXTRACT, wechat.EXTRACT_SHA, {'MAX_FACTS', 'SCHEMA'}, {}),
            (POOL, POOL_SHA, {'SCHEMA'}, {'core': SimpleNamespace(**core_values)})):
        source = path.read_bytes()
        need(wechat.sha(source) == expected)
        nodes = [node for node in ast.parse(source).body if isinstance(node, ast.Assign)
                 and any(isinstance(t, ast.Name) and t.id in names for t in node.targets)]
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), 'exec'), context)
        schemas.append(context['SCHEMA'])
    return namespace['request_payload'], schemas


def summarize(prompts, *, readers, output_tokens, factor_low=1.0, factor_high=1.5,
              wrapper_tokens=1024, input_margin=1.15, payload_bytes=None):
    """字符启发式与原 UTF-8 字节保守预留分列，避免拿均值作生产准入。"""
    need(type(readers) is int and readers > 0 and type(output_tokens) is int and output_tokens > 0)
    need(type(wrapper_tokens) is int and wrapper_tokens >= 0)
    need(all(type(v) in (int, float) and math.isfinite(v) for v in
             (factor_low, factor_high, input_margin)))
    need(0 < factor_low <= factor_high and input_margin >= 1)
    need(prompts and all(isinstance(prompt, str) and prompt for prompt in prompts))
    chars = [len(prompt) for prompt in prompts]
    raw_bytes = [len(prompt.encode()) for prompt in prompts]
    low = [math.ceil(value * factor_low * input_margin) + wrapper_tokens for value in chars]
    high = [math.ceil(value * factor_high * input_margin) + wrapper_tokens for value in chars]
    physical = [max(estimate, byte_count + wrapper_tokens) + output_tokens
                for estimate, byte_count in zip(high, raw_bytes)]
    complete = None
    if payload_bytes is not None:
        need(len(payload_bytes) == len(prompts)
             and all(type(value) is int and value > 0 for value in payload_bytes))
        complete = [max(prior, size + output_tokens + wrapper_tokens)
                    for prior, size in zip(physical, payload_bytes)]
    result = {'units': len(prompts), 'readers': readers, 'requests': len(prompts) * readers,
            'prompt_characters': distribution(chars),
            'input_tokens_low': sum(low) * readers, 'input_tokens_high': sum(high) * readers,
            'output_tokens_reserved': len(prompts) * readers * output_tokens,
            'total_tokens_low': (sum(low) + len(prompts) * output_tokens) * readers,
            'total_tokens_high': (sum(high) + len(prompts) * output_tokens) * readers,
            'prompt_only_physical_reservation_tokens': sum(physical) * readers,
            'physical_reservation_tokens': sum(complete or physical) * readers,
            'physical_per_request': distribution(physical),
            'assumptions': {'characters_to_tokens_low': factor_low,
                'characters_to_tokens_high': factor_high, 'input_margin': input_margin,
                'wrapper_tokens_per_request': wrapper_tokens, 'output_tokens_per_request': output_tokens}}
    if complete is not None:
        result['full_payload_bytes'] = distribution(payload_bytes)
        result['physical_per_request'] = distribution(complete)
    return result


def crosses(row, holds):
    return any(wechat.sha(row['evidence_id']) in hold['source_keys']
               or row['source_sha256'] in hold['source_sha256']
               or wechat.sha(row['body_text']) in hold['content_sha256'] for hold in holds)


def wechat_prompts(snapshot, holds, expected):
    """R3-C 已证最后历史调用之后入库候选；原动态授权仍须现场复核。"""
    snapshot = wechat.private(snapshot)
    ns = wechat.algorithms()
    prompts, selected_sources, rejected = [], 0, Counter()
    with closing(wechat.open_copy(snapshot / 'catalog.sqlite3')) as db, \
            closing(wechat.open_copy(snapshot / 'calls.sqlite3')) as calls, \
            closing(wechat.open_copy(snapshot / 'state.sqlite3')) as state:
        tables = {'channel_business_evidence': {'evidence_id', 'classification_id', 'owner', 'source_scope',
                    'source_created_at', 'created_at', 'received_at', 'source_kind', 'price_kind',
                    'body_text', 'source_sha256', 'is_cost_price'},
                  'channel_role_registry': {'classification_id', 'owner', 'source_scope',
                    'channel_kind', 'channel_role', 'channel_id_sha256'},
                  'kb_segment': {'chatroom_id'}, 'kb_learning_segment': {'chatroom_id'}}
        for table, fields in tables.items():
            need(fields <= {r[1] for r in db.execute('PRAGMA table_info(' + table + ')')})
        need('created_at' in {r[1] for r in calls.execute('PRAGMA table_info(calls)')})
        need('observation_json' in {r[1] for r in state.execute('PRAGMA table_info(channel_role_intake)')})
        last_request = calls.execute('SELECT MAX(created_at) FROM calls').fetchone()[0]
        need(type(last_request) in (int, float) and math.isfinite(last_request))
        observed = {}
        for table, role in (('kb_segment', 'supplier'), ('kb_learning_segment', 'learning')):
            for room in db.execute('SELECT DISTINCT chatroom_id FROM ' + table):
                key = wechat.sha(room[0])
                need(key not in observed or observed[key] == role)
                observed[key] = role
        learning = {r[0] for r in state.execute(
            "SELECT DISTINCT json_extract(observation_json,'$.classification_id') "
            "FROM channel_role_intake WHERE json_extract(observation_json,'$.registered_business_role')='learning'")}
        for r in db.execute('''SELECT e.*,r.channel_role,r.channel_id_sha256
            FROM channel_business_evidence e JOIN channel_role_registry r USING(classification_id)
            WHERE e.owner=r.owner AND e.source_scope=r.source_scope AND r.channel_kind='group'
              AND r.channel_role IN ('CHANNEL_ROLE_SUPPLIER','CHANNEL_ROLE_NEUTRAL')
              AND e.created_at>? ORDER BY e.evidence_id''', (last_request,)):
            row = dict(r, chatroom_id=r['channel_id_sha256'])
            role = 'supplier' if row['channel_role'] == 'CHANNEL_ROLE_SUPPLIER' else 'learning'
            if (observed.get(row['chatroom_id']) != role or role == 'learning'
                    and row['classification_id'] not in learning):
                rejected['role_unconfirmed'] += 1
                continue
            if (not ns['_eligible'](row) or role == 'learning' and not (
                    row['source_kind'] in ('message', 'historical_message')
                    and row['is_cost_price'] == 0 and row['price_kind'] == 'not_price')):
                rejected['not_eligible'] += 1
                continue
            if (type(row['source_created_at']) not in (int, float)
                    or not math.isfinite(row['source_created_at']) or row['source_created_at'] <= 0):
                rejected['source_time_unconfirmed'] += 1
                continue
            if crosses(row, holds):
                rejected['unknown_cross'] += 1
                continue
            units = ns['_document_units'](row)
            if len(units) >= ns['DOC_CHUNKS']:
                rejected['document_incomplete'] += 1
                continue
            if len(ns['cpq_windows']._one_line(row['body_text'])) <= 600:
                units = [ns['_make_unit']('KL:' + row['evidence_id'], row['chatroom_id'], [{
                    'evidence_id': row['evidence_id'], 'body': ns['cpq_windows']._one_line(row['body_text']),
                    'at': ns['cpq_windows']._time(row)}])]
            prompts.extend((ns['LEARNING_PROMPT'] if role == 'learning' else ns['PROMPT']) + unit['text']
                           for unit in units)
            selected_sources += 1
    need(selected_sources == expected)
    return prompts, {'sources': selected_sources, 'excluded': dict(rejected)}


def alibaba_prompts(snapshot, review, holds, expected):
    snapshot = ali.private_snapshot(snapshot)
    core = provenance.alibaba_algorithms()
    wanted = {item['source_id']: item for item in review['backlog_review_template']['entries']}
    prompts, excluded = [], 0
    with closing(wechat.open_copy(snapshot / 'analysis.sqlite3')) as db:
        need({'version', 'group_json'} <= {r[1] for r in db.execute('PRAGMA table_info(analyses)')})
        need('analysis_version' in {r[1] for r in db.execute('PRAGMA table_info(current_conversations)')})
        for r in db.execute('SELECT group_json FROM analyses WHERE version IN '
                            '(SELECT analysis_version FROM current_conversations) ORDER BY version'):
            group = json.loads(r['group_json'])
            for record in group['records']:
                source = wanted.pop(record['inbound_id'], None)
                if source is None:
                    continue
                need(source['source_sha256'] == record['source_sha256']
                     and source['content_sha256'] == wechat.sha(record['body']))
                row = {'evidence_id': record['inbound_id'], 'source_sha256': record['source_sha256'],
                       'body_text': record['body']}
                if crosses(row, holds):
                    excluded += 1
                    continue
                unit = provenance.alibaba_single_source(group, record, core=core)
                need(core.validate_coverage(unit['group']['records'], [unit['window']['evidence_spans']])['complete'])
                prompts.append(unit['prompt'])
    need(not wanted and len(prompts) == expected)
    return prompts, {'sources': len(prompts), 'excluded_unknown_cross': excluded}


def estimate(wechat_snapshot, alibaba_snapshot, *, expected_wechat=1600, expected_alibaba=106,
             factor_low=1.0, factor_high=1.5, wrapper_tokens=1024, input_margin=1.15):
    review = {}
    wreport = wechat.inventory(wechat_snapshot, review)
    _, areview = ali.analyze(alibaba_snapshot)
    holds = review['wechat_holds'] + areview['ali_holds']
    need(len(holds) == 4)
    wp, wc = wechat_prompts(wechat_snapshot, holds, expected_wechat)
    ap, ac = alibaba_prompts(alibaba_snapshot, areview, holds, expected_alibaba)
    need(len(wp) == wreport['从未请求排除未知交叉后单元数'])
    _, schemas = request_contracts()
    def sizes(prompts, schema, output):
        # 两个官方模型的 ID 不同长，所有请求按较长 ID 作保守预留。
        return [max(reservation_tokens(prompt, schema, model, max_output_tokens=output,
                    overhead_tokens=wrapper_tokens) - output - wrapper_tokens
                    for model in ('deepseek-flash', 'deepseek-v4-pro'))
                for prompt in prompts]
    options = dict(factor_low=factor_low, factor_high=factor_high,
                   wrapper_tokens=wrapper_tokens, input_margin=input_margin)
    return {'schema': 'kb-usage-estimate-v1',
            'scope': '只读候选统计，未核动态授权，不是执行清单或余额证明',
            'wechat': {**wc, **summarize(wp, readers=2, output_tokens=4096,
                                      payload_bytes=sizes(wp, schemas[0], 4096), **options)},
            'alibaba': {**ac, **summarize(ap, readers=1, output_tokens=6000,
                                       payload_bytes=sizes(ap, schemas[1], 6000), **options)},
            'model_requests': 0, 'production_writes': 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wechat-snapshot', required=True, type=Path)
    parser.add_argument('--alibaba-snapshot', required=True, type=Path)
    parser.add_argument('--expected-wechat', type=int, default=1600)
    parser.add_argument('--expected-alibaba', type=int, default=106)
    parser.add_argument('--factor-low', type=float, default=1.0)
    parser.add_argument('--factor-high', type=float, default=1.5)
    parser.add_argument('--wrapper-tokens', type=int, default=1024)
    parser.add_argument('--input-margin', type=float, default=1.15)
    args = parser.parse_args()
    result = estimate(args.wechat_snapshot, args.alibaba_snapshot,
        expected_wechat=args.expected_wechat, expected_alibaba=args.expected_alibaba,
        factor_low=args.factor_low, factor_high=args.factor_high,
        wrapper_tokens=args.wrapper_tokens, input_margin=args.input_margin)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error):
        raise SystemExit('无法估算，请核对私有副本、源码和候选数量；没有请求模型') from None
