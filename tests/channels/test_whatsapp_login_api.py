"""Login HTTP contract with fictional services and no network access."""
import asyncio
import json
import threading
from types import SimpleNamespace
from urllib.parse import parse_qs, urlencode
from unittest.mock import Mock, patch

import pytest
from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from starlette.testclient import TestClient

from console import v3_whatsapp_login_api as api
from console.channels.whatsapp_login import LoginError

PNG = b'\x89PNG\r\n\x1a\nfictional-qr-bytes'


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError('network forbidden')
    monkeypatch.setattr('socket.socket.connect', denied)


def request(*, fields=None, form=None, body=b'', principal=True, authenticated=True,
            recheck=True, headers=None, query=None):
    async def read_body():
        return body
    if headers is None:
        headers = {'content-length': str(len(urlencode(fields if fields is not None else form, doseq=True).encode())
            if fields is not None or form is not None else len(body))}
    return SimpleNamespace(state=SimpleNamespace(
        password_principal=SimpleNamespace(username='fictional-owner') if principal else None,
        session=SimpleNamespace(authenticated=authenticated), password_recheck=lambda: recheck,
        form={key: [value] for key, value in fields.items()} if fields is not None else form),
        headers=headers, query_params=query or {}, path_params={}, body=read_body)


class Client:
    def __init__(self, **kwargs):
        self.service = Mock()
        self.service.status.return_value = {'status': 'SCAN_QR_CODE', 'status_label': '等待扫码'}
        self.service.qr.return_value = PNG, 1_800_000_000.25
        self.service.write.return_value = {'status': 'STARTING', 'status_label': '正在启动'}
        self.gate = SimpleNamespace(error_response=lambda req, code:
            JSONResponse({'error': '请先完成身份认证'}, status_code=code))
        self.routes = api.build(self.gate, service=self.service, **kwargs)
        self.handlers = {route.path.rsplit('/', 1)[-1]: route.endpoint for route in self.routes}

    def call(self, action, **kwargs):
        return asyncio.run(self.handlers[action](request(**kwargs)))


@pytest.mark.parametrize('action', ['status', 'qr', 'start', 'logout'])
@pytest.mark.parametrize('denied', [{'principal': False}, {'authenticated': False}, {'recheck': False}])
def test_every_endpoint_authentication_and_recheck(action, denied):
    client = Client()
    result = client.call(action, fields={'confirm': '1', 'phrase': '退出登录'}, **denied)
    assert result.status_code == 403
    assert result.headers['cache-control'] == 'no-store'
    assert result.headers['x-content-type-options'] == 'nosniff'
    client.service.write.assert_not_called()
    if denied.get('principal') is False or denied.get('authenticated') is False:
        assert not client.service.mock_calls


def test_write_rechecks_before_body_and_service():
    client = Client()
    req = request(recheck=False)
    async def prohibited():
        pytest.fail('body read before recheck')
    req.body = prohibited
    assert asyncio.run(client.handlers['start'](req)).status_code == 403
    client.service.write.assert_not_called()


@pytest.mark.parametrize('action', ['status', 'qr'])
def test_get_rechecks_after_read_in_threadpool(action):
    client = Client()
    main_thread = threading.get_ident()
    events = []
    def read():
        assert threading.get_ident() != main_thread
        events.append('read')
        return (PNG, 123.5) if action == 'qr' else {'status': 'FAILED'}
    getattr(client.service, action).side_effect = read
    req = request()
    req.state.password_recheck = lambda: events.append('recheck') or True
    result = asyncio.run(client.handlers[action](req))
    assert result.status_code == 200
    assert events == ['read', 'recheck']


def test_png_headers_bytes_and_failed_recheck_never_returns_qr():
    client = Client()
    result = client.call('qr')
    assert result.body == PNG
    assert result.headers['content-type'] == 'image/png'
    assert result.headers['cache-control'] == 'no-store'
    assert result.headers['x-content-type-options'] == 'nosniff'
    assert result.headers['x-qr-fetched-at'] == '1800000000.25'
    denied = client.call('qr', recheck=False)
    assert denied.status_code == 403
    assert PNG not in denied.body
    assert 'x-qr-fetched-at' not in denied.headers


@pytest.mark.parametrize('action', ['start', 'logout'])
def test_rehearsal_write_gate_precedes_service(action):
    client = Client(allow_writes=False)
    result = client.call(action, fields={'confirm': '1', 'phrase': '退出登录'})
    assert result.status_code == 403
    assert '演练' in json.loads(result.body)['error']
    client.service.write.assert_not_called()


@pytest.mark.parametrize('kwargs', [
    {'form': {'confirm': ['1', '1']}}, {'form': {'confirm': '1'}},
    {'body': b'confirm=\xff'}, {'body': b'confirm=1&confirm=1'},
    {'body': b'x=' + b'x' * 8192}, {'fields': {'confirm': '1'}, 'headers': {}},
    {'fields': {}, 'headers': {'content-length': '8193'}},
    {'body': b'{}', 'headers': {'content-type': 'application/json', 'content-length': '2'}},
    {'form': {f'field{i}': ['x'] for i in range(41)}},
    {'fields': {'confirm': '1'}, 'query': {'session': 'fictional-untrusted-session'}},
])
def test_bad_forms_do_not_reach_service(kwargs):
    client = Client()
    result = client.call('start', **kwargs)
    assert result.status_code == 400
    assert any('\u4e00' <= c <= '\u9fff' for c in json.loads(result.body)['error'])
    client.service.write.assert_not_called()


