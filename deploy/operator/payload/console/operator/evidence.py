"""Public product facts from reviewed fields, never descriptions or internal notes."""
from datetime import datetime, timezone
import json
from console.archive.validity import current_offer, quote_state


def product_facts(products, offers, customer_key, now):
    result = []
    today = datetime.fromtimestamp(now, timezone.utc).date()
    labels = {'model': ('型号', 'Model', ''), 'wattage': ('功率', 'Power', ' W'),
              'product_l_cm': ('长度', 'Length', ' cm'), 'product_w_cm': ('宽度', 'Width', ' cm'),
              'product_h_cm': ('高度', 'Height', ' cm'), 'gross_kg': ('外箱毛重', 'Carton gross weight', ' kg'),
              'units_per_carton': ('每箱数量', 'Units per carton', '')}
    for p in products:
        if p['status'] != 'confirmed' or not p.get('source_refs'):
            continue
        states = json.loads(p.get('field_state_json') or '{}')
        for field, (zh, en, unit) in labels.items():
            state = states.get(field, {})
            if p.get(field) is None or state.get('state') not in ('confirmed', 'manual'):
                continue
            # A reviewed value must point at a source, not merely share a product ID.
            source = state.get('source')
            if not source or not state.get('at'):
                continue
            for lang, label in (('zh', zh), ('en', en)):
                result.append(dict(id=f"product:{p['id']}:{field}:{lang}", kind='specification',
                    text=f"{p['name']} — {label}: {p[field]}{unit}", language=lang,
                    status='confirmed', source_ref=source, observed_at=state['at'],
                    public=True, is_cost_price=False, confidential=False))
        offer = current_offer([o for o in offers if o['product_id'] == p['id']])
        if not offer or not offer.get('source_ref') or not quote_state(offer, today)['usable_for_quote']:
            continue
        low, high = offer.get('lead_days_min'), offer.get('lead_days_max')
        if low is None and high is None:
            continue
        days = f'{low}–{high}' if low is not None and high is not None and low != high else str(high if high is not None else low)
        for lang, text in (('zh', f"{p['name']} — 交期: {days} 天"), ('en', f"{p['name']} — Lead time: {days} days")):
            result.append(dict(id=f"lead:{offer['id']}:{lang}", kind='lead_time', text=text, language=lang,
                status='confirmed', source_ref=offer['source_ref'], observed_at=offer['quoted_on'],
                valid_until=offer['valid_until'], public=True, is_cost_price=False, confidential=False,
                customer_key=customer_key))
    return result
