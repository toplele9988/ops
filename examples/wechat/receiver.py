"""Synthetic callback archive example. Not the production WeChat protocol."""
import contextlib
import hashlib
import json
import sqlite3
import uuid


class Rejected(Exception): pass

def validate(value):
    fields={'account_id','conversation_id','message_id','sender_type','text'}
    if not isinstance(value,dict) or set(value)!=fields:raise Rejected('invalid_fields')
    for name,maximum in (('account_id',128),('conversation_id',128),('message_id',256),('text',16000)):
        v=value[name]
        if not isinstance(v,str) or not v.strip() or len(v)>maximum or '\x00' in v:raise Rejected('invalid_'+name)
    if value['sender_type'] not in ('buyer','seller','unknown'):raise Rejected('invalid_sender_type')
    return dict(value)

def canonical(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))

class Archive:
    def __init__(self,path):
        self.path=path
        with contextlib.closing(self.connect()) as db:
            db.executescript('''
CREATE TABLE IF NOT EXISTS messages(id TEXT PRIMARY KEY,account_id TEXT NOT NULL,conversation_id TEXT NOT NULL,message_id TEXT NOT NULL,content_hash TEXT NOT NULL,payload_json TEXT NOT NULL,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,UNIQUE(account_id,conversation_id,message_id));
CREATE TABLE IF NOT EXISTS knowledge_jobs(message_pk TEXT PRIMARY KEY REFERENCES messages(id),state TEXT NOT NULL,error_code TEXT);
CREATE TABLE IF NOT EXISTS knowledge_facts(id TEXT PRIMARY KEY,message_pk TEXT NOT NULL REFERENCES messages(id),fact TEXT NOT NULL,source_quote TEXT NOT NULL,UNIQUE(message_pk,fact,source_quote));
''')
    def connect(self):
        db=sqlite3.connect(self.path,timeout=10,isolation_level=None);db.row_factory=sqlite3.Row;db.execute('PRAGMA foreign_keys=ON');return db
    def accept(self,raw,verify,authorize):
        if not callable(verify) or verify(raw) is not True:raise Rejected('unauthorized')
        try:value=json.loads(raw)
        except (TypeError,ValueError):raise Rejected('invalid_json') from None
        msg=validate(value);payload=canonical(msg);digest=hashlib.sha256(payload.encode()).hexdigest()
        # Caller provides explicit identity policy; no default access grant.
        eligible=callable(authorize) and authorize(msg) is True
        with contextlib.closing(self.connect()) as db:
            db.execute('BEGIN IMMEDIATE')
            old=db.execute('SELECT id,content_hash FROM messages WHERE account_id=? AND conversation_id=? AND message_id=?',(msg['account_id'],msg['conversation_id'],msg['message_id'])).fetchone()
            if old:
                if old['content_hash']!=digest:db.rollback();raise Rejected('idempotency_conflict')
                db.commit();return {'id':old['id'],'duplicate':True}
            mid=str(uuid.uuid4())
            db.execute('INSERT INTO messages(id,account_id,conversation_id,message_id,content_hash,payload_json) VALUES(?,?,?,?,?,?)',(mid,msg['account_id'],msg['conversation_id'],msg['message_id'],digest,payload))
            if eligible:db.execute("INSERT INTO knowledge_jobs(message_pk,state) VALUES(?,'queued')",(mid,))
            db.commit();return {'id':mid,'duplicate':False,'queued':eligible}
