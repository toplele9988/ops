"""Composition root with explicit private paths and injected existing services."""
import json
from console.archive.validity import ROUTE_FIELDS
from .context import ArchiveContext
from .engine import Operator
from .events import Events


class QuotePolicyFromRound:
    """Reuse the newest quote round's human-confirmed terms, FX and margin.

No default exchange rate, margin, validity, seller or route is invented. For new
deals without a reviewed round, inject the owner's approved per-customer pricing
policy instead; an absent policy produces a review action.
"""
    def __init__(self, quotes):
        self.quotes = quotes

    def __call__(self, deal):
        with self.quotes.store.read() as db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE name='quote_rounds'").fetchone():
                return None
            row = db.execute('SELECT * FROM quote_rounds WHERE deal_id=? ORDER BY round_no DESC,id DESC LIMIT 1', (deal['id'],)).fetchone()
            if not row or row['status'] in ('rejected', 'superseded'):
                return None
            row = dict(row)
            state = self.quotes.state.get(self.quotes._state_key(row))
            terms = state.get('terms', {})
            if not terms.get('confirmed') or not terms.get('confirmed_by') or not terms.get('confirmed_at'):
                return None
            policy = {k: row.get(k) for k in ('fx_x10000', 'fx_source', 'target_margin_bp', 'currency', 'incoterm', 'valid_until')}
            policy.update({k: terms.get(k) for k in ('seller_name','seller_address','payment','delivery_scope')})
            policy.update(status='confirmed', terms_confirmed=True,
                          source_ref=f"quote_round:{row['id']}:terms:{terms['confirmed_at']}")
            if db.execute("SELECT 1 FROM quote_costs WHERE round_id=? AND kind!='freight' LIMIT 1", (row['id'],)).fetchone():
                # This operator kernel only prices products + one freight route.
                # Never drop existing accessory/domestic/manual cost lines.
                return None
            costs = list(db.execute("SELECT calc_json FROM quote_costs WHERE round_id=? AND kind='freight'", (row['id'],)))
            if len(costs) == 1:
                quote = json.loads(costs[0]['calc_json'] or '{}').get('quote', {})
                policy['freight_route'] = {k: quote.get(k) for k in ROUTE_FIELDS}
            return policy


def build_operator(*, operator_path, archive_path, inbox, hub, quote_policy,
                   model=None, bridge=None, language=None, clock=None):
    source = ArchiveContext(archive_path, inbox, hub=hub, quote_policy=quote_policy, language=language)
    operator = Operator(operator_path, source=source, model=model, bridge=bridge, clock=clock)
    source.profiles = operator.profiles
    return operator, Events(operator)
