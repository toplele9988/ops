"""Bounded business input; money never passes through a binary float."""
from datetime import date
from decimal import Decimal
import json
import math
import re
import unicodedata

from console.inbox.model import decode_key, BadRequest as InboxBadRequest


class BadRequest(ValueError):
    """Only fixed Chinese messages, never source data or exception text."""


class Missing(BadRequest):
    pass


class Conflict(BadRequest):
    pass


PRODUCT_TEXT = {'name': 200, 'model': 200, 'category': 100, 'description': 2000}
DIMENSIONS = ('product_l_cm', 'product_w_cm', 'product_h_cm',
              'carton_l_cm', 'carton_w_cm', 'carton_h_cm')
PACKING_FIELDS = (*DIMENSIONS, 'gross_kg', 'net_kg', 'units_per_carton')
PRODUCT_FIELDS = (*PRODUCT_TEXT, 'supplier_id', 'wattage', *PACKING_FIELDS)
TERM_FIELDS = ('lead_time', 'moq', 'payment', 'shipping_origin', 'warranty', 'contact_note')
MODES = ('air', 'sea', 'express', 'rail', 'truck')
UNITS = ('kg', 'cbm', 'ticket', 'container')
PARTY_KINDS = ('supplier', 'forwarder', 'customer', 'vendor')
FREIGHT_CHANNELS = ('platform_alibaba', 'forwarder', 'other')
MAX_FEN = 999_999_999_999


def text(value, limit=500, required=False):
    if not isinstance(value, str):
        raise BadRequest('字段须为文本')
    value = value.strip()
    try:
        value.encode('utf8')
    except UnicodeError:
        raise BadRequest('文字编码无效') from None
    if len(value) > limit or (required and not value) or any(
            unicodedata.category(c) in ('Cc', 'Cs') and c not in '\n\t' for c in value):
        raise BadRequest('字段为空、过长或含无效字符')
    return value or None


def integer(value, minimum=1, maximum=1_000_000):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9]{1,19}', value):
        raise BadRequest('请输入有效整数')
    number = int(value)
    if not minimum <= number <= maximum:
        raise BadRequest('数值超出允许范围')
    return number


def number(value, minimum, maximum):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9]{1,8}(?:\.[0-9]{1,6})?', value):
        raise BadRequest('请输入有效数值')
    n = float(value)
    if not math.isfinite(n) or not minimum <= n <= maximum:
        raise BadRequest('数值超出允许范围')
    return n


def money_to_fen(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9]{1,10}(?:\.[0-9]{1,2})?', value):
        raise BadRequest('金额须为最多两位小数的元字符串')
    fen = int(Decimal(value) * 100)
    if not 0 <= fen <= MAX_FEN:
        raise BadRequest('金额超出允许范围')
    return fen


def fen_to_money(fen):
    if type(fen) is not int or not 0 <= fen <= MAX_FEN:
        raise BadRequest('金额数据无效')
    return f'{fen // 100}.{fen % 100:02d}'


def iso_date(value):
    try:
        if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
            raise ValueError
        return date.fromisoformat(value).isoformat()
    except ValueError:
        raise BadRequest('日期须为有效的年-月-日') from None


def choice(value, values):
    if value not in values:
        raise BadRequest('字段选项无效')
    return value


def whitelist(fields, allowed):
    if set(fields) - set(allowed):
        raise BadRequest('包含不支持修改的字段')


def normalize_model(value):
    return re.sub(r'[\s_-]+', '', unicodedata.normalize('NFKC', value or '').casefold())


def conversation_ref(value):
    value = text(value, 8192)
    if not value:
        return None
    try:
        if value.startswith('{'):
            ref = json.loads(value)
            if not isinstance(ref, dict) or set(ref) != {'channel', 'scope'}:
                raise ValueError
            choice(ref['channel'], ('wechat', 'whatsapp', 'alibaba', 'website'))
            ref['scope'] = text(ref['scope'], 1000, True)
            return json.dumps(ref, ensure_ascii=False, separators=(',', ':'))
        decode_key(value, 'conversation')
        return value
    except (ValueError, TypeError, InboxBadRequest):
        raise BadRequest('关联会话引用无效') from None


def aliases(value):
    try:
        items = json.loads(value)
        if not isinstance(items, list) or len(items) > 30:
            raise ValueError
        items = list(dict.fromkeys(text(v, 500, True) for v in items))
        return json.dumps(items, ensure_ascii=False)
    except (ValueError, TypeError):
        raise BadRequest('别名须为不超过三十项的文本数组') from None


def product_fields(fields, create=False):
    whitelist(fields, PRODUCT_FIELDS)
    result = {}
    for key, value in fields.items():
        if key in PRODUCT_TEXT:
            result[key] = text(value, PRODUCT_TEXT[key], key == 'name')
        elif value == '':
            result[key] = None
        elif key == 'supplier_id':
            result[key] = integer(value, maximum=2**63-1)
        elif key == 'units_per_carton':
            result[key] = integer(value, maximum=10000)
        else:
            limits = ((2, 300) if key in DIMENSIONS else (0.05, 500)
                      if key in ('gross_kg', 'net_kg') else (0.01, 100000))
            result[key] = number(value, *limits)
    if create and not result.get('name'):
        raise BadRequest('请填写产品名称')
    return result


