"""Pure quote checks and arithmetic; inputs use integer fen/minor units."""
from decimal import Decimal, ROUND_CEILING
import json
from console.archive.validity import validity
from .money import money, rounded


def compute(round, lines, costs, today):
    blocking, warnings = [], []

    def issue(code, message, *, warning=False, **location):
        (warnings if warning else blocking).append(dict(code=code, message=message, **location))

    def expiry(value, prefix, label, location):
        state = validity(value, today)
        if state == 'no_expiry':
            issue(prefix + '_no_expiry', label + '没写有效期，请向对方确认并补填有效期', **location)
        elif state == 'expired':
            issue(prefix + '_expired', f'{label}已过期（{value}），请向对方要新价', **location)
        elif state == 'expiring':
            issue(prefix + '_expiring', f'{label}即将到期（{value}），请及时向对方确认新价', warning=True, **location)
        if value and round.get('valid_until') and round['valid_until'] > value:
            issue(prefix + '_ends_before_quote', f'客户报价有效期超过{label}有效期（{value}），请缩短客户有效期或向对方取得更长的有效报价', **location)

    fx = round.get('fx_x10000')
    if not fx or fx <= 0:
        issue('fx_missing', '汇率缺失，请手填“1 单位报价币种等于多少人民币”的汇率')
    if not lines:
        issue('lines_missing', '没有产品行，请添加产品、数量及销售单价')
    if not round.get('valid_until'):
        issue('quote_no_expiry', '对客户的报价有效期缺失，请填写报价有效期')
    elif validity(round['valid_until'], today) == 'expired':
        issue('quote_expired', '对客户的报价已过期，请更新报价有效期后再发出')
    if round.get('incoterm') == 'DDP' and not any(c['kind'] == 'freight' for c in costs):
        issue('ddp_no_freight', 'DDP 报价没有运费行，请添加有效运价或人工运费总价')
    if round.get('incoterm') == 'DDP':
        issue('ddp_scope_unverified', 'DDP 的税费、清关和附加费仍须人工核对；当前成本小计不代表全部到门费用已确认', warning=True)
    if round.get('incoterm') == 'FOB' and not any(c['kind'] == 'domestic' for c in costs):
        issue('fob_no_domestic', 'FOB 报价没有国内到港费用，可能漏了国内运费，请核对并添加国内费用行', warning=True)
    for index, line in enumerate(lines, 1):
        location = dict(line_no=index)
        if not line.get('qty_confirmed', 1):
            issue('qty_unconfirmed', '案子未填写产品数量，请核对并填写本轮数量', **location)
        if (not line.get('offer_id') or line.get('cost_unit_fen') is None or
                line.get('cost_validity') not in ('valid', 'expiring')):
            issue('offer_unusable', '产品成本未引用可用报价，请选择当前已确认且有有效期的供应商报价', **location)
        if line.get('cost_currency', 'CNY') != 'CNY':
            issue('cost_currency_invalid', '产品成本必须为人民币，请取得人民币供应商报价', **location)
        expiry(line.get('cost_valid_until'), 'offer', '产品报价', location)
        if line.get('sell_unit_minor') is None:
            issue('sell_price_missing', '产品没有销售单价，请填写对客户的外币单价', **location)
    for index, cost in enumerate(costs, 1):
        location = dict(cost_no=index)
        calc = cost.get('calc') or json.loads(cost.get('calc_json') or '{}')
        if calc.get('needs_review'):
            issue('manual_cost_review', '数量或单位已改变，人工总费用须向具体单位重新核对后确认', **location)
        source = calc.get('source_offer') or {}
        if source.get('product_id') in {line.get('product_id') for line in lines if line.get('product_id')}:
            issue('duplicate_accessory', '同一配套产品已计入产品成本，不能再计为配套费用；请保留一种计价方式', **location)
        if cost['kind'] == 'freight':
            quote = calc.get('quote') or calc
            if not round.get('destination_country') or quote.get('destination_country') != round['destination_country']:
                issue('freight_destination_mismatch', '运费目的国与本轮目的国不一致或未填写，请核对线路后重新选择', **location)
            if not quote.get('origin') or not quote.get('service'):
                issue('freight_scope_unverified', '运费起运地或服务覆盖范围尚待核对，请向货代确认', warning=True, **location)
            if quote.get('currency', 'CNY') != 'CNY':
                issue('freight_currency_invalid', '外币运价只能展示原币结果，不能计入人民币成本', **location)
        expiry(cost.get('valid_until'), 'cost', '费用报价', location)
        if cost.get('manual'):
            if not cost.get('source_note'):
                issue('manual_source_missing', '人工费用缺少来源说明，请填写报价来源', **location)
        elif cost['kind'] != 'freight' or not cost.get('freight_quote_id') or cost.get('validity') not in ('valid', 'expiring'):
            issue('freight_unusable', '运费未引用可用运价，请选择当前已确认且有有效期的运价', **location)
        if cost['kind'] == 'freight' and not cost.get('manual'):
            calc = cost.get('calc') or json.loads(cost.get('calc_json') or '{}')
            for problem in calc.get('blocking', []):
                issue(problem['code'], problem['message'], **location)
            if cost.get('amount_fen') is None and not calc.get('blocking'):
                issue('freight_packing_missing', '运费资料不全，无法自动计算；请补齐包装或填写有效的人工总价', **location)
        elif cost.get('amount_fen') is None:
            issue('cost_amount_missing', '费用总价缺失，请填写人民币费用总价', **location)

    total_qty = sum(l['qty'] for l in lines)
    fees = sum(c.get('amount_fen') or 0 for c in costs)
    product_cost = sum((l.get('cost_unit_fen') or 0) * l['qty'] for l in lines)
    cost_total = product_cost + fees
    cost_complete = (bool(lines) and all(l.get('cost_unit_fen') is not None and l.get('qty_confirmed', 1) for l in lines)
                     and all(c.get('amount_fen') is not None for c in costs))
    revenue_minor = sum((l.get('sell_unit_minor') or 0) * l['qty'] for l in lines)
    complete_revenue = bool(fx and all(l.get('sell_unit_minor') is not None for l in lines))
    revenue = rounded(Decimal(revenue_minor) * fx / 10000) if complete_revenue else None
    profit = revenue - cost_total if revenue is not None and cost_complete else None
    target = round.get('target_margin_bp', 1500)
    inverted = profit is not None and profit < 0
    below_target = profit is not None and (revenue == 0 or profit * 10000 < target * revenue)
    if inverted:
        issue('inverted', '报价倒挂，销售额低于总成本；请提高售价或取得更低的有效成本')
    if revenue == 0:
        issue('revenue_zero', '销售总额必须大于零，请填写有效销售单价')
    if below_target:
        issue('below_target', '利润率低于本轮目标，请核对费用并调整销售单价', warning=True)
    # Rounded cumulative allocation avoids negative tails; each incremental
    # allocation is deterministic and the total is exactly fees (including 1 fen).
    per_line, cumulative, allocated = [], 0, 0
    for index, line in enumerate(lines, 1):
        cumulative += line['qty']
        next_allocated = rounded(Decimal(fees) * cumulative / total_qty)
        share = next_allocated - allocated
        allocated = next_allocated
        landed = (line.get('cost_unit_fen') or 0) * line['qty'] + share
        minimum = None
        if fx and cost_complete:
            # This is a lower-bound search, not ordinary amount rounding. Find
            # the first foreign cent whose HALF_UP CNY revenue meets the target.
            needed = max(1, int((Decimal(landed) * 10000 / (10000 - target))
                               .to_integral_value(rounding=ROUND_CEILING)))
            lower = (Decimal(needed) - Decimal('0.5')) * 10000 / (fx * line['qty'])
            minimum = int(lower.to_integral_value(rounding=ROUND_CEILING))
        per_line.append(dict(line_id=line.get('id'), line_no=index, allocated_fee_cny=money(share),
            landed_cost_cny=money(landed), min_price_for_target_minor=minimum))
    margin = (format((Decimal(profit) * 100 / revenue).quantize(Decimal('0.01'), rounding='ROUND_HALF_UP'), '.2f') + '%'
              if revenue and profit is not None else None)
    return dict(totals=dict(cost_cny=money(cost_total), revenue_cny=money(revenue), profit_cny=money(profit),
                           margin=margin, revenue_minor=revenue_minor, cost_complete=cost_complete),
                per_line=per_line, blocking=blocking, warnings=warnings, inverted=inverted, below_target=below_target)
