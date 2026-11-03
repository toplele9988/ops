"""Pure date and current-record rules, shared by offers and freight quotes."""
from datetime import date

STATES = ('valid', 'expiring', 'expired', 'no_expiry')
NO_EXPIRY_TEXT = '没写有效期，请向对方确认'
ROUTE_FIELDS = ('forwarder_id', 'origin', 'destination_country', 'destination_detail',
                'mode', 'service', 'unit', 'currency', 'channel')


def validity(valid_until, today=None):
    today = today or date.today()
    if not valid_until:
        return 'no_expiry'
    days = (date.fromisoformat(valid_until) - today).days
    # The explicit >= today+7 rule takes precedence at the overlapping boundary.
    return 'expired' if days < 0 else 'expiring' if days < 7 else 'valid'


def current_offer(offers):
    """Confirmed leaf with latest quote date; id breaks ties deterministically.

    Suggested/rejected corrections do not retire confirmed records. Never fall
    back to an older price because the current price has expired or lacks expiry.
    Input is the offers of ONE product (or quotes of ONE route).
    """
    confirmed = [r for r in offers if r['status'] == 'confirmed']
    replaced = {r.get('corrects_offer_id') for r in confirmed}
    leaves = [r for r in confirmed if r['id'] not in replaced]
    return max(leaves, key=lambda r: (r['quoted_on'], r['id']), default=None)


def route_key(quote):
    return tuple(quote.get(field) or '' for field in ROUTE_FIELDS)


def current_quote(quotes):
    return current_offer(quotes)


def quote_state(row, today=None, *, current=True):
    today = today or date.today()
    state = validity(row.get('valid_until'), today)
    usable = (current and row['status'] == 'confirmed' and state in ('valid', 'expiring')
              and date.fromisoformat(row['quoted_on']) <= today)
    return dict(validity=state, usable_for_quote=usable,
                validity_notice=NO_EXPIRY_TEXT if state == 'no_expiry' else None)


def remaining_days(valid_until, today=None):
    """Days through the expiry date (including today); None means no expiry."""
    if not valid_until:
        return None
    today = today or date.today()
    return (date.fromisoformat(valid_until) - today).days


def counts(rows, today=None):
    """Current confirmed rows by product/route, plus every pending suggestion."""
    result = dict.fromkeys((*STATES, 'suggested'), 0)
    groups = {}
    for row in rows:
        if row['status'] == 'suggested':
            result['suggested'] += 1
        key = row['product_id'] if 'product_id' in row else route_key(row)
        groups.setdefault(key, []).append(row)
    for group in groups.values():
        row = current_offer(group)
        if row:
            result[validity(row.get('valid_until'), today)] += 1
    return result
