"""Allowlisted, bounded listing forms and filters."""
import json
import re
from decimal import Decimal

from console.archive import validation as v
from console.deals.validation import identity, currency
from console.roles.common import dumps

GENERATE_FIELDS = ('product_id', 'channel', 'tone', 'target_market', 'target_margin')
EDIT_FIELDS = ('title', 'keywords', 'category_hint', 'attributes', 'moq', 'unit', 'price_tiers',
               'lead_time_text', 'packaging', 'selling_points', 'description', 'image_refs', 'chinese_review')
STATUS_FIELDS = ('status', 'confirm', 'published_ref')
FILTERS = ('product_id', 'channel', 'status', 'page', 'page_size')


def _json(raw, name, kind, max_chars=32768):
    if not isinstance(raw, str) or len(raw) > max_chars:
        raise v.BadRequest('JSON 字段过长或格式无效')
    try:
        value = json.loads(raw, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, TypeError, json.JSONDecodeError):
        raise v.BadRequest('JSON 字段格式无效') from None
    if not isinstance(value, kind):
        raise v.BadRequest('JSON 字段类型无效')
    try:
        dumps(value)
    except (TypeError, ValueError):
        raise v.BadRequest('JSON 字段格式无效') from None
    return value


def generate(raw):
    v.whitelist(raw, GENERATE_FIELDS)
    if any(not isinstance(value, str) for value in raw.values()):
        raise v.BadRequest('字段须为文本')
    result = {'product_id': identity(raw.get('product_id', '')),
              'channel': v.choice(raw.get('channel'), ('alibaba', 'website'))}
    result['tone'] = v.text(raw.get('tone', ''), 160) or ''
    result['target_market'] = v.text(raw.get('target_market', ''), 100) or ''
    margin = raw.get('target_margin', '')
    if margin:
        if not re.fullmatch(r'(?:0|[1-9][0-9]?)(?:\.[0-9]{1,2})?', margin):
            raise v.BadRequest('目标利润率须为 0–90 的百分数，最多两位小数')
        value = Decimal(margin)
        if not Decimal(0) <= value <= Decimal(90):
            raise v.BadRequest('目标利润率须为 0–90 的百分数')
        result['target_margin_bp'] = int(value * 100)
    return result


def edit(raw):
    v.whitelist(raw, EDIT_FIELDS)
    result = {}
    for key, value in raw.items():
        if not isinstance(value, str):
            raise v.BadRequest('字段须为文本')
        if key == 'title':
            result[key] = v.text(value, 500) or ''
        elif key == 'category_hint':
            result[key] = v.text(value, 200) or ''
        elif key == 'unit':
            result[key] = v.text(value, 40) or ''
        elif key == 'lead_time_text':
            result[key] = v.text(value, 500) or ''
        elif key == 'description':
            # This is the sole 64 KiB text field; the API body limit is separately documented.
            result[key] = v.text(value, 65536) or ''
        elif key == 'chinese_review':
            result[key] = v.text(value, 8000) or ''
        elif key == 'moq':
            result[key] = v.integer(value, minimum=1, maximum=1_000_000) if value else None
        elif key == 'keywords':
            rows = _json(value, key, list, 8192)
            if len(rows) > 5 or any(not isinstance(row, str) or len(row) > 120 for row in rows):
                raise v.BadRequest('关键词须为最多 5 条短文本')
            result['keywords_json'] = dumps([v.text(row, 120, True) for row in rows])
        elif key == 'attributes':
            attrs = _json(value, key, dict, 16384)
            if len(attrs) > 50 or any(not isinstance(k, str) or not isinstance(val, str)
                                      or len(k) > 100 or len(val) > 500 for k, val in attrs.items()):
                raise v.BadRequest('属性表字段过多或过长')
            result['attributes_json'] = dumps({v.text(k, 100, True): v.text(val, 500) or ''
                                               for k, val in attrs.items()})
        elif key == 'price_tiers':
            tiers = _json(value, key, list, 16384)
            if len(tiers) > 20:
                raise v.BadRequest('价格阶梯最多 20 条')
            clean = []
            seen = set()
            for tier in tiers:
                if not isinstance(tier, dict) or set(tier) != {'min_qty', 'price', 'currency'}:
                    raise v.BadRequest('价格阶梯字段无效')
                qty = tier['min_qty']
                if type(qty) is not int or not 1 <= qty <= 1_000_000 or qty in seen:
                    raise v.BadRequest('价格阶梯起订量无效')
                seen.add(qty)
                price = tier['price']
                if not isinstance(price, str):
                    raise v.BadRequest('阶梯售价须为文本金额')
                price = v.fen_to_money(v.money_to_fen(price))
                code = currency(tier['currency'])
                clean.append(dict(min_qty=qty, price=price, currency=code))
            result['price_tiers_json'] = dumps(sorted(clean, key=lambda item: item['min_qty']))
        elif key in ('selling_points', 'image_refs'):
            rows = _json(value, key, list, 8192)
            limit = 20 if key == 'selling_points' else 10
            if len(rows) > limit or any(not isinstance(row, str) or len(row) > 500 for row in rows):
                raise v.BadRequest('列表项数量或长度超出范围')
            result['selling_points_json' if key == 'selling_points' else 'image_refs_json'] = dumps(
                [v.text(row, 500, key == 'selling_points') or '' for row in rows])
        elif key == 'packaging':
            packaging = _json(value, key, dict, 8192)
            if len(packaging) > 20 or any(not isinstance(k, str) or not isinstance(val, (str, int, float))
                                         or isinstance(val, bool) for k, val in packaging.items()):
                raise v.BadRequest('包装字段无效')
            result['packaging_json'] = dumps(packaging)
    return result


def status(raw):
    v.whitelist(raw, STATUS_FIELDS)
    if any(not isinstance(value, str) for value in raw.values()):
        raise v.BadRequest('字段须为文本')
    result = {'status': v.choice(raw.get('status'), ('draft', 'ready', 'published', 'archived'))}
    result['confirm'] = v.choice(raw.get('confirm', '0'), ('0', '1')) == '1'
    result['published_ref'] = v.text(raw.get('published_ref', ''), 500) or ''
    if result['status'] == 'published' and (not result['confirm'] or not result['published_ref']):
        raise v.BadRequest('标记已发布须确认并填写阿里商品链接或 ID')
    if result['status'] != 'published' and (result['confirm'] or result['published_ref']):
        raise v.BadRequest('只有发布记录可以填写确认和商品链接')
    return result


def query(params, resource='drafts'):
    allowed = FILTERS if resource == 'drafts' else ('page', 'page_size')
    v.whitelist(params, allowed)
    result = dict(page=1, page_size=30)
    for key in params:
        if hasattr(params, 'getlist') and len(params.getlist(key)) != 1:
            raise v.BadRequest('同一参数只能传入一次')
        value = params.get(key)
        if not isinstance(value, str):
            raise v.BadRequest('请求参数须为文本')
        if key in ('page', 'page_size'):
            result[key] = v.integer(value, maximum=100 if key == 'page_size' else 9_999_999)
        elif key == 'product_id' and value:
            result[key] = identity(value)
        elif key == 'channel' and value:
            result[key] = v.choice(value, ('alibaba', 'website'))
        elif key == 'status' and value:
            result[key] = v.choice(value, ('draft', 'ready', 'published', 'archived'))
    return result

