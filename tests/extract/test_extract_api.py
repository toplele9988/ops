"""Fictional owner requests through the real quotation-extraction routes."""
import asyncio
import json
import threading
from types import SimpleNamespace
from urllib.parse import parse_qs, urlencode
from unittest.mock import patch

import pytest
from starlette.applications import Starlette
from starlette.datastructures import QueryParams
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from starlette.testclient import TestClient

from console import v3_extract_api as api
from console.archive.service import Archive
from console.extract.service import Extract
from console.inbox.model import make_key
from console.roles.storage import RateLimited


def request(*, fields=None, form=None, body=b'', query=None, ids=None,
            principal=True, authenticated=True, recheck=True, headers=None):
    async def read_body():
        return body
    if headers is None:
        raw = urlencode(fields if fields is not None else form, doseq=True).encode() if fields is not None or form is not None else body
        headers = {'content-length': str(len(raw))}
    return SimpleNamespace(state=SimpleNamespace(
        password_principal=SimpleNamespace(username='fictional-owner') if principal else None,
        session=SimpleNamespace(authenticated=authenticated), password_recheck=lambda: recheck,
        form={k: [v] for k, v in fields.items()} if fields is not None else form),
        path_params=ids or {}, query_params=query or {}, headers=headers, body=read_body)


class Client:
    def __init__(self, service):
        self.gate = SimpleNamespace(error_response=lambda req, code: JSONResponse({'error': '请先完成身份认证'}, status_code=code))
        self.routes = api.build(self.gate, service=service)
        self.handlers = {(r.path.removeprefix('/api/v1/extract/'), 'POST' if 'POST' in r.methods else 'GET'): r.endpoint for r in self.routes}

    def call(self, path, method='GET', **kwargs):
        return asyncio.run(self.handlers[path, method](request(**kwargs)))

    def data(self, path, method='GET', **kwargs):
        result = self.call(path, method, **kwargs)
        assert result.status_code == 200, result.body.decode()
        assert result.headers['cache-control'] == 'no-store'
        assert result.headers['x-content-type-options'] == 'nosniff'
        return json.loads(result.body)


@pytest.fixture
def api_env(tmp_path):
    inbox = SimpleNamespace(read=lambda now: dict(ready=True, conversations={}), detail=lambda key, now: None)
    path = tmp_path / 'fictional-archive.sqlite3'
    service = Extract(path, inbox=inbox, runner=None, clock=lambda: 1894262400)
    return SimpleNamespace(path=path, service=service, inbox=inbox, client=Client(service))


def test_all_routes_auth_recheck_and_headers(api_env):
    client = api_env.client
    assert len(client.routes) == 5
    for route in client.routes:
        for denied in ({'principal': False}, {'authenticated': False}, {'recheck': False}):
            result = asyncio.run(route.endpoint(request(ids={'id': '1'}, fields={}, **denied)))
            assert result.status_code == 403
            assert result.headers['cache-control'] == 'no-store'
            assert result.headers['x-content-type-options'] == 'nosniff'
    assert not api_env.path.exists()
    req = request(recheck=False)
    async def forbidden_body():
        pytest.fail('recheck must precede form consumption')
    req.body = forbidden_body
    with patch.object(api_env.service, 'write', side_effect=AssertionError('unauthorized write')):
        assert asyncio.run(client.handlers['scan', 'POST'](req)).status_code == 403


def test_get_rechecks_after_read_and_errors_in_threadpool(api_env):
    main_thread = threading.get_ident()
    order = []
    req = request()
    req.state.password_recheck = lambda: order.append('recheck') or True
    def read(*args):
        assert threading.get_ident() != main_thread
        order.append('read')
        return dict(items=[])
    with patch.object(api_env.service, 'read', read):
        assert asyncio.run(api_env.client.handlers['suggestions', 'GET'](req)).status_code == 200
    assert order == ['read', 'recheck']
    assert api_env.client.call('suggestions', query={'status': 'invalid'}, recheck=False).status_code == 403


