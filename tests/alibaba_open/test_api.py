"""Fictional requests exercise owner authentication, CSRF and form boundaries."""
import asyncio
import json
import socket
import threading
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.parse import urlencode

import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse

from console import v3_alibaba_open_api as api
from console.alibaba_open.errors import OpenError


def request(*, fields=None, body=b'', form=None, query=None, ids=None, principal=True,
            authenticated=True, recheck=True, headers=None):
    if fields is not None:
        body = urlencode(fields, doseq=True).encode()
    async def read_body():
        return body
    return SimpleNamespace(state=SimpleNamespace(
        password_principal=SimpleNamespace(username='fictional-owner') if principal else None,
        session=SimpleNamespace(authenticated=authenticated), password_recheck=lambda: recheck,
        form=form), path_params=ids or {}, query_params=query or {},
        headers=headers if headers is not None else {'content-type': 'application/x-www-form-urlencoded',
                                                     'content-length': str(len(body))}, body=read_body)


class Client:
    def __init__(self):
        self.service = Mock()
        self.service.read.return_value = {'items': []}
        self.service.write.return_value = {'ok': True}
        self.module = SimpleNamespace(error_response=lambda req, code: JSONResponse(
            {'error': '请先完成身份认证'}, status_code=code))
        self.routes = api.build(self.module, service=self.service)
        self.handlers = {(route.path.removeprefix('/api/v1/alibaba-open/'),
            'POST' if 'POST' in route.methods else 'GET'): route.endpoint for route in self.routes}

    def call(self, path, method='GET', **kwargs):
        return asyncio.run(self.handlers[path, method](request(**kwargs)))


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError('阿里控制台测试只能使用假服务，不得联网')
    monkeypatch.setattr(socket.socket, 'connect', blocked)
    monkeypatch.setattr(socket, 'create_connection', blocked)


@pytest.fixture
def client():
    return Client()


def test_all_routes_require_login_and_have_private_response_headers(client):
    assert len(client.routes) == 7
    for route in client.routes:
        for denied in ({'principal': False}, {'authenticated': False}, {'recheck': False}):
            result = asyncio.run(route.endpoint(request(fields={'confirm': '1'}, ids={'order_id': 'fictional-1'}, **denied)))
            assert result.status_code == 403
            assert result.headers['cache-control'] == 'no-store'
            assert result.headers['x-content-type-options'] == 'nosniff'
    client.service.write.assert_not_called()


def test_post_rechecks_before_body_and_immediately_before_business(client):
    req = request(fields={'confirm': '1'}, recheck=False)
    async def forbidden_body():
        pytest.fail('登录失效后不应读取表单')
    req.body = forbidden_body
    assert asyncio.run(client.handlers['poll', 'POST'](req)).status_code == 403
    req = request(fields={'confirm': '1'})
    checks = []
    def check():
        checks.append(True)
        return len(checks) == 1
    req.state.password_recheck = check
    assert asyncio.run(client.handlers['poll', 'POST'](req)).status_code == 403
    assert len(checks) == 2
    client.service.write.assert_not_called()


def test_post_uses_threadpool_authenticated_actor_and_removes_csrf(client):
    main_thread = threading.get_ident()
    calls = []
    req = request(fields={'confirm': '1', 'code': 'fictional-code', 'csrf': 'fictional-csrf'})
    def check():
        calls.append('recheck')
        return True
    req.state.password_recheck = check
    def write(*args):
        assert threading.get_ident() != main_thread
        calls.append('write')
        assert args == ('authorize', {'confirm': '1', 'code': 'fictional-code'}, 'fictional-owner')
        return {'ok': True}
    client.service.write.side_effect = write
    assert asyncio.run(client.handlers['authorize', 'POST'](req)).status_code == 200
    assert calls == ['recheck', 'recheck', 'write']


def test_read_rechecks_after_business_and_errors(client):
    check = Mock(return_value=False)
    req = request()
    req.state.password_recheck = check
    assert asyncio.run(client.handlers['status', 'GET'](req)).status_code == 403
    client.service.read.assert_called_once_with('status')
    check.assert_called_once()
    client.service.read.side_effect = RuntimeError('FICTIONAL-TOKEN-SENTINEL')
    req.state.password_recheck = lambda: True
    result = asyncio.run(client.handlers['status', 'GET'](req))
    assert result.status_code == 503
    assert 'FICTIONAL-TOKEN-SENTINEL' not in result.body.decode()


@pytest.mark.parametrize('path,fields', [('poll', {}), ('poll', {'confirm': '0'}),
    ('authorize', {'code': 'fictional', 'confirm': 'yes'}), ('settings', {'paused': '1'})])
def test_mutations_require_explicit_confirmation(client, path, fields):
    assert client.call(path, 'POST', fields=fields).status_code == 400
    client.service.write.assert_not_called()


@pytest.mark.parametrize('path,fields', [('poll', {'confirm': '1', 'sql': 'select 1'}),
    ('authorize', {'confirm': '1', 'code': 'fictional', 'who': 'someone-else'}),
    ('settings', {'confirm': '1', 'path': '../../fake'})])
