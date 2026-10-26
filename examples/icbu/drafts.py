"""Injected retrieval/model draft core; contains no provider transport."""
import contextlib
import json


def draft_once(store,retrieve,generate):
    with contextlib.closing(store.connect()) as db:
        db.execute('BEGIN IMMEDIATE')
        row=db.execute("SELECT j.inbound_id,i.payload FROM draft_jobs j JOIN inbound i ON i.id=j.inbound_id WHERE j.state='queued' ORDER BY i.rowid LIMIT 1").fetchone()
        if row is None:db.commit();return False
        changed=db.execute("UPDATE draft_jobs SET state='processing' WHERE inbound_id=? AND state='queued'",(row['inbound_id'],)).rowcount
        if changed!=1:db.rollback();return False
        db.commit()
    try:
        message=json.loads(row['payload'])
        matches=retrieve(message['content'])
        if not isinstance(matches,list):raise ValueError('invalid_retrieval')
        result=generate(message,matches)
        if not isinstance(result,str) or not result.strip() or len(result)>16000:raise ValueError('invalid_draft')
    except Exception:
        with contextlib.closing(store.connect()) as db:
            db.execute('BEGIN IMMEDIATE');db.execute("UPDATE draft_jobs SET state='failed',error_code='pipeline_failed' WHERE inbound_id=? AND state='processing'",(row['inbound_id'],));db.execute("UPDATE inbound SET state='draft_failed' WHERE id=?",(row['inbound_id'],));db.commit()
        return True
    with contextlib.closing(store.connect()) as db:
        db.execute('BEGIN IMMEDIATE')
        changed=db.execute("UPDATE draft_jobs SET state='done' WHERE inbound_id=? AND state='processing'",(row['inbound_id'],)).rowcount
        if changed!=1:db.rollback();return False
        db.execute("UPDATE inbound SET state='draft_ready',draft=? WHERE id=?",(result,row['inbound_id']))
        db.commit();return True
