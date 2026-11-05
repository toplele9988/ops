"""HTTP boundaries use the existing principal, host CSRF and 8KB form contract."""
import asyncio
import json
import threading
from types import SimpleNamespace
from urllib.parse import parse_qs, urlencode
from unittest.mock import Mock

import pytest
from starlette.applications import Starlette
from starlette.datastructures import QueryParams
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from starlette.testclient import TestClient

from console import v3_lang_api as api
from console.lang.storage import RateLimited


def request(*, fields=None, form=None, body=b'', query=None, principal=True,
            authenticated=True, recheck=True, headers=None):
    async def read_body():
        return body
    if headers is None:
        headers = {'content-length': str(len(urlencode(fields if fields is not None else form, doseq=True).encode())
                    if fields is not None or form is not None else len(body))}
    return SimpleNamespace(state=SimpleNamespace(password_principal=SimpleNamespace(username='fictional-owner') if principal else None,
        session=SimpleNamespace(authenticated=authenticated), password_recheck=lambda: recheck,
        form={k: [v] for k, v in fields.items()} if fields is not None else form),
        path_params={}, query_params=query or {}, headers=headers, body=read_body)


class Client:
    def __init__(self, service):
        self.service = service
        self.gate = SimpleNamespace(error_response=lambda req, code: JSONResponse({'error': '请先完成身份认证'}, status_code=code))
        self.routes = api.build(self.gate, service=service)
        self.handlers = {(r.path.removeprefix('/api/v1/'), 'POST' if 'POST' in r.methods else 'GET'): r.endpoint for r in self.routes}

    def call(self, path, method='GET', **kwargs):
        return asyncio.run(self.handlers[path, method](request(**kwargs)))


@pytest.fixture
def client():
    return Client(SimpleNamespace(read=Mock(return_value={'items': []}), write=Mock(return_value={'saved': True})))


def test_all_routes_authentication_and_write_recheck_before_body(client):
    assert len(client.routes) == 11
    for route in client.routes:
        for denied in ({'principal': False}, {'authenticated': False}, {'recheck': False}):
            result = asyncio.run(route.endpoint(request(fields={}, **denied)))
            assert result.status_code == 403
            assert result.headers['cache-control'] == 'no-store'
            assert result.headers['x-content-type-options'] == 'nosniff'
        if 'POST' in route.methods:
            req = request(recheck=False)
            async def forbidden_body():
                pytest.fail('身份复查通过前不得读取正文')
            req.body = forbidden_body
            assert asyncio.run(route.endpoint(req)).status_code == 403
    client.service.write.assert_not_called()


def test_missing_auth_dependencies_deny_before_any_service_call(client):
    for route in client.routes:
        for field, value in (('password_recheck', None), ('password_recheck', 'invalid'),
                             ('session', None), ('session', SimpleNamespace())):
            req = request()
            setattr(req.state, field, value)
            assert asyncio.run(route.endpoint(req)).status_code == 403
    client.service.read.assert_not_called()
    client.service.write.assert_not_called()


def test_get_runs_in_worker_then_rechecks_even_on_parameter_errors(client):
    calls = []
    main = threading.get_ident()
    def read(*args):
        assert threading.get_ident() != main
        calls.append('read')
        return {'items': []}
    client.service.read = read
    req = request()
    req.state.password_recheck = lambda: calls.append('recheck') or True
    result = asyncio.run(client.handlers['lang/settings', 'GET'](req))
    assert result.status_code == 200
    assert calls == ['read', 'recheck']
    req = request(query={'unknown': 'x'}, recheck=False)
    assert asyncio.run(client.handlers['lang/settings', 'GET'](req)).status_code == 403


@pytest.mark.parametrize('kwargs', [
    dict(form={'action': ['a', 'b']}), dict(form={'action': 'a'}), dict(body=b'x=\xff'),
    dict(body=b'x=a&x=b'), dict(body=b'x=' + b'x' * 8192), dict(fields={}, headers={}),
    dict(fields={}, headers={'content-length': '8193'}),
    dict(body=b'{}', headers={'content-length': '2', 'content-type': 'application/json'}),
])
def test_form_rejections_are_chinese_before_service_write(client, kwargs):
    result = client.call('lang/settings', 'POST', **kwargs)
    assert result.status_code == 400
    assert any('\u4e00' <= c <= '\u9fff' for c in json.loads(result.body)['error'])
    client.service.write.assert_not_called()


def test_chunked_body_limit_cached_form_and_urlencoded_fallback(client):
    req = request()
    async def stream():
        yield b'csrf='
        yield b'x' * 8192
    req.stream = stream
    assert asyncio.run(client.handlers['lang/settings', 'POST'](req)).status_code == 400
    req = request(fields={'csrf': 'fictional-csrf', 'my_language': 'zh-CN'})
    async def no_body():
        pytest.fail('中间件缓存表单不得重复读取已消费的正文')
    req.body = no_body
    assert asyncio.run(client.handlers['lang/settings', 'POST'](req)).status_code == 200
    assert client.call('lang/settings', 'POST', body=b'my_language=zh-CN').status_code == 200
    args = client.service.write.call_args.args
    assert args[-1] == 'fictional-owner'
    assert 'csrf' not in args[-2]


