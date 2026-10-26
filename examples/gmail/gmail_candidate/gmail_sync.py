"""Gmail read synchronization candidate; injected transport, no credentials or sending.

The caller must bind a transport to an independently authorized mailbox; profile
identity is checked before processing. This module does not implement HTTP/OAuth.
"""
from __future__ import annotations
import contextlib
import fcntl
import hashlib
import json
import os
import re
import sqlite3
import stat
import uuid
from pathlib import Path

API = '/gmail/v1/users/me'

class ApiError(Exception):
    def __init__(self, status: int):
        super().__init__('gmail_http_' + str(status)); self.status = status

class SyncBusy(Exception): pass
class InvalidResponse(Exception): pass
class IdentityMismatch(Exception): pass


def canonical_account(value):
    if not isinstance(value,str) or not 3 <= len(value) <= 320 or value != value.strip() or value.count('@') != 1 or any(ord(c) < 33 for c in value):
        raise ValueError('invalid_account')
    return value.casefold()


def identifier(value, name='invalid_identifier', limit=2048):
    if not isinstance(value,str) or not value or len(value)>limit or any(ord(c)<32 or ord(c)==127 for c in value):
        raise InvalidResponse(name)
    return value


def history_id(value):
    if not isinstance(value,str) or not re.fullmatch(r'[0-9]{1,128}',value):
        raise InvalidResponse('invalid_history_id')
    return value


def payload_json(value):
    if not isinstance(value,dict): raise InvalidResponse('invalid_message')
    raw=json.dumps(value,ensure_ascii=False,allow_nan=False,separators=(',',':'))
    if len(raw.encode('utf-8')) > 32 * 1024 * 1024: raise InvalidResponse('message_too_large')
    return raw


