"""Bounded deal forms and list parameters; share archive money and error types."""
from datetime import datetime, timezone
import re

from console.archive import validation as v
from console.inbox.model import decode_key, BadRequest as KeyError
from .stages import KEYS, STATUSES


def key(value):
    try:
        decode_key(value, 'conversation')
        return value
    except KeyError:
        raise v.BadRequest('会话标识无效') from None


def identity(value):
    return v.integer(value, maximum=2**63-1)


def currency(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Z]{3}', value):
        raise v.BadRequest('币种须为三位大写字母')
    return value


def due(value):
    if not value:
        return None
    if len(value) == 10:
        return v.iso_date(value)
    try:
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:\d{2})', value):
            raise ValueError
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if result.utcoffset() is None:
            raise ValueError
        return result.astimezone(timezone.utc).isoformat(timespec='seconds')
    except (ValueError, OverflowError):
        raise v.BadRequest('到期时间须为有效日期或带时区的时间') from None


FIELDS = {
    'create': ('title', 'customer_party_id', 'conversation_key', 'party_kind', 'party_name', 'stage',
               'customer_name', 'product_name', 'qty', 'destination_country'),
    'update': ('title', 'stage', 'status', 'value', 'currency', 'next_action', 'next_action_due', 'notes', 'lost_reason'),
    'participant': ('party_id', 'role', 'note'),
    'conversation': ('conversation_key', 'party_id'),
    'item': ('name', 'product_id', 'qty', 'target_price', 'currency', 'note'),
    'task': ('title', 'due'),
    'task_status': ('status',),
    'event': ('note',),
    'identity': ('conversation_key', 'party_id', 'confirm'),
    'draft': ('kind', 'party_id', 'conversation_key'),
    'participant_remove': (), 'conversation_remove': (), 'item_remove': (),
}


def fields(operation, raw):
    v.whitelist(raw, FIELDS[operation])
    result = {}
    for name, value in raw.items():
        if name in ('customer_party_id', 'party_id', 'product_id'):
            result[name] = identity(value) if value else None
        elif name == 'conversation_key':
            result[name] = key(value) if value else None
        elif name == 'stage':
            result[name] = v.choice(value, KEYS)
        elif name == 'status':
            result[name] = v.choice(value, ('open', 'done', 'skipped') if operation == 'task_status' else STATUSES)
        elif name == 'kind':
            result[name] = v.choice(value, ('supplier_order', 'price_request'))
        elif name in ('party_kind', 'role'):
            result[name] = v.choice(value, v.PARTY_KINDS)
        elif name in ('value', 'target_price'):
            result[name + '_fen'] = v.money_to_fen(value) if value else None
        elif name == 'currency':
            result[name] = currency(value)
        elif name in ('due', 'next_action_due'):
            result[name] = due(value)
        elif name == 'qty':
            result[name] = v.integer(value) if value else None
        elif name == 'confirm':
            result[name] = v.choice(value, ('0', '1')) == '1'
        elif name in ('party_name', 'customer_name'):
            result[name] = v.party_name(value) if value else None
        elif name in ('product_name', 'destination_country'):
            result[name] = v.text(value, 200, True)
        else:
            limit = {'title': 500, 'name': 200, 'next_action': 500, 'lost_reason': 200,
                     'note': 500 if operation == 'event' else 2000, 'notes': 2000}[name]
            required = name == 'name' or (name == 'title' and operation != 'create') or (name == 'note' and operation == 'event')
            result[name] = v.text(value, limit, required)
    required = {'participant': ('party_id',), 'conversation': ('conversation_key',),
                'identity': ('conversation_key', 'party_id'), 'item': ('name',),
                'task': ('title',), 'task_status': ('status',), 'event': ('note',)}
    if any(not result.get(k) for k in required.get(operation, ())):
        raise v.BadRequest('请填写必填字段')
    if operation == 'create':
        result.setdefault('stage', 'inquiry')
        if result.get('customer_name') and result.get('customer_party_id'):
            raise v.BadRequest('请选择已有客户或填写新客户名称')
        if any(k in raw for k in ('product_name', 'destination_country', 'qty')):
            if not all(result.get(k) for k in ('product_name', 'destination_country', 'qty')):
                raise v.BadRequest('请填写产品、数量和目的国')
        if bool(result.get('party_kind')) != bool(result.get('party_name')):
            raise v.BadRequest('往来单位类型与名称须一起填写')
        if result.get('party_name') and not result.get('conversation_key'):
            raise v.BadRequest('新建会话归属时须提供会话标识')
    return result


def parameters(params, resource):
    allowed = ('key',) if resource in ('by_conversation', 'identity_lookup') else (
        ('stage', 'status', 'q', 'waiting', 'party', 'page', 'page_size') if resource == 'list' else ())
    v.whitelist(params, allowed)
    result = dict(page=1, page_size=30, status='open') if resource == 'list' else {}
    for name, value in params.items():
        if hasattr(params, 'getlist') and len(params.getlist(name)) != 1:
            raise v.BadRequest('同一参数只能传入一次')
        if name == 'key':
            result[name] = key(value)
        elif name in ('page', 'page_size', 'party'):
            result[name] = v.integer(value, maximum=100 if name == 'page_size' else 9999999 if name == 'page' else 2**63-1)
        elif name == 'q':
            result[name] = v.text(value, 500)
        elif value:
            result[name] = v.choice(value, {'stage': KEYS, 'status': (*STATUSES, 'all'), 'waiting': ('us', 'them')}[name])
    if resource in ('by_conversation', 'identity_lookup') and 'key' not in result:
        raise v.BadRequest('请提供会话标识')
    return result
