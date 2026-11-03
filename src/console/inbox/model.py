"""Pure parsing, identity and presentation functions; no channel operations."""
import base64
import hashlib
import json
import math
import re
from datetime import datetime


class BadRequest(ValueError):
    """Only fixed Chinese parameter errors may cross the API boundary."""


CHANNELS = ('alibaba', 'wechat', 'whatsapp', 'website')
WAITING_WINDOW_DAYS = 30


def make_key(kind, channel, account, identity):
    raw = json.dumps([kind, channel, account, identity], ensure_ascii=False,
                     separators=(',', ':')).encode('utf8')
    return base64.urlsafe_b64encode(raw).decode('ascii').rstrip('=')


def decode_key(key, kind):
    try:
        if not isinstance(key, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,8192}', key):
            raise ValueError
        values = json.loads(base64.b64decode(key + '=' * (-len(key) % 4),
                                            altchars=b'-_', validate=True).decode('utf8'))
        if (not isinstance(values, list) or len(values) != 4 or values[0] != kind
                or not all(isinstance(v, str) and v and len(v) <= 1000 for v in values)
                or values[1] not in CHANNELS or make_key(*values) != key):
            raise ValueError
        return tuple(values[1:])
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise BadRequest('标识无效') from None


def parse_message(raw, inbound_id=None):
    """Malformed/incomplete payloads are isolated to their own row."""
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            return None
        for field in ('account', 'conversation_id', 'buyer_name'):
            if not isinstance(data.get(field), str) or not data[field].strip() or len(data[field]) > 1000:
                return None
        if (not isinstance(data.get('content'), str) or data.get('role') not in ('buyer', 'seller', 'unknown')
                or not isinstance(data.get('sent_at'), str)):
            return None
        # JSON permits escaped lone surrogates; SQLite/UTF-8 JSON responses do not.
        # Isolate these malformed rows before they can poison an entire response.
        for field in ('account', 'conversation_id', 'buyer_name', 'content', 'sent_at',
                      'country', 'url', 'message_id'):
            if isinstance(data.get(field), str):
                data[field].encode('utf8')
        sent = datetime.fromisoformat(data['sent_at'].replace('Z', '+00:00'))
        if sent.utcoffset() is None:
            return None
        timestamp = sent.timestamp()
        if not math.isfinite(timestamp):
            return None
        message_id = data.get('message_id')
        dedup = ('id', message_id) if isinstance(message_id, str) and message_id else (
            'hash', hashlib.sha1(json.dumps([data['sent_at'], data['role'], data['content']],
                                          ensure_ascii=False).encode('utf8')).hexdigest())
        message = dict(account=data['account'], conversation_id=data['conversation_id'],
                    buyer_name=data['buyer_name'], country=data.get('country') if isinstance(data.get('country'), str) else '',
                    url=data.get('url') if isinstance(data.get('url'), str) else None,
                    role=data['role'], text=data['content'], sent_at=timestamp, dedup=dedup)
        if any(k in data for k in ('message_kind', 'attachments', 'cards')):
            message['_rich'] = {k: data[k] for k in ('message_kind', 'attachments', 'cards') if k in data}
            message['_inbound_id'] = str(inbound_id) if inbound_id is not None else None
        return message
    except (ValueError, TypeError, OverflowError, RecursionError):
        return None


# A buyer's last line like "Ok", "Thank you", "👍" ends the exchange; it does not ask the seller for anything.
# Counting these as "waiting for your reply" inflates the to-do number until nobody trusts it.
_CLOSERS = frozenset((
    'ok', 'okay', 'k', 'kk', 'oki', 'thanks', 'thank you', 'thank u', 'thanks a lot', 'thank you very much', 'thx', 'ty',
    'got it', 'noted', 'sure', 'yes', 'yep', 'yeah', 'great', 'good', 'nice', 'perfect', 'fine', 'cool', 'alright', 'all right',
    'no problem', 'no worries', 'ok thanks', 'ok thank you', 'okay thanks', 'okay thank you', 'ok great', 'ok good',
    'ok sure', 'ok noted', 'ok got it', 'ok understood', 'understood', 'received', 'ok received', 'welcome', "you're welcome",
    'gracias', 'ok gracias', 'muchas gracias', 'de nada', 'obrigado', 'merci', 'danke', 'xie xie', '谢谢', '好的', '好', '收到', '嗯', '行', '可以',
))


def is_closing(text):
    """True when the text carries no request: only a short acknowledgement, emoji or punctuation."""
    letters = ''.join(ch.lower() if (ch.isalnum() or ch.isspace() or "'" in ch) else ' ' for ch in text)
    words = ' '.join(letters.split())
    return not words or words in _CLOSERS


def waiting_state(messages, now):
    """Shared across channels; unknown observations never reset the reply clock.

    Return the next expiry too, so source caches need no periodic re-aggregation.
    An old buyer line is dormant even if it was merely a closing acknowledgement.
    """
    known = next((m for m in reversed(messages) if m['role'] != 'unknown'), None)
    buyer = known is not None and known['role'] == 'buyer'
    expiry = known['sent_at'] + WAITING_WINDOW_DAYS * 86400 if buyer else None
    dormant = bool(buyer and now > expiry)
    waiting = bool(buyer and not dormant and not is_closing(known['text']))
    return dict(waiting_reply=waiting, waiting_since=known['sent_at'] if waiting else None,
                dormant=dormant), expiry


