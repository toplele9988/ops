"""Alibaba checks use aggregate SQL over the observed inbound/probe schemas.

Only counts, timestamps, fixed labels and hashed locators leave these readers.
No message body or raw probe snapshot is loaded into Python.
"""
import json
import re

from console.channel_plan import (ALIBABA_EXPECTED_ACCOUNTS, ALIBABA_MAIN_COUNT,
                                  ALIBABA_OTHERS_COUNT, DEFAULT_PLAN)
from .common import finding, read_db, sample_id

DAY = 86400
EXPECTED_ACCOUNTS = ALIBABA_EXPECTED_ACCOUNTS
FIX = '系统后台 → 渠道接入 → 阿里国际站；在 Win10 已登录浏览器核对账号、探针和消息页'

# Same required identity/text/role/timestamp fields as inbox.model.parse_message.
# SQLite performs the projection; malformed JSON never reaches json_extract.
ROWS = """
WITH raw AS (
 SELECT id,created_at,CASE WHEN json_valid(payload) THEN payload ELSE '{}' END AS p
 FROM inbound
), projected AS (
 SELECT id,created_at,json_extract(p,'$.account') AS account,
 json_extract(p,'$.conversation_id') AS conversation_id,
 json_extract(p,'$.buyer_name') AS buyer_name,
 json_extract(p,'$.role') AS role,
 unixepoch(json_extract(p,'$.sent_at')) AS sent_at,
 json_extract(p,'$.history') AS historical,
 CASE WHEN json_extract(p,'$.content') GLOB '*[[]图片]*'
   OR json_extract(p,'$.content') GLOB '*[[]文件]*'
   OR json_extract(p,'$.content') GLOB '*[[]附件]*'
   OR json_extract(p,'$.content') GLOB '*[[]视频]*'
   OR json_extract(p,'$.content') GLOB '*[[]语音]*' THEN 1 ELSE 0 END AS placeholder
 FROM raw
 WHERE json_type(p,'$.account')='text'
 AND length(trim(json_extract(p,'$.account'))) BETWEEN 1 AND 1000
 AND json_type(p,'$.conversation_id')='text'
 AND length(trim(json_extract(p,'$.conversation_id'))) BETWEEN 1 AND 1000
 AND json_type(p,'$.buyer_name')='text'
 AND length(trim(json_extract(p,'$.buyer_name'))) BETWEEN 1 AND 1000
 AND json_type(p,'$.content')='text'
 AND json_extract(p,'$.role') IN ('buyer','seller','unknown')
 AND json_type(p,'$.sent_at')='text'
 AND (json_extract(p,'$.sent_at') GLOB '*Z' OR json_extract(p,'$.sent_at') GLOB '*[+-][0-9][0-9]:[0-9][0-9]')
), messages AS MATERIALIZED (SELECT * FROM projected WHERE sent_at IS NOT NULL)
"""


def _snapshot(ctx):
    """One SQL projection shared by all inbound checks; Python sees aggregates.

    The large source is never loaded into a Python collection. SQLite's CTE is
    transient query state, and every returned sample is bounded before transfer.
    """
    key = 'audit.alibaba.aggregate_stats'
    if key not in ctx.memo:
        with read_db(ctx.inbound_path) as db:
            db.create_function('audit_ref', 1, sample_id, deterministic=True)
            row = db.execute(ROWS + """,
            account_stats AS (SELECT account,MAX(sent_at) AS last FROM messages GROUP BY account ORDER BY account),
            attachment_samples AS (SELECT id,account,sent_at FROM messages WHERE placeholder=1 ORDER BY sent_at DESC,id LIMIT 5),
            live_buyers AS (SELECT created_at FROM messages WHERE role='buyer' AND historical=0
              AND typeof(created_at) IN ('integer','real') AND created_at<=:now),
            arrivals AS (SELECT DISTINCT created_at AS at FROM live_buyers WHERE created_at>=:start AND created_at<:end),
            gaps AS (SELECT at-lag(at) OVER (ORDER BY at) AS gap FROM arrivals),
            ranked AS (SELECT account,conversation_id,buyer_name,row_number() OVER (
              PARTITION BY account,conversation_id ORDER BY sent_at DESC,id DESC) AS n FROM messages),
            duplicates AS (SELECT account,lower(trim(buyer_name)) AS buyer,COUNT(*) AS n FROM ranked WHERE n=1
              GROUP BY account,lower(trim(buyer_name)) HAVING COUNT(*)>1),
            duplicate_samples AS (SELECT account,buyer,n FROM duplicates ORDER BY n DESC,account,buyer LIMIT 5)
            SELECT
              (SELECT json_group_array(json_object('account',audit_ref(account),'last_message_at',last)) FROM account_stats),
              (SELECT COUNT(*) FROM messages WHERE placeholder=1),
              (SELECT json_group_array(json_object('id',audit_ref(id),'account',audit_ref(account),'message_at',sent_at)) FROM attachment_samples),
              (SELECT MAX(created_at) FROM live_buyers),
              (SELECT COUNT(gap) FROM gaps WHERE gap>0),
              (SELECT AVG(gap) FROM gaps WHERE gap>0),
              (SELECT COUNT(*) FROM duplicates),
              (SELECT COALESCE(SUM(n-1),0) FROM duplicates),
              (SELECT json_group_array(json_object('account',audit_ref(account),'buyer',audit_ref(buyer),'conversations',n)) FROM duplicate_samples)
            """, dict(now=ctx.now, start=ctx.now - 8 * DAY, end=ctx.now - DAY)).fetchone()
            ctx.memo[key] = dict(accounts=json.loads(row[0]), attachment_count=row[1],
                attachment_samples=json.loads(row[2]), last_received_at=row[3],
                baseline_intervals=row[4], baseline_average_seconds=row[5],
                duplicate_count=row[6], duplicate_extra=row[7], duplicate_samples=json.loads(row[8]))
    return ctx.memo[key]


