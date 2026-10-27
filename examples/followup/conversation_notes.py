"""Internal follow-up notes, explicitly scoped to an authorized source conversation.

No platform API calls, emails or customer messages. Both authenticated users use
one shared note with optimistic concurrency. Callers must derive identity/scope
from current trusted source data, never directly from a browser form.
"""
from __future__ import annotations
import math,os,re,sqlite3,stat,time,unicodedata
from pathlib import Path
from contextlib import contextmanager

DB=Path(os.environ.get('FOLLOWUP_NOTES_DB', 'conversation_notes.sqlite3'))
APP_ID=0x4c434e54
STATES={'pending':'待跟进','following':'跟进中','waiting_customer':'等待客户','waiting_supplier':'等待供应商','done':'已完成'}
KEY=re.compile(r'[0-9a-f]{64}\Z')
ACTOR=re.compile(r'[a-z][a-z0-9_.-]{2,63}\Z')
class Conflict(Exception):pass
class ScopeMismatch(Exception):pass

def _scope(key,channel,account,scope,conversation):
    if not isinstance(key,str) or not KEY.fullmatch(key) or channel not in ('wechat','alibaba','whatsapp'):
        raise ValueError('invalid_source')
    for v in (account,scope,conversation):
        if not isinstance(v,str) or not v or len(v)>512 or any(unicodedata.category(c).startswith('C') for c in v):
            raise ValueError('invalid_source')
    return key,channel,account,scope,conversation

def _secure(path):
    path=Path(path)
    if any(p.is_symlink() for p in (path,*path.parents)):raise PermissionError('symlink_path')
    parent=path.parent.lstat();s=path.lstat()
    if (not stat.S_ISDIR(parent.st_mode) or parent.st_uid!=os.geteuid() or stat.S_IMODE(parent.st_mode)!=0o700 or
        not stat.S_ISREG(s.st_mode) or s.st_uid!=os.geteuid() or stat.S_IMODE(s.st_mode)!=0o600 or s.st_nlink!=1):
        raise PermissionError('unsafe_database')
    return path

@contextmanager
def _conn(path,write=False):
    path=_secure(path);c=sqlite3.connect(path.resolve().as_uri()+('?mode=rw' if write else '?mode=ro'),uri=True,timeout=3)
    try:
        c.row_factory=sqlite3.Row
        if c.execute('pragma application_id').fetchone()[0]!=APP_ID or c.execute('pragma user_version').fetchone()[0]!=1:raise ValueError('wrong_database')
        columns={'conversation_notes':('key','channel','account','scope','conversation_id','status','content','version','updated_at','updated_by'),
                 'note_events':('id','key','version','actor','status','at')}
        for table,expected in columns.items():
            if tuple(row[1] for row in c.execute('pragma table_info('+table+')'))!=expected:raise ValueError('schema_invalid')
        for name,verb in (('note_events_no_update','UPDATE'),('note_events_no_delete','DELETE')):
            row=c.execute("select sql from sqlite_master where type='trigger' and name=?",(name,)).fetchone()
            if not row or ('BEFORE '+verb+' ON note_events') not in row[0] or "RAISE(ABORT,'immutable')" not in row[0]:raise ValueError('schema_invalid')
        indexes=c.execute('pragma index_list(note_events)').fetchall()
        if not any(r[1]=='note_event_version' and r[2]==1 for r in indexes) or tuple(r[2] for r in c.execute('pragma index_info(note_event_version)'))!=('key','version'):raise ValueError('schema_invalid')
        if not write:c.execute('pragma query_only=on')
        with c:yield c
    finally:c.close()

