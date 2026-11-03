#!/usr/bin/env python3
"""Explicit offline install/rollback; no service, transport or network calls."""
import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import pwd
import shutil
import sqlite3
import subprocess
import sys
import tempfile

from prepare import HERE, checksum, manifest, matches, prepare, variant
from static_release import inventory, publish


def owner(args):
    user = pwd.getpwnam(args.owner)
    if os.geteuid() not in (0, user.pw_uid):
        raise ValueError('run_as_owner_or_root')
    return user.pw_uid, user.pw_gid


def atomic(raw, path, *, mode=0o600, uid=None, gid=None):
    path = Path(path)
    if path.is_symlink():
        raise ValueError('symlink_target')
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix='.dossiers-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temp, mode)
        if uid is not None:
            os.chown(temp, uid, gid)
        os.replace(temp, path)
    finally:
        Path(temp).unlink(missing_ok=True)


def backup_db(source, dest):
    if not source.exists():
        return
    if source.is_symlink():
        raise ValueError('symlink_database')
    with closing(sqlite3.connect(source.resolve().as_uri() + '?mode=ro', uri=True)) as src, closing(sqlite3.connect(dest)) as dst:
        src.execute('PRAGMA query_only=ON')
        src.backup(dst)
    dest.chmod(0o600)


def child(python, arguments, *, path=None, uid=None, gid=None):
    def demote():
        if os.geteuid() == 0 and uid is not None:
            os.setgroups([gid])
            os.setgid(gid)
            os.setuid(uid)
    result = subprocess.run([str(python), '-B', *map(str, arguments)], capture_output=True,
        env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1', **({'PYTHONPATH': str(path)} if path else {})},
        preexec_fn=demote if uid is not None else None)
    if result.returncode:
        # Do not echo arbitrary private DB errors, records, config or paths.
        raise ValueError('offline_step_failed:' + Path(str(arguments[0])).name)
    return result.stdout


def install(args):
    uid, gid = owner(args)
    root, target = args.root.resolve(strict=True), args.state.resolve()
    if target == root or root in target.parents or args.candidate.resolve() == target / 'dossiers.sqlite3':
        raise ValueError('state_must_be_separate')
    if args.backup.exists():
        raise ValueError('backup_must_be_new')
    # Check canonical importer byte-for-byte before invoking it.
    if not matches(HERE, 'importer.sha256'):
        raise ValueError('importer_hash_mismatch')
    candidate, kind, installed = prepare(root)
    try:
        route_report = child(args.python, [HERE / 'check_routes.py', candidate], path=candidate)
        # Plan is read only, even if the state directory is absent.
        plan = json.loads(child(args.python, [HERE / 'importer/import_candidate.py', '--candidate', args.candidate, '--target', target / 'dossiers.sqlite3'], uid=uid, gid=gid))
        if not args.nas_root.is_absolute() or not args.nas_root.is_dir() or not args.media_key.is_file():
            raise ValueError('source_runtime_unavailable')
        frontend = None
        if args.web_dist:
            expected = manifest('dist.sha256')
            if inventory(args.web_dist) != expected:
                raise ValueError('tested_web_build_hash_mismatch')
            if args.web_target.resolve() == args.web_dist.resolve() or any(p.is_symlink() for p in (args.web_target, *args.web_target.parents)):
                raise ValueError('unsafe_web_target')
            frontend = dict(target=str(args.web_target.absolute()), previous=inventory(args.web_target), candidate=expected)
        if target.is_symlink() or any(p.is_symlink() for p in target.parents):
            raise ValueError('unsafe_private_state')
        args.backup.mkdir(parents=True, mode=0o700)
        changed = manifest(kind + '.candidate.sha256')
        previous = {name: checksum(root / name) for name in changed}
        for name, sha in previous.items():
            if sha:
                dest = args.backup / 'code' / name
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(root / name, dest)
        for name in ('dossiers.sqlite3', 'drafts.sqlite3'):
            backup_db(target / name, args.backup / name)
        runtime = target / 'runtime.json'
        if runtime.exists():
            shutil.copyfile(runtime, args.backup / 'runtime.json')
            (args.backup / 'runtime.json').chmod(0o600)
        if frontend and frontend['previous'] is not None:
            shutil.copytree(args.web_target, args.backup / 'frontend')
        record = dict(root=str(root), state=str(target), previous=previous, candidate=changed, kind=kind,
                      runtime_existed=runtime.exists(), owner=args.owner, frontend=frontend, status='prepared')
        atomic(json.dumps(record).encode(), args.backup / 'release.json')
        target.mkdir(parents=True, exist_ok=True, mode=0o700)
        target.chmod(0o700)
        os.chown(target, uid, gid)
        # Owner-only files exist BEFORE SQLite gets a write handle.
        for name in ('dossiers.sqlite3', 'drafts.sqlite3'):
            p = target / name
            if p.is_symlink():
                raise ValueError('unsafe_private_database')
            fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
            os.close(fd)
            p.chmod(0o600)
            os.chown(p, uid, gid)
        # Recheck all source hashes just before mutation.
        assert variant(root) == (kind, installed)
        for name in changed:
            info = (root / name).stat() if (root / name).exists() else (root / 'console/v3.py').stat()
            parent = (root / name).parent
            if not parent.exists():
                parent.mkdir(parents=True, mode=0o755)
                parent.chmod(0o755)
                os.chown(parent, info.st_uid, info.st_gid)
            atomic((candidate / name).read_bytes(), root / name, mode=info.st_mode & 0o777, uid=info.st_uid, gid=info.st_gid)
        config = dict(nas_root=str(args.nas_root.resolve()), wechat_db=str(args.wechat_db.resolve()), blob_root=str(args.blob_root.resolve()), media_key=str(args.media_key.resolve()))
        atomic(json.dumps(config).encode(), runtime, uid=uid, gid=gid)
        # Import and explicit draft migration run as the service owner, never lazily from GET.
        result = json.loads(child(args.python, [HERE / 'importer/import_candidate.py', '--candidate', args.candidate, '--target', target / 'dossiers.sqlite3', '--apply'], uid=uid, gid=gid))
        child(args.python, ['-c', 'from console.dossiers.drafts import migrate; import sys; migrate(sys.argv[1])', target / 'drafts.sqlite3'], path=root, uid=uid, gid=gid)
        for name in ('dossiers.sqlite3', 'drafts.sqlite3', 'runtime.json'):
            p = target / name
            assert p.stat().st_uid == uid and p.stat().st_mode & 0o777 == 0o600
        assert matches(root, kind + '.candidate.sha256')
        if frontend:
            publish(args.web_dist, args.web_target, frontend['candidate'], uid, gid)
        record['status'] = 'installed_no_restart'
        atomic(json.dumps(record).encode(), args.backup / 'release.json')
        atomic(route_report, args.backup / 'routes.json')
        print(json.dumps(dict(installed=True, frontend_published=bool(frontend), restarted=False, variant=kind, imported=result['inserted'], legacy_tables_changed=plan['legacy_tables_changed'])))
    finally:
        shutil.rmtree(candidate, ignore_errors=True)


