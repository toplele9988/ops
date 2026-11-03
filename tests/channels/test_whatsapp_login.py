"""Login service contract, using fictional config and a transport that cannot send."""
import base64
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from email.message import Message
import json
import socket
import sqlite3
import struct
import threading
import time
from types import SimpleNamespace
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from unittest.mock import patch
from urllib.request import ProxyHandler
import zlib

import pytest

from console.channels import UnifiedInbox, WhatsAppAdapter
from console.channels.common import ChannelUnavailable
from console.channels.whatsapp import NoRedirect, SESSION_STATES
from console.channels.whatsapp_login import LoginError, WhatsAppLogin, local_login_fetch
from channel_fixtures import FakeWAHA, NOW, SECRET, fake_stat, seed_config


def png_chunk(kind, content):
    return (struct.pack('!I', len(content)) + kind + content
            + struct.pack('!I', zlib.crc32(kind + content) & 0xffffffff))


# A generated one-pixel image, not a real pairing credential or a copied QR.
PNG = (b'\x89PNG\r\n\x1a\n'
       + png_chunk(b'IHDR', struct.pack('!2I5B', 1, 1, 8, 2, 0, 0, 0))
       + png_chunk(b'IDAT', zlib.compress(b'\x00\xff\xff\xff'))
       + png_chunk(b'IEND', b''))
RAW_QR = 'fictional-pairing-content-must-not-leave-json'
ACTOR = 'fictional-login-owner'


class Clock:
    def __init__(self):
        self.now = NOW

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class LoginWAHA:
    """Only the fake login contract routes and status/QR can be used."""
    def __init__(self):
        self.status = 'FAILED'
        self.next_status = 'STARTING'
        self.me = {'id': '15559998888@c.us'}
        self.engine = {'engine': 'WEBJS'}
        self.read_error = self.write_error = None
        self.write_result = None
        self.qr_result = PNG
        self.calls = []

    def fetch(self, method, url, headers, timeout):
        assert headers.get('X-Api-Key') == SECRET
        assert 0 < timeout < 8
        parsed = urlsplit(url)
        assert parsed.hostname == '127.0.0.1' and parsed.port == 12345
        self.calls.append((method, parsed.path, parsed.query, timeout))
        if method == 'GET':
            if self.read_error:
                raise self.read_error
            if parsed.path == '/api/sessions/fictional-session':
                return dict(name='fictional-session', status=self.status,
                            me=self.me, engine=self.engine,
                            config={'waha_api_key': SECRET}, qr=RAW_QR,
                            private_error='/fictional/private/' + SECRET)
            assert parsed.path == '/api/fictional-session/auth/qr'
            return self.qr_result
        assert method == 'POST'
        assert parsed.path in {
            '/api/sessions/fictional-session/start',
            '/api/sessions/fictional-session/restart',
            '/api/sessions/fictional-session/logout',
        }
        if self.write_error:
            raise self.write_error
        if self.write_result is not None:
            return self.write_result
        self.status = 'STOPPED' if parsed.path.endswith('/logout') else self.next_status
        return dict(status=self.status, me=self.me, engine=self.engine,
                    config={'waha_api_key': SECRET}, qr=RAW_QR)

    @property
    def posts(self):
        return [call for call in self.calls if call[0] == 'POST']


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError('network forbidden in fictional login tests')
    monkeypatch.setattr(socket.socket, 'connect', denied)
    monkeypatch.setattr(socket.socket, 'connect_ex', denied)


@pytest.fixture
def env(tmp_path):
    config = tmp_path / 'fictional-wa.json'
    archive = tmp_path / 'fictional-archive.sqlite3'
    seed_config(config)
    net, clock = LoginWAHA(), Clock()

    def build(**overrides):
        arguments = dict(config_path=config, fetch=net.fetch, clock=clock,
                         stat_fn=fake_stat, group_gid=731)
        arguments.update(overrides)
        return WhatsAppLogin(archive, **arguments)

    return SimpleNamespace(root=tmp_path, config=config, archive=archive,
                           net=net, clock=clock, build=build, service=build(),
                           audit=archive.with_name(archive.stem + '-whatsapp-login.sqlite3'))


def audit_rows(env):
    with closing(sqlite3.connect(env.audit)) as db:
        db.row_factory = sqlite3.Row
        return [dict(row) for row in db.execute('SELECT * FROM login_audit ORDER BY id')]


