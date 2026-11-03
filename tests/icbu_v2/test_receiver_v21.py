"""旧稳定编号、读回和时钟记录只使用虚构消息、临时库和随机回环端口。"""
from contextlib import contextmanager
import hashlib
import http.client
import json
from pathlib import Path
import subprocess
import sys
import threading
from datetime import datetime

import pytest

from receiver_fixtures import attachment, load_receiver, message
from icbu_attachments import AttachmentRejected, AttachmentStore, event_alias


receiver = load_receiver(name='r3a_receiver')
EVENT = 'icbu:v1:' + 'a' * 64
BODY = b'fictional attachment\n'


@pytest.fixture
def store(tmp_path):
    registry = AttachmentStore(tmp_path / 'registry' / 'attachments.sqlite3', tmp_path / 'files')
    return receiver.Store(tmp_path / 'inbound.sqlite3', attachments=registry)


@contextmanager
def running(store):
    server = receiver.Server(('127.0.0.1', 0), store, 'fictional-r3a-test-instance-only-' + 'x' * 16,
                             import_archive=object(), probe_heartbeats=object())
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


def request(server, method, path, payload=None, *, authenticated=True, headers=None):
    supplied = dict(headers or {})
    if authenticated:
        supplied['Authorization'] = 'Bearer ' + server.token
    if isinstance(payload, dict):
        payload = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        supplied['Content-Type'] = 'application/json'
    connection = http.client.HTTPConnection(*server.server_address, timeout=5)
    try:
        connection.request(method, path, payload, supplied)
        response = connection.getresponse()
        return response.status, json.loads(response.read()), dict(response.getheaders())
    finally:
        connection.close()


def rich(**changes):
    return message(**dict(dict(event_id=EVENT, attachments=[attachment()], cards=[dict(
        card_type='product', title='虚构商品卡片')]), **changes))


def rows(store, table):
    assert table in ('inbound', 'dedup', 'inbound_observations')
    with store.connect() as db:
        return [dict(row) for row in db.execute('SELECT * FROM ' + table)]


def test_legacy_colon_enrichment_preserves_receipt_fingerprint_and_state(store):
    first = store.receive(receiver.validate(message(event_id=EVENT)))
    before, = rows(store, 'inbound')
    original_dedup = rows(store, 'dedup')
    enriched = store.receive(receiver.validate(rich()))
    after, = rows(store, 'inbound')
    assert enriched == dict(first, duplicate=True)
    for key in ('id', 'fingerprint', 'state', 'archive_reason', 'created_at', 'attempts'):
        assert after[key] == before[key]
    assert rows(store, 'dedup') == original_dedup
    assert json.loads(after['payload'])['event_id'] == EVENT
    assert json.loads(after['payload'])['attachments'] == [attachment()]
    internal = store.attachments.readback(EVENT, 'fictional-file', transport=False)
    external = store.attachments.readback(event_alias(EVENT), 'fictional-file')
    assert internal == external
    assert external['inbound_id'] == first['inbound_id']
    assert external['registered'] and not external['file_received']


def test_http_roundtrip_and_authenticated_metadata_only_readback(store):
    path = '/api/inbound/alibaba/attachments/' + event_alias(EVENT) + '/fictional-file'
    with running(store) as server:
        assert request(server, 'GET', path, authenticated=False)[0] == 401
        status, missing, response_headers = request(server, 'GET', path)
        assert status == 200 and missing['registered'] is missing['persisted'] is False
        assert missing['file_received'] is missing['duplicate'] is False
        assert missing['event_id'] == EVENT and missing['inbound_id'] is None
        assert response_headers['Cache-Control'] == 'no-store'
        code, first, _ = request(server, 'POST', '/api/inbound/alibaba', message(event_id=EVENT),
                                 headers={'X-Idempotency-Key': EVENT})
        assert code == 202 and first['persisted']
        code, supplemented, _ = request(server, 'POST', '/api/inbound/alibaba', rich(),
                                        headers={'X-Idempotency-Key': EVENT})
        assert code == 200 and supplemented['inbound_id'] == first['inbound_id']
        headers = {'Content-Type': 'text/plain', 'X-Content-SHA256': hashlib.sha256(BODY).hexdigest()}
        code, uploaded, _ = request(server, 'PUT', path, BODY, headers=headers)
        assert code == 201 and not uploaded['duplicate']
        assert uploaded['persisted'] and uploaded['event_id'] == EVENT
        assert uploaded['inbound_id'] == first['inbound_id']
        code, readback, _ = request(server, 'GET', path)
        assert code == 200 and readback['registered'] and readback['file_received']
        assert readback['inbound_id'] == first['inbound_id'] and readback['duplicate']
        assert readback['kind'] == 'file' and readback['size'] == len(BODY)
        assert readback['sha256'] == hashlib.sha256(BODY).hexdigest()
        assert set(readback) == {'ok', 'persisted', 'registered', 'file_received', 'size', 'sha256',
                                'kind', 'inbound_id', 'event_id', 'event_alias', 'attachment_id', 'duplicate'}
        assert str(store.path) not in json.dumps(readback)
        assert request(server, 'PUT', path, BODY, headers=headers)[1]['duplicate']
        assert request(server, 'GET', '/api/inbound/alibaba/' + first['inbound_id'])[1]['event_id'] == EVENT
        assert request(server, 'GET', path, headers={'Origin': 'https://evil.invalid'})[0] == 403
        assert request(server, 'GET', path + '?extra=1')[0] == 400
        code, _, cors = request(server, 'OPTIONS', path, authenticated=False, headers={
            'Origin': 'https://message.alibaba.com', 'Access-Control-Request-Method': 'GET',
            'Access-Control-Request-Headers': 'authorization'})
        assert code == 200 and 'GET' in cors['Access-Control-Allow-Methods']
    assert len(rows(store, 'inbound')) == 1


