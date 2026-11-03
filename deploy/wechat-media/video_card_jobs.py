"""Durable authenticated card -> downloader task -> NAS hash association.

A submission is never replayed after an uncertain network outcome. Read-only
status polling is retryable. No synthetic callbacks, provider messages or likes.
"""
import contextlib
import json
from pathlib import PureWindowsPath
import sqlite3
import threading
import time
from . import external_context as ec
from .assistant_policy import recipients,silent,authority
from .platform_comments import card
from .win_download_client import call_tool,document

DOWNLOAD_DIR=r'Z:\WeChatdownloads'

class State:
    def __init__(self,path):
        self.db=sqlite3.connect(path,timeout=10);self.db.row_factory=sqlite3.Row
        self.lock=threading.RLock()
    @contextlib.contextmanager
    def transaction(self):
        with self.lock,self.db:yield self.db
    def close(self):self.db.close()

def initialize(store):
    with store.lock:
        exists=store.db.execute("SELECT 1 FROM sqlite_master WHERE name='video_card_jobs'").fetchone()
        if exists and 'receipt_filename' not in {r[1] for r in store.db.execute('PRAGMA table_info(video_card_jobs)')}:
            store.db.execute('ALTER TABLE video_card_jobs ADD COLUMN receipt_filename TEXT')
        store.db.executescript('''CREATE TABLE IF NOT EXISTS video_card_jobs(
          job_id TEXT PRIMARY KEY,account_sha256 TEXT NOT NULL,source_scope TEXT NOT NULL,
          object_id TEXT NOT NULL CHECK(typeof(object_id)='text'),person TEXT NOT NULL,
          target_message_id TEXT NOT NULL,target_callback_sha256 TEXT NOT NULL,
          filename TEXT NOT NULL UNIQUE,state TEXT NOT NULL,task_id INTEGER,
          source_sha256 TEXT,source_bytes INTEGER,created_at REAL NOT NULL,
          updated_at REAL NOT NULL,not_before REAL NOT NULL DEFAULT 0,
          error_code TEXT,downloaded_at REAL,indexed_at REAL,receipt_filename TEXT,
          UNIQUE(account_sha256,source_scope,object_id));
          CREATE TRIGGER IF NOT EXISTS vcj_pin BEFORE UPDATE ON video_card_jobs
          WHEN NEW.job_id!=OLD.job_id OR NEW.account_sha256!=OLD.account_sha256
          OR NEW.source_scope!=OLD.source_scope OR NEW.object_id!=OLD.object_id
          OR NEW.person!=OLD.person OR NEW.target_message_id!=OLD.target_message_id
          OR NEW.target_callback_sha256!=OLD.target_callback_sha256
          OR NEW.filename!=OLD.filename OR NEW.created_at!=OLD.created_at
          OR (OLD.task_id IS NOT NULL AND NEW.task_id IS NOT OLD.task_id)
          OR (OLD.source_sha256 IS NOT NULL AND NEW.source_sha256 IS NOT OLD.source_sha256)
          BEGIN SELECT RAISE(ABORT,'immutable_card_pin'); END;
          CREATE TRIGGER IF NOT EXISTS vcj_receipt_pin BEFORE UPDATE ON video_card_jobs
          WHEN OLD.receipt_filename IS NOT NULL AND NEW.receipt_filename IS NOT OLD.receipt_filename
          BEGIN SELECT RAISE(ABORT,'immutable_download_receipt'); END;
          CREATE TRIGGER IF NOT EXISTS vcj_no_delete BEFORE DELETE ON video_card_jobs
          BEGIN SELECT RAISE(ABORT,'immutable_card_job'); END;''')
        store.db.commit()

def identity(config,payload,policy,now):
    if config.get('realtime_video_cards') is not True or not silent(config):return None
    origin=ec._text(payload.get('Data',{}).get('FromUserName'))
    if origin not in recipients(config):return None
    authority(config,policy,origin,now=now)
    stamp=payload.get('Data',{}).get('CreateTime')
    if type(stamp) not in (int,float) or not policy['active_after']<=stamp<=now+60:return None
    ident=ec.envelope(config,payload);sample=card(payload)
    return (ident,sample,origin) if ident and sample else None

