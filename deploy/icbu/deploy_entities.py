#!/usr/bin/env python3
"""Verified install/rollback. Default is read-only dry-run; no HTTP or network changes."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import subprocess
import tempfile
import time

ROOT = Path(__file__).resolve().parent
BUNDLE = ROOT / 'entities'
SCRIPTS = Path('/opt/imgw/scripts')
DATA = Path('/var/lib/imgw/icbu')
SERVICE = 'icbu-inbound.service'


class DeployError(Exception):
    pass


def require(ok, code):
    if not ok:
        raise DeployError(code)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def regular(path):
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, 'unsafe_source_file')
    return path.read_bytes()


def command(argv, *, input=None):
    result = subprocess.run(argv, input=input, capture_output=True, timeout=120, check=False)
    require(result.returncode == 0, 'command_failed_' + Path(argv[0]).name)
    return result.stdout


def unit_snapshot():
    # Configuration contains private paths; capture it only for the private backup.
    return command(['systemctl', 'cat', SERVICE])


def manifest(bundle=BUNDLE):
    result = json.loads(regular(bundle / 'manifest.json'))
    for name, expected in result['candidate'].items():
        require(digest(regular(bundle / name)) == expected, 'candidate_sha_mismatch')
    require(digest(regular(bundle.parent / 'receiver-entities.patch')) == result['patch_sha256'], 'patch_sha_mismatch')
    return result


def baseline(scripts, info, units):
    for name, expected in info['baseline'].items():
        require(digest(regular(scripts / name)) == expected, 'baseline_sha_mismatch')
    require(not (scripts / 'icbu_entities.py').exists() and not (scripts / 'icbu_entities.py').is_symlink(),
            'entity_module_already_present')
    require(digest(units) == info['systemd_cat_sha256'], 'systemd_baseline_mismatch')


def smoke_evidence(path, info):
    require(path is not None, 'full_smoke_evidence_required')
    value = json.loads(regular(path))
    require(value.get('command') == 'scripts/e2e_smoke.sh' and type(value.get('exit_code')) is int
            and value['exit_code'] == 0 and value.get('candidate_sha256') == info['candidate']['icbu_inbound_receiver.py']
            and value.get('baseline_sha256') == info['baseline']['icbu_inbound_receiver.py'], 'invalid_smoke_evidence')
    completed = datetime.fromisoformat(value['completed_at'].replace('Z', '+00:00'))
    require(completed.tzinfo is not None and 0 <= (datetime.now(timezone.utc) - completed).total_seconds() < 86400,
            'stale_smoke_evidence')


def atomic(path, raw, *, mode, uid, gid):
    fd, temporary = tempfile.mkstemp(prefix='.icbu-entities-', dir=path.parent)
    try:
        os.fchmod(fd, mode)
        os.fchown(fd, uid, gid)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def private_backup_root(root):
    require(root.is_absolute() and 'private_out' in root.parts and '..' not in root.parts,
            'backup_must_be_inside_workspace_private_out')
    require(not any(p.is_symlink() for p in (root, *root.parents)), 'unsafe_backup_path')
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    require(stat.S_IMODE(root.stat().st_mode) == 0o700 and root.stat().st_uid == os.geteuid(), 'unsafe_backup_permissions')


def backup_database(source, destination):
    if not source.exists():
        require(not source.is_symlink(), 'unsafe_database_path')
        return False
    require(not source.is_symlink() and source.stat().st_nlink == 1, 'unsafe_database_path')
    with sqlite3.connect(source.absolute().as_uri() + '?mode=ro', uri=True, timeout=10) as origin:
        origin.execute('PRAGMA query_only=ON')
        target = sqlite3.connect(destination)
        try:
            deadline = time.monotonic() + 60
            def progress(status, remaining, total):
                require(time.monotonic() < deadline, 'backup_timeout')
            origin.backup(target, pages=256, sleep=0.05, progress=progress)
            require(target.execute('PRAGMA quick_check').fetchone()[0] == 'ok', 'backup_check_failed')
        finally:
            target.close()
    destination.chmod(0o600)
    return True


def database_check(bundle, data, *, initialize=False):
    # Run as the service owner. The module reads back with mode=ro and query_only;
    # no token is loaded and no request is sent to 8791 (or any other port).
    argv = ['runuser', '-u', 'imgw', '--', '/usr/bin/python3', '-B', '-', '--db', str(data / 'entities.sqlite3')]
    if initialize:
        argv.append('--initialize')
    result = json.loads(command(argv, input=regular(bundle / 'icbu_entities.py')))
    require(result.get('ok') is True, 'entity_readback_failed')
    return result


def restart():
    command(['systemctl', 'restart', SERVICE])
    command(['systemctl', 'is-active', '--quiet', SERVICE])


def save_record(backup, value):
    raw = json.dumps(value, sort_keys=True, indent=2).encode() + b'\n'
    atomic(backup / 'deployment.json', raw, mode=0o600, uid=os.geteuid(), gid=os.getegid())


def restore_files(scripts, backup, record):
    # Refuse to overwrite a later release. Never restore database snapshots.
    for name, expected in record['installed'].items():
        path = scripts / name
        if name == 'icbu_entities.py' and not path.exists():
            continue
        allowed = {expected, record['baseline'].get(name)}
        require(digest(regular(path)) in allowed, 'rollback_source_drift')
    original = regular(backup / 'icbu_inbound_receiver.py')
    require(digest(original) == record['baseline']['icbu_inbound_receiver.py'], 'backup_sha_mismatch')
    attributes = record['receiver_attributes']
    atomic(scripts / 'icbu_inbound_receiver.py', original, **attributes)
    module = scripts / 'icbu_entities.py'
    if module.exists():
        module.unlink()


def install(*, bundle=BUNDLE, scripts=SCRIPTS, data=DATA, backup_root=None, evidence=None, apply=False):
    info = manifest(bundle)
    units = unit_snapshot()
    baseline(scripts, info, units)
    if not apply:
        return {'ok': True, 'action': 'install', 'dry_run': True,
                'baseline_verified': True, 'full_smoke_required_before_apply': True}
    smoke_evidence(evidence, info)
    require(backup_root is not None, 'backup_root_required')
    private_backup_root(backup_root)
    backup = Path(tempfile.mkdtemp(prefix='icbu-entities-', dir=backup_root))
    before = (scripts / 'icbu_inbound_receiver.py').stat()
    attributes = dict(mode=stat.S_IMODE(before.st_mode), uid=before.st_uid, gid=before.st_gid)
    for name in info['baseline']:
        (backup / name).write_bytes(regular(scripts / name))
    (backup / 'systemd.txt').write_bytes(units)
    (backup / 'smoke-evidence.json').write_bytes(regular(evidence))
    database_backups = []
    for name in ('inbound.sqlite3', 'attachments.sqlite3', 'entities.sqlite3'):
        if backup_database(data / name, backup / name):
            database_backups.append(name)
    record = dict(baseline=info['baseline'], installed=info['candidate'], systemd_sha256=digest(units),
                  receiver_attributes=attributes, database_backups=database_backups, status='prepared')
    save_record(backup, record)
    # Recheck after backups, before the first production source mutation.
    baseline(scripts, info, unit_snapshot())
    try:
        atomic(scripts / 'icbu_entities.py', regular(bundle / 'icbu_entities.py'), **attributes)
        atomic(scripts / 'icbu_inbound_receiver.py', regular(bundle / 'icbu_inbound_receiver.py'), **attributes)
        database_check(bundle, data, initialize=True)  # explicit schema initialization before restart
        restart()
        check = database_check(bundle, data)
        for name, expected in info['candidate'].items():
            require(digest(regular(scripts / name)) == expected, 'installed_sha_mismatch')
        require(unit_snapshot() == units, 'systemd_configuration_changed')
        record['status'] = 'installed'
        save_record(backup, record)
        return dict(ok=True, action='install', dry_run=False, backup=str(backup), readback=check)
    except Exception:
        try:
            restore_files(scripts, backup, record)
            restart()
            record['status'] = 'automatically_rolled_back'
            save_record(backup, record)
        except Exception:
            raise DeployError('install_failed_manual_rollback_required_private_backup_' + backup.name) from None
        raise DeployError('install_failed_original_code_restored_private_backup_' + backup.name) from None


def rollback(*, backup, bundle=BUNDLE, scripts=SCRIPTS, data=DATA, apply=False):
    require(backup.is_absolute() and 'private_out' in backup.parts
            and not any(p.is_symlink() for p in (backup, *backup.parents)), 'unsafe_backup_path')
    record = json.loads(regular(backup / 'deployment.json'))
    require(digest(unit_snapshot()) == record['systemd_sha256'], 'systemd_configuration_drift')
    require(digest(regular(scripts / 'icbu_attachments.py')) == record['baseline']['icbu_attachments.py'],
            'rollback_dependency_drift')
    for name, expected in record['installed'].items():
        path = scripts / name
        if name == 'icbu_entities.py' and not path.exists():
            continue
        require(digest(regular(path)) in {expected, record['baseline'].get(name)}, 'rollback_source_drift')
    require(digest(regular(backup / 'icbu_inbound_receiver.py')) == record['baseline']['icbu_inbound_receiver.py'], 'backup_sha_mismatch')
    if not apply:
        return dict(ok=True, action='rollback', dry_run=True, database_preserved=True)
    restore_files(scripts, backup, record)
    restart()
    check = database_check(bundle, data) if (data / 'entities.sqlite3').exists() else {'ok': True, 'database_absent': True}
    record['status'] = 'rolled_back'
    save_record(backup, record)
    return dict(ok=True, action='rollback', dry_run=False, database_preserved=True, readback=check)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('install', 'rollback'))
    parser.add_argument('--apply', action='store_true', help='Mutate source and restart the business service')
    parser.add_argument('--backup-root', type=Path, help='Install backup root inside workspace/private_out')
    parser.add_argument('--backup', type=Path, help='Exact backup directory for rollback')
    parser.add_argument('--smoke-evidence', type=Path, help='Private full smoke pass record, completed within 24 hours')
    args = parser.parse_args()
    os.umask(0o077)
    try:
        require(not args.apply or os.geteuid() == 0, 'apply_requires_service_install_privileges')
        if args.action == 'install':
            result = install(backup_root=args.backup_root, evidence=args.smoke_evidence, apply=args.apply)
        else:
            require(args.backup is not None, 'backup_required')
            result = rollback(backup=args.backup, apply=args.apply)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except DeployError as exc:
        print(json.dumps(dict(ok=False, error=str(exc))))
    except Exception:
        print(json.dumps(dict(ok=False, error='deployment_precondition_or_operation_failed')))
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
