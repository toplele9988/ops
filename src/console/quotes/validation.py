"""Strict URL-form fields; all money/rate parsing is Decimal or integer."""
from decimal import Decimal
import re
from console.archive import validation as v
from console.deals.validation import identity, currency

FIELDS = {
    'create': (),
    'update': ('incoterm','currency','fx_rate','fx_source','destination_country','target_margin','valid_until','notes'),
    'line': ('product_id','name','qty','unit','sell_unit_price','offer_id','note'),
    'line_update': ('product_id','name','qty','unit','sell_unit_price','offer_id','note'),
    'line_remove': (), 'cost_remove': (), 'cost_recalc': (), 'cost_review': ('confirm',),
    'cost': ('kind','party_id','freight_quote_id','description','manual','amount','valid_until','source_note',
             'source_offer_id','quantity_line_id','unit_price','quantity','unit','destination_country','mode','segment'),
    'negotiation': ('side','note','price','currency','party_id'),
    'freight_choose': ('confirm','freight_quote_id','reason_type','reason','selection_mode','replace_cost_id','request_key','segment'),
    'status': ('action','confirm'),
    'terms': ('confirm','payment','delivery_scope','seller_name','seller_address'),
    'native': ('conversation_key',),
    'review': ('confirm','source_hash','lang','text'),
}


