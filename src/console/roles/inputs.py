"""Read projections using the existing inbox, deal waiting and quote calculators."""
from collections import defaultdict

from console.archive.validation import Missing
from console.deals.service import Deals
from console.quotes.calc import compute
from .common import observation

BUSINESS_TABLES = ('parties', 'identities', 'deals', 'deal_participants', 'deal_conversations',
                   'deal_items', 'deal_tasks', 'deal_events', 'quote_rounds', 'quote_lines',
                   'quote_costs', 'quote_negotiations')
FULFIL_TABLES = ('deal_payments', 'fulfilment_nodes', 'deal_issues', 'deal_issue_notes')


def tables(db):
    return {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}


class Inputs:
    def __init__(self, store, inbox, now, today, config, *, histories=False, deal_id=None, followups_path=None):
        self.now, self.today = now, today
        try:
            source = inbox.read(now)
            self.conversations = dict(source['conversations'])
            self.ready = bool(source['ready'])
        except Exception:
            self.conversations, self.ready = {}, False
        self.unavailable = set()
        with store.read() as db:
            available = tables(db)
            self.rows = {t: [dict(r) for r in db.execute('SELECT * FROM ' + t + ' ORDER BY id')]
                         if t in available else [] for t in BUSINESS_TABLES + FULFIL_TABLES}
        if deal_id is not None and not any(d['id'] == deal_id for d in self.rows['deals']):
            raise Missing('未找到该案子')
        links = [c for c in self.rows['deal_conversations'] if deal_id is None or c['deal_id'] == deal_id]
        if histories:
            for key in dict.fromkeys(c['conversation_key'] for c in links):
                try:
                    conv = inbox.detail(key, now)
                except Exception:
                    conv = None
                if conv:
                    self.conversations[key] = conv
                else:
                    self.unavailable.add(key)
        self.unavailable.update(c['conversation_key'] for c in links if c['conversation_key'] not in self.conversations)
        parties = {p['id']: p for p in self.rows['parties']}
        groups = {}
        for table in ('deal_participants', 'deal_conversations', 'deal_items', 'deal_tasks', 'deal_events'):
            groups[table] = defaultdict(list)
            for row in self.rows[table]:
                groups[table][row['deal_id']].append(row)
        # _view centralizes stage labels, tasks, activity and waiting semantics.
        # Deals and roles share direction, closing and availability semantics.
        projector = Deals(store=store, lookup=lambda key: observation(self.conversations.get(key)),
                          clock=lambda: now, overdue_hours=config.reply_hours)
        self.deals = {d['id']: projector._view(d, parties, groups, projector.lookup, now, True)
                      for d in self.rows['deals'] if deal_id is None or d['id'] == deal_id}
        from console.deals.followups import default_path, records
        for item in records(followups_path if followups_path is not None else default_path(store.path), deal_id):
            if item['deal_id'] in self.deals:
                self.deals[item['deal_id']].setdefault('waiting_items', []).append(item)
        self.quotes = defaultdict(list)
        for q in self.rows['quote_rounds']:
            if deal_id is not None and q['deal_id'] != deal_id:
                continue
            lines = [l for l in self.rows['quote_lines'] if l['round_id'] == q['id']]
            costs = [c for c in self.rows['quote_costs'] if c['round_id'] == q['id']]
            self.quotes[q['deal_id']].append(dict(q, lines=lines, costs=costs, **compute(q, lines, costs, today)))
        for quotes in self.quotes.values():
            quotes.sort(key=lambda q: q['round_no'])

    def latest_quote(self, deal_id):
        quotes = self.quotes[deal_id]
        return quotes[-1] if quotes else None
