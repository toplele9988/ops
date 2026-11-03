"""扩展联调只使用虚构接收器，不读取真实凭据或访问真实渠道。"""
import base64
import io

import pytest

from test_selftest import FakeReceiver, TEST_TOKEN, selftest


class LegacyReceiver:
    def __init__(self):
        self.calls = []
        self.event = None
        self.attachment = None
        self.uploaded = False
        self.readback_changes = {}

    def request(self, method, path, payload=None, headers=None):
        self.calls.append((method, path, payload, headers))
        if path == '/healthz':
            return 200, dict(ok=True, service='icbu-inbound')
        if method == 'POST':
            assert headers['X-Idempotency-Key'] == payload['event_id']
            if len(payload['event_id'].encode('utf-8')) > 93:
                return 400, dict(ok=False, error='event_id_alias_too_long')
            duplicate = self.event is not None
            self.event = payload['event_id']
            self.attachment = (payload.get('attachments') or [None])[0]
            return (200 if duplicate else 202), dict(ok=True, persisted=True,
                event_id=self.event, inbound_id='fictional-inbound-v21', duplicate=duplicate)
        alias = 'e1_' + base64.urlsafe_b64encode(self.event.encode('utf-8')).decode('ascii').rstrip('=')
        if path.split('/')[-2] != alias:
            return 400, dict(ok=False, error='invalid_event_alias')
        if method == 'PUT':
            duplicate, self.uploaded = self.uploaded, True
            return (200 if duplicate else 201), dict(ok=True, persisted=True, duplicate=duplicate)
        return 200, dict(dict(ok=True, persisted=True, registered=True, file_received=self.uploaded,
            event_id=self.event, event_alias=alias, inbound_id='fictional-inbound-v21',
            attachment_id=self.attachment['attachment_id'], kind=self.attachment['kind'],
            size=self.attachment['size'], sha256=self.attachment['sha256'] if self.uploaded else None,
            duplicate=self.uploaded), **self.readback_changes)


def test_legacy_sequence_uses_original_post_identity_and_alias_readback():
    receiver, output = LegacyReceiver(), []
    selftest.run_v21(receiver, output.append)
    posts = [call for call in receiver.calls if call[0] == 'POST']
    assert posts[0][2]['event_id'].startswith('icbu:v1:')
    assert len(posts[0][2]['event_id']) == 72
    assert posts[0][2]['event_id'] == posts[1][2]['event_id']
    assert 'attachments' not in posts[0][2] and posts[1][2]['attachments']
    puts = [call for call in receiver.calls if call[0] == 'PUT']
    assert len(puts) == 2 and puts[0][1] == puts[1][1] and puts[0][2] == puts[1][2]
    assert '/e1_' in puts[0][1]
    assert len(receiver.calls) == 11
    assert len([call for call in receiver.calls if call[0] == 'GET' and '/attachments/' in call[1]]) == 5
    assert '仅在虚构数据上验证' in output[-1]


@pytest.mark.parametrize('changes', [
    dict(inbound_id='fictional-wrong-message'), dict(event_id='fictional-wrong-event'),
    dict(registered=False), dict(persisted=False), dict(file_received=True), dict(size=0),
])
def test_readback_mismatch_stops_before_upload(changes):
    receiver = LegacyReceiver()
    receiver.readback_changes = changes
    with pytest.raises(selftest.CheckFailed, match='登记、文件或关联身份不一致'):
        selftest.run_v21(receiver, lambda value: None)
    assert not any(call[0] == 'PUT' for call in receiver.calls)


def test_wrong_readback_hash_is_detected_after_one_upload():
    receiver = LegacyReceiver()
    original = receiver.request
    def wrong_hash(method, path, payload=None, headers=None):
        status, reply = original(method, path, payload, headers)
        if method == 'GET' and reply.get('file_received'):
            reply['sha256'] = '0' * 64
        return status, reply
    receiver.request = wrong_hash
    with pytest.raises(selftest.CheckFailed, match='文件校验值不一致'):
        selftest.run_v21(receiver, lambda value: None)
    assert len([call for call in receiver.calls if call[0] == 'PUT']) == 1


def test_cli_v21_runs_old_and_new_sequences(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(selftest.sys, 'stdin', io.StringIO(TEST_TOKEN + '\n'))
    monkeypatch.setattr(selftest, 'Client', lambda *args: object())
    monkeypatch.setattr(selftest, 'run', lambda client: calls.append('v2'))
    monkeypatch.setattr(selftest, 'run_v21', lambda client: calls.append('v21'))
    assert selftest.main(['--url', 'http://127.0.0.1:18888', '--v21']) == 0
    assert calls == ['v2', 'v21']
    assert TEST_TOKEN not in capsys.readouterr().out


def test_cli_default_preserves_original_scenario(monkeypatch):
    monkeypatch.setattr(selftest.sys, 'stdin', io.StringIO(TEST_TOKEN + '\n'))
    monkeypatch.setattr(selftest, 'Client', lambda *args: FakeReceiver())
    monkeypatch.setattr(selftest, 'run_v21', lambda *args: pytest.fail('默认不应调用扩展场景'))
    assert selftest.main(['--url', 'http://127.0.0.1:18888']) == 0


def test_v21_never_allows_production_port(monkeypatch):
    class Unreadable:
        def readline(self, *args):
            pytest.fail('拒绝8791时不应读取令牌')
    monkeypatch.setattr(selftest.sys, 'stdin', Unreadable())
    assert selftest.main(['--url', 'http://127.0.0.1:8791', '--v21']) == 1