def test_cached_and_streamed_forms_preserve_host_contract():
    client = Client()
    req = request(fields={'confirm': '1', 'csrf': 'fictional-csrf'})
    async def consumed():
        pytest.fail('must not reread middleware-consumed body')
    req.body = consumed
    assert asyncio.run(client.handlers['start'](req)).status_code == 200
    client.service.write.assert_called_once_with('start', {'confirm': '1'}, 'fictional-owner')
    client.service.write.reset_mock()
    req = request()
    async def stream():
        yield b'confirm=1&x='
        yield b'x' * 8192
    req.stream = stream
    assert asyncio.run(client.handlers['start'](req)).status_code == 400
    client.service.write.assert_not_called()


def test_urlencoded_body_runs_in_threadpool_and_passes_actor():
    client = Client()
    main_thread = threading.get_ident()
    def write(action, fields, actor):
        assert threading.get_ident() != main_thread
        assert (action, fields, actor) == ('logout', {'confirm': '1', 'phrase': '退出登录'}, 'fictional-owner')
        return {'status': 'STOPPED'}
    client.service.write.side_effect = write
    result = client.call('logout', body=urlencode({'confirm': '1', 'phrase': '退出登录', 'csrf': 'fictional-csrf'}).encode())
    assert result.status_code == 200


def test_login_errors_have_safe_code_retry_header_and_unknown_outcome():
    client = Client()
    client.service.write.side_effect = LoginError('操作过于频繁，请稍后再试', code='rate_limited', status_code=429, retry_after=10)
    result = client.call('start', fields={'confirm': '1'})
    assert result.status_code == 429
    assert result.headers['retry-after'] == '10'
    assert json.loads(result.body)['retry_after'] == 10
    client.service.write.side_effect = LoginError('结果不明，请稍后刷新状态', code='outcome_unknown', outcome='unknown')
    result = client.call('start', fields={'confirm': '1'})
    assert result.status_code == 503
    assert json.loads(result.body)['outcome'] == 'unknown'
    assert client.service.write.call_count == 2
    client.service.qr.side_effect = LoginError('当前已登录', code='already_working', status_code=409)
    result = client.call('qr')
    assert result.status_code == 409
    assert result.headers['content-type'].startswith('application/json')


@pytest.mark.parametrize('action', ['status', 'qr', 'start', 'logout'])
def test_unexpected_errors_never_leak_exception_details(action, caplog):
    client = Client()
    method = client.service.write if action in ('start', 'logout') else getattr(client.service, action)
    secret = 'fictional-sensitive-key'
    qr_data = 'fictional-sensitive-qr-base64'
    method.side_effect = RuntimeError('/fictional/private/' + secret + qr_data)
    result = client.call(action, fields={'confirm': '1'})
    assert result.status_code == 503
    combined = result.body.decode() + repr(dict(result.headers)) + caplog.text
    assert secret not in combined and qr_data not in combined and '/fictional/private/' not in combined
    if action in ('status', 'qr'):
        assert client.call(action, recheck=False).status_code == 403


def test_host_csrf_gate_blocks_writes_and_accepts_only_matching_form():
    client = Client()
    app = Starlette(routes=client.routes)
    async def host(request, call_next):
        request.state.password_principal = SimpleNamespace(username='fictional-owner')
        request.state.password_recheck = lambda: True
        request.state.session = SimpleNamespace(authenticated=True)
        if request.method == 'POST':
            form = parse_qs((await request.body()).decode(), keep_blank_values=True)
            if form.get('csrf') != ['fictional-csrf']:
                return JSONResponse({'error': '表单校验失败'}, status_code=403)
            request.state.form = form
        return await call_next(request)
    app.add_middleware(BaseHTTPMiddleware, dispatch=host)
    with TestClient(app) as http:
        for action in ('start', 'logout'):
            path = '/api/v1/system/whatsapp/' + action
            assert http.post(path, data={'confirm': '1'}).status_code == 403
            assert http.post(path, data={'confirm': '1', 'csrf': 'wrong'}).status_code == 403
        client.service.write.assert_not_called()
        assert http.post('/api/v1/system/whatsapp/start', data={'confirm': '1', 'csrf': 'fictional-csrf'}).status_code == 200
    client.service.write.assert_called_once_with('start', {'confirm': '1'}, 'fictional-owner')


def test_builder_keeps_injected_dependencies_and_fixed_routes(tmp_path):
    client = Client()
    fetch, clock, inbox, stat_fn = Mock(), Mock(), Mock(), Mock()
    with patch.object(api, 'WhatsAppLogin') as service:
        routes = api.build(client.gate, tmp_path / 'fictional.sqlite3', inbox=inbox, fetch=fetch,
            clock=clock, whatsapp_config_path=tmp_path / 'fictional.json', stat_fn=stat_fn,
            group_gid=731, allow_writes=False)
    service.assert_called_once_with(tmp_path / 'fictional.sqlite3', config_path=tmp_path / 'fictional.json',
        fetch=fetch, clock=clock, inbox=inbox, stat_fn=stat_fn, group_gid=731, allow_writes=False)
    assert len(routes) == 4
    assert {route.path.rsplit('/', 1)[-1] for route in routes} == {'status', 'qr', 'start', 'logout'}
    assert all(route.methods <= {'GET', 'HEAD'} for route in routes[:2])
    assert all(route.methods == {'POST'} for route in routes[2:])
