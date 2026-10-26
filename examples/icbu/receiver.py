"""Standalone ICBU-style inbound example; not a production receiver.

No site login, cookies, network scraping, model calls or message sending.
"""
import argparse
import contextlib
import hashlib
import hmac
import json
import os
import re
import sqlite3
import stat
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

ENDPOINT='/api/inbound/alibaba'
MAX_BODY=65536
FIELDS=frozenset(('account','conversation_id','buyer_name','content','url','country','message_id','event_id','history','role'))
IDENTIFIER=re.compile(r'[A-Za-z0-9._:-]{1,256}\Z')


class Rejected(Exception):
    def __init__(self,code,status=400):super().__init__(code);self.status=status


def field(value,name,maximum,empty=False):
    if not isinstance(value,str) or len(value)>maximum or (not empty and not value.strip()) or any(ord(c)<32 and c not in '\n\t' for c in value):
        raise Rejected('invalid_'+name)
    return value


def validate(payload, account, allowed_hosts, event_key=None):
    if not isinstance(payload,dict) or set(payload)-FIELDS:raise Rejected('invalid_fields')
    if payload.get('account')!=account:raise Rejected('account_mismatch',403)
    output={'account':account}
    for name,maximum in (('conversation_id',256),('buyer_name',200),('content',16000)):
        output[name]=field(payload.get(name),name,maximum)
    output['country']=field(payload.get('country',''),'country',100,True)
    for name in ('message_id','event_id'):
        value=payload.get(name)
        if value is not None and (not isinstance(value,str) or not IDENTIFIER.fullmatch(value)):raise Rejected('invalid_'+name)
        output[name]=value
    if event_key is not None:
        if not IDENTIFIER.fullmatch(event_key):raise Rejected('invalid_idempotency_key')
        if output['event_id'] is not None and output['event_id']!=event_key:raise Rejected('idempotency_key_mismatch')
        output['event_id']=event_key
    if not output['event_id'] and not output['message_id']:raise Rejected('stable_identifier_required')
    url=field(payload.get('url'),'url',4096)
    if any(ch.isspace() for ch in url) or '\\' in url:raise Rejected('invalid_url')
    try:
        parsed=urlsplit(url)
        if parsed.scheme!='https' or parsed.username is not None or parsed.password is not None or parsed.port not in (None,443) or parsed.hostname not in allowed_hosts:
            raise ValueError()
    except ValueError:raise Rejected('invalid_url') from None
    output['url']=urlunsplit(('https',parsed.hostname,parsed.path or '/','',''))
    if type(payload.get('history')) is not bool:raise Rejected('explicit_history_required')
    if payload.get('role') not in ('buyer','seller','system','unknown'):raise Rejected('explicit_role_required')
    output['history']=payload['history'];output['role']=payload['role']
    return output