def _accounts(ctx):
    return _snapshot(ctx)['accounts']


def check_accounts(ctx):
    rows = _accounts(ctx)
    missing = max(0, EXPECTED_ACCOUNTS - len(rows))
    plan = getattr(ctx, 'plan', DEFAULT_PLAN)['alibaba_accounts']
    main_operating = plan['main'] == 'operating'
    others_planned = plan['others'] in ('planned', 'not_started')
    # The current business plan has one data-bearing operating account. Account
    # identities remain hashed; aggregate coverage is the only role signal.
    main_present = len(rows) >= ALIBABA_MAIN_COUNT
    planned_missing = min(missing, ALIBABA_OTHERS_COUNT) if others_planned and main_present else 0
    if not missing:
        level, title = 'ok', '阿里账号数据覆盖'
        meaning = f'约定接入 {EXPECTED_ACCOUNTS} 个账号，目前 {len(rows)} 个有有效消息；账号数据覆盖完整。'
    elif planned_missing == missing and (main_present or not main_operating):
        level, title = 'low', '阿里子账号计划中'
        meaning = (f'约定接入 {EXPECTED_ACCOUNTS} 个账号，目前 {len(rows)} 个有消息；另外 {planned_missing} 个子账号'
                   '计划在自动发品和 AI 运营跑通后启用，目前没有数据是正常的。')
    elif not main_operating and others_planned:
        level, title = 'low', '阿里账号计划中'
        meaning = (f'约定接入 {EXPECTED_ACCOUNTS} 个账号，目前 {len(rows)} 个有消息；主账号和另外 '
                   f'{ALIBABA_OTHERS_COUNT} 个子账号尚未开始运营，目前没有数据属于计划范围。')
    elif main_operating and not main_present:
        level, title = 'high', '阿里主账号暂无数据'
        meaning = (f'约定接入 {EXPECTED_ACCOUNTS} 个账号，目前 {len(rows)} 个有消息；主账号没有有效消息，'
                   '运营中的账号需要检查。其余子账号仍按计划启用。')
    else:
        level, title = 'high', '阿里账号数据覆盖'
        meaning = (f'约定接入 {EXPECTED_ACCOUNTS} 个账号，目前 {len(rows)} 个有有效消息；'
                   '仍在运营计划中的账号缺少数据，需要核对采集。')
    remedy = ('无需处理；准备启用其余子账号时按计划逐个接入。' if level == 'low' else
              FIX + '，核对主账号及仍在运营账号的采集；不要重置已有登录。')
    return finding('alibaba.accounts', title, level, missing, meaning, remedy,
        rows[:5], expected_accounts=EXPECTED_ACCOUNTS, accounts_with_data=len(rows),
        plan=dict(plan), main_count=ALIBABA_MAIN_COUNT, others_count=ALIBABA_OTHERS_COUNT)


def check_probe(ctx):
    plan = getattr(ctx, 'plan', DEFAULT_PLAN)['alibaba_accounts']
    if plan['main'] != 'operating':
        return finding('alibaba.probe', '阿里探针心跳与版本', 'ok', 0,
            '阿里主账号尚未开始运营，暂无探针数据属于计划范围。',
            '准备开始运营时再核对主账号探针心跳。', [], plan=dict(plan))
    # Match observed heartbeat rows to the accounts that actually carry inbox
    # data. This drops stale heartbeat rows for inactive subaccounts without
    # disclosing account identifiers. The default role plan has one such account.
    active_refs = {account['account'] for account in _accounts(ctx)}
    with read_db(ctx.probe_path) as db:
        rows = list(db.execute("SELECT account,received_at,CASE WHEN json_valid(snapshot_json) "
                              "THEN json_extract(snapshot_json,'$.version') END FROM probe_state ORDER BY received_at DESC"))
    if active_refs:
        rows = [row for row in rows if sample_id(row[0]) in active_refs]
    if not rows:
        return finding('alibaba.probe', '阿里探针心跳与版本', 'high', 1,
            '阿里主账号没有探针心跳记录，无法确认 Win10 的采集脚本是否在运行。',
            FIX + '，核对主账号脚本启用状态和当前版本。', [], plan=dict(plan))
    samples, stale = [], 0
    for row in rows:
        at = row[1] if type(row[1]) in (int, float) and 0 < row[1] <= ctx.now else None
        version = row[2] if isinstance(row[2], str) and re.fullmatch(r'\d+(?:\.\d+){1,5}(?:[-+][A-Za-z0-9.]{1,24})?', row[2]) else None
        age = ctx.now - at if at is not None else None
        stale += int(age is None or age > 6 * 3600)
        samples.append(dict(account=sample_id(row[0]), last_heartbeat_at=at,
                            age_seconds=age, script_version=version))
    latest = samples[0]
    return finding('alibaba.probe', '阿里主账号探针心跳与版本', 'high' if stale else 'ok', stale,
        '主账号心跳超过 6 小时或时间无效，需要检查；计划中的子账号没有心跳不作为故障。'
        '记录中的版本是最后一次上报版本，不能证明当前安装版本。',
        FIX + '，检查主账号的 Chrome、Tampermonkey 脚本、消息页、电脑休眠与网络。', samples[:5],
        last_heartbeat_at=latest['last_heartbeat_at'], age_seconds=latest['age_seconds'],
        script_version=latest['script_version'], plan=dict(plan))