def fields(op, raw):
    v.whitelist(raw, FIELDS[op])
    if op in ('terms','native','review'):
        result = {}
        for key, value in raw.items():
            if key == 'confirm':
                result[key] = int(v.choice(value, ('1',)))
            elif key == 'payment':
                result[key] = v.choice(value, ('30_70',))
            elif key == 'source_hash':
                if not re.fullmatch(r'[a-f0-9]{64}', value or ''):
                    raise v.BadRequest('报价版本无效，请重新生成文本')
                result[key] = value
            else:
                result[key] = v.text(value, {'delivery_scope':500,'seller_name':200,'seller_address':500,
                                           'conversation_key':2048,'lang':20,'text':6000}[key], True)
        if op in ('terms','review') and result.get('confirm') != 1:
            raise v.BadRequest('请核对本轮内容后明确确认')
        if op == 'terms' and (not result.get('payment') or not result.get('delivery_scope')):
            raise v.BadRequest('请确认 30% 定金、70% 发货前付清，并填写交付范围')
        if op == 'native' and not result.get('conversation_key'):
            raise v.BadRequest('请选择本案客户会话，再确认客户母语')
        if op == 'review' and any(not result.get(k) for k in ('source_hash','lang','text')):
            raise v.BadRequest('请先生成本轮文本再审阅')
        return result
    result = {}
    if op == 'freight_choose':
        if any('〔请求:' in str(raw.get(key, '')) for key in ('reason','segment')):
            raise v.BadRequest('选择原因和运输段不能包含系统请求标记')
        for key, value in raw.items():
            if key == 'confirm':
                result[key] = int(v.choice(value, ('1',)))
            elif key == 'freight_quote_id':
                result[key] = identity(value)
            elif key == 'replace_cost_id':
                result[key] = identity(value) if value else None
            elif key == 'selection_mode':
                result[key] = v.choice(value, ('replace', 'append'))
            elif key == 'request_key':
                if not re.fullmatch(r'[A-Za-z0-9-]{8,80}', value or ''):
                    raise v.BadRequest('请求编号无效，请重新打开选择窗口')
                result[key] = value
            elif key == 'segment':
                result[key] = v.text(value, 100, True)
            elif key == 'reason_type':
                result[key] = v.choice(value, ('cheapest', 'fastest', 'other'))
            elif key == 'reason':
                result[key] = v.text(value, 200)
        if 'confirm' not in result or 'freight_quote_id' not in result:
            raise v.BadRequest('请确认并选择一条有效运价')
        result.setdefault('reason_type', 'other')
        labels = {'最便宜': 'cheapest', 'cheapest': 'cheapest', '最快': 'fastest', 'fastest': 'fastest',
                  '其他': 'other', 'other': 'other'}
        if result.get('reason') in labels and 'reason_type' not in raw:
            result['reason_type'] = labels[result.pop('reason')]
        return result
    for key, value in raw.items():
        if key in ('product_id','offer_id','party_id','freight_quote_id','source_offer_id','quantity_line_id'):
            result[key] = identity(value) if value else None
        elif key in ('qty','quantity'):
            result[key] = v.integer(value)
        elif key in ('sell_unit_price','amount','price','unit_price'):
            result[{'sell_unit_price':'sell_unit_minor','amount':'amount_fen','price':'price_minor','unit_price':'unit_price_fen'}[key]] = v.money_to_fen(value) if value else None
        elif key == 'mode':
            result[key] = v.choice(value, v.MODES)
        elif key == 'currency':
            result[key] = currency(value)
        elif key == 'valid_until':
            result[key] = v.iso_date(value) if value else None
        elif key == 'fx_rate':
            if not value:
                result['fx_x10000'] = None
            elif not isinstance(value, str) or not re.fullmatch(r'[0-9]{1,7}(?:\.[0-9]{1,4})?', value) or not Decimal('0.0001') <= Decimal(value) <= 1000000:
                raise v.BadRequest('汇率须为大于零、最多四位小数的人民币金额')
            else:
                result['fx_x10000'] = int(Decimal(value) * 10000)
        elif key == 'target_margin':
            if not isinstance(value, str) or not re.fullmatch(r'[0-9]{1,2}(?:\.[0-9]{1,2})?', value) or Decimal(value) > 90:
                raise v.BadRequest('目标利润率须为 0–90 的百分比，最多两位小数')
            result['target_margin_bp'] = int(Decimal(value) * 100)
        elif key in ('manual','confirm'):
            result[key] = int(v.choice(value, ('0','1')))
        elif key in ('incoterm','kind','side','action'):
            result[key] = v.choice(value, {'incoterm':('EXW','FOB','CIF','DAP','DDP'),
                'kind':('freight','vendor','domestic','other'), 'side':('customer','supplier','forwarder','vendor','us'),
                'action':('send','accept','reject')}[key])
        else:
            result[key] = v.text(value, {'name':200, 'unit':40, 'destination_country':100,
                'notes':2000, 'note':2000, 'description':500, 'source_note':500, 'fx_source':500, 'segment':100}[key],
                key == 'name' or key == 'note' and op == 'negotiation')
    if op == 'negotiation' and '〔请求:' in (result.get('note') or ''):
        raise v.BadRequest('协商正文不能包含系统请求标记')
    required = {'cost':('kind',), 'negotiation':('side','note'), 'status':('action',), 'line':('qty',)}
    if any(result.get(k) is None for k in required.get(op, ())):
        raise v.BadRequest('请填写必填字段')
    if op == 'line' and not (result.get('product_id') or result.get('name')):
        raise v.BadRequest('请选择产品或填写产品名称')
    if op == 'cost':
        manual = result['kind'] != 'freight' or result.get('manual') == 1
        if manual:
            if result.get('freight_quote_id'):
                raise v.BadRequest('人工总价不能同时引用自动运价，请选择一种计价方式')
            if (result.get('amount_fen') is None and result.get('unit_price_fen') is None and not result.get('source_offer_id')) or not result.get('valid_until') or not result.get('source_note'):
                raise v.BadRequest('人工费用必须填写人民币总价、有效期和来源说明')
            result['manual'] = 1
            if result.get('unit_price_fen') is not None and result.get('amount_fen') is not None:
                raise v.BadRequest('按数量计价和人工总价只能选一种')
        elif not result.get('freight_quote_id'):
            raise v.BadRequest('请选择有效运价，或改为人工填写总价')
        elif set(raw) & {'amount','valid_until','source_note','source_offer_id','quantity_line_id','unit_price','quantity','unit','destination_country','mode','segment'}:
            raise v.BadRequest('自动运费的总价与有效期取自运价，请勿手填覆盖')
    return result


def parameters(params, resource):
    allowed = {'text': ('lang',), 'freight_options': ('mode',),
               'freight_compare': ('destination_country', 'mode', 'page', 'page_size')}.get(resource, ())
    v.whitelist(params, allowed)
    result = dict(page=1, page_size=30) if resource == 'freight_compare' else {}
    for key in params:
        if hasattr(params, 'getlist') and len(params.getlist(key)) != 1:
            raise v.BadRequest('同一参数只能传入一次')
        value = params.get(key)
        if key == 'lang':
            result[key] = v.choice(value or 'en', ('en','zh','native'))
        elif key in ('page', 'page_size'):
            result[key] = v.integer(value, maximum=100 if key == 'page_size' else 9999999)
        elif key == 'mode' and value:
            result[key] = v.choice(value, v.MODES)
        elif key == 'destination_country' and value:
            result[key] = v.text(value, 200)
    if resource == 'text':
        result.setdefault('lang', 'en')
    return result