def assert_safe(value):
    rendered = json.dumps(value, ensure_ascii=False, default=str)
    for secret in (SECRET, RAW_QR, '/fictional/private/', '15559998888'):
        assert secret not in rendered


@pytest.mark.parametrize('state,label', [
    ('STOPPED', '未启动'), ('STARTING', '正在启动'),
    ('SCAN_QR_CODE', '等待扫码'), ('WORKING', '已登录'), ('FAILED', '登录失败'),
    ('PASSKEY_REQUIRED', None), ('PASSKEY_CONFIRMATION_REQUIRED', None),
])
def test_all_waha_states_are_chinese_safe_and_never_write(env, state, label):
    env.net.status = state
    before = set(env.root.iterdir())
    data = env.service.status()
    assert state in SESSION_STATES
    assert data['session'] == 'fictional-session'
    assert data['status'] == state
    if label is not None:
        assert data['status_label'] == label
    assert any('\u4e00' <= c <= '\u9fff' for c in data['status_label'])
    assert data['needs_qr'] == (state == 'SCAN_QR_CODE')
    assert data['engine'] == 'WEBJS'
    assert data['next_step'] and any('\u4e00' <= c <= '\u9fff' for c in data['next_step'])
    assert 'status_changed_at' in data and 'last_message_at' in data
    if state == 'WORKING':
        assert data['phone_masked'].endswith('8888')
        assert '*' in data['phone_masked']
    assert_safe(data)
    assert not env.net.posts
    assert set(env.root.iterdir()) == before


@pytest.mark.parametrize('state', [SECRET, RAW_QR, '<script>alert(1)</script>', None, {}, ['WORKING']])
def test_unknown_or_malformed_status_is_not_reflected(env, state):
    env.net.status = state
    try:
        data = env.service.status()
    except LoginError as error:
        assert error.status_code == 503
        assert_safe(str(error))
    else:
        assert data['status'] == 'UNKNOWN'
        assert not data['needs_qr']
        assert_safe(data)
    assert not env.net.posts


@pytest.mark.parametrize('owner', [None, {}, {'id': SECRET}, {'id': '8888@lid'}, {'id': ['15559998888@c.us']}])
def test_missing_or_invalid_phone_never_echoes_waha_identity(env, owner):
    env.net.status, env.net.me = 'WORKING', owner
    assert_safe(env.service.status())


def test_status_reads_fresh_state_instead_of_waiting_for_inbox_cache(env):
    assert env.service.status()['status'] == 'FAILED'
    env.net.status = 'STARTING'
    assert env.service.status()['status'] == 'STARTING'
    env.net.status = 'SCAN_QR_CODE'
    assert env.service.status()['needs_qr']
    assert len(env.net.calls) == 3


def test_status_request_deadline_bounds_a_transport_ignoring_timeout(env, monkeypatch):
    monkeypatch.setattr('console.channels.whatsapp_login.REQUEST_BUDGET', 0.5)
    release, finished = threading.Event(), threading.Event()

    def stalled(*args):
        try:
            value = env.net.fetch(*args)
            release.wait(2)
            return value
        finally:
            finished.set()

    service = env.build(fetch=stalled)
    started = time.monotonic()
    try:
        with pytest.raises(LoginError) as result:
            service.status()
        elapsed = time.monotonic() - started
        assert result.value.code == 'timeout'
        assert elapsed < 1.5
        assert len(env.net.calls) == 1
    finally:
        release.set()
        assert finished.wait(2)
    assert len(env.net.calls) == 1 and not env.net.posts


def test_write_deadline_returns_unknown_and_never_retries_after_late_completion(env, monkeypatch):
    release, finished = threading.Event(), threading.Event()

    def stalled(method, *args):
        value = env.net.fetch(method, *args)
        if method == 'POST':
            try:
                release.wait(2)
            finally:
                finished.set()
        return value

    service = env.build(fetch=stalled)
    call = service._call
    post_started = []

    def with_post_deadline(config, method, path, deadline, **kwargs):
        # Start the short network budget after the real SQLite reservation and
        # status preflight. Disk fsync speed must not choose the scenario tested.
        if method == 'POST':
            post_started.append(time.monotonic())
            deadline = post_started[-1] + 0.5
        return call(config, method, path, deadline, **kwargs)

    monkeypatch.setattr(service, '_call', with_post_deadline)
    try:
        with pytest.raises(LoginError) as result:
            service.write('start', {'confirm': '1'}, ACTOR)
        assert post_started
        assert time.monotonic() - post_started[0] < 1.5
        assert result.value.code == 'outcome_unknown' and result.value.outcome == 'unknown'
        assert len(env.net.posts) == 1 and len(env.net.calls) == 2
        assert audit_rows(env)[-1]['result'] == 'unknown'
    finally:
        release.set()
        assert finished.wait(2)
    assert len(env.net.posts) == 1 and len(env.net.calls) == 2
    assert audit_rows(env)[-1]['result'] == 'unknown'


