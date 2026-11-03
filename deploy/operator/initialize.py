#!/usr/bin/env python3
"""Explicit private schema initialization; all permissions disabled on install."""
import argparse
import importlib.util
import os
from pathlib import Path
import pwd
import stat
import sys

p=argparse.ArgumentParser()
p.add_argument('--source-root',type=Path,required=True)
p.add_argument('--database',type=Path,required=True)
p.add_argument('--allow-test-user',action='store_true')
a=p.parse_args()
if not a.allow_test_user and pwd.getpwuid(os.geteuid()).pw_name!='imgw':
    raise SystemExit('initialize must run as imgw')
if not a.database.is_absolute() or a.source_root.resolve() in a.database.resolve().parents:
    raise SystemExit('operator database must be outside source root')
if any(x.is_symlink() for x in (a.database,*a.database.absolute().parents)):
    raise SystemExit('symlink database path rejected')
os.umask(0o077)
module_path=a.source_root.resolve()/'console/operator/store.py'
spec=importlib.util.spec_from_file_location('operator_install_store',module_path)
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
Store,DEFAULTS,dumps=module.Store,module.DEFAULTS,module.dumps
store=Store(a.database)
store.initialize()
with store.write() as db:
    db.execute('UPDATE operator_settings SET value=? WHERE id=1',(dumps(DEFAULTS),))
    db.execute("UPDATE operator_actions SET state='dry_run',reason='master_stopped',due_at=NULL,approved_by=NULL WHERE state IN ('ready','scheduled','pending','dry_run')")
    import time
    store.decision(db,time.time(),'installation',dict(transport='fake',enabled=False,dry_run=True))
assert stat.S_IMODE(a.database.stat().st_mode)==0o600
assert a.database.stat().st_uid==os.geteuid()
print('operator initialized: fake; enabled=false; dry_run=true; all levels/types/contacts disabled; mode=0600')
