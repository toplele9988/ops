"""Read-only flags for inbox, draft queues and knowledge candidate producers."""
from .rules import IGNORED
from .store import Store


class PolicyLang:
    """Public-service wrapper for hosts sharing the P1 composition endpoint."""
    def __init__(self, service, policy):
        self.service, self.policy = service, policy

    def read(self, resource, values=None):
        return self.service.read(resource, values)

    def write(self, operation, fields, who):
        if operation == 'compose' and not self.policy.flags(fields.get('conversation_key'))['draft_eligible']:
            from console.archive.validation import Conflict
            raise Conflict('这个会话已标记为忽略或内部；如需起草，请先到待分类页面撤销标记')
        return self.service.write(operation, fields, who)


class Policy:
    def __init__(self, path=None):
        self.store = Store(path)

    def flags(self, key):
        return self.all_flags().get(key, self.flags_for(None))

    @staticmethod
    def flags_for(role):
        excluded = role in IGNORED
        return dict(classification=role, ignored=excluded, reply_eligible=not excluded,
                    draft_eligible=not excluded, kb_eligible=not excluded)

    def all_flags(self):
        with self.store.read() as db:
            rows = db.execute('SELECT key,decision_role FROM suggestions WHERE status != ?', ('pending',)) if db else []
            return {r['key']: self.flags_for(r['decision_role']) for r in rows}

    def project(self, conversation, flags=None):
        value = (flags if flags is not None else self.all_flags()).get(conversation['key'], self.flags_for(None))
        result = dict(conversation, **value)
        if value['ignored']:
            result.update(waiting_reply=False, waiting_since=None, waiting_hours=None)
        return result


class PolicyInbox:
    """Keeps source caches intact; reads current decisions on every request."""
    def __init__(self, inbox, policy):
        self.inbox, self.policy = inbox, policy

    def read(self, now=None):
        from console.customers import aggregate_customers
        snapshot = self.inbox.read(now)
        flags = self.policy.all_flags()
        conversations = {k: self.policy.project(c, flags) for k, c in snapshot['conversations'].items()}
        return dict(snapshot, conversations=conversations, customers=aggregate_customers(conversations.values()))

    def detail(self, key, now=None):
        conv = self.inbox.detail(key) if now is None else self.inbox.detail(key, now)
        return self.policy.project(conv) if conv is not None else None

    def summary(self, now):
        from console.inbox.model import account_stats
        snapshot = self.read(now)
        rows = list(snapshot['conversations'].values())
        accounts = account_stats(rows, now)
        adapters = getattr(self.inbox, 'adapters', {})
        channels = [(a.name, a.label, a.ready) for a in adapters.values() if a.name != 'website']
        if not adapters:
            channels = [(name, name, snapshot['ready']) for name in sorted({c['channel'] for c in rows})]
        by_channel = []
        for name, label, ready in channels:
            selected = [c for c in rows if c['channel'] == name]
            by_channel.append(dict(channel=name, label=label, ready=ready, conversations=len(selected),
                waiting=sum(c['waiting_reply'] for c in selected if c['counterpart_role'] in ('customer', 'unclassified')),
                messages_24h=sum(a['messages_24h'] for a in accounts if a['channel'] == name),
                last_message_at=max((c['last_seen'] for c in selected), default=None)))
        return dict(ready=snapshot['ready'], channels=accounts, by_channel=by_channel,
                    waiting_total=sum(c['waiting_reply'] for c in rows if c['counterpart_role'] in ('customer', 'unclassified')),
                    supplier_waiting=sum(c['waiting_reply'] for c in rows if c['counterpart_role'] == 'supplier'),
                    dormant_total=sum(c['dormant'] for c in rows),
                    new_24h=sum(a['messages_24h'] for a in accounts))