def test_late_scheduled_write_worker_does_not_start_a_request_after_deadline(env, monkeypatch):
    release, finished = threading.Event(), threading.Event()
    original_thread = threading.Thread
    workers = 0
    call = env.service._call

    def with_post_deadline(config, method, path, deadline, **kwargs):
        if method == 'POST':
            deadline = time.monotonic() + 0.5
        return call(config, method, path, deadline, **kwargs)

    def delayed_thread(*args, **kwargs):
        nonlocal workers
        workers += 1
        if workers == 2:
            target = kwargs['target']

            def delayed():
                try:
                    release.wait(2)
                    target()
                finally:
                    finished.set()

            kwargs['target'] = delayed
        return original_thread(*args, **kwargs)

    monkeypatch.setattr('console.channels.whatsapp_login.threading.Thread', delayed_thread)
    monkeypatch.setattr(env.service, '_call', with_post_deadline)
    try:
        with pytest.raises(LoginError) as result:
            env.service.write('start', {'confirm': '1'}, ACTOR)
        assert result.value.outcome == 'unknown'
        assert workers == 2
        assert len(env.net.calls) == 1 and not env.net.posts
    finally:
        release.set()
        assert finished.wait(2)
    assert len(env.net.calls) == 1 and not env.net.posts


def test_status_does_not_refresh_or_read_message_bodies_from_inbox(env):
    inbox_net = FakeWAHA()
    adapter = WhatsAppAdapter(env.config, fetch=inbox_net.fetch, clock=env.clock,
                              stat_fn=fake_stat, group_gid=731)
    assert adapter.health(NOW)['last_message_at'] == NOW - 20
    inbox = UnifiedInbox([adapter])
    service = env.build(inbox=inbox)
    with patch.object(adapter, 'health', side_effect=AssertionError('must not perform inbox network reads')), \
         patch.object(adapter, 'messages', side_effect=AssertionError('must not read message bodies')):
        data = service.status()
    assert data['last_message_at'] == NOW - 20
    assert len(env.net.calls) == 1


@pytest.mark.parametrize('state', sorted(SESSION_STATES - {'SCAN_QR_CODE'}))
def test_qr_is_rejected_for_every_non_scanning_state(env, state):
    env.net.status = state
    with pytest.raises(LoginError) as result:
        env.service.qr()
    assert result.value.status_code == 409
    assert any('\u4e00' <= c <= '\u9fff' for c in str(result.value))
    assert len(env.net.calls) == 1
    assert not any(call[1].endswith('/auth/qr') for call in env.net.calls)


def test_qr_is_fresh_memory_only_png_and_has_fetch_time(env, caplog, capsys):
    env.net.status = 'SCAN_QR_CODE'
    before = {p.name: p.read_bytes() for p in env.root.iterdir()}
    first, at = env.service.qr()
    assert first == PNG and at == env.clock()
    env.clock.advance(2)
    second, next_at = env.service.qr()
    assert second == PNG and next_at > at
    assert len([call for call in env.net.calls if call[1].endswith('/auth/qr')]) == 2
    assert {p.name: p.read_bytes() for p in env.root.iterdir()} == before
    output = capsys.readouterr()
    assert not output.out and not output.err and not caplog.text


@pytest.mark.parametrize('payload', [b'<html>private failure</html>', b'', {'data': RAW_QR}, SECRET])
def test_bad_qr_payload_is_not_returned_as_an_image(env, payload):
    env.net.status, env.net.qr_result = 'SCAN_QR_CODE', payload
    with pytest.raises(LoginError) as result:
        env.service.qr()
    assert result.value.status_code == 503
    assert_safe(str(result.value))


def test_qr_can_decode_png_wrapper_without_exposing_base64(env):
    env.net.status = 'SCAN_QR_CODE'
    env.net.qr_result = {'mimetype': 'image/png', 'data': base64.b64encode(PNG).decode()}
    image, at = env.service.qr()
    assert image == PNG and at == env.clock()


