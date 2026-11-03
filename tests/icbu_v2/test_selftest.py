"""联调工具只访问显式测试目标，假服务不连接任何真实渠道。"""
from contextlib import contextmanager
import importlib.util
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
from pathlib import Path
import sys
import threading

import pytest


spec = importlib.util.spec_from_file_location('icbu_v2_selftest', Path(__file__).resolve().parents[2] / 'scripts/icbu_v2_selftest.py')
selftest = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = selftest
spec.loader.exec_module(selftest)
TEST_TOKEN = 'fictional_test_instance_token_only_12345'


@pytest.mark.parametrize('value', [
    'http://127.0.0.1:8791', 'https://[::1]:8791', 'http://localhost:18888',
    'http://example.invalid:18888', 'http://127.0.0.1:18888/path',
    'http://name:password@127.0.0.1:18888', 'http://127.0.0.1:18888/?x=1',
    'http://127.0.0.1:18888/#x', 'ftp://127.0.0.1:18888', 'http://127.0.0.1:0',
    'http://[::1%lo]:18888', ' http://127.0.0.1:18888',
])
def test_bad_targets_rejected(value):
    with pytest.raises(selftest.CheckFailed):
        selftest.target_for(value, allow_lan=True)


@pytest.mark.parametrize('address', ['192.168.8.8', '10.7.8.9', '172.20.0.3', '[fd12::3]'])
def test_lan_requires_explicit_flag(address):
    target = f'http://{address}:18888'
    with pytest.raises(selftest.CheckFailed):
        selftest.target_for(target)
    assert selftest.target_for(target, allow_lan=True).port == 18888


@pytest.mark.parametrize('address', ['8.8.8.8', '169.254.2.3', '100.64.1.2', '[2001:4860::8888]'])
def test_public_and_link_local_refused_even_with_flag(address):
    with pytest.raises(selftest.CheckFailed):
        selftest.target_for(f'http://{address}:18888', allow_lan=True)


def test_dry_run_does_not_read_token_or_send(monkeypatch, capsys):
    class Unreadable:
        def readline(self, *args):
            raise AssertionError('不应读取任何令牌')
    monkeypatch.setattr(sys, 'stdin', Unreadable())
    monkeypatch.setattr(selftest.http.client, 'HTTPConnection', lambda *a, **kw: pytest.fail('不应连接网络'))
    assert selftest.main(['--url', 'http://192.168.8.8:18888', '--allow-lan', '--dry-run']) == 0
    assert 'http://192.168.8.8:18888' in capsys.readouterr().out


def test_default_has_no_target_and_does_not_read_token(monkeypatch, capsys):
    monkeypatch.setattr(sys, 'stdin', io.StringIO(TEST_TOKEN))
    assert selftest.main([]) == 1
    assert '没有默认接收端地址' in capsys.readouterr().err


@contextmanager
def fake_server(responder):
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_GET(self):
            requests.append((self.command, self.path))
            status, headers, body = responder(self)
            self.send_response(status)
            for key, value in headers.items():
                self.send_header(key, value)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.01}, daemon=True)
    thread.start()
    try:
        yield selftest.target_for(f'http://127.0.0.1:{server.server_port}'), requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_redirect_is_never_followed(monkeypatch):
    monkeypatch.setenv('http_proxy', 'http://127.0.0.1:8791')
    with fake_server(lambda request: (302, {'Location': 'http://127.0.0.1:8791/'}, b'')) as (target, requests):
        with pytest.raises(selftest.CheckFailed, match='重定向'):
            selftest.Client(target, TEST_TOKEN).request('GET', '/healthz')
        assert requests == [('GET', '/healthz')]


def test_invalid_response_does_not_echo_body_or_token():
    with fake_server(lambda request: (200, {}, TEST_TOKEN.encode())) as (target, requests):
        with pytest.raises(selftest.CheckFailed) as failure:
            selftest.Client(target, TEST_TOKEN).request('GET', '/healthz')
        assert TEST_TOKEN not in str(failure.value)
        assert len(requests) == 1


