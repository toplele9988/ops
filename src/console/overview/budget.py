"""Read persisted ai-task observations/ledgers, never import or execute its gateway."""
from datetime import date, datetime, time, timezone
import re
from zoneinfo import ZoneInfo

from console.pipeline.readers import require
from .safety import database, iso, json_file, timestamp

FREE_MODELS = ('deepseek-v4-pro-0813', 'deepseek-v4.1-flash', 'deepseek-v4.1-flash-1017',
               'glm-5.3', 'kimi-k3', 'qwen3.8-2.4t-a95b')
LEDGER_KEYS = ('cpq', 'wechat_usage', 'whatsapp_usage', 'drafts_usage', 'analysis_usage', 'media_usage')
RECORD_COUNTS = ('accounted_tokens', 'actual_total_tokens', 'actual_prompt_tokens',
                 'actual_completion_tokens', 'unknown_reserved_tokens', 'calls', 'completed_calls')


def nonnegative(value):
    return type(value) is int and 0 <= value <= 10 ** 15


def utc_time(value, now, *, future=False):
    if not isinstance(value, str) or len(value) > 64:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.utcoffset() != timezone.utc.utcoffset(parsed):
            return None
        at = parsed.timestamp()
        return at if future and at > 0 else timestamp(at, now)
    except (ValueError, OverflowError):
        return None


def official(db, now):
    # gate_balances is the coordinator's parsed balance, not gate_calls' raw responses.
    require(db, 'gate_balances', ('bucket', 'balance_json'))
    row = db.execute('''SELECT
        CASE WHEN json_valid(balance_json) THEN json_extract(balance_json,'$.provider') END,
        CASE WHEN json_valid(balance_json) THEN json_extract(balance_json,'$.currency') END,
        CASE WHEN json_valid(balance_json) AND json_type(balance_json,'$.total_micros')='integer'
            THEN json_extract(balance_json,'$.total_micros') END,
        CASE WHEN json_valid(balance_json) AND json_type(balance_json,'$.observed_at') IN ('integer','real')
            THEN json_extract(balance_json,'$.observed_at') END
        FROM gate_balances WHERE bucket='deepseek_official' LIMIT 1''').fetchone()
    if (not row or row[0] != 'deepseek_official' or row[1] not in ('CNY', 'USD')
            or not nonnegative(row[2]) or timestamp(row[3], now) is None):
        raise ValueError('balance_unknown')
    return dict(currency=row[1], balance_micros=row[2], observed_at=iso(row[3]))


def pools(paths, seconds, now):
    notes = []
    try:
        observed = json_file(paths['free_snapshot'], seconds)
        models = observed.get('models')
        if not isinstance(models, dict) or len(models) > 100:
            raise ValueError
        observed_at = utc_time(observed.get('verified_at'), now)
        valid_until = utc_time(observed.get('valid_until'), now, future=True)
        trusted = (observed.get('provider') == 'dashscope'
                   and observed.get('source') == 'owner_browser_observation'
                   and observed.get('confidence') == 'masked_key_match'
                   and observed_at is not None and valid_until is not None
                   and valid_until > now and valid_until > observed_at)
        if not trusted:
            notes.append('百炼观察已过期、时间无效或缺少核验，剩余额度未知。')
    except Exception:
        return [], ['百炼观察来源缺失或读取失败，免费额度未知。']

    # Each ledger has an independent bounded read. No missing ledger becomes zero usage.
    ledgers = []
    for key in LEDGER_KEYS:
        try:
            value = json_file(paths[key], seconds)
            if (type(value.get('version')) is not int or value['version'] != 1
                    or not isinstance(value.get('policy_sha256'), str)
                    or not re.fullmatch('[0-9a-f]{64}', value['policy_sha256'])
                    or not isinstance(value.get('models'), dict) or len(value['models']) > 100):
                raise ValueError
            ledgers.append(value['models'])
        except Exception:
            ledgers.append(None)
    if any(value is None for value in ledgers):
        notes.append('ai-task 的部分用量账本缺失或读取失败，免费剩余额度未知。')

    result = []
    for model in FREE_MODELS:
        item = models.get(model)
        if not isinstance(item, dict):
            continue
        remaining, expires = None, None
        known = trusted and item.get('free_only') is True and nonnegative(item.get('remaining_tokens'))
        try:
            day = date.fromisoformat(item['expires_on'])
            expires = day.isoformat()
            expiry = datetime.combine(day, time.min, tzinfo=ZoneInfo('Asia/Shanghai')).timestamp()
            known = known and now < expiry and valid_until <= expiry
        except (KeyError, TypeError, ValueError):
            known = False
        unknown = 0
        for ledger in ledgers:
            record = ledger.get(model) if ledger is not None else None
            if not isinstance(record, dict):
                known = False
                continue
            if ('blocked_reason' not in record
                    or not all(nonnegative(record.get(key)) for key in RECORD_COUNTS)
                    or type(record.get('in_flight')) is not bool):
                known = False
                continue
            if (record['actual_total_tokens'] != record['actual_prompt_tokens'] + record['actual_completion_tokens']
                    or record['accounted_tokens'] != record['actual_total_tokens'] + record['unknown_reserved_tokens']
                    or record['completed_calls'] > record['calls']
                    or record.get('blocked_reason') is not None or record['in_flight']):
                known = False
            unknown += record['unknown_reserved_tokens']
        if known:
            remaining = max(0, item['remaining_tokens'] - unknown)
        else:
            notes.append(f'{model} 的过期、阻断或账本证据不完整，剩余额度未知。')
        result.append(dict(model=model, remaining_tokens=remaining,
                           observed_at=iso(observed_at), expires_on=expires))
    if not result:
        notes.append('百炼没有可识别的已核验模型观察，免费额度未知。')
    return result, notes


def read_budget(paths, seconds, now):
    notes = []
    try:
        with database(paths['physical'], seconds) as db:
            balance = official(db, now)
    except Exception:
        balance = None
        notes.append('官方 DeepSeek 未找到可读的已落盘余额，余额未知。')
    free, pool_notes = pools(paths, seconds, now)
    notes.extend(pool_notes)
    notes.append('仅本地账本观察；免费数为已核验观察减去六本账的未知预留，不是实时云端余额或可调用额度。'
                 '不读取含凭据的 swarm 配置，未核验本地 cap；不据此授权调用。官方金额按 observed_at 展示，不换汇。')
    return dict(deepseek_official=balance, free_pools=free, note=' '.join(notes))