@pytest.mark.parametrize('wrapper', [
    {'mimetype': 'image/png', 'data': RAW_QR},
    {'mimetype': 'text/html', 'data': base64.b64encode(PNG).decode()},
    {'mimetype': 'image/png', 'data': base64.b64encode(SECRET.encode()).decode()},
])
def test_qr_rejects_wrong_or_invalid_wrappers(env, wrapper):
    env.net.status, env.net.qr_result = 'SCAN_QR_CODE', wrapper
    with pytest.raises(LoginError) as result:
        env.service.qr()
    assert result.value.status_code == 503
    assert_safe(str(result.value))


@pytest.mark.parametrize('state,action', [('STOPPED', 'start'), ('FAILED', 'restart')])
def test_start_uses_one_correct_lifecycle_write_and_returns_starting(env, state, action):
    env.net.status = state
    data = env.service.write('start', {'confirm': '1'}, ACTOR)
    assert data['status'] == 'STARTING'
    assert '正在启动' in data['next_step']
    assert not data['needs_qr']
    assert len(env.net.posts) == 1
    assert env.net.posts[0][1] == '/api/sessions/fictional-session/' + action
    assert_safe(data)
    rows = audit_rows(env)
    assert len(rows) >= 2
    assert all(row['actor'] == ACTOR and row['action'] == 'start' for row in rows)
    assert all(row['at'] == env.clock() for row in rows)
    assert rows[0]['result'] == 'attempt'
    assert_safe(rows)


@pytest.mark.parametrize('state', ['WORKING', 'STARTING', 'SCAN_QR_CODE',
                                 'PASSKEY_REQUIRED', 'PASSKEY_CONFIRMATION_REQUIRED'])
def test_start_never_logs_out_or_restarts_an_active_session(env, state):
    env.net.status = state
    with pytest.raises(LoginError) as result:
        env.service.write('start', {'confirm': '1'}, ACTOR)
    assert result.value.status_code == 409
    if state == 'WORKING':
        assert '已经登录，无需重新扫码' in str(result.value)
    assert not env.net.posts


@pytest.mark.parametrize('operation,fields', [
    ('start', {}), ('start', {'confirm': '0'}), ('start', {'confirm': 1}),
    ('start', {'confirm': '1', 'session': 'another'}),
    ('start', {'confirm': '1', 'waha_url': 'http://127.0.0.1:12345'}),
    ('logout', {'confirm': '1'}), ('logout', {'confirm': '1', 'phrase': '退出'}),
    ('logout', {'phrase': '退出登录'}),
    ('send', {'confirm': '1'}), ('stop', {'confirm': '1'}),
])
def test_confirmation_and_allowlisted_fields_precede_any_waha_call(env, operation, fields):
    with pytest.raises(LoginError) as result:
        env.service.write(operation, fields, ACTOR)
    assert result.value.status_code == 400
    assert not env.net.calls


def test_logout_requires_logged_in_and_uses_only_logout_endpoint(env):
    env.net.status = 'WORKING'
    data = env.service.write('logout', {'confirm': '1', 'phrase': '退出登录'}, ACTOR)
    assert len(env.net.posts) == 1
    assert env.net.posts[0][1] == '/api/sessions/fictional-session/logout'
    assert_safe(data)
    assert audit_rows(env)[0]['action'] == 'logout'


@pytest.mark.parametrize('state', sorted(SESSION_STATES - {'WORKING'}))
def test_logout_refuses_every_not_logged_in_state(env, state):
    env.net.status = state
    with pytest.raises(LoginError) as result:
        env.service.write('logout', {'confirm': '1', 'phrase': '退出登录'}, ACTOR)
    assert result.value.status_code == 409
    assert not env.net.posts


@pytest.mark.parametrize('error', [TimeoutError(SECRET), URLError(TimeoutError(SECRET)),
                                  ConnectionResetError(SECRET), ValueError(RAW_QR),
                                  HTTPError('http://127.0.0.1:12345/private', 500, SECRET, {}, None)])
def test_write_unknown_outcome_is_audited_and_never_retried(env, error, caplog, capsys):
    env.net.write_error = error
    with pytest.raises(LoginError) as result:
        env.service.write('start', {'confirm': '1'}, ACTOR)
    assert result.value.status_code == 503
    assert result.value.code == 'outcome_unknown'
    assert result.value.outcome == 'unknown'
    assert '结果不明，请稍后刷新状态' in str(result.value)
    assert len(env.net.posts) == 1
    rows = audit_rows(env)
    assert rows[0]['result'] == 'attempt'
    assert any('unknown' in row['result'] for row in rows)
    assert_safe(rows)
    assert_safe(str(result.value))
    output = capsys.readouterr()
    assert_safe(output.out + output.err + caplog.text)


