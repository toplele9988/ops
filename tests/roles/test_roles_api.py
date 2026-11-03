import asyncio
import json
import threading
from types import SimpleNamespace
from urllib.parse import parse_qs, urlencode
from unittest.mock import Mock, patch

import pytest
from starlette.applications import Starlette
from starlette.datastructures import QueryParams
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from starlette.testclient import TestClient

from console import v3_roles_api as api
from console.roles.common import Config
from .conftest import answer


def request(*, fields=None, form=None, body=b'', query=None, ids=None, principal=True,
            authenticated=True, recheck=True, headers=None):
    async def read_body():
        return body
    if headers is None:
        headers = {'content-length': str(len(urlencode(fields if fields is not None else form, doseq=True).encode())
                    if fields is not None or form is not None else len(body))}
    return SimpleNamespace(state=SimpleNamespace(password_principal=SimpleNamespace(username='fictional-owner') if principal else None,
        session=SimpleNamespace(authenticated=authenticated), password_recheck=lambda: recheck,
        form={k: [v] for k, v in fields.items()} if fields is not None else form),
        path_params=ids or {}, query_params=query or {}, headers=headers, body=read_body)


class Client:
    def __init__(self, roles):
        self.gate = SimpleNamespace(error_response=lambda req, code: JSONResponse({'error': '请先完成身份认证'}, status_code=code))
        self.routes = api.build(self.gate, service=roles)
        self.handlers = {(r.path.removeprefix('/api/v1/'), 'POST' if 'POST' in r.methods else 'GET'): r.endpoint for r in self.routes}

    def call(self, path, method='GET', **kwargs):
        return asyncio.run(self.handlers[path, method](request(**kwargs)))

    def data(self, path, method='GET', **kwargs):
        result = self.call(path, method, **kwargs)
        assert result.status_code == 200, result.body.decode()
        assert result.headers['cache-control'] == 'no-store'
        assert result.headers['x-content-type-options'] == 'nosniff'
        return json.loads(result.body)


def test_triage_list_exposes_suggested_name_and_accept_uses_it(env):
    key = env.conv(name='虚构晨星SUP-0042乐初供应商沟通')
    client = Client(env.roles)
    client.data('triage/scan', 'POST', fields={})
    suggestion = client.data('triage')['items'][0]
    assert suggestion['suggested_party_name'] == '虚构晨星'
    accepted = client.data('triage/{id}/accept', 'POST', ids={'id': str(suggestion['id'])}, fields={})
    assert accepted['deal_id'] is None
    assert env.deals.read('identity_lookup', {'key': key})['party']['name'] == '虚构晨星'


def test_c3_api_duplicate_acceptance_refreshes_list_and_ignore_is_dismissed(env):
    older = env.conv(name='虚构 API 买家', age=2)
    newer = env.conv(name='虚构 API 买家', age=1)
    ignored = env.conv(name='虚构行业群', channel='wechat')
    client = Client(env.roles)
    scanned = client.data('triage/scan', 'POST', fields={})['items']
    assert scanned[0]['conversation_key'] == newer and scanned[-1]['conversation_key'] == ignored
    items = {s['conversation_key']: s for s in scanned}
    blocked = client.call('triage/{id}/accept', 'POST', ids={'id': items[older]['id']}, fields={})
    assert blocked.status_code == 409
    accepted = client.data('triage/{id}/accept', 'POST', ids={'id': items[newer]['id']}, fields={})
    pending = {s['conversation_key']: s for s in client.data('triage')['items']}
    assert pending[older]['action'] == 'link' and pending[older]['suggested_deal_id'] == accepted['deal_id']
    client.data('triage/{id}/accept', 'POST', ids={'id': items[ignored]['id']}, fields={})
    dismissed = client.data('triage', query={'status': 'dismissed'})['items']
    assert len(dismissed) == 1 and dismissed[0]['conversation_key'] == ignored


