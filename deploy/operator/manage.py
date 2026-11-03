#!/usr/bin/env python3
"""Stage/build/check first, exact-hash backup/install, guarded source rollback.

Does not start/stop services. Invoke only after the coordinator stops console
workers. Store and delivery history survive both installation and rollback.
"""
import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import pwd
import shutil
import sqlite3
import subprocess
import tempfile
import time

from prepare import HERE,digest,locate,manifest,prepare,verify


def private_path(path):
    path=Path(path).absolute()
    if any(x.is_symlink() for x in (path,*path.parents)):
        raise ValueError('symlink_target_rejected')
    return path


def run_imgw(python,args):
    command=[str(python),*map(str,args)]
    if os.geteuid()==0:
        command=['runuser','-u','imgw','--',*command]
    elif pwd.getpwuid(os.geteuid()).pw_name!='imgw':
        raise ValueError('installer_requires_imgw_or_root')
    subprocess.run(command,check=True,env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'})


def file_hashes(directory):
    return {str(p.relative_to(directory)):digest(p) for p in directory.rglob('*') if p.is_file()}


def copy_atomic(source,target):
    missing=[]
    parent=target.parent
    while not parent.exists():
        missing.append(parent);parent=parent.parent
    target.parent.mkdir(parents=True,exist_ok=True)
    for parent in missing:
        parent.chmod(0o750)
        if os.geteuid()==0:
            account=pwd.getpwnam('imgw');os.chown(parent,account.pw_uid,account.pw_gid)
    fd,name=tempfile.mkstemp(prefix='.operator-install-',dir=target.parent)
    try:
        with os.fdopen(fd,'wb') as out,source.open('rb') as incoming:shutil.copyfileobj(incoming,out)
        os.chmod(name,0o640)
        if os.geteuid()==0:
            account=pwd.getpwnam('imgw');os.chown(name,account.pw_uid,account.pw_gid)
        os.replace(name,target)
    finally:
        if os.path.exists(name):os.unlink(name)


def install(a):
    backend,web=private_path(a.source_root),private_path(a.web_root)
    backup,database,dist=private_path(a.backup),private_path(a.database),private_path(a.dist)
    for private in (backup,database,dist):
        if any(private==root or root in private.parents for root in (backend,web)):
            raise ValueError('private_or_dist_target_inside_source')
    if backup.exists():raise ValueError('backup_already_exists')
    if database.name!='operator.sqlite3':raise ValueError('dedicated_operator_database_required')
    replacement=dist.with_name(dist.name+'.operator-new')
    if replacement.exists():raise ValueError('dist_staging_collision')
    os.umask(0o077)
    with tempfile.TemporaryDirectory(prefix='operator-install-') as temporary:
        stage=Path(temporary)/'candidate'
        prerequisite=prepare(backend,web,stage)
        subprocess.run([str(a.python),str(HERE/'check_routes.py'),str(stage),'--fallback',str(backend)],check=True)
        deps=web/'node_modules'
        if not deps.is_dir():raise ValueError('existing_web_dependencies_required')
        (stage/'web/node_modules').symlink_to(deps,target_is_directory=True)
        for tool,args in [('typescript/bin/tsc',['--noEmit']),('vite/bin/vite.js',['build'])]:
            subprocess.run(['node',str(deps/tool),*args],cwd=stage/'web',check=True)
        # Recheck immediately before backup/write; never silently rebase a peer patch.
        verify(backend,web)
        names=list(manifest('baseline.sha256'))+list(manifest('payload.sha256'))
        for name in names:private_path(locate(name,backend,web))
        for name in manifest('payload.sha256'):
            if locate(name,backend,web).exists():raise ValueError('new_file_collision:'+name)
        backup.mkdir(parents=True,mode=0o700)
        previous=[]
        for name in names:
            source=locate(name,backend,web)
            if source.exists():
                target=backup/'source'/name;target.parent.mkdir(parents=True,exist_ok=True)
                shutil.copy2(source,target);previous.append(name)
        if dist.exists():shutil.copytree(dist,backup/'dist',symlinks=False)
        if database.exists():
            with closing(sqlite3.connect(database.resolve().as_uri()+'?mode=ro',uri=True)) as src,closing(sqlite3.connect(backup/'operator-before.sqlite3')) as dst:src.backup(dst)
        state=dict(backend=str(backend),web=str(web),dist=str(dist),database=str(database),
                   previous=previous,names=names,dist_existed=dist.exists(),wechat_private=prerequisite,
                   installed={**manifest('candidate.sha256'),**manifest('payload.sha256')},
                   installed_dist=file_hashes(stage/'web/dist'),at=time.time())
        (backup/'state.json').write_text(json.dumps(state,indent=2)+'\n')
        # Initialization is explicit and precedes any host restart. Fresh defaults
        # and old pending grants are disabled; history is never cleared.
        database.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        if os.geteuid()==0:
            account=pwd.getpwnam('imgw');os.chown(database.parent,account.pw_uid,account.pw_gid)
            if database.exists():os.chown(database,account.pw_uid,account.pw_gid)
        os.chmod(database.parent,0o700)
        for name in names:copy_atomic(stage/name,locate(name,backend,web))
        verify(backend,web,'candidate.sha256');verify(backend,web,'payload.sha256')
        subprocess.run([str(a.python),str(HERE/'check_routes.py'),str(backend),'--fallback',str(backend),'--web-root',str(web)],check=True)
        run_imgw(a.python,[HERE/'initialize.py','--source-root',backend,'--database',database])
        shutil.copytree(stage/'web/dist',replacement)
        if os.geteuid()==0:
            account=pwd.getpwnam('imgw')
            for p in (replacement,*replacement.rglob('*')):os.chown(p,account.pw_uid,account.pw_gid)
        for p in replacement.rglob('*'):
            if p.is_file():p.chmod(0o640)
            elif p.is_dir():p.chmod(0o750)
        replacement.chmod(0o750)
        if dist.exists():shutil.rmtree(dist)
        replacement.rename(dist)
        (backup/'installed.ok').write_text('installed; coordinator must verify and restart\n')
        print('installed with private backup, active initialization and verified routes; no service restart performed')


def rollback(a):
    backup=private_path(a.backup)
    state=json.loads((backup/'state.json').read_text())
    backend,web,dist,database=(private_path(state[k]) for k in ('backend','web','dist','database'))
    # Stop the worker externally first; retain history and unknown attempts.
    run_imgw(a.python,[HERE/'initialize.py','--source-root',HERE/'payload','--database',database])
    for name,expected in state['installed'].items():
        target=locate(name,backend,web)
        original=backup/'source'/name
        accepted={expected}
        if original.exists():accepted.add(digest(original))
        if target.exists() and digest(target) not in accepted:
            raise ValueError('rollback_would_overwrite_later_change:'+name)
    if (backup/'installed.ok').exists() and file_hashes(dist)!=state['installed_dist']:
        raise ValueError('rollback_would_overwrite_later_frontend')
    previous=set(state['previous'])
    for name in state['names']:
        target=locate(name,backend,web)
        if name in previous:
            shutil.copy2(backup/'source'/name,target)
        elif target.exists():target.unlink()
    if dist.exists():shutil.rmtree(dist)
    if state['dist_existed']:shutil.copytree(backup/'dist',dist)
    (backup/'rolled-back.ok').write_text('ledger retained; coordinator must restart\n')
    print('source/UI restored; operator ledger retained and disabled; no service restart performed')


if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('action',choices=['install','rollback'])
    p.add_argument('--source-root',type=Path)
    p.add_argument('--web-root',type=Path)
    p.add_argument('--backup',type=Path,required=True)
    p.add_argument('--database',type=Path)
    p.add_argument('--dist',type=Path)
    p.add_argument('--python',type=Path,required=True)
    a=p.parse_args()
    (install if a.action=='install' else rollback)(a)