def quote_fields(fields, freight=False):
    common = ('price', 'currency', 'unit', 'quoted_on', 'valid_until', 'source_ref')
    extra = (('forwarder_id', 'origin', 'destination_country', 'destination_detail', 'mode',
              'service', 'min_charge', 'transit_days_min', 'transit_days_max', 'notes', 'channel') if freight else
             ('price_basis', 'tax_basis', 'includes_flightcase', 'moq_units',
              'lead_days_min', 'lead_days_max', 'corrects_offer_id'))
    whitelist(fields, (*common, *extra))
    result = dict(price_fen=money_to_fen(fields.get('price')), currency=fields.get('currency', 'CNY'),
                  quoted_on=iso_date(fields.get('quoted_on')), status='confirmed')
    if not re.fullmatch(r'[A-Z]{3}', result['currency']):
        raise BadRequest('币种须为三位大写字母')
    result['valid_until'] = iso_date(fields['valid_until']) if fields.get('valid_until') else None
    if result['valid_until'] and result['valid_until'] < result['quoted_on']:
        raise BadRequest('有效期不能早于报价日期')
    for key in ('source_ref', 'price_basis', 'tax_basis', 'origin', 'destination_country',
                'destination_detail', 'service', 'notes'):
        if key in fields or (freight and key in ('origin', 'destination_country', 'service')):
            result[key] = text(fields.get(key, ''), 2000 if key == 'notes' else 500 if key == 'source_ref' else 200,
                               freight and key in ('origin', 'destination_country', 'service'))
    result['unit'] = (choice(fields.get('unit'), UNITS) if freight
                      else text(fields.get('unit', ''), 40, True))
    for key in ('moq_units', 'lead_days_min', 'lead_days_max', 'transit_days_min', 'transit_days_max',
                'corrects_offer_id'):
        if key in fields:
            result[key] = (integer(fields[key], 0 if 'days' in key else 1,
                           3650 if 'days' in key else 2**63-1 if key == 'corrects_offer_id' else 1000000)
                           if fields[key] != '' else None)
    for prefix in ('lead_days', 'transit_days'):
        low, high = result.get(prefix + '_min'), result.get(prefix + '_max')
        if low is not None and high is not None and low > high:
            raise BadRequest('天数范围的起点不能大于终点')
    if 'includes_flightcase' in fields:
        result['includes_flightcase'] = integer(fields['includes_flightcase'], 0, 1) if fields['includes_flightcase'] else None
    if freight:
        result['forwarder_id'] = integer(fields.get('forwarder_id'), maximum=2**63-1)
        result['mode'] = choice(fields.get('mode'), MODES)
        result['min_charge_fen'] = money_to_fen(fields['min_charge']) if fields.get('min_charge') else None
        result['channel'] = choice(fields.get('channel', 'forwarder'), FREIGHT_CHANNELS)
    return result


def freight_update_fields(fields):
    """Validate a partial freight quote edit; cross-field rules run on the merged row."""
    allowed = ('forwarder_id', 'origin', 'destination_country', 'destination_detail', 'mode', 'service',
               'unit', 'price', 'currency', 'min_charge', 'transit_days_min', 'transit_days_max',
               'quoted_on', 'valid_until', 'source_ref', 'notes', 'channel')
    whitelist(fields, allowed)
    result = {}
    for key, value in fields.items():
        if key == 'forwarder_id':
            result[key] = integer(value, maximum=2**63-1)
        elif key == 'price':
            result['price_fen'] = money_to_fen(value)
        elif key == 'min_charge':
            result['min_charge_fen'] = money_to_fen(value) if value else None
        elif key == 'currency':
            if not isinstance(value, str) or not re.fullmatch(r'[A-Z]{3}', value):
                raise BadRequest('币种须为三位大写字母')
            result[key] = value
        elif key == 'unit':
            result[key] = choice(value, UNITS)
        elif key == 'mode':
            result[key] = choice(value, MODES)
        elif key == 'channel':
            result[key] = choice(value, FREIGHT_CHANNELS)
        elif key in ('quoted_on', 'valid_until'):
            result[key] = iso_date(value) if value else None
        elif key in ('transit_days_min', 'transit_days_max'):
            result[key] = integer(value, 0, 3650) if value else None
        elif key in ('origin', 'destination_country', 'service'):
            result[key] = text(value, 200, True)
        elif key == 'notes':
            result[key] = text(value, 2000)
        else:
            result[key] = text(value, 500)
    return result


def party_name(value):
    # Customer/vendor names must not lose geography, company suffixes or spelling.
    return text(value.replace('\u3000', ' ') if isinstance(value, str) else value, 500, True)