def initialize(path=DB):
    """Explicit one-time initialization; never called by a request handler."""
    path=Path(path);parent=path.parent.lstat()
    if path.parent.is_symlink() or not stat.S_ISDIR(parent.st_mode) or parent.st_uid!=os.geteuid() or stat.S_IMODE(parent.st_mode)!=0o700:raise PermissionError('unsafe_directory')
    try:fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW|os.O_CLOEXEC,0o600)
    except FileExistsError:
        with _conn(path) as c:pass
        return False
    os.close(fd)
    try:
        c=sqlite3.connect(path)
        try:
            c.executescript(f'''BEGIN IMMEDIATE; PRAGMA application_id={APP_ID}; PRAGMA user_version=1;
              CREATE TABLE conversation_notes (key TEXT PRIMARY KEY,channel TEXT NOT NULL,account TEXT NOT NULL,scope TEXT NOT NULL,conversation_id TEXT NOT NULL,
                status TEXT NOT NULL,content TEXT NOT NULL,version INTEGER NOT NULL CHECK(version>0),updated_at REAL NOT NULL,updated_by TEXT NOT NULL);
              CREATE TABLE note_events (id INTEGER PRIMARY KEY,key TEXT NOT NULL,version INTEGER NOT NULL,actor TEXT NOT NULL,status TEXT NOT NULL,at REAL NOT NULL);
              CREATE UNIQUE INDEX note_event_version ON note_events(key,version);
              CREATE TRIGGER note_events_no_update BEFORE UPDATE ON note_events BEGIN SELECT RAISE(ABORT,'immutable'); END;
              CREATE TRIGGER note_events_no_delete BEFORE DELETE ON note_events BEGIN SELECT RAISE(ABORT,'immutable'); END;
              COMMIT;''')
        finally:c.close()
    except Exception:
        # Preserve failed initialization for explicit diagnosis; do not silently reset.
        raise
    return True

def read(key,channel,account,scope,conversation,*,path=DB):
    identity=_scope(key,channel,account,scope,conversation)
    with _conn(path) as c:
        row=c.execute('select * from conversation_notes where key=?',(key,)).fetchone()
    if row is None:return {'exists':False,'version':0,'status':'pending','content':'','updated_at':None,'updated_by':None}
    if tuple(row[k] for k in ('key','channel','account','scope','conversation_id')) != identity:raise ScopeMismatch('source_changed')
    if row['status'] not in STATES or type(row['version']) is not int or row['version']<1:raise ValueError('invalid_note')
    return {'exists':True,'version':row['version'],'status':row['status'],'content':row['content'], 'updated_at':row['updated_at'],'updated_by':row['updated_by']}

def save(key,channel,account,scope,conversation,*,actor,status,content,expected_version,path=DB,clock=time.time):
    identity=_scope(key,channel,account,scope,conversation)
    if not isinstance(actor,str) or not ACTOR.fullmatch(actor):raise ValueError('invalid_actor')
    if status not in STATES or not isinstance(content,str) or len(content)>512 or any(unicodedata.category(c).startswith('C') and c not in '\n\t' for c in content):raise ValueError('invalid_note')
    if type(expected_version) is not int or not 0<=expected_version<=2147483647:raise ValueError('invalid_version')
    now=clock()
    if not isinstance(now,(int,float)) or not math.isfinite(now):raise ValueError('invalid_timestamp')
    with _conn(path,write=True) as c:
        c.execute('BEGIN IMMEDIATE')
        current=c.execute('select * from conversation_notes where key=?',(key,)).fetchone()
        if current and tuple(current[k] for k in ('key','channel','account','scope','conversation_id')) != identity:raise ScopeMismatch('source_changed')
        current_version=current['version'] if current else 0
        if current_version!=expected_version:raise Conflict('note_changed')
        version=current_version+1
        if current:
            c.execute('update conversation_notes set status=?,content=?,version=?,updated_at=?,updated_by=? where key=?', (status,content,version,now,actor,key))
        else:
            c.execute('insert into conversation_notes values(?,?,?,?,?,?,?,?,?,?)',(*identity,status,content,version,now,actor))
        c.execute('insert into note_events(key,version,actor,status,at) values(?,?,?,?,?)',(key,version,actor,status,now))
    return version
