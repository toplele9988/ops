"""Read-only channel coverage counters; availability does not prove connectivity."""
from pathlib import Path
import time

from starlette.routing import Route

from .channel_plan import (ALIBABA_EXPECTED_ACCOUNTS, ALIBABA_OTHERS_COUNT,
                           CHANNEL_PLAN_PATH, public_plan, resolve_plan)
from .inbox.http import get_handler
from .inbox.model import account_stats
from .inbox.store import INBOUND_PATH, WECHAT_PATH, WHATSAPP_PATH, inbound_cache, read_database

# User-reported account coverage: one main account and five subaccounts.
EXPECTED_ALIBABA_ACCOUNTS = ALIBABA_EXPECTED_ACCOUNTS


def activity_state(last, now):
    if last is None:
        return 'stale'
    age = now - last
    return 'ok' if age <= 86400 else 'quiet' if age <= 7 * 86400 else 'stale'


def _main_probe(probe, accounts, account_plan):
    """Project probe health onto the one planned operating Alibaba account."""
    if probe is None:
        return None
    rows = probe.get('accounts', [])
    if accounts and account_plan['others'] in ('planned', 'not_started'):
        # The current plan has one data-bearing account. Match it against the
        # probe's existing account metadata; identity is not added to the plan.
        main = max(accounts, key=lambda row: (row.get('last_message_at') or 0,
                                               row.get('messages') or 0))
        rows = [row for row in rows if row.get('account') == main.get('name')]
        if not rows:
            return None
        return max(rows, key=lambda row: row.get('last_heartbeat_at') or 0)
    return probe


def _channel(channel, label, ready=False, accounts=()):
    recent = [a['last_message_at'] for a in accounts if a['last_message_at'] is not None]
    counts = [a['messages_24h'] for a in accounts]
    return dict(channel=channel, label=label, ready=ready, accounts=list(accounts),
                messages_24h=sum(counts) if all(c is not None for c in counts) else None,
                last_message_at=max(recent) if recent else None)


