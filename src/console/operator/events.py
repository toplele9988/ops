"""Post-commit integration hooks. No HTTP endpoint, background thread or polling on import."""
from .store import sha, dumps


class Events:
    def __init__(self, operator):
        self.operator = operator

    def emit(self, kind, conversation_key, source_id, *, deal_id=None):
        if not isinstance(source_id, str) or not source_id:
            raise ValueError('A durable source ID is required')
        event = dict(id=kind+':'+sha(dumps([conversation_key, source_id, deal_id])),
                     kind=kind, conversation_key=conversation_key, message_id=source_id)
        if deal_id is not None:
            event['deal_id'] = deal_id
        actions = self.operator.handle(event)
        # Each recipient progresses independently. tick resumes remaining segments.
        return [self.operator.dispatch(a['id']) if a['state'] == 'ready' else a for a in actions]

    def inbound(self, conversation_key, source_id, *, deal_id=None):
        return self.emit('inbound', conversation_key, source_id, deal_id=deal_id)

    def supplier_reply(self, conversation_key, source_id, *, deal_id):
        return self.emit('supplier_reply', conversation_key, source_id, deal_id=deal_id)

    def forwarder_reply(self, conversation_key, source_id, *, deal_id):
        return self.emit('forwarder_reply', conversation_key, source_id, deal_id=deal_id)

    def payment_confirmed(self, conversation_key, source_id, *, deal_id):
        # Merely a wake-up: snapshot requires the existing human confirmation record.
        return self.emit('payment_confirmed', conversation_key, source_id, deal_id=deal_id)
