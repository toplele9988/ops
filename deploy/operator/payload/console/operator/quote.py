"""Reuse the existing quote and freight kernels; no parallel price arithmetic."""
from datetime import date
from console.archive.validity import current_offer, quote_state
from console.quotes.calc import compute
from console.quotes.freight import calculate, PACKING
from console.quotes.money import money
from .store import dumps
from .store import sha


def calculate_quote(context, today):
    policy = context.get('quote_policy') or {}
    if (policy.get('status') != 'confirmed' or not policy.get('source_ref')
            or not policy.get('fx_source') or not policy.get('delivery_scope')
            or not policy.get('terms_confirmed') or policy.get('incoterm') not in ('EXW', 'DDP')
            or policy.get('currency') not in ('CNY', 'USD', 'EUR', 'GBP')
            or type(policy.get('fx_x10000')) is not int or policy['fx_x10000'] <= 0
            or type(policy.get('target_margin_bp')) is not int or not 0 <= policy['target_margin_bp'] <= 9000):
        return None, ['quote_policy_required']
    lines, dependencies, expiries = [], [policy['source_ref']], []
    for item in context.get('items', []):
        product = next((p for p in context.get('products', []) if p['id'] == item.get('product_id')), None)
        if not product or product.get('status') != 'confirmed' or not product.get('source_refs'):
            return None, ['product_unapproved']
        offer = current_offer([o for o in context.get('offers', []) if o['product_id'] == product['id']])
        if (not offer or not offer.get('source_ref') or not quote_state(offer, date.fromisoformat(today))['usable_for_quote']
                or offer.get('currency') != 'CNY' or type(offer.get('price_fen')) is not int):
            return None, ['cost_required']
        if type(item.get('qty')) is not int or item['qty'] <= 0:
            return None, ['quantity_required']
        if offer.get('moq_units') and item['qty'] < offer['moq_units']:
            return None, ['minimum_quantity_required']
        line = dict(id=item['id'], name=product['name'], product_id=product['id'], qty=item['qty'],
                    offer_id=offer['id'], cost_unit_fen=offer['price_fen'], cost_currency=offer['currency'],
                    cost_valid_until=offer['valid_until'], cost_validity='valid', sell_unit_minor=None,
                    **{k: product.get(k) for k in PACKING})
        lines.append(line)
        dependencies.extend([offer['source_ref'], *product['source_refs'], f"deal_item:{item['id']}"])
        expiries.append(offer['valid_until'])
    if not lines:
        return None, ['quantity_required']
    costs = []
    if policy['incoterm'] == 'DDP':
        candidates = [f for f in context.get('freight_quotes', [])
                      if f['destination_country'] == context.get('destination_country')]
        # Do not silently reuse a superseded/expired tariff or mix route scopes.
        route = policy.get('freight_route')
        if not route:
            return None, ['freight_required']
        candidates = [f for f in candidates if all(f.get(k) == v for k, v in route.items())]
        from console.archive.validity import route_key
        if len({route_key(f) for f in candidates}) > 1:
            return None, ['freight_route_ambiguous']
        f = current_offer(candidates)
        if not f or not f.get('source_ref') or not quote_state(f, date.fromisoformat(today))['usable_for_quote'] or f.get('currency') != 'CNY':
            return None, ['freight_required']
        if any(not p.get('packing_confirmed') for p in context['products'] if p['id'] in {l['product_id'] for l in lines}):
            return None, ['packing_required']
        freight = calculate(lines, f)
        if freight['blocking']:
            return None, ['packing_required']
        costs.append(dict(kind='freight', amount_fen=freight['amount_fen'], freight_quote_id=f['id'],
                          validity='valid', valid_until=f['valid_until'], calc=dict(freight, quote=f)))
        dependencies.append(f['source_ref'])
        expiries.append(f['valid_until'])
    if not policy.get('valid_until') or policy['valid_until'] < today:
        return None, ['quote_policy_required']
    valid_until = min(*expiries, policy['valid_until'])
    round = dict(policy, valid_until=valid_until, destination_country=context.get('destination_country'))
    first = compute(round, lines, costs, date.fromisoformat(today))
    for line, projected in zip(lines, first['per_line']):
        line['sell_unit_minor'] = projected['min_price_for_target_minor']
    result = compute(round, lines, costs, date.fromisoformat(today))
    if result['blocking']:
        return None, [r['code'] for r in result['blocking']]
    key = sha(dumps([round, lines, costs]))
    public = []
    for lang in ('zh', 'en', 'es', 'fr', 'pt'):
        parts = [f"{l['qty']} × {l['name']}: {policy['currency']} {money(l['sell_unit_minor'])}" for l in lines]
        total = money(result['totals']['revenue_minor'])
        expiry_label = {'en':'valid until', 'es':'válido hasta', 'fr':"valable jusqu’au", 'pt':'válido até'}.get(lang)
        text = ('；'.join(parts) + f"；合计 {policy['currency']} {total}，{policy['incoterm']}，有效期 {valid_until}。"
                if lang == 'zh' else '; '.join(parts) + f"; Total {policy['currency']} {total}, {policy['incoterm']}, {expiry_label} {valid_until}.")
        public.append(dict(id='sale:'+key+':'+lang, kind='sale_price', text=text,
            status='confirmed', source_ref='operator_quote:'+key, observed_at=today, valid_until=valid_until,
            public=True, is_cost_price=False, confidential=False,
            customer_key=context.get('customer_key'), language=lang, dependencies=dependencies))
    return dict(round=round, lines=lines, costs=costs, result=result, facts=public, digest=key), []
