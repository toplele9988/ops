"""Read committed roles/archive approvals and human payments; never mutate them.

A stable record+version wake-up complements raw inbound events. Arbitrary free
text never becomes confirmed cost and fake delivery never marks a quote sent.
"""
from .store import dumps,sha


class ArchiveEvents:
    def __init__(self,operator,events):
        self.operator,self.events=operator,events

    def poll(self):
        pending=[]
        with self.operator.source.archive.read() as db:
            tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not {'deals','deal_conversations','identities'}<=tables:
                return 0
            links=[dict(r) for r in db.execute('SELECT c.* FROM deal_conversations c JOIN identities i '
                   'ON i.conversation_key=c.conversation_key AND i.party_id=c.party_id JOIN deals d ON d.id=c.deal_id '
                   "WHERE d.status IN ('open','won')")]
            def add(kind,record,pid,deal_id):
                recipients=[c for c in links if c['party_id']==pid and c['deal_id']==deal_id]
                if len(recipients)!=1:
                    self.operator.store.health('archive.recipient',self.operator.clock(),ValueError())
                    return
                pending.append((kind,recipients[0]['conversation_key'],kind+':'+sha(dumps(record)),deal_id))
            if {'offers','products','deal_items'}<=tables:
                for r in db.execute("SELECT o.*,p.supplier_id,i.deal_id FROM offers o JOIN products p ON p.id=o.product_id "
                       "JOIN deal_items i ON i.product_id=p.id WHERE o.status='confirmed' AND o.source_ref IS NOT NULL AND o.source_ref!=''"):
                    add('supplier_reply',dict(r),r['supplier_id'],r['deal_id'])
            if 'freight_quotes' in tables:
                for r in db.execute("SELECT * FROM freight_quotes WHERE status='confirmed' AND source_ref IS NOT NULL AND source_ref!=''"):
                    for did in {c['deal_id'] for c in links if c['party_id']==r['forwarder_id']}:
                        add('forwarder_reply',dict(r),r['forwarder_id'],did)
            if 'deal_payments' in tables:
                for r in db.execute("SELECT p.*,d.customer_party_id FROM deal_payments p JOIN deals d ON d.id=p.deal_id "
                       "WHERE p.status='confirmed' AND p.qualifies=1 AND p.kind!='refund' AND p.confirmed_at IS NOT NULL "
                       "AND p.confirmed_by IS NOT NULL AND p.confirmed_by NOT IN ('operator_auto','model_auto')"):
                    add('payment_confirmed',dict(r),r['customer_party_id'],r['deal_id'])
        admitted=0
        for kind,key,version,did in pending:
            try:
                eid=kind+':'+sha(dumps([key,version,did]))
                with self.operator.store.read() as ledger:
                    if ledger is not None and ledger.execute('SELECT 1 FROM operator_events WHERE id=?',(eid,)).fetchone():
                        continue
                # Events claims are the durable replay ledger for these versions.
                self.events.emit(kind,key,version,deal_id=did)
                admitted+=1
            except Exception as error:
                self.operator.store.health('archive.event',self.operator.clock(),error)
        return admitted