def enqueue(store,config,payload,now,*,policy=None):
    from .assistant_policy import read_policy
    if config.get('realtime_video_cards') is not True:return 'disabled'
    parsed=identity(config,payload,policy or read_policy(),now)
    if not parsed:return 'ignored'
    ident,sample,person=parsed;initialize(store)
    key=ec._sha(ec._json([ident['account_sha256'],ident['source_scope'],sample[0]]))
    with store.transaction() as db:
        witness=db.execute('SELECT payload_json,received_at FROM received_callback_archive WHERE account_sha256=? AND callback_sha256=? AND source_scope=? AND channel_kind=?',
            (ident['account_sha256'],ident['callback_sha256'],ident['source_scope'],'private')).fetchone()
        if not witness or ec._sha(witness[0])!=ident['callback_sha256']:raise ValueError('card_witness_missing')
        cur=db.execute('''INSERT OR IGNORE INTO video_card_jobs
          (job_id,account_sha256,source_scope,object_id,person,target_message_id,target_callback_sha256,filename,state,created_at,updated_at)
          VALUES(?,?,?,?,?,?,?,?,'queued',?,?)''',
          (key,ident['account_sha256'],ident['source_scope'],sample[0],person,ident['message_id'],ident['callback_sha256'],'wxcard_'+key+'.mp4',witness[1],now))
    return 'queued' if cur.rowcount else 'duplicate'

def source(store,config,policy,job,now):
    authority(config,policy,job['person'],now=now)
    with store.lock:
        row=store.db.execute('SELECT payload_json FROM received_callback_archive WHERE account_sha256=? AND source_scope=? AND callback_sha256=?',
            (job['account_sha256'],job['source_scope'],job['target_callback_sha256'])).fetchone()
    if not row or ec._sha(row[0])!=job['target_callback_sha256']:raise ValueError('card_source_missing')
    parsed=identity(config,json.loads(row[0]),policy,now)
    if not parsed:raise ValueError('card_authority_changed')
    ident,sample,person=parsed
    if (person!=job['person'] or sample[0]!=job['object_id'] or ident['message_id']!=job['target_message_id']
        or ident['source_scope']!=job['source_scope']):raise ValueError('card_source_changed')
    return sample

def task_id(doc):
    # Only an explicit task identity is acceptable. No guess from list order.
    for value in (doc.get('id'),doc.get('task_id'),doc.get('task',{}).get('id') if isinstance(doc.get('task'),dict) else None):
        if type(value) is int and value>0:return value
    raise ValueError('download_receipt_unknown')

def verify_detail(doc,job):
    if doc.get('id')!=job['task_id'] or doc.get('content_id')!='wxchannels:'+job['object_id']:
        raise ValueError('download_identity_mismatch')
    status=doc.get('status')
    if type(status) is not int or status not in range(8):raise ValueError('download_status_invalid')
    if status!=5:return status,None
    files=doc.get('files')
    if not isinstance(files,list) or len(files)!=1:raise ValueError('download_files_ambiguous')
    f=files[0];size=f.get('size')
    # This downloader appends a variant suffix even to a supplied filename.
    # Both accepted shapes are fixed derivatives of the unique requested name.
    name=f.get('name');allowed=(job['filename'],job['filename']+'_.mp4')
    expected=PureWindowsPath(DOWNLOAD_DIR)/(name if isinstance(name,str) else '')
    if (name not in allowed or (job.get('receipt_filename') and name!=job['receipt_filename']) or PureWindowsPath(f.get('file_path',''))!=expected
        or PureWindowsPath(f.get('download_dir',''))!=PureWindowsPath(DOWNLOAD_DIR)
        or f.get('status')!='finished' or f.get('exists') is False
        or type(size) is not int or not 0<size<=1024**3 or f.get('downloaded')!=size):
        raise ValueError('download_file_mismatch')
    return status,size


# Only fixed source/receipt codes can leave this worker. Raw parser/RPC errors
# can contain transport data and must never become error_code or log text.
_FAILURE_CODES = frozenset((
    'assistant_person_unauthorized', 'card_source_missing', 'card_authority_changed',
    'card_source_changed', 'download_identity_mismatch', 'download_status_invalid',
    'download_files_ambiguous', 'download_file_mismatch', 'nas_source_ambiguous',
    'catalog_schema_version_unsupported', 'video_annotation_unprivileged_required',
    'video_annotation_catalog_not_private', 'video_annotation_catalog_changed',
))


def failure_code(error, phase):
    value = str(error)
    if value in _FAILURE_CODES:
        return value
    return {'source': 'source_validation_failed', 'poll': 'download_receipt_invalid',
            'nas': 'nas_receipt_invalid', 'index': 'catalog_validation_failed'}[phase]


