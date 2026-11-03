"""Contract tests apply the proposed patch to a temporary bridge copy only."""
import base64
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
from types import SimpleNamespace

import pytest

BASELINE = Path('/opt/imgw/whatsapp/bridge.py')
PATCH = Path(__file__).resolve().parents[2] / 'deploy/whatsapp/bridge-send-video.patch'
BASELINE_SHA = '0f23743b95e5feea6277504c8caa8aeddae816a25a7c78f600a2c2b151016a69'


@pytest.fixture
def bridge(tmp_path):
    if not BASELINE.exists():
        pytest.skip('现场 bridge 基线不可读取')
    assert hashlib.sha256(BASELINE.read_bytes()).hexdigest() == BASELINE_SHA
    shutil.copyfile(BASELINE, tmp_path / 'bridge.py')
    subprocess.run(['patch', '-s', '-p1', '-i', str(PATCH)], cwd=tmp_path, check=True)
    spec = importlib.util.spec_from_file_location('fixture_bridge', tmp_path / 'bridge.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def configured(bridge, tmp_path):
    c = {'db': str(tmp_path / 'bridge.db'), 'session': 'fixture', 'owner': 'owner',
         'profile_id': 'profile', 'auto_send': False}
    bridge.initialize(c)
    with sqlite3.connect(c['db']) as db:
        db.execute('INSERT INTO binding VALUES(?,?,?,?,?)', ('fixture', '100001@c.us', 'owner', 'scope', 0))
        db.execute('INSERT INTO recipients VALUES(?,?,?,?,?)', ('fixture', '100001@c.us', '200002@c.us', 'fixture', 0))
    return c


def packet():
    return {'idempotency_key': 'fixture-video-1', 'session': 'fixture',
            'account_id': '100001@c.us', 'chat_id': '200002@c.us', 'actor': 'owner_explicit',
            'file': {'filename': 'fixture.mp4', 'mimetype': 'video/mp4',
                     'data': base64.b64encode(b'fictional video').decode()}}


@pytest.mark.parametrize('uncertain', [False, True])
def test_video_send_replay_never_sends_again(bridge, configured, uncertain):
    calls = []
    def fake_waha(config, path, body=None, **kwargs):
        if path.startswith('/api/sessions/'):
            return {'status': 'WORKING', 'me': {'id': '100001@c.us'}}
        calls.append((path, body))
        if uncertain:
            raise TimeoutError()
        return {'id': 'fixture-receipt'}
    first = bridge.send(configured, 'sendVideo', packet(), fake_waha)
    second = bridge.send(configured, 'sendVideo', packet(), fake_waha)
    assert first['state'] == ('unknown' if uncertain else 'accepted')
    assert second['replayed'] is True
    assert len(calls) == 1
    assert calls[0][0] == '/api/sendVideo'
    assert calls[0][1]['file'] == packet()['file']


def test_video_route_requires_authentication(bridge, monkeypatch):
    data = json.dumps(packet()).encode()
    monkeypatch.setattr(bridge, 'cfg', lambda: {'bridge_api_key': 'fixture-secret'})
    class Request:
        server = SimpleNamespace(role='api')
        connection = SimpleNamespace(settimeout=lambda _: None)
        path = '/api/sendVideo'
        headers = {'Content-Length': str(len(data)), 'Authorization': 'invalid'}
        rfile = io.BytesIO(data)
        api_auth = bridge.Handler.api_auth
        def reply(self, status, body):
            self.result = status, body
    request = Request()
    bridge.Handler.do_POST(request)
    assert request.result == (401, {'error': 'api_auth_failed'})


def test_video_route_forwards_only_authenticated_request(bridge, monkeypatch):
    data = json.dumps(packet()).encode()
    monkeypatch.setattr(bridge, 'cfg', lambda: {'bridge_api_key': 'fixture-secret'})
    calls = []
    monkeypatch.setattr(bridge, 'send', lambda config, kind, payload: calls.append((kind, payload)) or {'state': 'accepted'})
    class Request:
        server = SimpleNamespace(role='api')
        connection = SimpleNamespace(settimeout=lambda _: None)
        path = '/api/sendVideo'
        headers = {'Content-Length': str(len(data)), 'Authorization': 'Bearer fixture-secret'}
        rfile = io.BytesIO(data)
        api_auth = bridge.Handler.api_auth
        def reply(self, status, body):
            self.result = status, body
    request = Request()
    bridge.Handler.do_POST(request)
    assert request.result == (200, {'state': 'accepted'})
    assert calls == [('sendVideo', packet())]
