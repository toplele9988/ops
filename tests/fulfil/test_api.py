"""Fictional requests exercise the shared authentication/CSRF/form boundary."""
import asyncio
from datetime import date, datetime, timezone
import importlib.util
import json
from pathlib import Path
import socket
import threading
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlencode

import pytest
from starlette.applications import Starlette
from starlette.datastructures import QueryParams
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from starlette.testclient import TestClient

from console import v3_fulfil_api as api
from console.archive.validation import Missing
from console.archive.service import Archive
from console.fulfil.service import Fulfil


def request(*, fields=None, form=None, body=b'', query=None, ids=None, principal=True,
            authenticated=True, recheck=True, headers=None):
    async def read_body():
        return body
    if headers is None:
        headers = {'content-length': str(len(urlencode(fields if fields is not None else form, doseq=True).encode())
                    if fields is not None or form is not None else len(body))}
    return SimpleNamespace(state=SimpleNamespace(
        password_principal=SimpleNamespace(username='fictional-owner') if principal else None,
        session=SimpleNamespace(authenticated=authenticated), password_recheck=lambda: recheck,
        form={key: [value] for key, value in fields.items()} if fields is not None else form),
        path_params=ids or {}, query_params=query or {}, headers=headers, body=read_body)


class Client:
    def __init__(self, service=None):
        self.service = service or Mock()
        if service is None:
            self.service.read.return_value = {'items': []}
            self.service.write.return_value = {'ok': True, 'id': 1}
        self.gate = SimpleNamespace(error_response=lambda req, code: JSONResponse(
            {'error': '请先完成身份认证'}, status_code=code))
        self.routes = api.build(self.gate, service=self.service)
        self.handlers = {(route.path.removeprefix('/api/v1/'), 'POST' if 'POST' in route.methods else 'GET'):
                         route.endpoint for route in self.routes}

    def call(self, path, method='GET', **kwargs):
        return asyncio.run(self.handlers[path, method](request(**kwargs)))


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def blocked(*args, **kwargs):
        pytest.fail('fulfil API tests must not connect to any external service')
    monkeypatch.setattr(socket.socket, 'connect', blocked)
    monkeypatch.setattr(socket, 'create_connection', blocked)


@pytest.fixture
def client():
    return Client()


def test_all_routes_authentication_and_response_headers(client):
    assert len(client.routes) == 17
    for route in client.routes:
        for denied in ({'principal': False}, {'authenticated': False}, {'recheck': False}):
            result = asyncio.run(route.endpoint(request(ids={'id': '1', 'pid': '1', 'iid': '1', 'node': 'inspection'},
                                                       fields={}, **denied)))
            assert result.status_code == 403, route.path
            assert result.headers['cache-control'] == 'no-store'
            assert result.headers['x-content-type-options'] == 'nosniff'
    client.service.write.assert_not_called()


def test_recheck_happens_before_body_and_again_before_confirmation(client):
    req = request(ids={'pid': '1'}, recheck=False)
    async def forbidden_body():
        pytest.fail('body must not be read before authentication recheck')
    req.body = forbidden_body
    assert asyncio.run(client.handlers['payments/{pid}/confirm', 'POST'](req)).status_code == 403
    calls = []
    req = request(ids={'pid': '1'}, body=b'confirm=1')
    def check():
        calls.append('recheck')
        return len(calls) == 1
    req.state.password_recheck = check
    assert asyncio.run(client.handlers['payments/{pid}/confirm', 'POST'](req)).status_code == 403
    assert calls == ['recheck', 'recheck']
    client.service.write.assert_not_called()