def check_latest_messages(ctx):
    rows = _accounts(ctx)
    stale = sum(row['last_message_at'] > ctx.now or ctx.now - row['last_message_at'] > 7 * DAY for row in rows)
    count = stale if rows else 1
    return finding('alibaba.latest_messages', '阿里各账号最近消息', 'medium' if count else 'ok', count,
        '按消息发生时间查看各账号最近记录；超过 7 天只表示数据陈旧，可能是业务安静，也可能是采集停了。' if rows else
        '尚无有效账号消息，无法确认最近收件时间。', FIX + '，结合心跳与平台消息核对是否需要补采。',
        rows[:5], accounts=rows)


def check_attachments(ctx):
    result = _snapshot(ctx)
    count, samples = result['attachment_count'], result['attachment_samples']
    return finding('alibaba.attachments', '阿里附件只有占位', 'medium' if count else 'ok', count,
        '现有入库契约只保存文字，图片、文件等占位没有对应二进制归档证据；此数量只是可见缺口下限，零条也不证明附件完整。',
        '/inbox → 阿里国际站原会话核对附件；在系统后台 → 渠道接入补齐附件采集，人工把缺失文件补入资料档案。', samples)


def check_buyer_gap(ctx):
    # Arrival timestamps (not historical message dates). History imports must not
    # make a disconnected live collector look healthy. Three completed gaps are
    # required; zero gaps from batch inserts are not a meaningful baseline.
    result = _snapshot(ctx)
    last = result['last_received_at']
    baseline = result['baseline_intervals'], result['baseline_average_seconds']
    enough = baseline[0] >= 3
    gap = max(0, ctx.now - last) if last is not None else None
    limit = max(6 * 3600, 3 * baseline[1]) if enough else None
    alarm = enough and (gap is None or gap > limit)
    return finding('alibaba.buyer_gap', '阿里买家消息入库间隔', 'high' if alarm else 'ok', int(alarm),
        '非历史买家消息的当前入库空档超过过去日常间隔的 3 倍且超过 6 小时，需核对采集是否中断；这不能证明漏收。' if alarm else
        '当前入库空档未超出规则阈值；历史补采不计入实时买家到达。' if enough else
        '前 7 天（不含近 24 小时）不足 3 个有效入库间隔，暂不能判断买家断流，不能据此认定采集正常。',
        FIX + '，对照平台最近买家消息与收件箱，核实有无漏入库。', [],
        last_received_at=last, gap_seconds=gap, baseline_intervals=baseline[0],
        baseline_average_seconds=baseline[1], threshold_seconds=limit, baseline_sufficient=enough)


def check_duplicate_buyers(ctx):
    # Use the latest name of each (account,conversation), as the inbox does.
    # Same names across different accounts are kept separate: no identity merge.
    result = _snapshot(ctx)
    count, extra, samples = result['duplicate_count'], result['duplicate_extra'], result['duplicate_samples']
    return finding('alibaba.duplicate_buyers', '阿里同名买家重复会话', 'low' if count else 'ok', count,
        '同一账号内同名买家对应多个会话，可能是重复采集，也可能只是同名；需要人工核实身份后再关联。',
        '/inbox → 阿里国际站核对同名会话及原平台买家身份，再到 /customers 归并确认的同一客户。',
        samples, extra_conversations=extra)


CHECKS = [
    ('alibaba.accounts', '阿里账号数据覆盖', check_accounts),
    ('alibaba.probe', '阿里探针心跳与版本', check_probe),
    ('alibaba.latest_messages', '阿里各账号最近消息', check_latest_messages),
    ('alibaba.attachments', '阿里附件只有占位', check_attachments),
    ('alibaba.buyer_gap', '阿里买家消息入库间隔', check_buyer_gap),
    ('alibaba.duplicate_buyers', '阿里同名买家重复会话', check_duplicate_buyers),
]
