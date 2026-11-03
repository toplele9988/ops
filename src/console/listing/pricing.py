"""Public sale price suggestions using the existing quote and freight kernels."""
from collections import defaultdict
from decimal import Decimal, InvalidOperation

from console.archive.validity import current_offer, current_quote, quote_state, route_key
from console.quotes.calc import compute
from console.quotes.freight import calculate as freight_calculate
from console.quotes.money import money, rounded


def current_usable_offer(offers, today):
    current = current_offer(offers)
    if not current:
        return None
    state = quote_state(current, today, current=True)
    return current if state['usable_for_quote'] else None


def _freight(product, quotes, target_market, today):
    if not target_market:
        return None, '未填写目标市场，未计入运费'
    grouped = defaultdict(list)
    for row in quotes:
        if row.get('destination_country', '').strip().casefold() == target_market.strip().casefold():
            grouped[route_key(row)].append(row)
    current = []
    for rows in grouped.values():
        quote = current_quote(rows)
        if quote and quote_state(quote, today, current=True)['usable_for_quote']:
            current.append(quote)
    if len(current) != 1:
        return None, ('目标市场没有唯一的当前有效运价，未计入运费' if current else
                      '目标市场没有当前有效运价，未计入运费')
    quote = current[0]
    if quote.get('currency') != 'CNY' or quote.get('unit') not in ('kg', 'cbm'):
        return None, '有效运价币种或计费单位暂不支持自动计算，未计入运费'
    line = dict(qty=1, qty_confirmed=1, **{key: product.get(key) for key in
        ('units_per_carton', 'carton_l_cm', 'carton_w_cm', 'carton_h_cm', 'gross_kg')})
    calculation = freight_calculate([line], quote)
    if calculation.get('amount_fen') is None:
        return None, '包装资料不全，未计入运费'
    state = quote_state(quote, today, current=True)
    cost = dict(kind='freight', manual=False, freight_quote_id=quote['id'],
                valid_until=quote.get('valid_until'), validity=state['validity'],
                amount_fen=calculation['amount_fen'], calc={'blocking': []})
    return (cost, quote), None


def suggest(product, offers, freight_quotes, today, *, fx_data, target_margin_bp=1500, target_market=''):
    """Return one evidence-backed MOQ tier and an internal-only calculation record."""
    offer = current_usable_offer(offers, today)
    sources = []
    if offer:
        sources.append(dict(kind='offer', id=offer['id']))
    empty = []
    if not offer:
        return empty, dict(available=False, reason='没有当前且有效的已确认成本报价'), sources
    if offer.get('moq_units') is None:
        return empty, dict(available=False, reason='有效成本报价没有确认起订量'), sources
    if not offer.get('unit'):
        return empty, dict(available=False, reason='有效成本报价没有计价单位'), sources
    try:
        fx = Decimal(str((fx_data or {}).get('usd_cny')))
        if not fx.is_finite() or not Decimal('0.0001') <= fx <= Decimal(1000000):
            raise InvalidOperation
    except (InvalidOperation, TypeError, ValueError):
        return empty, dict(available=False, reason='USD/CNY 汇率缓存不可用，无法建议美元售价'), sources
    if offer.get('currency') == 'CNY':
        unit_cost_cny_fen = offer['price_fen']
    elif offer.get('currency') == 'USD':
        unit_cost_cny_fen = rounded(Decimal(offer['price_fen']) * fx)
    else:
        return empty, dict(available=False, reason='现有汇率缓存不支持该成本报价币种'), sources

    quantity = offer['moq_units']
    costs = []
    freight_source = None
    freight_note = None
    selected_freight = _freight(product, freight_quotes, target_market, today)
    if selected_freight[0] is not None:
        freight_cost, quote = selected_freight[0]
        # The freight kernel calculates complete cartons at the suggested MOQ.
        line = dict(qty=quantity, qty_confirmed=1, **{key: product.get(key) for key in
            ('units_per_carton', 'carton_l_cm', 'carton_w_cm', 'carton_h_cm', 'gross_kg')})
        calculation = freight_calculate([line], quote)
        if calculation.get('amount_fen') is not None:
            freight_cost['amount_fen'] = calculation['amount_fen']
            freight_cost['calc'] = {'blocking': calculation.get('blocking', [])}
            costs.append(freight_cost)
            freight_source = quote['id']
            sources.append(dict(kind='freight_quote', id=quote['id']))
        else:
            freight_note = '包装资料不全，未计入运费'
    else:
        freight_note = selected_freight[1]

    validity = quote_state(offer, today, current=True)
    line = dict(qty=quantity, qty_confirmed=1, offer_id=offer['id'],
                cost_unit_fen=unit_cost_cny_fen, cost_currency='CNY',
                cost_valid_until=offer.get('valid_until'), cost_validity=validity['validity'])
    quote = dict(fx_x10000=int(fx * 10000), target_margin_bp=target_margin_bp,
                 valid_until='9999-12-31', incoterm='EXW')
    result = compute(quote, [line], costs, today)
    minor = result['per_line'][0]['min_price_for_target_minor']
    if minor is None:
        return empty, dict(available=False, reason='成本和汇率不足以计算目标售价'), sources
    tier = dict(min_qty=quantity, price=money(minor), currency='USD')
    internal = dict(available=True, currency='USD', fx_source=(fx_data or {}).get('source'),
                    fx_as_of=(fx_data or {}).get('as_of'), usd_cny=format(fx, 'f'),
                    target_margin=format(Decimal(target_margin_bp) / 100, '.2f') + '%',
                    cost_cny_total=result['totals']['cost_cny'], cost_unit_cny=money(unit_cost_cny_fen),
                    offer_id=offer['id'], freight_quote_id=freight_source,
                    freight_note=freight_note,
                    pricing_note='仅有一个已确认成本报价，未推演不同采购量折扣；阶梯价按已确认 MOQ 给出一档',
                    min_sale_price_minor=minor)
    return [tier], internal, sources