@pytest.mark.parametrize('payload', [{}, {'status': SECRET}, {'status': RAW_QR},
                                    {'status': None}, [SECRET], b'private'])
def test_ambiguous_start_response_is_unknown_without_retry(env, payload):
    env.net.write_result = payload
    with pytest.raises(LoginError) as result:
        env.service.write('start', {'confirm': '1'}, ACTOR)
    assert result.value.code == 'outcome_unknown'
    assert len(env.net.posts) == 1
    assert_safe(str(result.value))


def test_rate_limit_survives_new_service_instance_and_is_exactly_ten_seconds(env):
    env.service.write('start', {'confirm': '1'}, ACTOR)
    env.net.status = 'FAILED'
    env.clock.advance(9)
    with pytest.raises(LoginError) as result:
        env.build().write('start', {'confirm': '1'}, 'another-fictional-user')
    assert result.value.status_code == 429
    assert result.value.retry_after == 1
    assert len(env.net.posts) == 1
    env.clock.advance(1)
    env.build().write('start', {'confirm': '1'}, ACTOR)
    assert len(env.net.posts) == 2


def test_unknown_write_is_also_throttled(env):
    env.net.write_error = TimeoutError(SECRET)
    with pytest.raises(LoginError):
        env.service.write('start', {'confirm': '1'}, ACTOR)
    with pytest.raises(LoginError) as result:
        env.service.write('start', {'confirm': '1'}, ACTOR)
    assert result.value.status_code == 429
    assert len(env.net.posts) == 1


def test_concurrent_instances_cannot_start_twice_in_one_window(env):
    first, second = env.build(), env.build()

    def start(service):
        try:
            service.write('start', {'confirm': '1'}, ACTOR)
            return 200
        except LoginError as error:
            return error.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(start, [first, second]))
    assert sorted(results) == [200, 429]
    assert len(env.net.posts) == 1


def test_audit_storage_failure_blocks_waha_mutation(env):
    env.audit.mkdir()
    with pytest.raises(LoginError) as result:
        env.service.write('start', {'confirm': '1'}, ACTOR)
    assert result.value.status_code == 503
    assert not env.net.posts
    assert_safe(str(result.value))


def test_distinct_actions_have_distinct_throttle_windows(env):
    env.service.write('start', {'confirm': '1'}, ACTOR)
    env.net.status = 'WORKING'
    env.service.write('logout', {'confirm': '1', 'phrase': '退出登录'}, ACTOR)
    assert len(env.net.posts) == 2


@pytest.mark.parametrize('error,word', [
    (ConnectionRefusedError(111, SECRET), '启动'),
    (URLError(ConnectionRefusedError(111, SECRET)), '启动'),
    (HTTPError('http://127.0.0.1:12345/private', 401, SECRET, {}, None), '密钥'),
    (HTTPError('http://127.0.0.1:12345/private', 403, SECRET, {}, None), '密钥'),
    (TimeoutError(SECRET), '超时'),
    (URLError(TimeoutError(SECRET)), '超时'),
])
def test_status_unavailable_has_clear_chinese_reason_and_no_upstream_detail(env, error, word):
    env.net.read_error = error
    with pytest.raises(LoginError) as result:
        env.service.status()
    assert result.value.status_code == 503
    assert word in str(result.value)
    assert_safe(str(result.value))
    assert not env.net.posts


@pytest.mark.parametrize('url', ['http://example.invalid:12345', 'https://127.0.0.1:12345',
                               'http://127.0.0.1:12345/api', 'http://user@localhost:12345',
                               'http://localhost:12345?private=1'])
def test_configuration_keeps_existing_local_only_restrictions(env, url):
    data = json.loads(env.config.read_text())
    data['waha_url'] = url
    env.config.write_text(json.dumps(data))
    with pytest.raises(LoginError) as result:
        env.service.status()
    assert result.value.status_code == 503
    assert not env.net.calls
    assert_safe(str(result.value))


def test_configuration_reuses_existing_reader_permissions_and_symlink_rejection(env):
    env.config.chmod(0o600)
    with pytest.raises(LoginError):
        env.service.status()
    assert not env.net.calls
    env.config.chmod(0o640)
    target = env.config.with_name('fictional-target.json')
    env.config.rename(target)
    env.config.symlink_to(target)
    with pytest.raises(LoginError):
        env.service.status()
    assert not env.net.calls