def test_read_and_write_use_threadpool_and_current_principal(client):
    calls = []
    main_thread = threading.get_ident()
    req = request(ids={'pid': '9'}, fields={'confirm': '1', 'csrf': 'fictional-csrf'})
    req.state.password_recheck = lambda: calls.append('recheck') or True
    def write(*args):
        assert threading.get_ident() != main_thread
        calls.append('write')
        assert args == ('payment_confirm', 9, {'confirm': '1'}, 'fictional-owner', None)
        return {'ok': True}
    client.service.write.side_effect = write
    response = asyncio.run(client.handlers['payments/{pid}/confirm', 'POST'](req))
    assert response.status_code == 200
    assert calls == ['recheck', 'recheck', 'write']
    calls.clear()
    req = request(ids={'id': '9'})
    req.state.password_recheck = lambda: calls.append('recheck') or False
    def read(*args):
        assert threading.get_ident() != main_thread
        calls.append('read')
        return {'items': []}
    client.service.read.side_effect = read
    assert asyncio.run(client.handlers['deals/{id}/payments', 'GET'](req)).status_code == 403
    assert calls == ['read', 'recheck']


@pytest.mark.parametrize('kwargs', [
    {'form': {'note': ['a', 'b']}}, {'form': {'note': 'a'}}, {'body': b'note=\xff'},
    {'body': b'note=%FF'}, {'body': b'note=a&note=b'}, {'body': b'note=' + b'x' * 8192},
    {'fields': {'note': '字' * 1000}}, {'fields': {}, 'headers': {}},
    {'fields': {}, 'headers': {'content-length': '8193'}},
    {'fields': {}, 'headers': {'content-length': '-1'}},
    {'body': b'{}', 'headers': {'content-type': 'application/json', 'content-length': '2'}},
    {'body': b'&'.join(b'x%d=1' % number for number in range(41))},
    {'fields': {'confirmed_by': 'forged-user'}}, {'fields': {'status': 'confirmed'}},
])
def test_invalid_forms_never_reach_business_service(client, kwargs):
    result = client.call('deals/{id}/payments', 'POST', ids={'id': '1'}, **kwargs)
    assert result.status_code == 400
    assert any('\u4e00' <= char <= '\u9fff' for char in json.loads(result.body)['error'])
    client.service.write.assert_not_called()


def test_stream_limit_and_cached_host_form(client):
    req = request(ids={'pid': '1'})
    async def stream():
        yield b'note='
        yield b'x' * 8192
    req.stream = stream
    assert asyncio.run(client.handlers['payments/{pid}/void', 'POST'](req)).status_code == 400
    req = request(ids={'pid': '1'}, fields={'note': '虚构作废', 'csrf': 'fictional-csrf'})
    async def no_body():
        pytest.fail('a verified cached host form must not reread the consumed body')
    req.body = no_body
    assert asyncio.run(client.handlers['payments/{pid}/void', 'POST'](req)).status_code == 200
    assert client.service.write.call_args.args[2] == {'note': '虚构作废'}


@pytest.mark.parametrize('path,query', [
    ('issues/summary', {'unknown': '1'}), ('issues/summary', {'start': '2030-02-30'}),
    ('issues/summary', {'start': '2030-02-01', 'end': '2030-01-01'}),
    ('issues/summary', QueryParams('start=2030-01-01&start=2030-01-02')),
    ('reorder/leads', {'days': '0'}), ('reorder/leads', {'days': '-1'}),
    ('reorder/leads', {'days': '30,,60'}), ('reorder/leads', {'days': '30,30'}),
    ('reorder/leads', {'days': '30.0'}), ('reorder/leads', {'days': '3651'}),
    ('reorder/leads', {'days': ','.join(str(i) for i in range(1, 14))}),
    ('reorder/leads', QueryParams('days=30&days=60')),
    ('issues/{iid}/draft', {'target': 'customer'}), ('fulfilment/board', {'send': '1'}),
])
def test_bad_queries_rejected_without_service_read(client, path, query):
    assert client.call(path, ids={'iid': '1'}, query=query).status_code == 400
    client.service.read.assert_not_called()


def test_query_values_forwarded_once_and_draft_post_only_reads(client):
    assert client.call('issues/summary', query={'start': '2030-01-01', 'end': '2030-02-01'}).status_code == 200
    assert client.service.read.call_args.args == ('issue_summary', {'start': '2030-01-01', 'end': '2030-02-01'}, None)
    assert client.call('reorder/leads', query={'days': '90,30,60'}).status_code == 200
    assert client.service.read.call_args.args == ('reorder_leads', {'days': (30, 60, 90)}, None)
    assert client.call('issues/{iid}/draft', 'POST', ids={'iid': '7'}, fields={'target': 'forwarder'}).status_code == 200
    assert client.service.read.call_args.args == ('issue_draft', {'target': 'forwarder'}, 7)
    client.service.write.assert_not_called()


