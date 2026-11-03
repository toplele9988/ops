#!/usr/bin/env python3
"""Offline preparation and explicit, backed-up deployment. Never runs on import."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile

from prepare import HERE, NEW, manifest, prepare, verify

SERVICES = ('wechat-receiver.service', 'imgw-console.service', 'imgw-console-lan.service')


def service(action, name):
    return subprocess.run(['systemctl', action, name], check=True, capture_output=True)


def active(name):
    return subprocess.run(['systemctl', 'is-active', '--quiet', name]).returncode == 0


def atomic_copy(source, target, owner=None):
    fd, name = tempfile.mkstemp(prefix='.wechat-private-', dir=target.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(source.read_bytes())
            stream.flush()
            os.fsync(stream.fileno())
        info = target.stat() if target.exists() else owner
        os.chmod(name, info.st_mode & 0o777 if target.exists() else 0o644)
        os.chown(name, info.st_uid, info.st_gid)
        os.replace(name, target)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def restore(backup):
    metadata = json.loads((backup / 'release.json').read_text())
    root = Path(metadata['root'])
    for name in metadata['changed']:
        target = root / name
        # Refuse to overwrite a later release or operator edit.
        current = hashlib.sha256(target.read_bytes()).hexdigest() if target.exists() else None
        if current not in (metadata['candidate'][name], metadata['baseline'].get(name)):
            raise ValueError('rollback_source_drift:' + name)
    for name in metadata['changed']:
        if name in metadata['baseline']:
            atomic_copy(backup / 'code' / name, root / name)
        else:
            (root / name).unlink(missing_ok=True)
    verify(root, 'baseline.sha256')


def install(args):
    smoke = json.loads(args.smoke_result.read_text())
    if smoke.get('failures') != [] or not smoke.get('checked', 0):
        raise ValueError('required_smoke_not_passed')
    candidate = prepare(args.root)
    baseline, expected = manifest('baseline.sha256'), manifest('candidate.sha256')
    changed = [n for n in expected if baseline.get(n) != expected[n]]
    config = json.loads(args.config.read_text())
    state = Path(config['state_file'])
    state = state if state.is_absolute() else args.config.parent / state
    state = state.resolve(strict=True)
    backup = args.backup_root / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    backup.mkdir(parents=True, mode=0o700)
    running = [s for s in SERVICES if active(s)]
    stopped = []
    modified = False
    try:
        for name in running:
            service('stop', name)
            stopped.append(name)
        verify(args.root, 'baseline.sha256')
        if any((args.root / name).exists() for name in NEW):
            raise ValueError('new_module_appeared_after_preparation')
        for name in changed:
            if name in baseline:
                target = backup / 'code' / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(args.root / name, target)
        with sqlite3.connect(state.as_uri() + '?mode=ro', uri=True) as source:
            source.execute('PRAGMA query_only=ON')
            with sqlite3.connect(backup / 'state.sqlite3') as destination:
                source.backup(destination)
        (backup / 'state.sqlite3').chmod(0o600)
        metadata = dict(root=str(args.root), changed=changed, running=running, baseline=baseline, candidate=expected)
        (backup / 'release.json').write_text(json.dumps(metadata, sort_keys=True))
        shutil.copy2(args.smoke_result, backup / 'smoke-result.json')
        owner = (args.root / 'api_groupbot.py').stat()
        modified = True
        for name in changed:
            atomic_copy(candidate / name, args.root / name, owner)
        verify(args.root, 'candidate.sha256')
        # Migration is active, not deferred until the first callback. This CLI
        # borrows only a SQLite connection; it never constructs the send Store.
        env = dict(os.environ, PYTHONPATH=str(args.root), PYTHONDONTWRITEBYTECODE='1')
        subprocess.run([args.python, str(HERE / 'manage.py'), 'migrate', '--config', str(args.config)],
                       check=True, env=env)
        print(json.dumps(dict(backup=str(backup), installed=len(changed), migrated=1)))
    except BaseException:
        if modified:
            restore(backup)
        raise
    finally:
        shutil.rmtree(candidate)
        for name in stopped:
            service('start', name)
    for name in running:
        if not active(name):
            raise RuntimeError('service_restart_failed:' + name)


def rollback(args):
    metadata = json.loads((args.backup / 'release.json').read_text())
    running = [s for s in SERVICES if active(s)]
    stopped = []
    try:
        for name in running:
            service('stop', name)
            stopped.append(name)
        restore(args.backup)
    finally:
        for name in set(stopped) | set(metadata['running']):
            service('start', name)
    # Deliberately preserve additive tables and collected_private messages.
    # Replacing the DB would lose callbacks and change unrelated send state.
    print(json.dumps(dict(code_restored=1, data_preserved=1)))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='action', required=True)
    p = sub.add_parser('install')
    p.add_argument('--root', type=Path, default=Path('/opt/imgw/wechat'))
    p.add_argument('--config', type=Path, default=Path('/var/lib/imgw/wechat/api_config.json'))
    p.add_argument('--backup-root', type=Path, default=Path('/var/lib/imgw/wechat-private-rollouts'))
    p.add_argument('--python', default='/opt/imgw/venv/bin/python')
    p.add_argument('--smoke-result', type=Path, required=True)
    p = sub.add_parser('rollback')
    p.add_argument('--backup', type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    (install if args.action == 'install' else rollback)(args)