@pytest.mark.parametrize('session', ['.', '..'])
def test_configuration_rejects_session_dot_segments_before_transport(env, session):
    data = json.loads(env.config.read_text())
    data['session'] = session
    env.config.write_text(json.dumps(data))
    with pytest.raises(LoginError) as result:
        env.service.status()
    assert result.value.code == 'configuration'
    assert not env.net.calls


@pytest.mark.parametrize('operation,fields', [('start', {'confirm': '1'}),
                                            ('logout', {'confirm': '1', 'phrase': '退出登录'})])
def test_rehearsal_service_flag_blocks_all_lifecycle_writes(env, operation, fields):
    with pytest.raises(LoginError) as result:
        env.build(allow_writes=False).write(operation, fields, ACTOR)
    assert result.value.status_code == 403
    assert not env.net.calls


def test_entire_lifecycle_never_calls_message_or_outbox_paths(env):
    with patch('console.outbox.bridge_client.BridgeClient.deliver', side_effect=AssertionError('no send')), \
         patch('console.outbox.bridge_client.local_post', side_effect=AssertionError('no bridge writes')):
        env.service.status()
        env.service.write('start', {'confirm': '1'}, ACTOR)
        env.net.status = 'SCAN_QR_CODE'
        env.service.qr()
        env.net.status = 'WORKING'
        env.service.status()
        env.service.write('logout', {'confirm': '1', 'phrase': '退出登录'}, ACTOR)
    assert len(env.net.posts) == 2
    assert all('/messages' not in call[1] and '/send' not in call[1] for call in env.net.calls)


@pytest.mark.parametrize('method,path', [
    ('POST', '/api/sendText'), ('POST', '/api/sessions/fictional-session/stop'),
    ('DELETE', '/api/sessions/fictional-session'), ('POST', '/api/sessions'),
    ('GET', '/api/fictional-session/chats/overview?limit=1'),
    ('GET', '/api/fictional-session/chats/123@c.us/messages'),
    ('GET', '/api/sessions/fictional-session?redirect=1'),
    ('POST', '/api/sessions/fictional-session/start?extra=1'),
])
def test_transport_rejects_every_unrelated_path_before_open(method, path):
    with patch('console.channels.whatsapp_login.build_opener') as opener:
        with pytest.raises(ValueError):
            local_login_fetch(method, 'http://127.0.0.1:12345' + path,
                              {'X-Api-Key': SECRET}, 2)
    opener.assert_not_called()


@pytest.mark.parametrize('url', ['https://127.0.0.1:12345/api/sessions/fictional-session',
                               'http://example.invalid/api/sessions/fictional-session',
                               'http://user@localhost/api/sessions/fictional-session'])
def test_transport_itself_keeps_local_url_boundary(url):
    with patch('console.channels.whatsapp_login.build_opener') as opener:
        with pytest.raises(ValueError):
            local_login_fetch('GET', url, {'X-Api-Key': SECRET}, 2)
    opener.assert_not_called()


def test_transport_has_no_environment_proxy_or_redirect_and_returns_png():
    headers = Message()
    headers['Content-Type'] = 'image/png'
    response = SimpleNamespace(headers=headers, read=lambda limit: PNG)

    class OpenResponse:
        def __enter__(self):
            return response

        def __exit__(self, *args):
            return False

    recorded = []

    def build(*handlers):
        assert any(isinstance(handler, ProxyHandler) and handler.proxies == {} for handler in handlers)
        redirect = next(handler for handler in handlers if isinstance(handler, NoRedirect))
        with pytest.raises(ChannelUnavailable):
            redirect.redirect_request(None, None, 302, 'redirect', {}, 'http://example.invalid')

        def opened(request, timeout):
            recorded.append(request)
            assert request.method == 'GET' and timeout == 2
            assert request.get_header('X-api-key') == SECRET
            return OpenResponse()

        return SimpleNamespace(open=opened)

    with patch('console.channels.whatsapp_login.build_opener', side_effect=build):
        result = local_login_fetch('GET', 'http://127.0.0.1:12345/api/fictional-session/auth/qr?format=image',
                                   {'X-Api-Key': SECRET, 'Accept': 'image/png'}, 2)
    assert result == PNG and len(recorded) == 1
