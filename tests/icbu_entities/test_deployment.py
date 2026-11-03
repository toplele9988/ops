from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess

import pytest

from entities_fixtures import CANDIDATE, ROOT, batch, entities, load

deploy = load('entities_deployment_tests', ROOT / 'deploy' / 'icbu' / 'deploy_entities.py')


def test_patch_forward_reverse_and_manifest_match_real_baseline(tmp_path):
    info = deploy.manifest()
    for name, expected in info['baseline'].items():
        source = ROOT / 'deploy' / 'icbu' / 'v2.2' / name
        assert deploy.digest(source.read_bytes()) == expected
        shutil.copyfile(source, tmp_path / name)
    patch = (ROOT / 'deploy' / 'icbu' / 'receiver-entities.patch').read_bytes()

    def apply(*flags):
        reply = subprocess.run(['patch', '--batch', '--fuzz=0', '-p1', *flags], cwd=tmp_path,
                               input=patch, capture_output=True, timeout=10)
        assert reply.returncode == 0, reply.stdout + reply.stderr
        assert b'offset' not in reply.stdout.lower() and b'fuzz' not in reply.stdout.lower()

    apply('--dry-run')
    assert not (tmp_path / 'icbu_entities.py').exists()
    apply()
    for name in info['candidate']:
        assert (tmp_path / name).read_bytes() == (CANDIDATE / name).read_bytes()
    apply('-R', '--dry-run')
    apply('-R')
    for name, expected in info['baseline'].items():
        assert deploy.digest((tmp_path / name).read_bytes()) == expected
    assert not (tmp_path / 'icbu_entities.py').exists()


@pytest.fixture
def installation(tmp_path, monkeypatch):
    scripts = tmp_path / 'scripts'
    data = tmp_path / 'data'
    scripts.mkdir(mode=0o700)
    data.mkdir(mode=0o700)
    for name in ('icbu_inbound_receiver.py', 'icbu_attachments.py'):
        shutil.copyfile(ROOT / 'deploy' / 'icbu' / 'v2.2' / name, scripts / name)
    with sqlite3.connect(data / 'inbound.sqlite3') as db:
        db.execute('CREATE TABLE inbound(payload TEXT)')
        db.execute("INSERT INTO inbound VALUES('fictional private chat')")
    unit = b'fictional service configuration\n'
    real_manifest = deploy.manifest()
    info = dict(real_manifest, systemd_cat_sha256=deploy.digest(unit))
    calls = []
    monkeypatch.setattr(deploy, 'manifest', lambda *args: info)
    monkeypatch.setattr(deploy, 'unit_snapshot', lambda: unit)
    monkeypatch.setattr(deploy, 'restart', lambda: calls.append('restart'))

    def check(bundle, data, *, initialize=False):
        store = entities.EntityStore(data / 'entities.sqlite3', initialize=initialize)
        result = store.stats()
        calls.append('initialize' if initialize else 'readback')
        return {key: result[key] for key in ('ok', 'total_entities', 'total_versions', 'total_batches')}
    monkeypatch.setattr(deploy, 'database_check', check)
    evidence = tmp_path / 'smoke.json'
    evidence.write_text(json.dumps(dict(command='scripts/e2e_smoke.sh', exit_code=0,
                                       completed_at=datetime.now(timezone.utc).isoformat(),
                                       baseline_sha256=info['baseline']['icbu_inbound_receiver.py'],
                                       candidate_sha256=info['candidate']['icbu_inbound_receiver.py'])))
    options = dict(bundle=CANDIDATE, scripts=scripts, data=data,
                   backup_root=tmp_path / 'private_out' / 'backups', evidence=evidence)
    return options, calls, info


def test_dry_run_is_read_only_and_apply_requires_full_smoke(installation):
    options, calls, info = installation
    reply = deploy.install(**options)
    assert reply['dry_run'] and reply['baseline_verified']
    assert calls == [] and not options['backup_root'].exists()
    options['evidence'].write_text('{"exit_code":1}')
    with pytest.raises(deploy.DeployError, match='invalid_smoke_evidence'):
        deploy.install(**options, apply=True)
    assert calls == [] and not options['backup_root'].exists()