def test_node_scoped_key_and_global_id_are_unambiguous(client):
    assert client.call('deals/{id}/fulfilment/{node}/update', 'POST',
                       ids={'id': '2', 'node': 'inspection'}, fields={'status': 'done'}).status_code == 200
    assert client.service.write.call_args.args == ('node_update', 2, {'status': 'done'}, 'fictional-owner', 'inspection')
    assert client.call('fulfilment/{node}/update', 'POST', ids={'node': '8'}, fields={'status': 'doing'}).status_code == 200
    assert client.service.write.call_args.args == ('node_update_id', 8, {'status': 'doing'}, 'fictional-owner', None)


def test_unknown_ids_and_fixed_errors_are_not_leaked(client):
    for method, path, ids, operation in [
        ('GET', 'deals/{id}/payments', {'id': '999999'}, 'read'),
        ('POST', 'payments/{pid}/confirm', {'pid': '999999'}, 'write'),
        ('POST', 'issues/{iid}/note', {'iid': '999999'}, 'write'),
    ]:
        with patch.object(client.service, operation, side_effect=Missing('未找到该记录')):
            assert client.call(path, method, ids=ids, fields={}).status_code == 404
        with patch.object(client.service, operation, side_effect=RuntimeError('/fictional/private/TOKEN')):
            result = client.call(path, method, ids=ids, fields={})
            assert result.status_code == 503
            assert 'private' not in result.body.decode() and 'TOKEN' not in result.body.decode()
    assert client.call('deals/{id}/payments', ids={'id': '-1'}).status_code == 400
    assert client.call('deals/{id}/payments', ids={'id': '-1'}, recheck=False).status_code == 403


def test_host_csrf_gate_prevents_confirmation_and_actor_forgery(client):
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
        for fields in ({'confirm': '1'}, {'confirm': '1', 'csrf': 'wrong'}):
            assert http.post('/api/v1/payments/1/confirm', data=fields).status_code == 403
        client.service.write.assert_not_called()
        assert http.post('/api/v1/payments/1/confirm', data={
            'confirm': '1', 'csrf': 'fictional-csrf', 'who': 'forged-user'}).status_code == 400
        client.service.write.assert_not_called()
        assert http.post('/api/v1/payments/1/confirm', data={
            'confirm': '1', 'csrf': 'fictional-csrf'}).status_code == 200
    assert client.service.write.call_args.args == ('payment_confirm', 1, {'confirm': '1'}, 'fictional-owner', None)


def test_builder_injects_path_clock_and_today_without_opening_store(tmp_path):
    client = Client()
    clock, today = Mock(), Mock()
    with patch.object(api, 'Fulfil') as service:
        assert len(api.build(client.gate, tmp_path / 'fictional.db', clock=clock, today=today)) == 17
    service.assert_called_once_with(tmp_path / 'fictional.db', clock=clock, today=today)
    assert not list(tmp_path.iterdir())


def test_rehearsal_automounts_fulfilment_on_disposable_archive(tmp_path, monkeypatch):
    monkeypatch.setenv('IMGW_REHEARSAL', '1')
    monkeypatch.setenv('REAL_ARCHIVE', str(tmp_path / 'missing-fictional.db'))
    path = Path(__file__).resolve().parents[2] / 'scripts/rehearsal_server.py'
    spec = importlib.util.spec_from_file_location('fulfil_rehearsal_test', path)
    rehearsal = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rehearsal)
    for name in ('v3_inspiration_api', 'v3_packing_api', 'v3_inbox_api', 'v3_customers_api',
                 'v3_channels_api', 'v3_outbox_api', 'v3_media_api', 'v3_kb_api', 'v3_archive_api',
                 'v3_deals_api', 'v3_app'):
        monkeypatch.setattr(getattr(rehearsal, name), 'build', lambda *args, **kwargs: [])
    with patch.object(api, 'build', wraps=api.build) as build:
        routes = rehearsal.build_routes(str(tmp_path))
    assert build.call_args.args[1] == str(tmp_path / 'archive.sqlite3')
    assert {'/api/v1/fulfilment/board', '/api/v1/deals/{id}/payments',
            '/api/v1/reorder/leads', '/api/v1/issues/summary'} <= {route.path for route in routes}
    assert not list(tmp_path.iterdir())