@pytest.mark.parametrize('kwargs', [
    dict(form={'days': ['1', '2']}), dict(form={'days': '1'}), dict(body=b'days=\xff'),
    dict(body=b'days=1&days=2'), dict(body=b'x=' + b'x' * 8192),
    dict(fields={'conversation_key': '字' * 500}), dict(fields={}, headers={}),
    dict(fields={}, headers={'content-length': '8193'}), dict(fields={'status': 'confirmed'}),
    dict(body=b'{}', headers={'content-length': '2', 'content-type': 'application/json'}),
    dict(body='&'.join('f%d=x' % i for i in range(41)).encode()),
])
def test_invalid_forms_never_scan_and_return_chinese(api_env, kwargs):
    with patch.object(api_env.service, 'scan', side_effect=AssertionError('invalid request reached scan')):
        result = api_env.client.call('scan', 'POST', **kwargs)
    assert result.status_code == 400
    assert any('\u4e00' <= c <= '\u9fff' for c in json.loads(result.body)['error'])
    assert not api_env.path.exists()


def test_stream_limits_and_cached_form(api_env):
    req = request()
    async def stream():
        yield b'csrf='
        yield b'x' * 8192
    req.stream = stream
    assert asyncio.run(api_env.client.handlers['scan', 'POST'](req)).status_code == 400
    req = request(fields={'csrf': 'fictional-csrf'})
    async def no_body():
        pytest.fail('cached form must not reread consumed body')
    req.body = no_body
    assert asyncio.run(api_env.client.handlers['scan', 'POST'](req)).status_code == 200
    assert api_env.client.call('scan', 'POST', body=b'csrf=fictional-csrf').status_code == 200


def test_empty_read_is_nonmutating_and_unknown_ids_are_404(api_env):
    assert api_env.client.data('suggestions')['items'] == []
    summary = api_env.client.data('summary')
    assert summary['pending'] == {'offer': 0, 'freight': 0}
    assert summary['last_scan_at'] is None
    assert not api_env.path.exists()
    for path in ('suggestions/{id}/accept', 'suggestions/{id}/dismiss'):
        assert api_env.client.call(path, 'POST', ids={'id': '999'}, fields={}).status_code == 404
        assert api_env.client.call(path, 'POST', ids={'id': 'invalid'}, fields={}).status_code == 400


@pytest.mark.parametrize('query', [
    {'status': 'confirmed'}, {'kind': 'invalid'}, {'page': '0'}, {'page': '-1'},
    {'secret': 'fictional'}, QueryParams('status=pending&status=accepted'),
    {'conversation_key': 'invalid'},
])
def test_query_validation(api_env, query):
    assert api_env.client.call('suggestions', query=query).status_code == 400
    assert api_env.client.call('summary', query={'page': '1'}).status_code == 400
    assert not api_env.path.exists()


def test_failed_operations_hide_raw_exception(api_env):
    for operation, path, method in (('read', 'summary', 'GET'), ('write', 'scan', 'POST')):
        with patch.object(api_env.service, operation, side_effect=RuntimeError('/fictional/private/API-SECRET')):
            result = api_env.client.call(path, method, fields={})
        assert result.status_code == 503
        assert 'API-SECRET' not in result.body.decode() and '/fictional' not in result.body.decode()


def test_rate_limit_returns_rules_and_retry_header(api_env):
    error = RateLimited(60)
    error.result = dict(scanned=1, added=1, remaining=0, items=[{'source': 'rules'}])
    with patch.object(api_env.service, 'write', side_effect=error):
        result = api_env.client.call('scan', 'POST', fields={})
    assert result.status_code == 429 and result.headers['retry-after'] == '60'
    assert json.loads(result.body)['result']['items'] == [{'source': 'rules'}]


def test_host_csrf_rejects_before_scan_and_principal_cannot_be_forged(api_env):
    app = Starlette(routes=api_env.client.routes)
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
        assert http.post('/api/v1/extract/scan', data={}).status_code == 403
        assert http.post('/api/v1/extract/scan', data={'csrf': 'wrong'}).status_code == 403
        assert not api_env.path.exists()
        assert http.post('/api/v1/extract/scan', data={'csrf': 'fictional-csrf', 'who': 'forged'}).status_code == 400
        assert http.post('/api/v1/extract/scan', data={'csrf': 'fictional-csrf'}).status_code == 200


def test_builder_preserves_injected_inbox_clock_and_runner(api_env):
    with patch.object(api, 'shared_inbox', side_effect=AssertionError('injected inbox must be used')):
        routes = api.build(api_env.client.gate, api_env.path, inbox=api_env.inbox, runner=None, clock=lambda: 1894262400)
    assert len(routes) == 5 and not api_env.path.exists()