def digest(payload):
    core={key:payload[key] for key in ('account','conversation_id','buyer_name','content','country','history','role')}
    return hashlib.sha256(json.dumps(core,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()


class Store:
    def __init__(self,path):
        self.path=Path(path)
        parent=self.path.parent.lstat()
        if self.path.parent.is_symlink() or not stat.S_ISDIR(parent.st_mode) or parent.st_uid!=os.geteuid() or stat.S_IMODE(parent.st_mode)!=0o700:
            raise ValueError('unsafe_directory')
        fd=os.open(self.path,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
        try:
            s=os.fstat(fd)
            if not stat.S_ISREG(s.st_mode) or s.st_uid!=os.geteuid() or s.st_nlink!=1 or stat.S_IMODE(s.st_mode)!=0o600:raise ValueError('unsafe_database')
        finally:os.close(fd)
        with contextlib.closing(self.connect()) as db:
            db.executescript('''
CREATE TABLE IF NOT EXISTS inbound(id TEXT PRIMARY KEY,account TEXT NOT NULL,conversation_id TEXT NOT NULL,message_id TEXT,event_id TEXT,content_hash TEXT NOT NULL,payload TEXT NOT NULL,state TEXT NOT NULL,draft TEXT,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,UNIQUE(account,conversation_id,message_id),UNIQUE(account,event_id));
CREATE TABLE IF NOT EXISTS draft_jobs(inbound_id TEXT PRIMARY KEY REFERENCES inbound(id),state TEXT NOT NULL,error_code TEXT);
''')
    def connect(self):
        db=sqlite3.connect(self.path,timeout=10,isolation_level=None)
        db.row_factory=sqlite3.Row;db.execute('PRAGMA foreign_keys=ON');return db
    def receive(self,payload,live_proof=None):
        # No production proof validator is included. Callers must inject one.
        eligible=False
        if payload['history'] is False and payload['role']=='buyer' and callable(live_proof):
            try:eligible=live_proof(payload) is True
            except Exception:eligible=False
        value=digest(payload)
        with contextlib.closing(self.connect()) as db:
            db.execute('BEGIN IMMEDIATE')
            rows=[]
            if payload['message_id']:
                rows+=db.execute('SELECT id,content_hash,state,conversation_id,message_id FROM inbound WHERE account=? AND conversation_id=? AND message_id=?',(payload['account'],payload['conversation_id'],payload['message_id'])).fetchall()
            if payload['event_id']:
                rows+=db.execute('SELECT id,content_hash,state,conversation_id,message_id FROM inbound WHERE account=? AND event_id=?',(payload['account'],payload['event_id'])).fetchall()
            if rows:
                if len({x['id'] for x in rows})!=1 or any(x['content_hash']!=value or x['conversation_id']!=payload['conversation_id'] or (x['message_id'] and payload['message_id'] and x['message_id']!=payload['message_id']) for x in rows):
                    db.rollback();raise Rejected('idempotency_conflict',409)
                db.commit();return {'id':rows[0]['id'],'state':rows[0]['state'],'duplicate':True}
            identifier=str(uuid.uuid4());state='draft_queued' if eligible else 'archived'
            db.execute('INSERT INTO inbound(id,account,conversation_id,message_id,event_id,content_hash,payload,state) VALUES(?,?,?,?,?,?,?,?)', (identifier,payload['account'],payload['conversation_id'],payload['message_id'],payload['event_id'],value,json.dumps(payload,ensure_ascii=False),state))
            if eligible:db.execute('INSERT INTO draft_jobs(inbound_id,state) VALUES(?,?)',(identifier,'queued'))
            db.commit();return {'id':identifier,'state':state,'duplicate':False}


def make_server(store,account,allowed_hosts,token,port=0,live_proof=None):
    if not token or not account or not allowed_hosts:raise ValueError('missing_config')
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def setup(self):
            super().setup();self.connection.settimeout(10)
        def reply(self,status,value):
            raw=json.dumps(value).encode();self.send_response(status);self.send_header('Content-Type','application/json');self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
        def do_POST(self):
            if self.path!=ENDPOINT:return self.reply(404,{'error':'not_found'})
            try:authorized=hmac.compare_digest(self.headers.get('Authorization',''),'Bearer '+token)
            except TypeError:authorized=False
            if not authorized:return self.reply(401,{'error':'unauthorized'})
            try:
                if self.headers.get('Transfer-Encoding') is not None:raise Rejected('unsupported_transfer_encoding')
                try:length=int(self.headers.get('Content-Length','0'))
                except ValueError:raise Rejected('invalid_body_length') from None
                if not 1<=length<=MAX_BODY:raise Rejected('invalid_body_length')
                raw=self.rfile.read(length)
                if len(raw)!=length:raise Rejected('incomplete_body')
                try:payload=json.loads(raw.decode('utf-8'))
                except (ValueError,UnicodeError):raise Rejected('invalid_json') from None
                value=validate(payload,account,allowed_hosts,self.headers.get('Idempotency-Key'))
                result=store.receive(value,live_proof)
                return self.reply(200 if result['duplicate'] else 201,result)
            except Rejected as exc:return self.reply(exc.status,{'error':str(exc)})
            except Exception:return self.reply(500,{'error':'internal_error'})
    return ThreadingHTTPServer(('127.0.0.1',port),Handler)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--port',type=int,default=8791);args=parser.parse_args()
    os.umask(0o077)
    path=os.environ.get('INBOUND_DB','');account=os.environ.get('INBOUND_ACCOUNT','');token=os.environ.get('INBOUND_TOKEN','')
    allowed=set(filter(None,os.environ.get('INBOUND_ALLOWED_HOSTS','').split(',')))
    if not path or not Path(path).is_absolute() or not account or len(token)<32 or not allowed:raise SystemExit('Required INBOUND_DB, INBOUND_ACCOUNT, INBOUND_TOKEN and INBOUND_ALLOWED_HOSTS are not configured.')
    # Deliberately loopback only. No default or demo live validator in runtime.
    server=make_server(Store(path),account,allowed,token,args.port)
    try:server.serve_forever()
    finally:server.server_close()


if __name__=='__main__':main()