def test_unknown_fields_cannot_choose_identity_or_paths(client, path, fields):
    assert client.call(path, 'POST', fields=fields).status_code == 400
    client.service.write.assert_not_called()


@pytest.mark.parametrize('code_value', ['', 'a b', 'a\nb', 'a' * 2049,
    'https://fake.invalid/callback?code=fictional'])
def test_authorize_rejects_missing_or_malformed_code_without_echo(client, code_value):
    result = client.call('authorize', 'POST', fields={'confirm': '1', 'code': code_value})
    assert result.status_code == 400
    assert 'fake.invalid' not in result.body.decode()
    client.service.write.assert_not_called()


@pytest.mark.parametrize('headers', [{}, {'content-type': 'application/json'},
    {'content-type': 'text/plain'}, {'content-type': 'multipart/form-data'}])
def test_only_urlencoded_forms_are_accepted(client, headers):
    assert client.call('poll', 'POST', fields={'confirm': '1'}, headers=headers).status_code == 400
    client.service.write.assert_not_called()


@pytest.mark.parametrize('body', [b'confirm=1&confirm=1', b'confirm=1&junk=' + b'a' * 8192,
    b'confirm=1&code=%FF'])
def test_duplicate_oversize_and_bad_encoding_forms_are_rejected(client, body):
    result = client.call('authorize', 'POST', body=body)
    assert result.status_code == 400
    client.service.write.assert_not_called()


def test_query_parameters_rejected_for_reads_and_writes(client):
    assert client.call('status', query={'path': '../../fake'}).status_code == 400
    assert client.call('poll', 'POST', fields={'confirm': '1'}, query={'force': '1'}).status_code == 400
    client.service.read.assert_not_called()
    client.service.write.assert_not_called()


def test_claim_accepts_safe_order_and_positive_deal_identity(client):
    result = client.call('candidates/{order_id}/claim', 'POST',
        fields={'confirm': '1', 'deal_id': '7'}, ids={'order_id': 'fictional-order-1'})
    assert result.status_code == 200
    client.service.write.assert_called_once_with('claim',
        {'confirm': '1', 'deal_id': '7', 'order_id': 'fictional-order-1'}, 'fictional-owner')


@pytest.mark.parametrize('order_id,deal_id', [('../fake', '7'), ('fake/7', '7'),
    ('a' * 129, '7'), ('fictional-order', '0'), ('fictional-order', '1 OR 1=1')])
def test_claim_rejects_paths_and_injection(client, order_id, deal_id):
    result = client.call('candidates/{order_id}/claim', 'POST', fields={'confirm': '1', 'deal_id': deal_id},
        ids={'order_id': order_id})
    assert result.status_code == 400
    client.service.write.assert_not_called()


def test_open_error_exposes_only_safe_status_and_never_retries(client):
    client.service.write.side_effect = OpenError('授权已失效，请重新进行浏览器授权', reauthorize=True)
    result = client.call('poll', 'POST', fields={'confirm': '1'})
    assert result.status_code == 409 and json.loads(result.body)['reauthorize']
    client.service.write.assert_called_once()
    client.service.write.reset_mock()
    client.service.write.side_effect = OpenError('授权结果还需核对，请重新读取状态', unknown=True)
    result = client.call('authorize', 'POST', fields={'confirm': '1', 'code': 'fictional'})
    assert result.status_code == 503 and json.loads(result.body)['unknown']
    client.service.write.assert_called_once()


def test_upstream_exception_never_leaks_credentials_or_code(client, caplog):
    sentinel = 'FICTIONAL-TOKEN-SENTINEL'
    client.service.write.side_effect = RuntimeError(sentinel)
    result = client.call('authorize', 'POST', fields={'confirm': '1', 'code': 'fictional-code'})
    assert result.status_code == 503
    assert sentinel not in result.body.decode() and sentinel not in caplog.text


def test_host_csrf_gate_blocks_post_before_candidate_write(client):
    app = Starlette(routes=client.routes)
    async def dispatch(csrf):
        sent = []
        body = b'confirm=1'
        async def receive():
            return {'type': 'http.request', 'body': body, 'more_body': False}
        async def send(message):
            sent.append(message)
        scope = dict(type='http', asgi={'version': '3.0'}, http_version='1.1', method='POST',
            scheme='http', path='/api/v1/alibaba-open/poll', raw_path=b'/api/v1/alibaba-open/poll',
            query_string=b'', root_path='', headers=[(b'content-type', b'application/x-www-form-urlencoded')],
            server=('fictional', 80), client=('fictional', 1), state=vars(request().state))
        async def host(scope, receive, send):
            if not csrf:
                return await JSONResponse({'error': '请刷新页面后重新确认'}, status_code=403)(scope, receive, send)
            await app(scope, receive, send)
        await host(scope, receive, send)
        return sent[0]['status']
    assert asyncio.run(dispatch(False)) == 403
    client.service.write.assert_not_called()
    assert asyncio.run(dispatch(True)) == 200
    client.service.write.assert_called_once()
