"""Counts only. No refresh, media registration, signing key or cache writes."""
from collections import Counter
import time

from .clean import clean_name
from .resolver import NamesResolver
from .sources import STATE_PATH, readonly


def count_names(rows, *, ready=True, complete=True):
    missing = [row for row in rows if row.get('name_missing', not bool(clean_name(row.get('buyer_name'))))]
    return {'会话总数': len(rows), '有名字': len(rows) - len(missing), '没名字': len(missing),
            '原因': dict(Counter(row.get('name_missing_reason') or '本地记录没有姓名' for row in missing)),
            '来源可读': int(ready), '统计完整': int(ready and complete)}


def whatsapp_rows(adapter, labels):
    """Reuse only WAHA configuration/GET transport, never its media ingestion."""
    from urllib.parse import quote
    from console.channels.whatsapp import CHAT_ID
    from console.channels.common import timestamp
    config = adapter._configuration()
    session = quote(config['session'], safe='')
    info = adapter._get(config, '/api/sessions/' + session)
    if info.get('status') != 'WORKING':
        raise ValueError('渠道暂不可读')
    chats = adapter._get(config, '/api/' + session + '/chats/overview?limit=500')
    if not isinstance(chats, list):
        raise ValueError('渠道暂不可读')
    rows = {}
    for chat in chats:
        if not isinstance(chat, dict):
            continue
        identity, message = chat.get('id'), chat.get('lastMessage')
        if (not isinstance(identity, str) or not CHAT_ID.fullmatch(identity) or len(identity) > 1000
                or not isinstance(message, dict) or timestamp(message.get('timestamp')) is None):
            continue
        body = message.get('body') or ''
        if not isinstance(body, str) or not body and not message.get('hasMedia'):
            continue
        name = labels.contact(identity, channel='whatsapp', provided_name=chat.get('name'))
        rows[identity] = dict(name_missing=not name.named, name_missing_reason=name.reason)
    return list(rows.values()), len(chats) < 500


def report(*, state_path=STATE_PATH, alibaba_path=None, whatsapp_config=None,
           resolver=None, whatsapp_adapter=None, now=None):
    now = time.time() if now is None else now
    resolver = resolver or NamesResolver(state_path)
    result = {}
    try:
        from console.channels.wechat import WechatAdapter
        from console.channels.common import ROLES
        adapter = WechatAdapter(state_path, include_roles=ROLES, media_tokens=False,
                                names_resolver=resolver, contact_path=resolver.contacts_path, database=readonly)
        rows = list(adapter._load().values())
        result['微信'] = count_names(rows)
    except Exception:
        result['微信'] = count_names([], ready=False)
    try:
        from console.inbox.model import aggregate_messages
        from console.inbox.store import INBOUND_PATH
        with readonly(INBOUND_PATH if alibaba_path is None else alibaba_path) as db:
            rows = aggregate_messages((identity, payload) for identity, payload in db.execute('SELECT id,payload FROM inbound ORDER BY id'))
        result['阿里国际站'] = count_names(list(rows.values()))
    except Exception:
        result['阿里国际站'] = count_names([], ready=False)
    try:
        from console.channels.whatsapp import CONFIG_PATH, WhatsAppAdapter
        adapter = whatsapp_adapter or WhatsAppAdapter(CONFIG_PATH if whatsapp_config is None else whatsapp_config)
        rows, complete = whatsapp_rows(adapter, resolver.snapshot())
        result['WhatsApp'] = count_names(rows, complete=complete)
    except Exception:
        result['WhatsApp'] = count_names([], ready=False)
    return result