def test_install_backup_restart_readback_then_rollback_preserves_all_data(installation):
    options, calls, info = installation
    original_chat = (options['data'] / 'inbound.sqlite3').read_bytes()
    installed = deploy.install(**options, apply=True)
    assert calls == ['initialize', 'restart', 'readback']
    backup = Path(installed['backup'])
    assert backup.parent == options['backup_root']
    assert (backup / 'systemd.txt').read_bytes() == b'fictional service configuration\n'
    with sqlite3.connect(backup / 'inbound.sqlite3') as db:
        assert db.execute('SELECT payload FROM inbound').fetchone()[0] == 'fictional private chat'
    store = entities.EntityStore(options['data'] / 'entities.sqlite3')
    store.receive(batch())
    rollback_options = dict(bundle=CANDIDATE, scripts=options['scripts'], data=options['data'], backup=backup)
    assert deploy.rollback(**rollback_options)['dry_run']
    reply = deploy.rollback(**rollback_options, apply=True)
    assert reply['database_preserved'] and reply['readback']['total_entities'] == 1
    assert not (options['scripts'] / 'icbu_entities.py').exists()
    assert deploy.digest((options['scripts'] / 'icbu_inbound_receiver.py').read_bytes()) == info['baseline']['icbu_inbound_receiver.py']
    assert (options['data'] / 'inbound.sqlite3').read_bytes() == original_chat
    assert entities.EntityStore(store.path, initialize=False).stats()['total_versions'] == 1


def test_failed_post_restart_check_automatically_restores_code(installation, monkeypatch):
    options, calls, info = installation
    real = deploy.database_check

    def fail_check(bundle, data, *, initialize=False):
        if not initialize:
            raise deploy.DeployError('fictional_readback_failure')
        return real(bundle, data, initialize=True)
    monkeypatch.setattr(deploy, 'database_check', fail_check)
    with pytest.raises(deploy.DeployError, match='install_failed_original_code_restored'):
        deploy.install(**options, apply=True)
    assert calls.count('restart') == 2
    assert deploy.digest((options['scripts'] / 'icbu_inbound_receiver.py').read_bytes()) == info['baseline']['icbu_inbound_receiver.py']
    assert not (options['scripts'] / 'icbu_entities.py').exists()
    assert (options['data'] / 'entities.sqlite3').exists()


def test_baseline_mismatch_prevents_backup_or_restart(installation):
    options, calls, info = installation
    (options['scripts'] / 'icbu_inbound_receiver.py').write_text('fictional unexpected release')
    with pytest.raises(deploy.DeployError, match='baseline_sha_mismatch'):
        deploy.install(**options, apply=True)
    assert calls == [] and not options['backup_root'].exists()


def test_rollback_refuses_overwriting_later_release(installation):
    options, calls, info = installation
    installed = deploy.install(**options, apply=True)
    (options['scripts'] / 'icbu_inbound_receiver.py').write_text('fictional later release')
    with pytest.raises(deploy.DeployError, match='rollback_source_drift'):
        deploy.rollback(backup=Path(installed['backup']), bundle=CANDIDATE,
                        scripts=options['scripts'], data=options['data'], apply=True)
    assert calls.count('restart') == 1


def test_database_selfcheck_command_has_no_http_and_initializes_only_when_explicit(tmp_path, monkeypatch):
    seen = []
    def capture(argv, *, input=None):
        seen.append((argv, input))
        return b'{"ok":true,"total_entities":0,"total_versions":0,"total_batches":0}'
    monkeypatch.setattr(deploy, 'command', capture)
    deploy.database_check(CANDIDATE, tmp_path)
    deploy.database_check(CANDIDATE, tmp_path, initialize=True)
    assert '--initialize' not in seen[0][0] and '--initialize' in seen[1][0]
    assert all(argv[:5] == ['runuser', '-u', 'imgw', '--', '/usr/bin/python3'] for argv, source in seen)
    assert all('http' not in str(argv) and '8791' not in str(argv) for argv, source in seen)
