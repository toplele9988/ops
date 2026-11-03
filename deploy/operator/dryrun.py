#!/usr/bin/env python3
"""One real read-only backlog replay into a disposable *private* fake ledger.

Only counts and redacted examples survive in private_out/operator-dryrun.
Network, subprocess/model execution and non-ledger SQLite writes are forbidden.
No production library/store is initialized or migrated.
"""
import argparse
from contextlib import contextmanager
from contextlib import closing
from datetime import datetime,timezone
import json
import logging
import os
import stat
from pathlib import Path
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile

p=argparse.ArgumentParser()
p.add_argument('--source-root',type=Path,default=Path(__file__).resolve().parents[2]/'src')
p.add_argument('--fallback',type=Path,default=Path('/opt/imgw/wechat'))
p.add_argument('--output',type=Path,default=Path('private_out/operator-dryrun'))
a=p.parse_args()
os.umask(0o077)
sys.dont_write_bytecode=True
sys.path[:0]=[str(a.source_root.resolve()),str(a.fallback.resolve())]
# Workspace is a partial console package. Keep its new modules first.
import console
console.__path__.append(str(a.fallback/'console'))
def forbidden(*args,**kwargs):raise RuntimeError('real rehearsal forbids external execution')
socket.socket.connect=forbidden
socket.create_connection=forbidden
subprocess.run=forbidden
from console.operator.host import assemble
from console.operator.sources import readonly
from console.archive.store import Store as ArchiveStore
from console.kb import Hub
from console.operator.store import Store,SCHEMA,DEFAULTS,dumps
logging.basicConfig(level=logging.ERROR,format='%(levelname)s %(message)s')


def rehearsal_authorization():
    # Read-only managed sandboxes project host root as a nonzero UID. Compare
    # against the projected / owner, while production keeps _bridge_identity.
    path=Path('/etc/imgw/whatsapp-bridge.json')
    root_uid=Path('/').stat().st_uid
    for parent in path.parents:
        info=parent.lstat()
        if stat.S_ISLNK(info.st_mode) or info.st_uid!=root_uid or stat.S_IMODE(info.st_mode)&0o022:
            raise ValueError('rehearsal_config_parent_invalid')
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
    with os.fdopen(fd) as stream:
        info=os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid!=root_uid or stat.S_IMODE(info.st_mode)!=0o640 or info.st_size>65536:
            raise ValueError('rehearsal_config_invalid')
        config=json.load(stream)
    values=tuple(config.get(k) for k in ('session','owner'))
    if not all(isinstance(x,str) and x for x in values):raise ValueError('rehearsal_binding_invalid')
    return values


class VolatileLedger(Store):
    """Same schema and engine transactions, in memory for this read-only replay.

    Durable cursor/restart behavior is tested separately against real temporary
    SQLite files. No real chat/identity is written even to a scratch ledger.
    """
    def __init__(self,path):
        super().__init__(path)
        self.db=sqlite3.connect(':memory:')
        self.db.row_factory=sqlite3.Row
        for sql in SCHEMA:self.db.execute(sql)
        self.db.execute('INSERT INTO operator_settings VALUES(1,?)',(dumps(DEFAULTS),))
        self.db.commit()

    @contextmanager
    def read(self):
        yield self.db

    @contextmanager
    def write(self):
        self.db.execute('BEGIN IMMEDIATE')
        with self.db:yield self.db

with tempfile.TemporaryDirectory(prefix='operator-private-dryrun-') as temporary:
    private=Path(temporary)
    original_connect=sqlite3.connect
    def connect(path,*args,**kwargs):
        raw=str(path)
        if raw!=':memory:' and not raw.startswith(str(private)) and not (kwargs.get('uri') and 'mode=ro' in raw):
            raise RuntimeError('non-ledger write forbidden')
        return original_connect(path,*args,**kwargs)
    sqlite3.connect=connect
    operator,worker=assemble(operator_path=private/'operator.sqlite3',hub=Hub(embedder=None))
    for source in worker.sources:
        if source.kind=='whatsapp':source.authorize=rehearsal_authorization
    operator.store=VolatileLedger(private/'operator.sqlite3')
    operator.store.initialize()
    # Hold archive identity/deal metadata at one read-only snapshot throughout.
    with readonly(operator.source.archive.path) as archive:
        operator.source.archive=ArchiveStore(connection=archive,target_version=9)
        context_counts={t:archive.execute('SELECT count(*) FROM '+t).fetchone()[0] for t in ('identities','deals')}
        worker.batch=1000000
        observed={}
        for source in worker.sources:
            observed[source.name]=dict(present=source.path.exists())
            if source.path.exists():
                try:
                    snapshot=source.scan()
                    observed[source.name].update(rows=0 if snapshot is None else len(snapshot['rows']),not_installed=snapshot is None)
                except Exception as error:
                    observed[source.name]['inspection_error']=type(error).__name__
            result=worker._run('source.'+source.name,lambda s=source:worker.ingest(s))
            observed[source.name]['admitted']=result
            print(json.dumps(dict(source=source.name,admitted=result)),flush=True)
        gaps=worker._run('dossiers',worker.gaps.poll)
        archive_admitted=worker._run('archive',worker.archive_events.poll)
        worker._run('tick',operator.tick)
    with operator.store.read() as db:
        counts={str(r[0]):r[1] for r in db.execute("SELECT level,count(*) FROM operator_actions WHERE state='dry_run' GROUP BY level")}
        states={r[0]:r[1] for r in db.execute('SELECT state,count(*) FROM operator_actions GROUP BY state')}
        failures=[dict(r) for r in db.execute('SELECT component,error_type,failures FROM operator_health WHERE ok=0')]
        events=db.execute('SELECT count(*) FROM operator_events').fetchone()[0]
        attempts=db.execute('SELECT count(*) FROM operator_attempts').fetchone()[0]
        samples=[dict(kind=r['kind'],level=r['level'],state=r['state'],object='对象已脱敏',text='正文已脱敏；请在认证运营台核对原文与来源') for r in db.execute("SELECT kind,level,state FROM operator_actions WHERE level=1 ORDER BY id LIMIT 3")]
        cursors={r['name']:r['position'] for r in db.execute('SELECT name,position FROM operator_cursors')}
    assert not operator.bridge.calls and not operator.bridge.commands and attempts==0
    report=dict(at=datetime.now(timezone.utc).isoformat(),mode='fake/dry_run/master_off',
                sources=observed,cursors=cursors,events=events,action_states=states,
                level1_rehearsal_proposals=int(counts.get('1',0)),level_counts=counts,
                dossier_present=worker.gaps.path.exists(),new_dossier_proposals=gaps,
                archive_wakeups=archive_admitted,context_counts=context_counts,
                errors=failures,real_sends=0,model_execution='forbidden; unavailable context becomes owner review',
                ledger='volatile memory; cursor durability verified by isolated disk tests',
                authorization='read-only root-UID projection check; production validator unchanged',
                examples=samples)
    if not samples:
        report['examples']=[dict(kind='ask_owner',object='未绑定对象（已脱敏）',why='需要确认身份和案子；未猜测供应商',draft='核对归属后再生成第 1 级草稿')]
    a.output.mkdir(parents=True,exist_ok=True,mode=0o700)
    (a.output/'summary.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    (a.output/'summary.json').chmod(0o600)
    print(json.dumps(dict(level1_rehearsal_proposals=report['level1_rehearsal_proposals'],events=events,real_sends=0,errors=len(failures))),flush=True)
    operator.store.db.close()