class Store:
    def __init__(self, db_path):
        self.path=Path(db_path)
        parent=self.path.parent.lstat()
        if self.path.parent.is_symlink() or not stat.S_ISDIR(parent.st_mode) or parent.st_uid!=os.geteuid() or stat.S_IMODE(parent.st_mode)!=0o700:
            raise PermissionError('unsafe_store_directory')
        try:
            fd=os.open(self.path,os.O_CREAT|os.O_EXCL|os.O_RDWR|os.O_NOFOLLOW,0o600)
            new=True
        except FileExistsError:
            fd=os.open(self.path,os.O_RDWR|os.O_NOFOLLOW);new=False
        try:
            st=os.fstat(fd)
            if not stat.S_ISREG(st.st_mode) or st.st_uid!=os.geteuid() or st.st_nlink!=1 or stat.S_IMODE(st.st_mode)!=0o600:
                raise PermissionError('unsafe_store_file')
        finally:os.close(fd)
        self.conn=sqlite3.connect(str(self.path),timeout=5)
        self.conn.execute('PRAGMA foreign_keys=ON')
        try:
            if new:
                self.conn.executescript("""
                PRAGMA application_id=1196249945;
                PRAGMA user_version=2;
                CREATE TABLE gmail_accounts(
                  account TEXT PRIMARY KEY, state TEXT NOT NULL, checkpoint TEXT,
                  scan_anchor TEXT, scan_generation TEXT, completed_generation TEXT);
                CREATE TABLE gmail_messages(
                  account TEXT NOT NULL, message_id TEXT NOT NULL, thread_id TEXT,
                  message_json TEXT, remote_state TEXT NOT NULL, last_seen_generation TEXT,
                  PRIMARY KEY(account,message_id), FOREIGN KEY(account) REFERENCES gmail_accounts(account));
                CREATE TABLE gmail_scans(account TEXT PRIMARY KEY,anchor TEXT NOT NULL,generation TEXT NOT NULL,page_token TEXT,listing_done INTEGER NOT NULL,FOREIGN KEY(account) REFERENCES gmail_accounts(account));
                CREATE TABLE gmail_failures(account TEXT NOT NULL,message_id TEXT NOT NULL,code TEXT NOT NULL,attempts INTEGER NOT NULL,PRIMARY KEY(account,message_id),FOREIGN KEY(account) REFERENCES gmail_accounts(account));
                """)
                self.conn.commit()
            if self.conn.execute('PRAGMA application_id').fetchone()[0]!=1196249945 or self.conn.execute('PRAGMA user_version').fetchone()[0]!=2:
                raise ValueError('unrelated_store')
            objects=self.conn.execute("SELECT type,name FROM sqlite_master WHERE type IN ('table','view','trigger') AND name NOT LIKE 'sqlite_%'").fetchall()
            if set(objects)!={('table','gmail_accounts'),('table','gmail_messages'),('table','gmail_scans'),('table','gmail_failures')}:
                raise ValueError('invalid_store_schema')
            expected={'gmail_accounts':('account','state','checkpoint','scan_anchor','scan_generation','completed_generation'),
                      'gmail_messages':('account','message_id','thread_id','message_json','remote_state','last_seen_generation'),
                      'gmail_scans':('account','anchor','generation','page_token','listing_done'),
                      'gmail_failures':('account','message_id','code','attempts')}
            for table,cols in expected.items():
                info=self.conn.execute('PRAGMA table_info('+table+')').fetchall()
                pks=tuple(x[1] for x in sorted((r for r in info if r[5]),key=lambda r:r[5]))
                want=('account',) if table in ('gmail_accounts','gmail_scans') else ('account','message_id')
                if tuple(x[1] for x in info)!=cols or pks!=want:
                    raise ValueError('invalid_store_schema')
            for table in ('gmail_messages','gmail_scans','gmail_failures'):
                foreign=self.conn.execute('PRAGMA foreign_key_list('+table+')').fetchall()
                if len(foreign)!=1 or tuple(foreign[0][2:5])!=('gmail_accounts','account','account'):
                    raise ValueError('invalid_store_schema')
        except BaseException:
            self.conn.close()
            raise

    def close(self): self.conn.close()

    @contextlib.contextmanager
    def lock(self, account):
        # Across processes, only one worker per source account holds this file lock.
        # It is deliberately separate from SQLite: no DB transaction spans network I/O.
        directory=Path(str(self.path)+'.locks'); directory.mkdir(mode=0o700,exist_ok=True)
        st=directory.lstat()
        if directory.is_symlink() or not stat.S_ISDIR(st.st_mode) or st.st_uid!=os.geteuid() or stat.S_IMODE(st.st_mode)!=0o700:
            raise ValueError('unsafe_lock_directory')
        name=hashlib.sha256(account.encode()).hexdigest()+'.lock'
        fd=os.open(directory/name,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
        try:
            st=os.fstat(fd)
            if not stat.S_ISREG(st.st_mode) or st.st_uid!=os.geteuid() or st.st_nlink!=1 or stat.S_IMODE(st.st_mode)!=0o600:
                raise ValueError('unsafe_lock_file')
            try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError as exc:raise SyncBusy('account_sync_busy') from exc
            yield
        finally:os.close(fd)

    def status(self,account):
        account=canonical_account(account)
        row=self.conn.execute('SELECT state,checkpoint,scan_anchor,scan_generation,completed_generation FROM gmail_accounts WHERE account=?',(account,)).fetchone()
        return dict(zip(('state','checkpoint','scan_anchor','scan_generation','completed_generation'),row)) if row else None

    def begin_scan(self,account,anchor,generation):
        with self.conn:
            self.conn.execute('''INSERT INTO gmail_accounts(account,state,checkpoint,scan_anchor,scan_generation)
              VALUES(?,'full_in_progress',NULL,?,?) ON CONFLICT(account) DO UPDATE SET
              state='full_in_progress',scan_anchor=excluded.scan_anchor,scan_generation=excluded.scan_generation''',
              (account,anchor,generation))
            self.conn.execute('INSERT INTO gmail_scans VALUES(?,?,?,NULL,0) ON CONFLICT(account) DO UPDATE SET anchor=excluded.anchor,generation=excluded.generation,page_token=NULL,listing_done=0',(account,anchor,generation))

    def scan(self,account):
        row=self.conn.execute('SELECT anchor,generation,page_token,listing_done FROM gmail_scans WHERE account=?',(account,)).fetchone()
        return dict(zip(('anchor','generation','page_token','listing_done'),row)) if row else None

    def scan_page_done(self,account,next_token):
        with self.conn:
            self.conn.execute('UPDATE gmail_scans SET page_token=?,listing_done=? WHERE account=?',(next_token, int(next_token is None),account))

    def record_failure(self,account,mid,code):
        mid=identifier(mid,'invalid_message_id',512)
        if code not in ('fetch_missing','message_too_large','response_too_large','invalid_json_response','invalid_message','invalid_thread_id','message_id_mismatch','gmail_http_400','gmail_http_413','gmail_http_422'):raise ValueError('invalid_failure_code')
        with self.conn:
            self.conn.execute('INSERT INTO gmail_failures VALUES(?,?,?,1) ON CONFLICT(account,message_id) DO UPDATE SET code=excluded.code,attempts=attempts+1',(account,mid,code))

    def clear_failure(self,account,mid):
        with self.conn:self.conn.execute('DELETE FROM gmail_failures WHERE account=? AND message_id=?',(account,mid))

    def failures(self,account,limit=100,max_attempts=None):
        if not 1<=limit<=1000 or (max_attempts is not None and (type(max_attempts) is not int or not 1<=max_attempts<=100)):
            raise ValueError('invalid_limit')
        if max_attempts is None:
            return self.conn.execute('SELECT message_id,code,attempts FROM gmail_failures WHERE account=? ORDER BY attempts,message_id LIMIT ?',(account,limit)).fetchall()
        return self.conn.execute('SELECT message_id,code,attempts FROM gmail_failures WHERE account=? AND attempts<? ORDER BY attempts,message_id LIMIT ?',(account,max_attempts,limit)).fetchall()

    def exhausted_count(self,account,max_attempts):
        return self.conn.execute('SELECT count(*) FROM gmail_failures WHERE account=? AND attempts>=?',(account,max_attempts)).fetchone()[0]

    def failure_count(self,account):
        return self.conn.execute('SELECT count(*) FROM gmail_failures WHERE account=?',(account,)).fetchone()[0]

    def full_required(self,account):
        with self.conn:
            self.conn.execute("UPDATE gmail_accounts SET state='full_required',scan_anchor=NULL,scan_generation=NULL WHERE account=?",(account,))
            self.conn.execute('DELETE FROM gmail_scans WHERE account=?',(account,))

    def commit_checkpoint(self,account,checkpoint,completed_generation=None):
        with self.conn:
            if completed_generation:
                # A full listing may omit old messages. Preserve their content;
                # absence alone cannot prove deletion or current existence.
                self.conn.execute("UPDATE gmail_messages SET remote_state='not_seen_in_scan' WHERE account=? AND remote_state='present' AND (last_seen_generation IS NULL OR last_seen_generation<>?)",(account,completed_generation))
            self.conn.execute("UPDATE gmail_accounts SET state='ready',checkpoint=?,scan_anchor=NULL,scan_generation=NULL,completed_generation=COALESCE(?,completed_generation) WHERE account=?",(checkpoint,completed_generation,account))
            if completed_generation:self.conn.execute('DELETE FROM gmail_scans WHERE account=?',(account,))

    def put(self,account,mid,value,generation=None):
        mid=identifier(mid,'invalid_message_id',512)
        if not isinstance(value,dict) or value.get('id')!=mid: raise InvalidResponse('message_id_mismatch')
        thread=identifier(value.get('threadId'),'invalid_thread_id',512)
        raw=payload_json(value)
        with self.conn:
            self.conn.execute('''INSERT INTO gmail_messages(account,message_id,thread_id,message_json,remote_state,last_seen_generation)
              VALUES(?,?,?,?,'present',?) ON CONFLICT(account,message_id) DO UPDATE SET
              thread_id=excluded.thread_id,message_json=excluded.message_json,remote_state='present',
              last_seen_generation=COALESCE(excluded.last_seen_generation,gmail_messages.last_seen_generation)''',
              (account,mid,thread,raw,generation))

    def mark(self,account,mid,state):
        mid=identifier(mid,'invalid_message_id',512)
        if state not in ('deleted_remote','fetch_missing'):raise ValueError('invalid_state')
        with self.conn:
            self.conn.execute('''INSERT INTO gmail_messages(account,message_id,remote_state) VALUES(?,?,?)
              ON CONFLICT(account,message_id) DO UPDATE SET remote_state=CASE WHEN gmail_messages.remote_state='deleted_remote' AND excluded.remote_state='fetch_missing' THEN 'deleted_remote' ELSE excluded.remote_state END''',(account,mid,state))


def profile(transport,account):
    p=transport.get(API+'/profile',{})
    if not isinstance(p,dict):raise InvalidResponse('invalid_profile')
    if canonical_account(p.get('emailAddress'))!=account:raise IdentityMismatch('account_mismatch')
    return history_id(p.get('historyId'))


def pages(transport,path,params):
    seen=set();token=None
    while True:
        q=dict(params)
        if token:q['pageToken']=token
        result=transport.get(path,q)
        if not isinstance(result,dict):raise InvalidResponse('invalid_page')
        yield result
        token=result.get('nextPageToken')
        if token is None:break
        identifier(token,'invalid_page_token',8192)
        if token in seen:raise InvalidResponse('repeated_page_token')
        seen.add(token)


def fetch(store,transport,account,mid,generation=None):
    identifier(mid,'invalid_message_id',512)
    if mid in ('.','..') or '/' in mid or '\\' in mid:raise InvalidResponse('unsafe_message_id')
    # Gmail IDs are opaque: encode as a path segment; never interpolate raw paths.
    from urllib.parse import quote
    try:message=transport.get(API+'/messages/'+quote(mid,safe=''),{'format':'full'})
    except ApiError as e:
        if e.status!=404:raise
        store.mark(account,mid,'fetch_missing');return False
    store.put(account,mid,message,generation);return True


def fetch_recorded(store,transport,account,mid,generation=None):
    try:
        found=fetch(store,transport,account,mid,generation)
    except InvalidResponse as exc:
        code=str(exc)
        if code not in ('message_too_large','response_too_large','invalid_json_response','invalid_message','invalid_thread_id','message_id_mismatch'):raise
        store.record_failure(account,mid,code);return False
    except ApiError as exc:
        if exc.status not in (400,413,422):raise
        store.record_failure(account,mid,'gmail_http_'+str(exc.status));return False
    if found:store.clear_failure(account,mid)
    else:store.record_failure(account,mid,'fetch_missing')
    return found

def retry_failures(store,transport,account,limit=100,max_attempts=None):
    account=canonical_account(account)
    with store.lock(account):
        profile(transport,account)
        rows=store.failures(account,limit,max_attempts)
        attempted=0
        for mid,_,_ in rows:
            if max_attempts is not None:
                state=store.conn.execute('SELECT remote_state FROM gmail_messages WHERE account=? AND message_id=?',(account,mid)).fetchone()
                if state and state[0]=='deleted_remote':
                    # Preserve the failed-message evidence, but never resurrect a
                    # provider-confirmed deletion from a stale message response.
                    with store.conn:
                        store.conn.execute('UPDATE gmail_failures SET attempts=? WHERE account=? AND message_id=?',(max_attempts,account,mid))
                    continue
            fetch_recorded(store,transport,account,mid);attempted+=1
        remaining=store.failure_count(account)
        if max_attempts is None:return {'remaining':remaining}
        return {'remaining':remaining,'attempted':attempted,'exhausted':store.exhausted_count(account,max_attempts)}

def consume_history(store,transport,account,anchor,generation=None):
    final=None
    for result in pages(transport,API+'/history',{'startHistoryId':history_id(anchor),'maxResults':500}):
        history=result.get('history',[])
        if not isinstance(history,list):raise InvalidResponse('invalid_history')
        for event in history:
            if not isinstance(event,dict):raise InvalidResponse('invalid_history_event')
            # Specific event arrays avoid duplicates from the generic messages array.
            actions={}
            for key in ('messagesAdded','labelsAdded','labelsRemoved','messagesDeleted'):
                entries=event.get(key,[])
                if not isinstance(entries,list):raise InvalidResponse('invalid_history_event')
                for change in entries:
                    if not isinstance(change,dict) or not isinstance(change.get('message'),dict):raise InvalidResponse('invalid_history_event')
                    mid=identifier(change['message'].get('id'),'invalid_message_id',512)
                    actions[mid]='delete' if key=='messagesDeleted' else 'fetch'
            for mid,action in actions.items():
                if action=='delete':store.mark(account,mid,'deleted_remote')
                else:fetch_recorded(store,transport,account,mid,generation)
        if result.get('nextPageToken') is None:
            final=history_id(result.get('historyId'))
    if final is None:raise InvalidResponse('missing_final_history_id')
    if int(final)<int(anchor):raise InvalidResponse('history_regressed')
    return final


def sync(store,transport,account):
    """One safe full or incremental run; exceptions leave checkpoints unadvanced."""
    account=canonical_account(account)
    with store.lock(account):
        baseline=profile(transport,account)
        current=store.status(account)
        if not current or current['state']!='ready' or not current['checkpoint']:
            scan=store.scan(account) if current and current['state']=='full_in_progress' else None
            if scan is None:
                generation=uuid.uuid4().hex
                store.begin_scan(account,baseline,generation)
                scan=store.scan(account)
            anchor=history_id(scan['anchor']);generation=identifier(scan['generation'])
            token=scan['page_token'];seen=set()
            while not scan['listing_done']:
                if token in seen:raise InvalidResponse('repeated_page_token')
                seen.add(token)
                params={'includeSpamTrash':'true','maxResults':500}
                if token:params['pageToken']=identifier(token,'invalid_page_token',8192)
                try:result=transport.get(API+'/messages',params)
                except ApiError as exc:
                    if token and exc.status==400:
                        store.full_required(account)
                        return {'status':'full_required','reason':'listing_cursor_rejected','pending_failures':store.failure_count(account)}
                    raise
                if not isinstance(result,dict):raise InvalidResponse('invalid_page')
                messages=result.get('messages',[])
                if not isinstance(messages,list):raise InvalidResponse('invalid_messages_page')
                for item in messages:
                    if not isinstance(item,dict):raise InvalidResponse('invalid_message_ref')
                    fetch_recorded(store,transport,account,identifier(item.get('id'),'invalid_message_id',512),generation)
                next_token=result.get('nextPageToken')
                if next_token is not None:
                    identifier(next_token,'invalid_page_token',8192)
                    if next_token in seen:raise InvalidResponse('repeated_page_token')
                # Only advance after every message is either stored or recorded
                # in the per-message retry queue. Replays are idempotent.
                store.scan_page_done(account,next_token);token=next_token;scan=store.scan(account)
            try:final=consume_history(store,transport,account,anchor,generation)
            except ApiError as exc:
                if exc.status!=404:raise
                store.full_required(account)
                return {'status':'full_required','reason':'history_expired_during_full'}
            store.commit_checkpoint(account,final,generation)
            return {'status':'ready_with_failures' if store.failure_count(account) else 'ready','mode':'full','history_id':final,'scan_generation':generation,'pending_failures':store.failure_count(account)}
        try:final=consume_history(store,transport,account,current['checkpoint'])
        except ApiError as exc:
            if exc.status!=404:raise
            store.full_required(account)
            return {'status':'full_required','reason':'history_expired'}
        store.commit_checkpoint(account,final)
        return {'status':'ready_with_failures' if store.failure_count(account) else 'ready','mode':'incremental','history_id':final,'pending_failures':store.failure_count(account)}
