"""Fictional signed bridge events and fake local WAHA; no sockets or real data."""
import hashlib
import hmac
import importlib.util
import io
import json
from pathlib import Path
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from console.wamedia import MediaStore, identity
from console.wamedia.ingest import IngestWorker, WahaDownload


class Response(io.BytesIO):
    status = 200
    def __init__(self, data):
        super().__init__(data)
        self.headers = {'Content-Length': str(len(data))}


class FakeWaha:
    def __init__(self, messages):
        self.messages = messages
        self.calls = []

    def open(self, request, timeout):
        url = request.full_url
        self.calls.append(url)
        assert url.startswith('http://127.0.0.1:5000/api/')
        assert request.method == 'GET' and timeout == 10
        if '/api/files/' in url:
            return Response(b'fictional attachment')
        for mid, mime in self.messages.items():
            if '/messages/' + mid + '?' in url:
                return Response(json.dumps({'id': mid, 'hasMedia': True, 'media': {
                    'mimetype': mime, 'url': 'http://127.0.0.1:5000/api/files/' + mid + '.bin'}}).encode())
        raise AssertionError('unexpected fake request')


def baseline():
    path = Path('/opt/imgw/whatsapp/bridge.py')
    if not path.exists():
        pytest.skip('现场 bridge 基线不可读取')
    spec = importlib.util.spec_from_file_location('fixture_receive_bridge', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_signed_events_through_worker_and_duplicate(tmp_path):
    bridge = baseline()
    config = {'db': str(tmp_path / 'bridge.db'), 'session': 'fixture', 'owner': 'fixture',
              'profile_id': 'fixture', 'webhook_key': 'fictional-hmac'}
    bridge.initialize(config)
    with sqlite3.connect(config['db']) as db:
        db.execute('INSERT INTO binding VALUES(?,?,?,?,?)', ('fixture', '100001@c.us', 'fixture', 'scope', 0))
    mime_by_id = {'img': 'image/png', 'video': 'video/mp4', 'doc': 'application/pdf', 'voice': 'audio/ogg'}
    for mid, mime in mime_by_id.items():
        event = {'id': 'event-' + mid, 'event': 'message.any', 'session': 'fixture', 'payload': {
            'id': mid, 'fromMe': False, 'from': '200002@c.us', 'body': '', 'timestamp': 1,
            'hasMedia': True, 'media': {'mimetype': mime, 'filename': mid + '.bin', 'url': None}}}
        raw = json.dumps(event).encode()
        headers = {'X-Webhook-Hmac-Algorithm': 'sha512',
                   'X-Webhook-Hmac': hmac.new(b'fictional-hmac', raw, hashlib.sha512).hexdigest()}
        fake_binding = lambda *_: {'status': 'WORKING', 'me': {'id': '100001@c.us'}}
        bridge.receive(config, raw, headers, fake_binding)
        bridge.receive(config, raw, headers, fake_binding)
    with sqlite3.connect(config['db']) as db:
        assert db.execute('SELECT count(*) FROM messages').fetchone()[0] == 4
    store = MediaStore(tmp_path / 'media.db', tmp_path / 'blobs')
    fake = FakeWaha(mime_by_id)
    fetch = WahaDownload({'waha_url': 'http://127.0.0.1:5000', 'session': 'fixture', 'waha_api_key': 'fictional'}, fake)
    worker = IngestWorker(store, tmp_path / 'queue.db', fetch)
    assert worker.scan(config['db'], 2) == 2
    worker = IngestWorker(store, tmp_path / 'queue.db', fetch)
    assert worker.scan(config['db'], 10) == 2
    assert worker.scan(config['db']) == 0
    for _ in mime_by_id:
        assert worker.run_one()
    assert not worker.run_one()
    for mid in mime_by_id:
        assert store.read(identity('fixture', mid))[1] == b'fictional attachment'
    assert len(fake.calls) == 8
    assert len(list((tmp_path / 'blobs').glob('*.blob'))) == 1
    worker.enqueue('fixture', 'img', '200002@c.us', {'mimetype': 'image/png'})
    assert not worker.run_one()


def test_bounded_retries_survive_restart_and_duplicate_registration(tmp_path):
    ticks = [1000]
    store = MediaStore(tmp_path / 'media.db', tmp_path / 'blobs')
    calls = []
    def fail(*args):
        calls.append(args)
        raise TimeoutError('private detail')
    for attempt in range(5):
        worker = IngestWorker(store, tmp_path / 'queue.db', fail, clock=lambda: ticks[0])
        key = worker.enqueue('fixture', 'img', '200002@c.us', {'mimetype': 'image/png'})
        assert worker.run_one() is (attempt < 4)
        ticks[0] += 61
    assert len(calls) == 4
    assert store.get(key)['state'] == 'failed'


def test_concurrent_workers_claim_only_once(tmp_path):
    import threading
    store = MediaStore(tmp_path / 'media.db', tmp_path / 'blobs')
    entered, release = threading.Event(), threading.Event()
    def fetch(*args):
        entered.set()
        assert release.wait(5)
        return b'fictional', 'image/png'
    one = IngestWorker(store, tmp_path / 'queue.db', fetch)
    two = IngestWorker(store, tmp_path / 'queue.db', fetch)
    one.enqueue('fixture', 'img', '200002@c.us', {'mimetype': 'image/png'})
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(one.run_one)
        assert entered.wait(5)
        second = pool.submit(two.run_one)
        assert second.result(timeout=5) is False
        release.set()
        assert first.result(timeout=5) is True


@pytest.mark.parametrize('url', ['http://example.invalid/api/files/a', 'http://127.0.0.1:5000/api/files/%2e%2e',
                               'http://127.0.0.1:5000/api/files/%2fetc', 'http://127.0.0.1:5000/api/files/a?x=y'])
def test_reject_untrusted_media_urls(tmp_path, url):
    class Fake:
        def open(self, request, timeout):
            return Response(json.dumps({'id': 'img', 'hasMedia': True, 'media': {'mimetype': 'image/png', 'url': url}}).encode())
    fetch = WahaDownload({'waha_url': 'http://127.0.0.1:5000', 'session': 'fixture', 'waha_api_key': 'fictional'}, Fake())
    with pytest.raises(ValueError):
        fetch('fixture', '200002@c.us', 'img')


def test_reject_invalid_mime_and_oversized_response(tmp_path, monkeypatch):
    fake = FakeWaha({'img': 'application/x-executable'})
    fetch = WahaDownload({'waha_url': 'http://127.0.0.1:5000', 'session': 'fixture', 'waha_api_key': 'fictional'}, fake)
    with pytest.raises(ValueError):
        fetch('fixture', '200002@c.us', 'img')
    assert len(fake.calls) == 1
    monkeypatch.setattr('console.wamedia.ingest.MAX_BYTES', 2)
    fake = FakeWaha({'img': 'image/png'})
    fetch = WahaDownload({'waha_url': 'http://127.0.0.1:5000', 'session': 'fixture', 'waha_api_key': 'fictional'}, fake)
    with pytest.raises(ValueError):
        fetch('fixture', '200002@c.us', 'img')


def test_manual_requeue_only_exhausted_jobs(tmp_path):
    ticks = [1000]
    store = MediaStore(tmp_path / 'media.db', tmp_path / 'blobs')
    worker = IngestWorker(store, tmp_path / 'queue.db', lambda *_: (_ for _ in ()).throw(TimeoutError()), clock=lambda: ticks[0])
    worker.enqueue('fixture', 'img', '200002@c.us', {'mimetype': 'image/png'})
    assert worker.reset_failed() == 0
    for _ in range(4):
        assert worker.run_one()
        ticks[0] += 61
    assert not worker.run_one()
    assert worker.reset_failed() == 1
    worker.fetch = lambda *_: (b'fictional', 'image/png')
    assert worker.run_one()
    assert store.get(identity('fixture', 'img'))['state'] == 'ready'
    assert worker.reset_failed() == 0


def test_missing_metadata_still_queues_and_uses_download_mime(tmp_path):
    bridge_db = tmp_path / 'bridge.db'
    with sqlite3.connect(bridge_db) as db:
        db.execute('CREATE TABLE messages(session TEXT,message_id TEXT,chat_id TEXT,has_media INTEGER,media_json TEXT)')
        db.execute('INSERT INTO messages VALUES(?,?,?,?,?)', ('fixture', 'img', '200002@c.us', 1, 'not-json'))
        db.execute('INSERT INTO messages VALUES(?,?,?,?,?)', ('fixture', 'bad-filename', '200002@c.us', 1, '{"filename":42}'))
    store = MediaStore(tmp_path / 'media.db', tmp_path / 'blobs')
    fake = FakeWaha({'img': 'image/png', 'bad-filename': 'image/png'})
    fetch = WahaDownload({'waha_url': 'http://127.0.0.1:5000', 'session': 'fixture', 'waha_api_key': 'fictional'}, fake)
    worker = IngestWorker(store, tmp_path / 'queue.db', fetch)
    assert worker.scan(bridge_db) == 2
    assert worker.run_one() and worker.run_one()
    for mid in ('img', 'bad-filename'):
        assert store.get(identity('fixture', mid))['kind'] == 'image'
        assert store.get(identity('fixture', mid))['state'] == 'ready'


def test_bad_webhook_signature_is_not_archived(tmp_path):
    bridge = baseline()
    config = {'db': str(tmp_path / 'bridge.db'), 'session': 'fixture', 'owner': 'fixture',
              'profile_id': 'fixture', 'webhook_key': 'fictional-hmac'}
    bridge.initialize(config)
    raw = json.dumps({'event': 'message.any', 'session': 'fixture', 'payload': {
        'id': 'img', 'fromMe': False, 'from': '200002@c.us', 'hasMedia': True}}).encode()
    with pytest.raises(ValueError):
        bridge.receive(config, raw, {'X-Webhook-Hmac-Algorithm': 'sha512', 'X-Webhook-Hmac': 'invalid'})
    with sqlite3.connect(config['db']) as db:
        assert db.execute('SELECT count(*) FROM messages').fetchone()[0] == 0
        assert db.execute('SELECT count(*) FROM events').fetchone()[0] == 0