def aggregate_messages(rows):
    grouped, seen = {}, set()
    for raw in rows:
        message = parse_message(raw[1], raw[0]) if isinstance(raw, tuple) and len(raw) == 2 else parse_message(raw)
        if message is None:
            continue
        identity = message['account'], message['conversation_id']
        dedup = identity + message['dedup']
        if dedup in seen:
            continue
        seen.add(dedup)
        grouped.setdefault(identity, []).append(message)
    conversations = {}
    for (account, conversation_id), messages in grouped.items():
        messages.sort(key=lambda m: m['sent_at'])  # stable: inbound id breaks timestamp ties
        last = messages[-1]
        known = next((m for m in reversed(messages) if m['role'] != 'unknown'), None)
        waiting = known is not None and known['role'] == 'buyer' and not is_closing(known['text'])
        key = make_key('conversation', 'alibaba', account, conversation_id)
        conversations[key] = dict(key=key, channel='alibaba', account=account,
                                  buyer_name=last['buyer_name'],
                                  country=next((m['country'] for m in reversed(messages) if m['country']), ''),
                                  first_seen=messages[0]['sent_at'], last_seen=last['sent_at'],
                                  source_url=last['url'] if last['url'] and last['url'].startswith('https://') else None,
                                  n_messages=len(messages), messages=tuple(messages), waiting_reply=waiting,
                                  waiting_since=known['sent_at'] if waiting else None)
    return conversations


def last_message(conversation):
    last = conversation['messages'][-1]
    return dict(role=last['role'], text=last['text'][:140], sent_at=last['sent_at'])


def conversation_item(conversation, now, draft_keys=()):
    fields = ('key', 'channel', 'account', 'buyer_name', 'country', 'n_messages', 'waiting_reply')
    return dict({k: conversation[k] for k in fields},
                channel_label=conversation.get('channel_label', '阿里国际站'),
                counterpart_role=conversation.get('counterpart_role', 'customer'),
                dormant=conversation.get('dormant', False), last=last_message(conversation),
                classification=conversation.get('classification'), ignored=conversation.get('ignored', False),
                draft_eligible=conversation.get('draft_eligible', True), kb_eligible=conversation.get('kb_eligible', True),
                waiting_hours=max(0.0, (now - conversation['waiting_since']) / 3600.0)
                if conversation['waiting_reply'] else None, has_draft=conversation['key'] in draft_keys)


def account_stats(conversations, now):
    accounts = {}
    for conv in conversations:
        account = accounts.setdefault((conv['channel'], conv['account']), dict(account=conv['account'], channel=conv['channel'],
            conversations=0, waiting_reply=0, messages=0, messages_24h=0, last_message_at=None))
        account['conversations'] += 1
        account['waiting_reply'] += int(conv['waiting_reply'])
        account['messages'] += conv['n_messages']
        account['messages_24h'] += sum(now - 86400 <= m['sent_at'] <= now for m in conv['messages'])
        account['last_message_at'] = max(account['last_message_at'] or conv['last_seen'], conv['last_seen'])
    return [accounts[name] for name in sorted(accounts)]


def single(params, key, default=''):
    if hasattr(params, 'getlist') and key in params and len(params.getlist(key)) != 1:
        raise BadRequest('同一参数只能传入一次')
    value = params.get(key, default)
    if not isinstance(value, str):
        raise BadRequest('请求参数须为文本')
    return value


def parameters(params):
    page, size = single(params, 'page', '1'), single(params, 'page_size', '30')
    if not re.fullmatch(r'[0-9]{1,7}', page) or not re.fullmatch(r'[0-9]{1,3}', size):
        raise BadRequest('页码和每页数量须为有效正整数')
    page, size = int(page), int(size)
    if page < 1 or not 1 <= size <= 100:
        raise BadRequest('页码须大于零，每页数量须为 1 至 100')
    channel, account = single(params, 'channel'), single(params, 'account')
    state, query = single(params, 'state', 'all'), single(params, 'q')
    role = single(params, 'role', 'customer')
    if role not in ('customer', 'supplier', 'all'):
        raise BadRequest('对方角色无效')
    if channel and channel not in CHANNELS:
        raise BadRequest('渠道无效')
    if state not in ('waiting', 'replied', 'all'):
        raise BadRequest('会话状态无效')
    if len(account) > 1000 or len(query) > 500:
        raise BadRequest('账号或搜索词过长')
    return dict(channel=channel, account=account, state=state, role=role, q=query.casefold(), page=page, page_size=size)


def matches(item, values, search_text=''):
    return ((not values['channel'] or item['channel'] == values['channel'])
            and (values.get('role', 'customer') == 'all'
                 or item.get('counterpart_role', 'customer') in
                 (('customer', 'unclassified') if values.get('role', 'customer') == 'customer' else ('supplier',)))
            and (not values['account'] or item['account'] == values['account'])
            and (values['state'] == 'all' or item['waiting_reply'] == (values['state'] == 'waiting'))
            and (not values['q'] or values['q'] in item['buyer_name'].casefold()
                 or values['q'] in search_text.casefold()))


def paginate(items, values, ready):
    start = (values['page'] - 1) * values['page_size']
    return dict(ready=ready, items=items[start:start + values['page_size']], total=len(items),
                page=values['page'], page_size=values['page_size'])