@pytest.fixture
def real_api(tmp_path):
    service = Fulfil(tmp_path / 'fictional-http.sqlite3', today=lambda: date(2030, 5, 1),
                     clock=lambda: datetime(2030, 5, 1, tzinfo=timezone.utc).timestamp())
    archive = Archive(store=service.store)
    customer = archive.write('party_create', None, {'kind': 'customer', 'name': '虚构星河客户'}, 'fictional-owner')['id']
    did = service.deals.write('create', None, {'title': '虚构灯具订单', 'customer_party_id': str(customer)}, 'fictional-owner')['id']
    service.deals.write('item', did, {'name': '虚构舞台灯', 'qty': '2', 'target_price': '987.65',
                                    'note': '虚构内部价格说明'}, 'fictional-owner')
    client = Client(service)
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
        def post(path, fields=None):
            return http.post('/api/v1/' + path, data=dict(fields or {}, csrf='fictional-csrf'))
        yield SimpleNamespace(service=service, http=http, post=post, did=did)


def test_real_http_payment_node_issue_and_reorder_loop_never_sends(real_api):
    env = real_api
    with patch('console.outbox.store.Store._write') as outbox, \
         patch('console.outbox.bridge_client.BridgeClient.deliver') as delivery, \
         patch('console.outbox.bridge_client.local_post') as channel:
        response = env.post(f'deals/{env.did}/payments', dict(kind='deposit', amount_fen='12345', currency='USD',
                            method='wire', received_on='2030-05-01', evidence='虚构电汇回单文字'))
        assert response.status_code == 200, response.text
        pid = response.json()['id']
        assert env.service.deals.read('detail', row_id=env.did)['stage'] == 'inquiry'
        assert env.post(f'payments/{pid}/confirm').status_code == 400
        assert env.post(f'payments/{pid}/confirm', {'confirm': '1'}).status_code == 200
        paid = env.service.deals.read('detail', row_id=env.did)
        assert paid['stage'] == 'paid' and paid['status'] == 'won' and paid['won_at']
        assert env.post(f'payments/{pid}/confirm', {'confirm': '1'}).status_code == 200
        assert env.service.deals.read('detail', row_id=env.did) == paid
        payment = env.http.get(f'/api/v1/deals/{env.did}/payments').json()['items'][0]
        assert payment['status'] == 'confirmed' and payment['confirmed_by'] == 'fictional-owner'

        response = env.post(f'deals/{env.did}/fulfilment/inspection/update', {'status': 'done', 'inspection_result': 'pass'})
        assert response.status_code == 200, response.text
        nid = response.json()['id']
        assert env.post(f'fulfilment/{nid}/update', {'note': '虚构验货记录'}).status_code == 200
        progress = env.http.get(f'/api/v1/deals/{env.did}/fulfilment').json()
        assert any(s['stage'] == 'shipped' for s in progress['suggestions'])
        assert env.service.deals.read('detail', row_id=env.did)['stage'] == 'paid'
        assert env.http.get('/api/v1/fulfilment/board').json()['total'] == 1

        response = env.post(f'deals/{env.did}/issues', {'kind': 'damage', 'description': '虚构外箱破损，请核对包装'})
        assert response.status_code == 200, response.text
        iid = response.json()['id']
        assert env.post(f'issues/{iid}/note', {'note': '虚构核对记录'}).status_code == 200
        assert env.post(f'issues/{iid}/update', {'status': 'waiting_party'}).status_code == 200
        draft = env.http.get(f'/api/v1/issues/{iid}/draft', params={'target': 'forwarder'}).json()
        assert draft['copy_only'] and draft['source'] == 'rules' and '破损' in draft['text']
        assert draft['needs_manual_details'] and '虚构外箱' not in draft['text']
        assert env.post(f'issues/{iid}/draft', {'target': 'supplier'}).status_code == 200
        assert env.post(f'issues/{iid}/update', {'status': 'resolved', 'resolution': '虚构补发包装',
                        'impact_kind': 'compensation', 'impact_fen': '1500', 'currency': 'USD'}).status_code == 200
        assert env.post(f'issues/{iid}/update', {'status': 'closed'}).status_code == 200
        issues = env.http.get(f'/api/v1/deals/{env.did}/issues').json()['items']
        assert len(issues) == 1 and issues[0]['status'] == 'closed' and len(issues[0]['notes']) >= 3
        summary = env.http.get('/api/v1/issues/summary', params={'start': '2030-05-01', 'end': '2030-05-01'}).json()
        assert summary['total'] == 1 and summary['unresolved'] == 0

        assert env.post(f'deals/{env.did}/fulfilment/signoff/update', {'status': 'done', 'signed_on': '2030-03-01'}).status_code == 200
        leads = env.http.get('/api/v1/reorder/leads', params={'days': '30,60,90'}).json()['items']
        assert len(leads) == 1 and leads[0]['deal_id'] == env.did
        response = env.post(f'deals/{env.did}/reorder')
        assert response.status_code == 200, response.text
        copied = env.service.deals.read('detail', row_id=response.json()['id'])
        assert copied['stage'] == 'inquiry' and '返单' in copied['title']
        assert copied['customer']['id'] == paid['customer']['id']
        assert copied['items'][0]['name'] == '虚构舞台灯' and copied['items'][0]['target_price'] is None
        assert copied['items'][0]['note'] is None
        with env.service.store.read() as db:
            assert db.execute('SELECT COUNT(*) FROM quote_rounds WHERE deal_id=?', (copied['id'],)).fetchone()[0] == 0
        assert env.http.get('/api/v1/reorder/leads').json()['items'] == []
        outbox.assert_not_called()
        delivery.assert_not_called()
        channel.assert_not_called()