def tick(store,config,policy,*,rpc=call_tool,now=time.time):
    if config.get('realtime_video_cards') is not True or not silent(config):return {'status':'disabled'}
    initialize(store);stamp=now()
    with store.transaction() as db:
        job=db.execute("SELECT * FROM video_card_jobs WHERE state IN ('queued','downloading','downloaded','archive_verified') AND not_before<=? ORDER BY updated_at LIMIT 1",(stamp,)).fetchone()
        if not job:return {'status':'idle'}
        job=dict(job)
        if job['state']=='queued':db.execute("UPDATE video_card_jobs SET state='submitting',updated_at=? WHERE job_id=?",(stamp,job['job_id']))
    state=job['state'];code=None;updates={};submission_attempted=False;phase='source'
    try:
        oid,nid=source(store,config,policy,job,stamp)
        if state=='queued':
            # Existing-action=error and a deterministic private filename prohibit
            # overwrite. A crash after this call remains uncertain, never retried.
            submission_attempted=True
            result=document(rpc('download_wxchannels_video',{'oid':oid,'nid':nid,
                'download_dir':DOWNLOAD_DIR,'filename':job['filename'],'existing_action':'error'}))
            updates['task_id']=task_id(result);state='downloading'
        elif state=='downloading':
            phase='poll'
            doc=document(rpc('get_download_task_detail',{'id':job['task_id']}))
            status,size=verify_detail(doc,job)
            if status==5:state='downloaded';updates.update(source_bytes=size,downloaded_at=now(),receipt_filename=doc['files'][0]['name'])
            elif status in (3,6,7):state='download_failed';code='download_paused_or_failed'
        elif state=='downloaded':
            phase='nas'
            # Watcher validates MP4, mount, size and immutable archive SHA. The
            # download receipt pins this exact filename and length to the oid.
            with store.lock:
                rows=store.db.execute('SELECT DISTINCT source_sha256 FROM vk_nas_sources WHERE filename=? AND source_bytes=? AND observed_at>=?',
                    (job.get('receipt_filename') or job['filename'],job['source_bytes'],job['created_at'])).fetchall()
            if len(rows)>1:raise ValueError('nas_source_ambiguous')
            if rows:updates['source_sha256']=rows[0][0];state='archive_verified'
        else:
            phase='index'
            from retrieval.catalog_reader import CatalogReader
            pairs=authority(config,policy,job['person'],now=stamp)
            with CatalogReader(policy['catalog']) as reader:
                rows=reader.connection.execute('''SELECT a.owner,a.source_scope FROM multimedia_assets a
                    WHERE a.source_sha256=? AND EXISTS(SELECT 1 FROM knowledge_chunks c
                    WHERE c.asset_id=a.asset_id AND c.owner=a.owner AND c.source_scope=a.source_scope)''',
                    (job['source_sha256'],)).fetchall()
                if any(tuple(r) in pairs for r in rows):state='indexed';updates['indexed_at']=now()
                reader.unchanged()
    except Exception as exc:
        if state=='queued' and submission_attempted:
            state='download_unknown';code='download_outcome_unknown'
        elif state=='queued' or isinstance(exc,ValueError):
            state='blocked';code=failure_code(exc,phase)
        else:code='status_temporarily_unavailable'
    updates.update(state=state,updated_at=now(),not_before=now()+(15 if state=='archive_verified' else 3),error_code=code)
    with store.transaction() as db:
        db.execute('UPDATE video_card_jobs SET '+','.join(k+'=?' for k in updates)+' WHERE job_id=?',(*updates.values(),job['job_id']))
    if state=='indexed':
        # Enqueue only: domestic synthesis has its own worker and cannot delay
        # downloads. Missing/revoked owner receipt grant remains silent.
        try:
            from .owner_digest import discover
            discover(store,config,policy,now())
        except Exception:pass
    return {'status':state,'job_id':job['job_id'],'error_code':code,'api_send_attempted':False}

def backfill(store,config,policy,now):
    # Only retained, authorized real cards since the current grant, bounded to
    # one day; originals are not reinjected into AddMsg or outbound handlers.
    with store.lock:
        rows=store.db.execute("SELECT payload_json FROM received_callback_archive WHERE channel_kind='private' AND message_type=49 AND received_at>=? ORDER BY received_at LIMIT 200",(max(policy['active_after'],now-86400),)).fetchall()
    return [enqueue(store,config,json.loads(r[0]),now,policy=policy) for r in rows]

def main():
    import fcntl,os
    from pathlib import Path
    from .assistant_policy import read_policy
    os.umask(0o077)
    lease=open('/var/lib/imgw/wechat/video-cards.lock','a')
    fcntl.flock(lease,fcntl.LOCK_EX|fcntl.LOCK_NB)
    store=State('/var/lib/imgw/wechat/api_state.sqlite3');initialize(store)
    with store.transaction() as db:
        db.execute("UPDATE video_card_jobs SET state='download_unknown',error_code='interrupted_submission' WHERE state='submitting'")
    while True:
        try:
            config=json.loads(Path('/var/lib/imgw/wechat/api_config.json').read_text());policy=read_policy()
            backfill(store,config,policy,time.time())
            result=tick(store,config,policy)
        except Exception:result={'status':'card_lane_deferred','api_send_attempted':False}
        if result['status'] not in ('idle','disabled'):print(json.dumps(result),flush=True)
        time.sleep(2)

if __name__=='__main__':main()