def _legacy_build(app_module, inbound_path, wechat_path, whatsapp_path, plan):
    cache = inbound_cache(inbound_path)
    wechat, whatsapp = Path(wechat_path), Path(whatsapp_path)

    def read(_):
        now, notes = time.time(), []
        alibaba = _channel('alibaba', '阿里国际站')
        try:
            snapshot = cache.read()
            accounts = []
            for entry in account_stats(snapshot['conversations'].values(), now):
                accounts.append(dict(name=entry['account'], **{k: entry[k] for k in (
                    'conversations', 'messages', 'messages_24h', 'last_message_at')},
                    state=activity_state(entry['last_message_at'], now)))
            alibaba = _channel('alibaba', '阿里国际站', snapshot['ready'], accounts)
        except Exception:
            notes.append('阿里国际站数据暂时无法读取')
        alibaba['plan'] = public_plan(plan)['alibaba_accounts']
        notes.append(f'阿里国际站共有 {EXPECTED_ALIBABA_ACCOUNTS} 个账号（1 个主账号和 5 个子账号），'
                     f'当前只有 {len(alibaba["accounts"])} 个账号有数据')
        if len(alibaba['accounts']) == 1 and plan['alibaba_accounts']['others'] in ('planned', 'not_started'):
            notes.append(f'主账号正常采集；另外 {ALIBABA_OTHERS_COUNT} 个子账号计划在自动发品和 AI 运营跑通后启用，目前没有数据是正常的')
        elif len(alibaba['accounts']) > 1 and plan['alibaba_accounts']['others'] in ('planned', 'not_started'):
            notes.append(f'阿里国际站目前有 {len(alibaba["accounts"])} 个账号有数据；其余子账号按运营计划启用。')
        elif plan['alibaba_accounts']['main'] == 'operating':
            notes.append('阿里主账号暂无数据，需要核对采集；其余子账号仍按计划启用')
        wx = _channel('wechat', '微信')
        try:
            if wechat.exists():
                with read_database(wechat) as db:
                    total, count, last = db.execute('SELECT COUNT(*), COALESCE(SUM(CASE WHEN '
                        "typeof(created_at) IN ('integer','real') AND created_at BETWEEN ? AND ? "
                        'THEN 1 ELSE 0 END), 0), MAX(CASE WHEN '
                        "typeof(created_at) IN ('integer','real') THEN created_at END) FROM messages",
                        (now - 86400, now)).fetchone()
                wx = _channel('wechat', '微信', True, [dict(name='微信', conversations=None,
                    messages=total, messages_24h=count, last_message_at=last, state=activity_state(last, now))])
        except Exception:
            notes.append('微信数据暂时无法读取')
        wx['plan'] = plan['wechat']
        wa = _channel('whatsapp', 'WhatsApp')
        try:
            # Only discover files in the specified directory, or use the explicit file.
            paths = sorted(whatsapp.glob('*.sqlite3')) if whatsapp.is_dir() else [whatsapp] if whatsapp.is_file() else []
            accounts = []
            for path in paths:
                with read_database(path) as db:
                    total = db.execute('SELECT COUNT(*) FROM events').fetchone()[0]
                accounts.append(dict(name=f'归档 {len(accounts) + 1}', conversations=None, messages=total,
                                     messages_24h=None, last_message_at=None, state='stale'))
            wa = _channel('whatsapp', 'WhatsApp', bool(paths), accounts)
            if paths:
                notes.append('WhatsApp 仅统计归档事件数，不代表消息数；未读取消息时间，近24小时数和最近时间未知')
        except Exception:
            notes.append('WhatsApp 数据暂时无法读取')
        wa['plan'] = plan['whatsapp']
        if not wa['ready'] and plan['whatsapp'] in ('not_started', 'planned'):
            wa.update(state='quiet', summary='还没开始运营',
                      diagnosis='还没开始运营：准备开始时到 系统后台 → 渠道接入 扫码登录', checklist=[])
        for channel in (alibaba, wx, wa):
            if not channel['ready'] and not (channel['channel'] == 'whatsapp' and plan['whatsapp'] in ('not_started', 'planned')):
                notes.append(f'{channel["label"]}暂无可用数据')
        if plan['whatsapp'] in ('not_started', 'planned'):
            notes.append('WhatsApp 还没开始运营：准备开始时到 系统后台 → 渠道接入 扫码登录')
        return dict(generated_at=now, channels=[alibaba, wx, wa], notes=notes, plan=public_plan(plan)), 200

    return [Route('/api/v1/system/channels', get_handler(app_module, lambda r: None, read,
                  '渠道状态暂时无法读取'), methods=['GET'], name='system_channels')]


def whatsapp_disconnected(status, plan='operating'):
    """The service is up but the phone link is gone: say so plainly instead of "unavailable"."""
    if plan in ('not_started', 'planned'):
        return dict(
            summary='还没开始运营',
            diagnosis='还没开始运营：准备开始时到 系统后台 → 渠道接入 扫码登录',
            checklist=[])
    if not status or status == 'WORKING':
        return {}
    return dict(
        summary=f'WhatsApp 连接已断开（状态 {status}），暂时收不到也发不出消息',
        diagnosis=f'WhatsApp 连接已断开（状态 {status}），需要重新扫码登录，去系统后台 → 渠道接入 → WhatsApp',
        checklist=['手机打开 WhatsApp →「已关联的设备」，看有没有这台电脑的那一项',
                   '到系统后台 → 渠道接入 → WhatsApp 点“登录”，等待二维码后用手机扫码',
                   '配对过程中不要在手机上退出 WhatsApp，也不要同时在别处登录同一个号'])