@pytest.mark.parametrize('method,path,fields', [
    ('GET', 'deals/999999/payments', {}), ('GET', 'deals/999999/fulfilment', {}),
    ('GET', 'deals/999999/issues', {}), ('GET', 'issues/999999/draft', {}),
    ('POST', 'deals/999999/payments', {'kind': 'full', 'amount_fen': '100', 'currency': 'USD',
                                     'method': 'wire', 'received_on': '2030-01-01'}),
    ('POST', 'payments/999999/confirm', {'confirm': '1'}), ('POST', 'payments/999999/void', {}),
    ('POST', 'deals/999999/fulfilment/po/update', {'status': 'doing'}),
    ('POST', 'fulfilment/999999/update', {'status': 'doing'}),
    ('POST', 'deals/999999/issues', {'kind': 'quality', 'description': '虚构问题'}),
    ('POST', 'issues/999999/update', {'status': 'waiting_party'}),
    ('POST', 'issues/999999/note', {'note': '虚构处理'}), ('POST', 'issues/999999/draft', {}),
    ('POST', 'deals/999999/reorder', {}),
])
def test_real_http_missing_records_are_404(real_api, method, path, fields):
    response = real_api.post(path, fields) if method == 'POST' else real_api.http.get('/api/v1/' + path)
    assert response.status_code == 404, response.text


@pytest.mark.parametrize('amount', ['0', '-1', '+1', '1.0', '1e2', ' 1', '1 ', '１', '1000000000000'])
def test_real_http_strict_payment_amount_rejects_without_ledger_or_stage_change(real_api, amount):
    response = real_api.post(f'deals/{real_api.did}/payments', dict(kind='full', amount_fen=amount,
                            currency='USD', method='wire', received_on='2030-05-01'))
    assert response.status_code == 400, response.text
    assert real_api.service.read('payments', row_id=real_api.did)['items'] == []
    assert real_api.service.deals.read('detail', row_id=real_api.did)['stage'] == 'inquiry'