def test_upload_duplicate_does_not_claim_message_link(store):
    path = '/api/inbound/alibaba/attachments/' + event_alias(EVENT) + '/fictional-file'
    headers = {'Content-Type': 'text/plain', 'X-Content-SHA256': hashlib.sha256(BODY).hexdigest()}
    with running(store) as server:
        assert request(server, 'PUT', path, BODY, headers=headers)[0] == 201
        duplicate = request(server, 'PUT', path, BODY, headers=headers)[1]
        assert duplicate['duplicate'] and duplicate['inbound_id'] is None
        pending = request(server, 'GET', path)[1]
        assert pending['registered'] and pending['file_received'] and pending['inbound_id'] is None
        assert pending['kind'] is None
        inbound = request(server, 'POST', '/api/inbound/alibaba', rich())[1]['inbound_id']
        linked = request(server, 'GET', path)[1]
        assert linked['inbound_id'] == inbound and linked['kind'] == 'file'


def test_v2_attachment_kind_reads_original_payload_without_requiring_replay(store):
    result = store.receive(receiver.validate(rich()))
    with store.attachments._connect() as db:
        db.execute('DELETE FROM attachment_kinds')
    path = '/api/inbound/alibaba/attachments/' + event_alias(EVENT) + '/fictional-file'
    with running(store) as server:
        before = request(server, 'GET', path)[1]
        assert before['kind'] == 'file' and before['inbound_id'] == result['inbound_id']
        headers = {'Content-Type': 'text/plain', 'X-Content-SHA256': hashlib.sha256(BODY).hexdigest()}
        assert request(server, 'PUT', path, BODY, headers=headers)[1]['kind'] == 'file'
    with store.attachments._connect() as db:
        assert db.execute('SELECT COUNT(*) FROM attachment_kinds').fetchone()[0] == 0


def test_complete_cli_selftest_uses_temporary_candidate_and_dynamic_loopback_port(store):
    with running(store) as server:
        assert server.server_address[1] != 8791
        script = Path(__file__).resolve().parents[2] / 'scripts' / 'icbu_v2_selftest.py'
        result = subprocess.run([sys.executable, str(script), '--url',
                                 'http://127.0.0.1:' + str(server.server_address[1]), '--v21'],
                                input=server.token + '\n', text=True, capture_output=True, timeout=20)
        assert server.token not in result.stdout + result.stderr
        assert result.returncode == 0, result.stdout + result.stderr
        assert 'v2.1 自测完成' in result.stdout
    assert len(rows(store, 'inbound')) == 3


@pytest.mark.parametrize('event', ['e1_native', 'e1_', 'e1_aWNidTp2MTph'])
def test_reserved_namespace_rejected_as_original_post_id(event):
    with pytest.raises(receiver.Rejected) as caught:
        receiver.validate(message(event_id=event))
    assert caught.value.code == 'event_id_reserved_prefix'
    with pytest.raises(receiver.Rejected) as caught:
        receiver.validate(message(event_id=None), event)
    assert caught.value.code == 'event_id_reserved_prefix'


def test_long_text_id_remains_legacy_compatible_but_rich_transport_rejects_it(store):
    event = 'a:' + 'x' * 92
    first = store.receive(receiver.validate(message(event_id=event)))
    assert first['persisted'] and first['event_id'] == event
    with pytest.raises(receiver.Rejected) as caught:
        receiver.validate(rich(event_id=event))
    assert caught.value.code == 'event_id_alias_too_long'
    with pytest.raises(receiver.Rejected) as caught:
        receiver.validate(rich(), event_alias(EVENT))
    assert caught.value.code == 'event_id_header_mismatch'