def build(app_module, inbound_path=INBOUND_PATH, wechat_path=None, whatsapp_path=None, *,
          inbox=None, whatsapp_config_path=None, fetch=None, clock=None, probe_path=None,
          include_roles=('customer', 'supplier', 'unclassified'), stat_fn=None, group_gid=None,
          plan=None, plan_path=None):
    """New default: adapter health. Explicit old whatsapp_path retains archive counters.

    Old SQLite/directory whatsapp_path is deprecated; *.json aliases the new
    config path. Prefer inbox= or whatsapp_config_path= and an injected fetch.
    """
    from .channels import build_inbox
    from .channels.probe import CHECKLIST, PROBE_PATH, diagnose, read_probe

    if whatsapp_path is not None and Path(whatsapp_path).suffix.lower() == '.json' and whatsapp_config_path is None:
        whatsapp_config_path = whatsapp_path
    plan_source = Path(inbound_path)
    if inbox is not None:
        adapter = getattr(inbox, 'adapters', {}).get('alibaba')
        cache = getattr(adapter, 'cache', None)
        plan_source = Path(getattr(cache, 'path', plan_source))
    if plan_path is None:
        plan_path = (CHANNEL_PLAN_PATH if plan_source.resolve() == Path(INBOUND_PATH).resolve()
                     else plan_source.parent / 'disabled-channel-operation-plan.json')
    if whatsapp_path is not None and inbox is None and whatsapp_config_path is None and fetch is None:
        return _legacy_build(app_module, inbound_path, wechat_path or WECHAT_PATH, whatsapp_path,
                             resolve_plan(plan, plan_path))
    sources = inbox if inbox is not None else build_inbox(inbound_path, wechat_path=wechat_path,
        whatsapp_config_path=whatsapp_config_path, fetch=fetch, clock=clock, include_roles=include_roles,
        stat_fn=stat_fn, group_gid=group_gid)
    probe_path = probe_path if probe_path is not None else (
        PROBE_PATH if Path(inbound_path) == Path(INBOUND_PATH) else Path(inbound_path).parent / 'disabled-probe.sqlite3')

    def read(_):
        now, notes, channels = time.time(), [], []
        operation_plan = resolve_plan(plan, plan_path)
        for adapter in sources.adapters.values():
            try:
                entry = adapter.health(now)
            except Exception:
                entry = dict(channel=adapter.name, label=adapter.label, ready=False, conversations=0,
                             waiting=0, messages_24h=0, last_message_at=None)
            entry['accounts'] = []
            entry['state'] = activity_state(entry['last_message_at'], now) if entry['ready'] else 'stale'
            entry['summary'] = (f'{adapter.label}消息正常' if entry['state'] == 'ok' else
                                f'{adapter.label}最近消息较少' if entry['state'] == 'quiet' else
                                f'{adapter.label}很久没有消息' if entry['ready'] else f'{adapter.label}暂不可用')
            if adapter.name == 'alibaba':
                entry['plan'] = public_plan(operation_plan)['alibaba_accounts']
                if entry['ready']:
                    for account in account_stats(adapter.list_conversations(now).values(), now):
                        entry['accounts'].append(dict(name=account['account'], **{k: account[k] for k in (
                            'conversations', 'messages', 'messages_24h', 'last_message_at')},
                            state=activity_state(account['last_message_at'], now)))
                entry['probe'] = read_probe(probe_path)
                main_probe = _main_probe(entry['probe'], entry['accounts'],
                                         operation_plan['alibaba_accounts'])
                if operation_plan['alibaba_accounts']['main'] == 'operating':
                    entry['diagnosis'], entry['checklist'] = diagnose(entry['last_message_at'], main_probe, now)
                else:
                    entry['diagnosis'], entry['checklist'] = None, []
                probe_stale = main_probe is not None and now - main_probe['last_heartbeat_at'] > 6 * 3600
                if operation_plan['alibaba_accounts']['main'] == 'operating' and probe_stale:
                    if not entry['diagnosis']:
                        entry['diagnosis'] = '阿里主账号探针心跳已超过 6 小时，请到 Win10 上检查'
                        entry['checklist'] = list(CHECKLIST)
                if entry['diagnosis']:
                    entry['state'], entry['summary'] = 'stale', entry['diagnosis']
                elif (operation_plan['alibaba_accounts']['main'] == 'operating' and main_probe and
                      now - main_probe['last_heartbeat_at'] <= 6 * 3600):
                    if entry['last_message_at'] is None or now - entry['last_message_at'] > 6 * 3600:
                        entry['state'], entry['summary'] = 'quiet', '阿里最近消息较少，探针心跳正常'
                elif operation_plan['alibaba_accounts']['main'] == 'operating' and main_probe is None:
                    entry['summary'] += '；探针心跳暂不可用'
                n = len(entry['accounts'])
                notes.append(f'阿里国际站共有 {EXPECTED_ALIBABA_ACCOUNTS} 个账号（1 个主账号和 5 个子账号），'
                             f'当前只有 {n} 个账号有数据')
                if n == 1 and operation_plan['alibaba_accounts']['others'] in ('planned', 'not_started'):
                    notes.append(f'主账号正常采集；另外 {ALIBABA_OTHERS_COUNT} 个子账号计划在自动发品和 AI 运营跑通后启用，目前没有数据是正常的')
                elif n > 1 and operation_plan['alibaba_accounts']['others'] in ('planned', 'not_started'):
                    notes.append(f'阿里国际站目前有 {n} 个账号有数据；其余子账号按运营计划启用。')
                elif operation_plan['alibaba_accounts']['main'] == 'operating':
                    notes.append('阿里主账号暂无数据，需要核对采集；其余子账号仍按计划启用')
                notes.append(f'6 个账号只有 {n} 个有数据')
            elif adapter.name == 'whatsapp':
                entry['plan'] = operation_plan['whatsapp']
                entry['accounts'] = [dict(name='WhatsApp 主号', conversations=entry['conversations'],
                    messages=entry['conversations'], messages_24h=entry['messages_24h'],
                    last_message_at=entry['last_message_at'], state=entry['state'],
                    session_status=entry.get('session_status'))] if entry['ready'] else []
                entry.setdefault('chats_total', entry['conversations'])
                if entry['ready']:
                    if (operation_plan['whatsapp'] in ('not_started', 'planned') and
                            entry.get('session_status') == 'WORKING' and entry['chats_total'] == 0):
                        entry['state'] = 'quiet'
                        entry['summary'] = '已登录，等待第一批联系人和会话'
                        entry['diagnosis'] = '已登录，等待第一批联系人和会话'
                        entry['checklist'] = []
                    else:
                        entry['summary'] = f'WhatsApp 会话 {entry.get("session_status")}，共 {entry["chats_total"]} 个聊天，近 7 天活跃 {entry.get("chats_7d", 0)} 个'
                else:
                    entry.update(whatsapp_disconnected(entry.get('session_status'), operation_plan['whatsapp']))
                    if operation_plan['whatsapp'] in ('not_started', 'planned'):
                        entry['state'] = 'quiet'
            elif adapter.name == 'wechat':
                entry['plan'] = operation_plan['wechat']
                if entry['ready']:
                    entry['accounts'] = [dict(name='微信', conversations=entry['conversations'], messages=entry['messages'],
                        messages_24h=entry['messages_24h'], last_message_at=entry['last_message_at'], state=entry['state'])]
                    entry['summary'] = f'微信共 {entry["conversations"]} 个群，24 小时 {entry["messages_24h"]} 条消息'
            elif adapter.name == 'website':
                entry['summary'] = '独立站接入预留，尚未启用'
            if not entry['ready'] and not (adapter.name == 'whatsapp' and operation_plan['whatsapp'] in ('not_started', 'planned')):
                notes.append(f'{entry["label"]}暂无可用数据')
            channels.append(entry)
        if operation_plan['whatsapp'] in ('not_started', 'planned'):
            notes.append('WhatsApp 还没开始运营：准备开始时到 系统后台 → 渠道接入 扫码登录')
        return dict(generated_at=now, channels=channels, notes=notes, plan=public_plan(operation_plan)), 200

    return [Route('/api/v1/system/channels', get_handler(app_module, lambda r: None, read,
                  '渠道状态暂时无法读取'), methods=['GET'], name='system_channels')]
