"""Bounded owner forms; use the archive's exact monetary and date contracts."""
from console.archive import validation as v
from console.deals.validation import key as conversation_key, currency

ACCEPT_FIELDS = (
    'valid_until', 'confirm_valid_until', 'product_id', 'create_product',
    'product_name', 'product_model', 'party_id', 'forwarder_id',
    'price', 'currency', 'unit', 'quoted_on', 'moq_units', 'lead_days_min',
    'lead_days_max', 'tax_basis', 'price_basis', 'includes_flightcase',
    'mode', 'origin', 'destination_country', 'destination_detail', 'service',
    'min_charge', 'transit_days_min', 'transit_days_max', 'notes', 'channel',
)
FIELDS = {'scan': ('days', 'limit', 'conversation_key'), 'accept': ACCEPT_FIELDS, 'dismiss': ()}


def fields(operation, raw):
    if operation not in FIELDS:
        raise v.BadRequest('抽取操作无效')
    v.whitelist(raw, FIELDS[operation])
    result = {}
    for name, value in raw.items():
        if hasattr(raw, 'getlist') and len(raw.getlist(name)) != 1:
            raise v.BadRequest('同一字段只能传入一次')
        if not isinstance(value, str):
            raise v.BadRequest('字段须为文本')
        if name in ('days', 'limit'):
            result[name] = v.integer(value, maximum=365 if name == 'days' else 100)
        elif name == 'conversation_key':
            result[name] = conversation_key(value)
        elif name in ('product_id', 'party_id', 'forwarder_id'):
            result[name] = v.integer(value, maximum=2**63-1) if value else None
        elif name in ('create_product', 'confirm_valid_until'):
            result[name] = v.choice(value, ('0', '1')) == '1'
        elif name in ('valid_until', 'quoted_on'):
            result[name] = v.iso_date(value) if value else None
        elif name in ('price', 'min_charge'):
            result[name] = v.fen_to_money(v.money_to_fen(value)) if value else None
        elif name == 'currency':
            result[name] = currency(value) if value else None
        elif name == 'mode':
            result[name] = v.choice(value, v.MODES) if value else None
        elif name == 'channel':
            result[name] = v.choice(value, v.FREIGHT_CHANNELS)
        elif name in ('moq_units', 'lead_days_min', 'lead_days_max', 'transit_days_min', 'transit_days_max', 'includes_flightcase'):
            number = v.integer(value, minimum=0 if name != 'moq_units' else 1,
                               maximum=1 if name == 'includes_flightcase' else 3650 if 'days' in name else 1000000) if value else None
            result[name] = str(number) if number is not None else ''
        else:
            result[name] = v.text(value, 2000 if name == 'notes' else 40 if name == 'unit' else 200)
    if result.get('create_product') and result.get('product_id'):
        raise v.BadRequest('请选择已有产品或新建候选，不能同时选择')
    if result.get('party_id') and result.get('forwarder_id') and result['party_id'] != result['forwarder_id']:
        raise v.BadRequest('往来单位与货代选择不一致')
    return result


def query(params, resource):
    allowed = ('status', 'kind', 'conversation_key', 'page', 'page_size', 'include_low') if resource == 'suggestions' else ()
    v.whitelist(params, allowed)
    result = dict(status='pending', page=1, page_size=30) if resource == 'suggestions' else {}
    for name, value in params.items():
        if hasattr(params, 'getlist') and len(params.getlist(name)) != 1:
            raise v.BadRequest('同一参数只能传入一次')
        if not isinstance(value, str):
            raise v.BadRequest('请求参数须为文本')
        if name in ('page', 'page_size'):
            result[name] = v.integer(value, maximum=100 if name == 'page_size' else 9999999)
        elif name == 'status':
            result[name] = v.choice(value, ('pending', 'accepted', 'dismissed', 'all'))
        elif name == 'include_low':
            result[name] = v.choice(value, ('0', '1')) == '1'
        elif name == 'kind' and value:
            result[name] = v.choice(value, ('offer', 'freight'))
        elif name == 'conversation_key' and value:
            result[name] = conversation_key(value)
    return result


parameters = query
