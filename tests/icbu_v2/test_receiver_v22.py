"""v2.2候选只使用临时库、虚构消息、假依赖和动态回环端口。"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from email.message import Message
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading
from unittest.mock import patch

import pytest

from receiver_fixtures import ROOT, attachment, load_receiver, message
from test_receiver_v21 import request

CANDIDATE = ROOT / 'deploy' / 'icbu' / 'v2.2'
spec = importlib.util.spec_from_file_location('r3d_attachments', CANDIDATE / 'icbu_attachments.py')
attachments = importlib.util.module_from_spec(spec)
spec.loader.exec_module(attachments)
with patch.dict(sys.modules, {'icbu_attachments': attachments}):
    receiver = load_receiver(CANDIDATE / 'icbu_inbound_receiver.py', name='r3d_receiver')
BODY = b'fictional attachment\n'
EVENT = 'icbu:v1:' + 'a' * 64
BASELINES = {
    'icbu_inbound_receiver.py': '49481feb23f44d381418ac4de7eed3d05f429e3b1e2c155b02efacd76b287f5d',
    'icbu_attachments.py': 'f81a670a3f67e34004c64d8d0ac12eba7c85572857c4ba3a040970ed476900ae',
}


def rich(**changes):
    return message(**dict(dict(event_id=EVENT, message_kind='file', attachments=[attachment()],
                              cards=[dict(card_type='product', title='虚构存储测试商品')]), **changes))


def headers():
    result = Message()
    for key, value in {'Content-Length': str(len(BODY)), 'Content-Type': 'text/plain',
                       'X-Content-SHA256': hashlib.sha256(BODY).hexdigest()}.items():
        result[key] = value
    return result


def registry(tmp_path, state='ok'):
    archive = tmp_path / 'fictional-nas'
    root = archive / 'icbu' / 'attachments'
    if state != 'absent':
        archive.mkdir(mode=0o700)
    if state in ('ok', 'missing-sentinel'):
        root.mkdir(parents=True, mode=0o700)
    if state in ('ok', 'missing-root'):
        (archive / '.imgw-archive-id').touch(mode=0o600)
    return attachments.AttachmentStore(tmp_path / 'local' / 'attachments.sqlite3', root,
                                       archive_root=archive)


def restore(reg):
    reg.root.mkdir(parents=True, exist_ok=True, mode=0o700)
    marker = reg.archive_root / '.imgw-archive-id'
    if not marker.exists():
        marker.touch(mode=0o600)


@contextmanager
def running(store):
    server = receiver.Server(('127.0.0.1', 0), store, 'fictional-r3d-instance-only-' + 'x' * 24,
                             import_archive=object(), probe_heartbeats=object())
    assert server.server_address[1] != 8791
    thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.01}, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        if server._maintenance:
            server._maintenance.join(timeout=5)


def records(store):
    with store.connect() as db:
        return [dict(row) for row in db.execute('SELECT * FROM inbound')]


@pytest.mark.parametrize('state', ['absent', 'missing-sentinel', 'missing-root'])
def test_startup_offline_preserves_text_and_recovers_without_restart(tmp_path, state, capsys):
    reg = registry(tmp_path, state)
    assert reg.storage_status() == 'unavailable'
    assert not reg.path.exists()
    if state == 'absent':
        assert not reg.archive_root.exists()
    store = receiver.Store(tmp_path / 'inbound.sqlite3', attachments=reg)
    path = '/api/inbound/alibaba/attachments/' + attachments.event_alias(EVENT) + '/fictional-file'
    with running(store) as server:
        log = capsys.readouterr().err
        assert 'unavailable' in log and '文字入库继续' in log and str(tmp_path) not in log
        status, health, _ = request(server, 'GET', '/healthz', authenticated=False)
        assert status == 200 and health['ok'] and health['attachments_storage'] == 'unavailable'
        assert health['protocol_schema_version'] == health['database_schema_version'] == 2
        assert health['attachments_cleanup_ok'] is False
        for method in ('PUT', 'GET'):
            code, reply, _ = request(server, method, path, BODY if method == 'PUT' else None, headers=dict(headers()))
            assert code == 503 and reply['error'] == 'attachment_storage_unavailable'
            assert reply['persisted'] is False
        plain = message(event_id='fictional-text')
        first = request(server, 'POST', '/api/inbound/alibaba', plain)
        assert first[0] == 202 and first[1]['persisted'] and 'attachments_pending' not in first[1]
        assert request(server, 'POST', '/api/inbound/alibaba', plain)[0] == 200
        assert request(server, 'GET', '/api/inbound/alibaba/' + first[1]['inbound_id'])[0] == 200
        rich_payload = rich(message_id='fictional-rich')
        code, partial, _ = request(server, 'POST', '/api/inbound/alibaba', rich_payload)
        assert code == 202 and partial['persisted'] and partial['attachments_pending']
        assert partial['attachments_storage'] == 'unavailable' and partial['duplicate'] is False
        assert request(server, 'POST', '/api/inbound/alibaba', rich_payload)[0] == 202
        before = next(row for row in records(store) if row['id'] == partial['inbound_id'])
        assert json.loads(before['payload'])['attachments'] == [attachment()]
        assert not reg.path.exists()  # 不在不可用NAS上建锁、建库或写回落目录。
        restore(reg)
        assert request(server, 'GET', '/healthz')[1]['attachments_storage'] == 'ok'
        code, complete, _ = request(server, 'POST', '/api/inbound/alibaba', rich_payload)
        assert code == 200 and complete['duplicate'] and complete['inbound_id'] == partial['inbound_id']
        assert 'attachments_pending' not in complete
        after = next(row for row in records(store) if row['id'] == partial['inbound_id'])
        for field in ('fingerprint', 'state', 'created_at', 'attempts', 'archive_reason'):
            assert after[field] == before[field]
        assert request(server, 'GET', path)[1]['registered'] is True
        assert request(server, 'PUT', path, BODY, headers=dict(headers()))[0] == 201
        readback = request(server, 'GET', path)[1]
        assert readback['file_received'] and readback['inbound_id'] == partial['inbound_id']
        assert len(records(store)) == 2
        with reg._connect() as db:
            assert db.execute('PRAGMA user_version').fetchone()[0] == 1
            assert db.execute('SELECT COUNT(*) FROM enrichment_ledger').fetchone()[0] == 1


def test_runtime_detach_and_restore_same_mount_never_falls_back(tmp_path):
    reg = registry(tmp_path)
    store = receiver.Store(tmp_path / 'inbound.sqlite3', attachments=reg)
    original = store.receive(receiver.validate(rich()))
    reg.receive_upload(attachments.event_alias(EVENT), 'fictional-file', headers(), io.BytesIO(BODY))
    detached = tmp_path / 'detached-nas'
    with running(store) as server:
        reg.archive_root.rename(detached)
        assert reg.storage_status() == 'unavailable' and not reg.archive_root.exists()
        assert request(server, 'POST', '/api/inbound/alibaba', message(message_id='offline-text'))[0] == 202
        partial = request(server, 'POST', '/api/inbound/alibaba', rich())[1]
        assert partial['attachments_pending'] and partial['inbound_id'] == original['inbound_id']
        assert not reg.archive_root.exists()
        detached.rename(reg.archive_root)
        assert reg.storage_status() == 'ok'
        assert request(server, 'POST', '/api/inbound/alibaba', rich())[0] == 200
        assert reg.readback(attachments.event_alias(EVENT), 'fictional-file')['file_received']
        assert len(records(store)) == 2


def test_mid_commit_loss_returns_partial_and_replay_repairs_aliases(tmp_path, monkeypatch):
    reg = registry(tmp_path)
    store = receiver.Store(tmp_path / 'inbound.sqlite3', attachments=reg)
    original = reg.link_messages
    marker = reg.archive_root / '.imgw-archive-id'
    hidden = marker.with_name('hidden-marker')
    def lose(*args, **kwargs):
        marker.rename(hidden)
        return original(*args, **kwargs)
    monkeypatch.setattr(reg, 'link_messages', lose)
    first = store.receive(receiver.validate(rich()))
    assert first['persisted'] and first['attachments_pending'] and not first['duplicate']
    assert len(records(store)) == 1
    hidden.rename(marker)
    monkeypatch.setattr(reg, 'link_messages', original)
    second = store.receive(receiver.validate(rich(event_id='fictional-alias')))
    assert second['inbound_id'] == first['inbound_id'] and second['duplicate']
    for event in (EVENT, 'fictional-alias'):
        assert reg.readback(attachments.event_alias(event), 'fictional-file')['inbound_id'] == first['inbound_id']


@pytest.mark.parametrize('variant', ['cards', 'empty-content', 'event-only'])
def test_offline_rich_variants_and_conflicts_are_durable_and_idempotent(tmp_path, variant):
    reg = registry(tmp_path, 'absent')
    store = receiver.Store(tmp_path / 'inbound.sqlite3', attachments=reg)
    payload = rich()
    if variant == 'cards':
        payload.pop('attachments')
    elif variant == 'empty-content':
        payload['content'] = ''
    else:
        payload['message_id'] = None
    normalized = receiver.validate(payload)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: store.receive(normalized), range(8)))
    assert len({row['inbound_id'] for row in results}) == 1 and len(records(store)) == 1
    assert sum(not row['duplicate'] for row in results) == 1
    assert all(row['attachments_pending'] for row in results)
    before = records(store)[0]
    with pytest.raises(receiver.Rejected) as failure:
        store.receive(receiver.validate(dict(payload, content='虚构冲突正文')))
    assert failure.value.code == 'idempotency_payload_conflict'
    if variant != 'cards':
        with pytest.raises(receiver.Rejected) as failure:
            store.receive(receiver.validate(dict(payload, attachments=[attachment(filename='other.txt')])))
        assert failure.value.code == 'idempotency_attachments_conflict'
    assert records(store)[0] == before
    restore(reg)
    assert store.receive(normalized)['inbound_id'] == results[0]['inbound_id']


def test_offline_auth_origin_validation_and_non_attachment_delegation(tmp_path, monkeypatch):
    reg = registry(tmp_path, 'absent')
    store = receiver.Store(tmp_path / 'inbound.sqlite3', attachments=reg)
    clock = dict(ok=True, server_time_utc='2026-11-01T12:00:00Z', server_day_shanghai='2026-11-01', poll_after_seconds=120)
    heartbeat = dict(schema='fictional-fake-only')
    imported = dict(ok=True, duplicate=False, persisted=True)
    seen = []
    class Heartbeats:
        def receive(self, value):
            seen.append(value)
            return dict(clock, persisted=True)
    class Imports:
        def receive(self, headers, stream):
            seen.append('import')
            return imported
    monkeypatch.setattr(receiver, 'server_clock', lambda: clock)
    monkeypatch.setattr(receiver, 'read_payload', lambda *_: heartbeat)
    with running(store) as server:
        server.probe_heartbeats, server.import_archive = Heartbeats(), Imports()
        assert request(server, 'GET', receiver.HEARTBEAT_PATH)[1] == clock
        assert request(server, 'POST', receiver.HEARTBEAT_PATH, heartbeat)[1] == dict(clock, persisted=True)
        assert request(server, 'POST', receiver.IMPORT_PATH, {})[:2] == (201, imported)
        assert request(server, 'POST', '/api/inbound/alibaba', rich(), authenticated=False)[0] == 401
        assert request(server, 'POST', '/api/inbound/alibaba', rich(), headers={'Origin': 'https://example.invalid'})[0] == 403
        assert request(server, 'POST', '/api/inbound/alibaba', rich(attachments=[attachment(filename='../bad.txt')]))[0] == 400
    assert seen == [heartbeat, 'import'] and records(store) == []


@pytest.mark.parametrize('replacement', ['symlink', 'directory'])
def test_replaced_mount_or_root_stays_unavailable_until_original_returns(tmp_path, replacement):
    reg = registry(tmp_path)
    saved = reg.root.with_name('original-root')
    reg.root.rename(saved)
    if replacement == 'symlink':
        reg.root.symlink_to(saved, target_is_directory=True)
    else:
        reg.root.mkdir(mode=0o700)
    assert reg.storage_status() == 'unavailable'
    with pytest.raises(attachments.AttachmentRejected):
        reg.receive_upload('fictional-event', 'fictional-file', headers(), io.BytesIO(BODY))
    if replacement == 'symlink':
        reg.root.unlink()
    else:
        assert list(reg.root.iterdir()) == []
        reg.root.rmdir()
    saved.rename(reg.root)
    assert reg.storage_status() == 'ok'


def test_main_starts_when_storage_is_offline(tmp_path, monkeypatch, capsys):
    reg = registry(tmp_path, 'absent')
    captured = []
    class TestServer(receiver.Server):
        def __init__(self, address, *args, **kwargs):
            assert address == ('127.0.0.1', 18888)  # 不监听此参数端口；实际仅bind动态端口。
            super().__init__(('127.0.0.1', 0), *args, **kwargs)
            captured.append(self)
        def serve_forever(self, **kwargs):
            assert self.store.attachments.storage_status() == 'unavailable'
            assert self.store.receive(receiver.validate(message()))['persisted']
    monkeypatch.setattr(receiver, 'AttachmentStore', lambda *args, **kwargs: reg)
    monkeypatch.setattr(receiver, 'Server', TestServer)
    monkeypatch.setattr(receiver.live, 'POLICY_PATH', '/fictional-unused', raising=False)
    monkeypatch.setattr(receiver.live, 'load_policy', lambda *args: None, raising=False)
    monkeypatch.setattr(receiver.signal, 'signal', lambda *args: None)
    monkeypatch.setattr(receiver.os, 'umask', lambda _: None)
    token_path = tmp_path / 'fictional-token'
    token_path.write_text('fictional-instance-only-' + 'x' * 32)
    token_path.chmod(0o600)
    assert receiver.main(['--bind', '127.0.0.1', '--port', '18888', '--token-file', str(token_path),
                          '--db', str(tmp_path / 'main.sqlite3')]) == 0
    assert len(captured) == 1 and captured[0].socket.fileno() == -1
    assert 'unavailable' in capsys.readouterr().err


def test_v22_cli_on_temporary_loopback_instance(tmp_path, monkeypatch):
    fault = tmp_path / 'fault'
    fault.mkdir(mode=0o700)
    monkeypatch.setenv('ICBU_V22_TEST_DIR', str(fault))
    reg = registry(tmp_path)
    store = receiver.Store(tmp_path / 'inbound.sqlite3', attachments=reg)
    with running(store) as server:
        result = subprocess.run([sys.executable, str(ROOT / 'scripts' / 'icbu_v2_selftest.py'),
                                 '--url', 'http://127.0.0.1:' + str(server.server_address[1]), '--v21', '--v22'],
                                input=server.token + '\n', text=True, capture_output=True, timeout=30)
        assert server.token not in result.stdout + result.stderr
        assert result.returncode == 0, result.stdout + result.stderr
        assert 'v2.1 自测完成' in result.stdout and 'v2.2 自测完成' in result.stdout
    assert not (fault / 'attachments-unavailable').exists() and len(records(store)) == 5


def test_environment_fault_at_startup_and_recovery(tmp_path, monkeypatch):
    fault = tmp_path / 'fault'
    fault.mkdir(mode=0o700)
    flag = fault / 'attachments-unavailable'
    flag.touch(mode=0o600)
    monkeypatch.setenv('ICBU_V22_TEST_DIR', str(fault))
    reg = registry(tmp_path)
    assert reg.storage_status() == 'unavailable' and not reg.path.exists()
    flag.unlink()
    assert reg.storage_status() == 'ok'


def test_partial_success_requires_main_database_commit(tmp_path, monkeypatch):
    reg = registry(tmp_path, 'absent')
    store = receiver.Store(tmp_path / 'inbound.sqlite3', attachments=reg)
    def unavailable(*args, **kwargs):
        raise sqlite3.OperationalError('fictional main database failure')
    monkeypatch.setattr(store, '_receive', unavailable)
    with running(store) as server:
        code, reply, _ = request(server, 'POST', '/api/inbound/alibaba', rich())
        assert code == 503 and reply['persisted'] is False
        assert reply['error'] == 'temporarily_unavailable' and 'attachments_pending' not in reply
    assert records(store) == []


@pytest.mark.skip(reason='superseded: v2.2 files now include the 2026-11-01 review fixes (deploy/review-20261101)')
def test_v22_patch_dry_run_apply_and_reverse_match_exact_bytes(tmp_path):
    patch_bytes = (CANDIDATE.parent / 'receiver-v2.2.patch').read_bytes()
    for name, digest in BASELINES.items():
        data = (CANDIDATE.parent / name).read_bytes()
        assert hashlib.sha256(data).hexdigest() == digest
        (tmp_path / name).write_bytes(data)
    def apply(*options):
        result = subprocess.run(['patch', '--batch', '--fuzz=0', '-p1', *options], cwd=tmp_path,
                                input=patch_bytes, capture_output=True, timeout=10)
        assert result.returncode == 0, (result.stdout + result.stderr).decode()
        assert b'offset' not in result.stdout
    apply('--dry-run')
    apply()
    for name in BASELINES:
        assert (tmp_path / name).read_bytes() == (CANDIDATE / name).read_bytes()
    apply('--dry-run', '-R')
    apply('-R')
    for name, digest in BASELINES.items():
        assert hashlib.sha256((tmp_path / name).read_bytes()).hexdigest() == digest
