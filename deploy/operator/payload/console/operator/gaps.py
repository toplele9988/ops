"""High-priority dossier gaps -> one frozen rehearsal draft per supplier.

Free-text ask_text is evidence for the owner, never an executable instruction.
No source edits, orders, payment discussion, channel binding or model invocation.
"""
import json
from pathlib import Path
from .sources import readonly
from .store import dumps, sha

SAFE_ASK = '您好，麻烦补充最新的产品规格、配置、装箱资料、报价有效期及交期，便于我们核对档案。谢谢。'


class Gaps:
    def __init__(self, operator, path):
        self.operator, self.path = operator, Path(path)

    def poll(self):
        if not self.path.exists():
            return 0  # Parallel dossier import is optional and may not exist yet.
        with readonly(self.path) as db:
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            # Production importer is sd_*; raw candidate uses unprefixed names.
            prefix = 'sd_' if 'sd_gap' in tables else ''
            if prefix+'gap' not in tables or prefix+'supplier' not in tables:
                raise ValueError('dossier_schema_unavailable')
            rows = [dict(r) for r in db.execute('SELECT g.*,s.name,s.supplier_type FROM '+prefix+'gap g JOIN '+prefix+
                    'supplier s ON s.supplier_id=g.supplier_id WHERE g.state=? AND g.priority IN (1,2) ORDER BY g.supplier_id,g.priority,g.gap_id', ('open',))]
        grouped = {}
        for row in rows:
            grouped.setdefault(row['supplier_id'], []).append(row)
        active = {'dossier:'+sha(sid) for sid in grouped}
        with self.operator.store.write() as db:
            old = list(db.execute("SELECT id,contact_key FROM operator_actions WHERE state='dry_run' AND reason='dossier_rehearsal'"))
            for row in old:
                if row['contact_key'] not in active:
                    db.execute("UPDATE operator_actions SET state='rejected',reason='dossier_resolved',updated_at=? WHERE id=?",
                               (self.operator.clock(),row['id']))
                    self.operator.store.decision(db,self.operator.clock(),'dossier_resolved',dict(reason='no_open_high_priority_gap'),action_id=row['id'])
        created = 0
        for sid, gaps in grouped.items():
            created += self.propose(sid, gaps)
        return created

    def propose(self, sid, gaps):
        now, store = self.operator.clock(), self.operator.store
        key = 'dossier:'+sha(sid)
        types = {'产品':'supplier_product', '物流':'supplier_logistics', '配套':'supplier_support'}
        contact = dict(name=gaps[0]['name'], type=types[gaps[0]['supplier_type']], channel='unbound', key=key,
                       timezone=None, language='zh', quota_key=key)
        refs, details = [], []
        for gap in gaps:
            raw_refs = json.loads(gap['source_refs_json'])
            if not isinstance(raw_refs, list):
                raise ValueError('dossier_sources_invalid')
            refs.extend(dict(ref=r if isinstance(r, str) else ' · '.join(str(r.get(k,'')) for k in ('source_id','locator') if r.get(k)), label='档案引用') for r in raw_refs)
            refs.append(dict(ref='dossiers:'+gap['gap_id'], label='缺口 '+gap['gap_type']))
            details.append(dict(id=gap['gap_id'], kind=gap['gap_type'], impact=gap['impact'], priority=gap['priority']))
        event_id = 'dossier-gap:'+sha(dumps([sid, details, refs]))
        event = dict(id=event_id, kind='supplier_gap', conversation_key=key, supplier_id=sid)
        ask = ('您好，麻烦补充可服务的线路、计费方式、运价有效期及运输时效，便于我们核对档案。谢谢。'
               if contact['type'] == 'supplier_logistics' else SAFE_ASK)
        proposal = dict(kind='ask_supplier', contact_key=key, parts=[ask], fact_ids=[], rehearsal_only=True,
                        payload=dict(gaps=details, sources=refs, why='供应商档案存在高优先级待补资料；每家合并一条，请先核对缺口与原渠道联系人。'),
                        context=dict(contacts={key:contact}, deal=None))
        with store.write() as db:
            if db.execute('SELECT 1 FROM operator_events WHERE id=?', (event_id,)).fetchone():
                return 0
            # Replace stale *unexecuted* gap drafts when the gap set changes.
            db.execute("UPDATE operator_actions SET state='rejected',reason='dossier_revised',updated_at=? "
                       "WHERE contact_key=? AND kind='ask_supplier' AND state='dry_run'", (now,key))
            db.execute('INSERT INTO operator_events VALUES(?,?,?,?,?)', (event_id,sha(dumps(event)),dumps(event),'complete',now))
            aid = db.execute('INSERT INTO operator_actions(event_id,kind,contact_key,level,state,reason,dry_run,data,created_at,updated_at) '
                             "VALUES(?,?,?,1,'dry_run','dossier_rehearsal',1,?,?,?)", (event_id,'ask_supplier',key,dumps(proposal),now,now)).lastrowid
            store.decision(db,now,'decision',dict(trigger=event,proposals=[proposal],sources=refs),event_id,aid)
        return 1