def test_all_endpoints_auth_headers_and_recheck_before_body(env):
    client = Client(env.roles)
    assert len(client.routes) == 13
    for route in client.routes:
        for denied in ({'principal': False}, {'authenticated': False}, {'recheck': False}):
            result = asyncio.run(route.endpoint(request(ids={'id': '1'}, fields={}, **denied)))
            assert result.status_code == 403
            assert result.headers['cache-control'] == 'no-store'
            assert result.headers['x-content-type-options'] == 'nosniff'
    assert not env.path.exists()
    req = request(recheck=False, ids={'id': '1'})
    async def forbidden_body():
        pytest.fail('read body before recheck')
    req.body = forbidden_body
    with patch.object(env.roles, 'write', side_effect=AssertionError('write before recheck')):
        assert asyncio.run(client.handlers['deals/{id}/secretary/run', 'POST'](req)).status_code == 403


def test_get_rechecks_errors_and_io_runs_in_threadpool(env):
    client = Client(env.roles)
    calls = []
    req = request()
    req.state.password_recheck = lambda: calls.append('recheck') or True
    def read(*args):
        assert threading.get_ident() != main_thread
        calls.append('read')
        return dict(items=[])
    main_thread = threading.get_ident()
    with patch.object(env.roles, 'read', read):
        assert asyncio.run(client.handlers['roles', 'GET'](req)).status_code == 200
    assert calls == ['read', 'recheck']
    assert client.call('roles/runs', query={'role': 'bad'}, recheck=False).status_code == 403


@pytest.mark.parametrize('kwargs', [
    dict(form={'action': ['a', 'b']}), dict(form={'action': 'a'}), dict(body=b'action=\xff'),
    dict(body=b'action=a&action=b'), dict(body=b'x=' + b'x' * 8192),
    dict(fields={'title': '字' * 500}), dict(fields={}, headers={}),
    dict(fields={}, headers={'content-length': '8193'}), dict(fields={'stage': 'paid'}),
    dict(body=b'{}', headers={'content-length': '2', 'content-type': 'application/json'}),
])
def test_form_rejections_are_chinese_and_never_run_roles(env, kwargs):
    client = Client(env.roles)
    with patch.object(env.roles, 'run_brief', side_effect=AssertionError('must reject first')):
        result = client.call('brief/run', 'POST', **kwargs)
    assert result.status_code == 400
    assert any('\u4e00' <= c <= '\u9fff' for c in json.loads(result.body)['error'])
    assert not env.path.exists()


def test_stream_limit_cached_form_and_urlencoded_fallback(env):
    client = Client(env.roles)
    req = request()
    async def stream():
        yield b'csrf='
        yield b'x' * 8192
    req.stream = stream
    assert asyncio.run(client.handlers['brief/run', 'POST'](req)).status_code == 400
    req = request(fields={'csrf': 'fictional-csrf'})
    async def no_body():
        pytest.fail('cached form must not reread consumed body')
    req.body = no_body
    assert asyncio.run(client.handlers['brief/run', 'POST'](req)).status_code == 200
    client.data('patrol/run', 'POST', body=b'csrf=fictional-csrf')


def test_unknown_ids_empty_states_and_query_validation(env):
    client = Client(env.roles)
    for path in ('deals/{id}/secretary',):
        assert client.call(path, ids={'id': '9999'}).status_code == 404
    for path in ('deals/{id}/secretary/run', 'triage/{id}/accept', 'triage/{id}/dismiss', 'patrol/alerts/{id}/dismiss'):
        assert client.call(path, 'POST', ids={'id': '9999'}, fields={}).status_code == 404
    did = env.deal()
    assert json.loads(client.call('deals/{id}/secretary', ids={'id': did}).body)['error'] == '还没有分析'
    assert client.call('brief/today').status_code == 404
    assert client.call('patrol/alerts', query={'deal_id': '9999'}).status_code == 404
    for query in ({'limit': '201'}, {'role': 'bad'}, {'secret': 'x'}, QueryParams('role=brief&role=triage')):
        assert client.call('roles/runs', query=query).status_code == 400
    assert client.call('triage', query={'status': 'bad'}).status_code == 400
    assert client.call('patrol/alerts', query={'include_dismissed': 'true'}).status_code == 400


def test_errors_hide_stacks_paths_and_model_raw_text(env):
    client = Client(env.roles)
    for operation, path, method in (('read', 'roles', 'GET'), ('write', 'brief/run', 'POST')):
        with patch.object(env.roles, operation, side_effect=RuntimeError('/fictional/private/API-SECRET')):
            result = client.call(path, method, fields={})
        assert result.status_code == 503
        assert 'private' not in result.body.decode() and 'API-SECRET' not in result.body.decode()