def test_errors_hide_private_paths_and_raw_model_output(client):
    for method, path, operation in (('GET', 'lang/settings', 'read'), ('POST', 'lang/settings', 'write')):
        setattr(client.service, operation, Mock(side_effect=RuntimeError('/fictional/private/MODEL-RAW-TEXT')))
        result = client.call(path, method, fields={})
        assert result.status_code == 503
        assert 'private' not in result.body.decode() and 'MODEL-RAW-TEXT' not in result.body.decode()
        assert result.headers['cache-control'] == 'no-store'


def test_quota_response_exposes_retry_after_and_partial_success(client):
    partial = {'items': [{'index': 0, 'complete': True, 'text': '虚构已完成译文'}]}
    client.service.write.side_effect = RateLimited(37, result=partial)
    result = client.call('lang/translate', 'POST', fields={})
    assert result.status_code == 429 and result.headers['retry-after'] == '37'
    data = json.loads(result.body)
    assert data['result'] == partial and data['retry_after'] == 37
    assert '额度' in data['error']


def test_write_io_runs_in_worker_after_recheck(client):
    main, calls = threading.get_ident(), []
    req = request(fields={})
    req.state.password_recheck = lambda: calls.append('recheck') or True
    def write(*args):
        assert threading.get_ident() != main
        calls.append('write')
        return {'saved': True}
    client.service.write = write
    assert asyncio.run(client.handlers['lang/settings', 'POST'](req)).status_code == 200
    assert calls == ['recheck', 'write']


def test_duplicate_and_unknown_query_fields_rejected(client):
    for query in ({'unknown': 'x'}, QueryParams('conversation_key=a&conversation_key=b')):
        assert client.call('lang/contact', query=query).status_code == 400
    client.service.read.assert_not_called()


def test_host_csrf_blocks_write_without_calling_language_service(client):
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
        assert http.post('/api/v1/lang/settings', data={}).status_code == 403
        assert http.post('/api/v1/lang/settings', data={'csrf': 'wrong'}).status_code == 403
        client.service.write.assert_not_called()
        response = http.post('/api/v1/lang/settings', data={'csrf': 'fictional-csrf', 'my_language': 'zh-CN'})
        assert response.status_code == 200


def workflow_call(inputs, service, fields):
    gate = SimpleNamespace(error_response=lambda req, code: JSONResponse({'error': '请先完成身份认证'}, status_code=code))
    routes = api.build(gate, lang_path=inputs.path, service=service)
    route = next(r for r in routes if r.path.endswith('/' + ('compose' if 'text' in fields else 'translate')))
    return asyncio.run(route.endpoint(request(fields=fields)))


def test_automatic_translation_quota_is_http_200_status_with_partial_results(inputs):
    from test_lang_service import BlockRunner, service
    key = inputs.inbox.conversation(['Fictional incoming line ' + chr(97 + i) for i in range(9)])
    runner = BlockRunner()
    lang = service(inputs, runner, hourly_limit=1)
    result = workflow_call(inputs, lang, dict(conversation_key=key, mode='auto'))
    payload = json.loads(result.body)
    assert result.status_code == 200 and payload['quota_exhausted']
    assert payload['hint'] == '本小时翻译额度已用完，60 分钟后继续'
    assert sum(item['complete'] for item in payload['items']) == 8
    assert 'error' not in payload
    assert result.headers['cache-control'] == 'no-store'


def test_existing_translate_route_serves_cached_previews_without_model_calls(inputs):
    from test_lang_service import BlockRunner, service, write
    runner = BlockRunner()
    key = inputs.inbox.conversation(['Fictional incoming request'])
    lang = service(inputs, runner)
    write(lang, 'translate', conversation_key=key, mode='auto')
    calls = len(runner.calls)
    result = workflow_call(inputs, lang, dict(mode='preview', conversation_keys=json.dumps([key])))
    assert result.status_code == 200
    assert json.loads(result.body)['previews'][0]['conversation_key'] == key
    assert len(runner.calls) == calls


def test_compose_http_allows_unknown_language_and_checks_english(inputs):
    from test_lang_service import service
    from test_translate_workflow import JsonRunner
    key = inputs.inbox.conversation(['👍'])
    result = workflow_call(inputs, service(inputs, JsonRunner()), dict(conversation_key=key, text='请确认虚构资料 FX-730'))
    payload = json.loads(result.body)
    assert result.status_code == 200
    assert payload['native_language'] is None
    assert [v['language'] for v in payload['versions']] == ['en']
    assert payload['validation']['ok'] and payload['back_validation']['ok']
