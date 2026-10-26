"""Synthetic extraction queue; no network, model SDK, or vector index."""
import contextlib
import hashlib
import json


def work_once(archive,extract):
    with contextlib.closing(archive.connect()) as db:
        db.execute('BEGIN IMMEDIATE')
        row=db.execute("SELECT j.message_pk,m.payload_json FROM knowledge_jobs j JOIN messages m ON m.id=j.message_pk WHERE j.state='queued' ORDER BY m.rowid LIMIT 1").fetchone()
        if row is None:db.commit();return False
        db.execute("UPDATE knowledge_jobs SET state='processing' WHERE message_pk=? AND state='queued'",(row['message_pk'],));db.commit()
    try:
        message=json.loads(row['payload_json']);facts=extract(message)
        if not isinstance(facts,list) or len(facts)>20:raise ValueError('invalid_facts')
        for item in facts:
            if not isinstance(item,dict) or set(item)!={'fact','source_quote'} or not all(isinstance(item[k],str) and item[k].strip() and len(item[k])<=2000 for k in item):raise ValueError('invalid_fact')
            if item['source_quote'] not in message['text']:raise ValueError('unsupported_quote')
    except Exception:
        with contextlib.closing(archive.connect()) as db:
            db.execute('BEGIN IMMEDIATE');db.execute("UPDATE knowledge_jobs SET state='failed',error_code='extraction_failed' WHERE message_pk=? AND state='processing'",(row['message_pk'],));db.commit()
        return True
    with contextlib.closing(archive.connect()) as db:
        db.execute('BEGIN IMMEDIATE')
        current=db.execute('SELECT state FROM knowledge_jobs WHERE message_pk=?',(row['message_pk'],)).fetchone()
        if not current or current[0]!='processing':db.rollback();return False
        for item in facts:
            key=hashlib.sha256((row['message_pk']+'\0'+item['fact']+'\0'+item['source_quote']).encode()).hexdigest()
            db.execute('INSERT OR IGNORE INTO knowledge_facts VALUES(?,?,?,?)',(key,row['message_pk'],item['fact'],item['source_quote']))
        db.execute('UPDATE knowledge_jobs SET state=? WHERE message_pk=?',('done' if facts else 'no_supported_facts',row['message_pk']));db.commit()
    return True