def test_transport_failure_is_not_retried(monkeypatch):
    class BrokenConnection:
        attempts = 0
        def __init__(self, *args, **kwargs):
            pass
        def request(self, *args, **kwargs):
            self.attempts += 1
            calls.append(self.attempts)
            raise ConnectionResetError(TEST_TOKEN)
        def close(self):
            pass
    calls = []
    monkeypatch.setattr(selftest.http.client, 'HTTPConnection', BrokenConnection)
    with pytest.raises(selftest.CheckFailed, match='没有自动重试') as failure:
        selftest.Client(selftest.target_for('http://127.0.0.1:18888'), TEST_TOKEN).request('GET', '/healthz')
    assert calls == [1]
    assert TEST_TOKEN not in str(failure.value)


class FakeReceiver:
    def __init__(self):
        self.calls = []
        self.messages = {}
        self.uploads = set()
        self.blobs = set()
    def request(self, method, path, payload=None, headers=None):
        self.calls.append((method, path, payload, headers))
        if path == '/healthz':
            return 200, dict(ok=True, service='icbu-inbound', protocol_schema_version=2)
        if method == 'GET':
            return 200, dict(ok=True)
        if method == 'POST':
            if any(a['filename'].startswith('../') for a in payload.get('attachments', [])):
                return 400, dict(ok=False, error='invalid_attachment_filename')
            if any(c.get('url', '').startswith('http:') for c in payload.get('cards', [])):
                return 400, dict(ok=False, error='invalid_card_url')
            key = payload['event_id']
            duplicate = key in self.messages
            self.messages[key] = 'fictional-inbound-' + str(list(self.messages).index(key)) if duplicate else 'fictional-inbound-' + str(len(self.messages))
            return (200 if duplicate else 202), dict(ok=True, inbound_id=self.messages[key], duplicate=duplicate)
        if headers['X-Content-SHA256'] == '0' * 64:
            return 400, dict(ok=False, error='attachment_sha256_mismatch')
        duplicate = headers['X-Content-SHA256'] in self.blobs
        self.blobs.add(headers['X-Content-SHA256'])
        self.uploads.add(path)
        return (200 if duplicate else 201), dict(ok=True, duplicate=duplicate)


def test_full_fictional_sequence_and_no_duplicate_message():
    receiver, output = FakeReceiver(), []
    selftest.run(receiver, output.append)
    assert len(receiver.messages) == 2
    assert len(receiver.uploads) == 2
    assert len(receiver.calls) == 12
    assert len(output) == 13
    assert '实际客户数据' in output[-1]
    messages = [payload for method, _, payload, _ in receiver.calls if method == 'POST']
    assert all(payload['history'] is True for payload in messages)
    assert {payload['role'] for payload in messages} == {'buyer', 'seller'}
    assert all('虚构' in payload['content'] for payload in messages)


@pytest.mark.parametrize('suffix', ['', '/private/path'])
def test_response_error_code_is_not_arbitrary_output(suffix):
    receiver = FakeReceiver()
    receiver.request = lambda *args, **kwargs: (503, dict(error=TEST_TOKEN + suffix))
    with pytest.raises(selftest.CheckFailed) as failure:
        selftest.run(receiver, lambda value: None)
    assert TEST_TOKEN not in str(failure.value)
    assert '/private/path' not in str(failure.value)


def test_main_never_prints_temporary_test_token(monkeypatch, capsys):
    monkeypatch.setattr(sys, 'stdin', io.StringIO(TEST_TOKEN + '\n'))
    monkeypatch.setattr(selftest, 'Client', lambda *args: FakeReceiver())
    assert selftest.main(['--url', 'http://127.0.0.1:18888']) == 0
    captured = capsys.readouterr()
    assert TEST_TOKEN not in captured.out + captured.err


def test_help_is_in_chinese(capsys):
    with pytest.raises(SystemExit) as exit_status:
        selftest.main(['--help'])
    assert exit_status.value.code == 0
    output = capsys.readouterr().out
    assert '用法：' in output
    assert '选项：' in output
    assert 'usage:' not in output and 'options:' not in output


def test_repeated_tool_runs_use_distinct_fictional_content():
    receiver = FakeReceiver()
    selftest.run(receiver, lambda value: None)
    selftest.run(receiver, lambda value: None)
    assert len(receiver.messages) == 4
    assert len(receiver.uploads) == 4
    assert len(receiver.blobs) == 2