def test_all_business_routes_and_history_filters(env):
    client = Client(env.roles)
    did = env.deal(stage='awaiting_payment')
    env.clock.advance(3 * 86400)
    run = client.data('deals/{id}/secretary/run', 'POST', ids={'id': did}, fields={})
    assert client.data('deals/{id}/secretary', ids={'id': did})['id'] == run['id']
    client.data('patrol/run', 'POST', fields={})
    alert = client.data('patrol/alerts', query={'deal_id': str(did)})['items'][0]
    client.data('patrol/alerts/{id}/dismiss', 'POST', ids={'id': alert['id']}, fields={})
    assert client.data('patrol/alerts', query={'include_dismissed': '1'})['items'][0]['dismissed_by'] == 'fictional-owner'
    env.conv(name='虚构新客户')
    suggestion = client.data('triage/scan', 'POST', fields={})['items'][0]
    client.data('triage/{id}/accept', 'POST', ids={'id': suggestion['id']}, fields={})
    assert client.data('triage', query={'status': 'accepted'})['items'][0]['decided_by'] == 'fictional-owner'
    env.conv(name='虚构另一个客户')
    suggestion = client.data('triage/scan', 'POST', fields={})['items'][0]
    client.data('triage/{id}/dismiss', 'POST', ids={'id': suggestion['id']}, fields={})
    brief = client.data('brief/run', 'POST', fields={})
    assert client.data('brief/today')['id'] == brief['id']
    assert len(client.data('roles/runs', query={'role': 'brief', 'limit': '1'})['items']) == 1
    roles = client.data('roles')['items']
    assert {r['level'] for r in roles} <= {'L0', 'L1'}
    assert all(r['runs_24h'] > 0 for r in roles if r['key'] not in ('audit', 'extract', 'listing'))
    assert next(r for r in roles if r['key'] == 'audit')['runs_24h'] == 0
    extractor = next(r for r in roles if r['key'] == 'extract')
    assert extractor['runs_24h'] == 0 and extractor['name'] == '报价抽取员' and extractor['level'] == 'L0'
    listing = next(r for r in roles if r['key'] == 'listing')
    assert listing['runs_24h'] == 0 and listing['name'] == '发品员' and listing['level'] == 'L1'


def test_rate_limit_response_includes_rule_output_and_retry_after(env):
    runner = Mock(return_value=answer('现状：请确认\n下一步：1\n风险：无'))
    roles = env.service(runner=runner, config=Config(timezone='UTC', per_minute=3, per_hour=3))
    client = Client(roles)
    first, second = env.deal(), env.deal()
    client.data('deals/{id}/secretary/run', 'POST', ids={'id': first}, fields={})
    result = client.call('deals/{id}/secretary/run', 'POST', ids={'id': second}, fields={})
    assert result.status_code == 429 and result.headers['retry-after'] == '3600'
    data = json.loads(result.body)
    assert '上限' in data['error'] and data['result']['output_text']


def test_host_csrf_gate_blocks_write_and_valid_form_reaches_routes(env):
    client = Client(env.roles)
    app = Starlette(routes=client.routes)
    async def host(request, call_next):
        request.state.password_principal = SimpleNamespace(username='fictional-owner')
        request.state.password_recheck = lambda: True
        request.state.session = SimpleNamespace(authenticated=True)
        if request.method == 'POST':
            body = await request.body()
            form = parse_qs(body.decode(), keep_blank_values=True)
            if form.get('csrf') != ['fictional-csrf']:
                return JSONResponse({'error': '表单校验失败'}, status_code=403)
            request.state.form = form
        return await call_next(request)
    app.add_middleware(BaseHTTPMiddleware, dispatch=host)
    with TestClient(app) as http:
        assert http.post('/api/v1/brief/run', data={}).status_code == 403
        assert http.post('/api/v1/brief/run', data={'csrf': 'wrong'}).status_code == 403
        assert not env.path.exists()
        assert http.post('/api/v1/brief/run', data={'csrf': 'fictional-csrf'}).status_code == 200


def test_builder_signature_preserves_injected_dependencies(env):
    client = Client(env.roles)
    with patch.object(api, 'shared_inbox', side_effect=AssertionError('must use injected inbox')):
        routes = api.build(client.gate, env.path, inbox=env.inbox, hub=env.hub, runner=None, clock=env.clock)
    assert len(routes) == 13
