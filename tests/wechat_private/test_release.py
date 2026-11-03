from argparse import Namespace
import hashlib
import json
from pathlib import Path
import shutil
import sys

import pytest

from conftest import ROOT, SOURCE, module


def test_baseline_candidate_hashes_and_patch_only_target_receive_paths(candidate):
    prepare=module('checked_private_prepare',ROOT/'deploy/wechat-private/prepare.py')
    prepare.verify(SOURCE,'baseline.sha256')
    prepare.verify(candidate,'candidate.sha256')
    patch=(ROOT/'deploy/wechat-private/production.patch').read_text()
    assert 'wechat_outbound_queue' not in patch and 'send_enabled' not in patch
    assert 'from wechat_private import' in patch
    assert 'network' not in patch


def test_install_active_migration_backup_and_rollback_preserve_all_data(env,candidate,monkeypatch):
    sys.path.insert(0,str(ROOT/'deploy/wechat-private'))
    release=module('private_release',ROOT/'deploy/wechat-private/release.py')
    baseline=release.manifest('baseline.sha256')
    root=env.root/'fake-production'
    staged=env.root/'fake-candidate'
    for name in release.manifest('candidate.sha256'):
        target=staged/name;target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(candidate/name,target)
    for name in baseline:
        target=root/name;target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(SOURCE/name,target)
    monkeypatch.setattr(release,'prepare',lambda source:staged)
    live=set(release.SERVICES);actions=[]
    monkeypatch.setattr(release,'active',lambda name:name in live)
    def service(action,name):
        actions.append((action,name))
        if action=='stop':live.discard(name)
        else:live.add(name)
    monkeypatch.setattr(release,'service',service)
    # A fictional unrelated send queue must survive both migration and rollback.
    env.store.db.execute('CREATE TABLE send_queue(id INTEGER,state TEXT)')
    env.store.db.execute("INSERT INTO send_queue VALUES(1,'held')")
    env.store.db.commit()
    config=env.root/'config.json';config.write_text(json.dumps(env.config))
    smoke=env.root/'smoke.json';smoke.write_text('{"checked":108,"failures":[]}')
    calls=[]
    def migrate(command,**kwargs):
        assert command[2]=='migrate'
        import wechat_private
        calls.append(command)
        wechat_private.initialize(env.store)
    monkeypatch.setattr(release.subprocess,'run',migrate)
    args=Namespace(root=root,config=config,backup_root=env.root/'backups',python='fictional-python',smoke_result=smoke)
    release.install(args)
    release.verify(root,'candidate.sha256')
    assert len(calls)==1
    backup,=args.backup_root.iterdir()
    assert (backup/'state.sqlite3').stat().st_mode & 0o777==0o600
    assert env.store.db.execute('SELECT * FROM send_queue').fetchone()['state']=='held'
    # A post-install source record is retained through rollback.
    from test_private import capture,callback
    capture(env,callback())
    release.rollback(Namespace(backup=backup))
    release.verify(root,'baseline.sha256')
    assert not (root/'wechat_private.py').exists()
    assert env.store.db.execute('SELECT count(*) FROM wechat_private_messages').fetchone()[0]==1
    assert env.store.db.execute('SELECT state FROM send_queue').fetchone()[0]=='held'
    assert live==set(release.SERVICES)


def test_failed_migration_restores_source_and_restarts_services(env,candidate,monkeypatch):
    # Source deployment behavior is covered above; exercise the actual exception
    # path using an in-memory service driver and a synthetic source tree.
    sys.path.insert(0,str(ROOT/'deploy/wechat-private'))
    release=module('private_failed_release',ROOT/'deploy/wechat-private/release.py')
    root=env.root/'production';staged=env.root/'candidate'
    for name in release.manifest('candidate.sha256'):
        target=staged/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(candidate/name,target)
    for name in release.manifest('baseline.sha256'):
        target=root/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(SOURCE/name,target)
    monkeypatch.setattr(release,'prepare',lambda source:staged)
    monkeypatch.setattr(release,'active',lambda name:True)
    actions=[]
    monkeypatch.setattr(release,'service',lambda a,n:actions.append((a,n)))
    monkeypatch.setattr(release.subprocess,'run',lambda *a,**kw:(_ for _ in ()).throw(RuntimeError('fictional migration failure')))
    config=env.root/'config.json';config.write_text(json.dumps(env.config))
    smoke=env.root/'smoke.json';smoke.write_text('{"checked":108,"failures":[]}')
    with pytest.raises(RuntimeError):
        release.install(Namespace(root=root,config=config,backup_root=env.root/'backups',python='fictional',smoke_result=smoke))
    release.verify(root,'baseline.sha256')
    assert set(a for a,n in actions)=={'stop','start'}