@pytest.mark.parametrize('alias', ['e1__w', 'e1_ZmljdGlvbmFs', 'e1_', 'e1_YTpiYwA'])
def test_bad_alias_rejected_for_get_and_put(store, alias):
    path = '/api/inbound/alibaba/attachments/' + alias + '/fictional-file'
    with running(store) as server:
        for method in ('GET', 'PUT'):
            status, result, _ = request(server, method, path, BODY if method == 'PUT' else None)
            assert status == 400 and result['error'] == 'invalid_event_alias'
            assert result['persisted'] is False and 'message' in result
    assert rows(store, 'inbound') == []


def test_lost_link_commit_is_repaired_and_enrichment_ledger_is_idempotent(store, monkeypatch):
    payload = receiver.validate(rich())
    original = store.attachments.link_messages
    def fail(*args, **kwargs):
        raise AttachmentRejected('attachment_storage_unavailable', 503)
    monkeypatch.setattr(store.attachments, 'link_messages', fail)
    with pytest.raises(receiver.Rejected):
        store.receive(payload)
    before, = rows(store, 'inbound')
    assert not store.attachments.readback(event_alias(EVENT), 'fictional-file')['registered']
    monkeypatch.setattr(store.attachments, 'link_messages', original)
    result = store.receive(payload)
    store.receive(payload)
    assert result['duplicate'] and result['inbound_id'] == before['id']
    with store.attachments._connect() as db:
        entries = db.execute('SELECT * FROM enrichment_ledger').fetchall()
    assert len(entries) == 1
    assert dict(entries[0]) == dict(event_id=EVENT, event_alias=event_alias(EVENT),
                                   inbound_id=before['id'], request_sha256=hashlib.sha256(
                                       receiver.compact(payload).encode('utf-8')).hexdigest(),
                                   created_at=entries[0]['created_at'])
    assert len(rows(store, 'inbound')) == 1
    assert rows(store, 'inbound')[0]['fingerprint'] == before['fingerprint']


@pytest.mark.parametrize('observed,expected', [
    ('2026-10-30T10:00:01Z', 45), ('2026-10-30T10:05:46Z', -300), (None, None),
])
def test_clock_skew_is_internal_and_does_not_change_dedup_or_heartbeat(store, observed, expected):
    payload = receiver.validate(message(event_id=EVENT, observed_at=observed))
    now = datetime.fromisoformat('2026-10-30T10:00:46+00:00').timestamp()
    response = store.receive(payload, now=now)
    store.receive(payload, now=now + 600)
    record, = rows(store, 'inbound_observations')
    assert record['clock_skew_seconds'] == expected
    assert record['received_at'] == now and record['observed_at'] == payload['observed_at']
    assert record['inbound_id'] == response['inbound_id']
    assert 'clock_skew_seconds' not in response
    assert 'clock_skew_seconds' not in store.status(response['inbound_id'])
    stored, = rows(store, 'inbound')
    assert 'clock_skew_seconds' not in json.loads(stored['payload'])
    assert stored['fingerprint'] == receiver.fingerprint(payload)


def test_clock_skew_does_not_include_time_waiting_for_the_upload_writer(store, monkeypatch):
    started = datetime.fromisoformat('2026-10-30T10:00:46+00:00').timestamp()
    clock = [started]
    original = store.attachments.guard
    @contextmanager
    def busy_writer():
        clock[0] += 60
        with original() as root:
            yield root
    monkeypatch.setattr(receiver.time, 'time', lambda: clock[0])
    monkeypatch.setattr(store.attachments, 'guard', busy_writer)
    store.receive(receiver.validate(rich()))
    observed, = rows(store, 'inbound_observations')
    assert observed['received_at'] == started and observed['clock_skew_seconds'] == 45


def test_heartbeat_paths_still_delegate_without_extending_payload(store, monkeypatch):
    heartbeat = dict(schema='icbu_probe_heartbeat_v1', account='Amy', instance_id='fictional',
                     version='1.0', state='paused', range={}, counters=dict(contacts_loaded=0,
                     contacts_scanned=0, unbound=0, metadata_skipped=0, queued=2, blocked=1),
                     last_error='clock_skew', cycle_complete=False)
    clock = dict(ok=True, server_time_utc='2026-11-01T10:00:00Z',
                 server_day_shanghai='2026-11-01', poll_after_seconds=120)
    received = []
    monkeypatch.setattr(receiver, 'server_clock', lambda: clock)
    monkeypatch.setattr(receiver, 'read_payload', lambda *_: heartbeat)
    with running(store) as server:
        class Heartbeats:
            def receive(self, payload):
                received.append(payload)
                return dict(clock, persisted=True)
        server.probe_heartbeats = Heartbeats()
        assert request(server, 'GET', receiver.HEARTBEAT_PATH)[1] == clock
        assert request(server, 'POST', receiver.HEARTBEAT_PATH, heartbeat)[1] == dict(clock, persisted=True)
    assert received == [heartbeat]
    assert rows(store, 'inbound') == [] and rows(store, 'inbound_observations') == []