def supplier_conversation(api_env, text, name='SUP-123 虚构报价供应商'):
    now = api_env.service.clock()
    key = make_key('conversation', 'wechat', 'fictional-account', 'fictional-supplier')
    conv = dict(key=key, channel='wechat', channel_label='微信', account='fictional-account',
                buyer_name=name, counterpart_role='supplier', is_group=True,
                first_seen=now, last_seen=now, n_messages=1,
                messages=[dict(role='buyer', text=text, sent_at=now)])
    api_env.inbox.read = lambda at: dict(ready=True, conversations={key: conv})
    api_env.inbox.detail = lambda lookup, at: conv if lookup == key else None
    return key


def test_offer_scan_list_accept_and_repeated_accept_roundtrip(api_env):
    key = supplier_conversation(api_env, '虚构光束灯 1280元/台，含税，本周有效')
    scanned = api_env.client.data('scan', 'POST', fields={'conversation_key': key})
    assert scanned['scanned'] == 1 and scanned['added'] == 1
    suggestion = api_env.client.data('suggestions', query={'kind': 'offer'})['items'][0]
    with api_env.service.store.read() as db:
        assert db.execute('SELECT count(*) FROM offers').fetchone()[0] == 0
    assert api_env.client.call('suggestions/{id}/accept', 'POST', ids={'id': suggestion['id']},
                               fields={'create_product': '1'}).status_code == 400
    accepted = api_env.client.data('suggestions/{id}/accept', 'POST', ids={'id': suggestion['id']},
                                  fields={'create_product': '1', 'product_name': '虚构光束灯', 'confirm_valid_until': '1'})
    repeated = api_env.client.data('suggestions/{id}/accept', 'POST', ids={'id': suggestion['id']}, fields={})
    assert repeated['reused'] and repeated['offer_id'] == accepted['offer_id']
    with api_env.service.store.read() as db:
        offer = db.execute('SELECT * FROM offers').fetchone()
        assert offer['status'] == 'confirmed' and offer['price_fen'] == 128000
        assert offer['valid_until'] == suggestion['suggested_valid_until']
        assert db.execute('SELECT status FROM products').fetchone()[0] == 'candidate'
    decided = api_env.client.data('suggestions', query={'status': 'accepted'})['items'][0]
    assert decided['decided_by'] == 'fictional-owner'
    assert api_env.client.data('summary')['pending'] == {'offer': 0, 'freight': 0}


def test_freight_accept_and_dismiss_roundtrip(api_env):
    key = supplier_conversation(api_env, '空运 USD 45/kg，最低收费 USD 500，到美国，本周有效', '虚构货代物流')
    forwarder = Archive(api_env.path).write('party_create', None,
        {'kind': 'forwarder', 'name': '虚构货代'}, 'fictional-owner')['id']
    suggestion = api_env.client.data('scan', 'POST', fields={'conversation_key': key})['items'][0]
    assert suggestion['kind'] == 'freight'
    accepted = api_env.client.data('suggestions/{id}/accept', 'POST', ids={'id': suggestion['id']},
        fields={'forwarder_id': str(forwarder), 'origin': '虚构起运地', 'service': '虚构航空服务',
                'channel': 'platform_alibaba', 'confirm_valid_until': '1'})
    with api_env.service.store.read() as db:
        row = db.execute('SELECT * FROM freight_quotes WHERE id=?', (accepted['freight_quote_id'],)).fetchone()
        assert row['status'] == 'confirmed' and row['unit'] == 'kg' and row['price_fen'] == 4500
        assert row['channel'] == 'platform_alibaba'
        assert row['destination_country'] == '美国' and row['min_charge_fen'] == 50000
    supplier_conversation(api_env, '虚构备选灯 180元/台，本周有效')
    pending = api_env.client.data('scan', 'POST', fields={})['items'][0]
    api_env.client.data('suggestions/{id}/dismiss', 'POST', ids={'id': pending['id']}, fields={})
    api_env.client.data('suggestions/{id}/dismiss', 'POST', ids={'id': pending['id']}, fields={})
    dismissed = api_env.client.data('suggestions', query={'status': 'dismissed'})['items'][0]
    assert dismissed['decided_by'] == 'fictional-owner'
    assert api_env.client.call('suggestions/{id}/accept', 'POST', ids={'id': pending['id']}, fields={}).status_code == 409
