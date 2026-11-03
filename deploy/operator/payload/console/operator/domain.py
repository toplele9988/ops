"""Optional integration for internal commands; never constructs channel clients.

Inject existing Deals/Fulfil services configured to an explicitly chosen database.
Receipt IDs come from the operator's durable action/part reservation.
"""
from console.archive.validation import BadRequest
from datetime import datetime, timezone


class DomainActions:
    def __init__(self, deals, fulfil):
        self.deals, self.fulfil = deals, fulfil

    def apply(self, *, kind, payload, operation_key, actor):
        if kind == 'create_deal':
            self.deals.write('create', None, dict(conversation_key=payload['conversation_key'], stage='inquiry',
                             operation_key=operation_key), actor)
        elif kind == 'advance_deal':
            if payload.get('stage') not in ('sourcing','quoting','negotiating','awaiting_payment'):
                raise BadRequest('AI 不得推进到付款或成交阶段')
            self.deals.write('update', payload['deal_id'], dict(stage=payload['stage'], operation_key=operation_key), actor)
        elif kind == 'update_profile':
            # Append a sourced note; do not silently overwrite an approved business field.
            if not payload.get('source_ref') or not payload.get('note'):
                raise BadRequest('档案更新缺少来源')
            from console.customers.operations import install, Parties
            from console.deals.operations import once
            stamp = datetime.fromtimestamp(self.deals.clock(), timezone.utc).isoformat()
            summary = payload['note']+' [来源:'+payload['source_ref']+']'
            with self.deals.store.write() as db:
                install(db)
                def record():
                    self.deals._event(db, payload['deal_id'], 'note', summary, actor, stamp)
                    if payload.get('party_id'):
                        Parties.event(db, payload['party_id'], summary, actor, stamp)
                    return {'ok': True}
                once(db, 'operator-profile:'+str(payload.get('party_id')), operation_key, payload, record)
        elif kind == 'schedule_shipping':
            # Scheduling only creates a todo; never claims a physical shipment occurred.
            current = self.fulfil.read('fulfilment', row_id=payload['deal_id'])
            for node in payload['nodes']:
                if node not in ('inspection','booking','transit'):
                    raise BadRequest('发货节点无效')
                if any(n['node'] == node and n.get('id') for n in current['items']):
                    continue  # Never reset an existing in-progress/completed node.
                self.fulfil.write('node_update', payload['deal_id'],
                                  {'status':'todo', 'operation_key': operation_key+'-'+node}, actor, child_id=node)
        else:
            raise BadRequest('内部动作无效')
        return 'done'