def rollback(args):
    record = json.loads((args.backup / 'release.json').read_text())
    root = Path(record['root'])
    frontend = record.get('frontend')
    if frontend:
        current = inventory(Path(frontend['target']))
        if current not in (frontend['candidate'], frontend['previous']):
            raise ValueError('rollback_static_drift')
        if frontend['previous'] is not None and inventory(args.backup / 'frontend') != frontend['previous']:
            raise ValueError('backup_static_hash_mismatch')
    # Validate the ENTIRE restore before changing any source file.
    for name, expected in record['candidate'].items():
        if checksum(root / name) not in (expected, record['previous'][name]):
            raise ValueError('rollback_source_drift')
    for name, sha in record['previous'].items():
        if sha is not None and checksum(args.backup / 'code' / name) != sha:
            raise ValueError('backup_hash_mismatch')
    for name, sha in record['previous'].items():
        p = root / name
        if sha is None:
            p.unlink(missing_ok=True)
        else:
            info = p.stat()
            atomic((args.backup / 'code' / name).read_bytes(), p, mode=info.st_mode & 0o777, uid=info.st_uid, gid=info.st_gid)
    if frontend:
        target = Path(frontend['target'])
        if frontend['previous'] is None:
            if target.exists():
                shutil.rmtree(target)
        else:
            service_user = pwd.getpwnam(record['owner'])
            publish(args.backup / 'frontend', target, frontend['previous'], service_user.pw_uid, service_user.pw_gid)
    # Keep imported dossiers and all newly written drafts for traceability.
    # Runtime config stays private alongside retained state for later recovery.
    print(json.dumps(dict(rolled_back=True, restarted=False, private_state_retained=True)))


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest='action', required=True)
    a = sub.add_parser('install')
    a.add_argument('--root', type=Path, default=Path('/opt/imgw/wechat'))
    a.add_argument('--state', type=Path, default=Path('/var/lib/imgw/ops-app/dossiers'))
    a.add_argument('--candidate', type=Path, required=True)
    a.add_argument('--backup', type=Path, required=True)
    a.add_argument('--nas-root', type=Path, required=True)
    a.add_argument('--wechat-db', type=Path, default=Path('/var/lib/imgw/wechat/api_state.sqlite3'))
    a.add_argument('--blob-root', type=Path, default=Path('/var/lib/imgw/wechat/owner_workspace/media_archive'))
    a.add_argument('--media-key', type=Path, default=Path('/var/lib/imgw/ops-app/media.key'))
    a.add_argument('--owner', default='imgw')
    a.add_argument('--python', type=Path, default=Path('/opt/imgw/venv/bin/python'))
    a.add_argument('--web-dist', type=Path, help='Publish this exact tested static build; omitted means backend only')
    a.add_argument('--web-target', type=Path, default=Path('/var/lib/imgw/ops-app/dist'))
    b = sub.add_parser('rollback')
    b.add_argument('--backup', type=Path, required=True)
    args = p.parse_args()
    try:
        old = os.umask(0o077)
        try:
            (install if args.action == 'install' else rollback)(args)
        finally:
            os.umask(old)
    except Exception as error:
        # Error class only: paths/data from sqlite and parser exceptions stay private.
        print('dossier_release_failed:' + type(error).__name__ + '; inspect private backup and follow INTEGRATION.md', file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == '__main__':
    main()
