import asyncio
import json
from types import SimpleNamespace
from urllib.parse import parse_qs, urlencode

from starlette.applications import Starlette
from starlette.datastructures import QueryParams
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from starlette.testclient import TestClient

from console import v3_listing_api as api


def request(*, fields=None, body=b'', query=None, ids=None, principal=True, authenticated=True,
            recheck=True, headers=None):
    async def read_body():
        return body
    raw = urlencode(fields or {}, doseq=True).encode() if fields is not None else body
    headers = headers or {'content-length': str(len(raw)), 'content-type': 'application/x-www-form-urlencoded'}
    return SimpleNamespace(state=SimpleNamespace(
        password_principal=SimpleNamespace(username='fictional-owner') if principal else None,
        session=SimpleNamespace(authenticated=authenticated), password_recheck=lambda: recheck,
        form={key: [value] for key, value in fields.items()} if fields is not None else None),
        path_params=ids or {}, query_params=query or {}, headers=headers, body=read_body)


class Client:
    def __init__(self, env):
        self.env = env
        self.gate = SimpleNamespace(error_response=lambda req, code:
            JSONResponse({'error': '请先完成身份认证'}, status_code=code))
        self.routes = api.build(self.gate, service=env.service)
        self.handlers = {(route.path.removeprefix('/api/v1/listing/'),
                          'POST' if 'POST' in route.methods else 'GET'): route.endpoint for route in self.routes}

    def call(self, path, method='GET', **kwargs):
        return asyncio.run(self.handlers[(path, method)](request(**kwargs)))

    def data(self, path, method='GET', **kwargs):
        result = self.call(path, method, **kwargs)
        assert result.status_code == 200, result.body.decode()
        assert result.headers['cache-control'] == 'no-store'
        assert result.headers['x-content-type-options'] == 'nosniff'
        return json.loads(result.body)


def test_routes_auth_recheck_unknown_ids_and_allowlisted_filters(env):
    client = Client(env)
    assert len(client.routes) == 7
    assert client.data('drafts')['items'] == []
    assert client.data('products-ready')['total'] == 1
    for route in client.routes:
        for denied in ({'principal': False}, {'authenticated': False}, {'recheck': False}):
            result = asyncio.run(route.endpoint(request(ids={'id': '999999'}, fields={}, **denied)))
            assert result.status_code == 403
            assert result.headers['cache-control'] == 'no-store'
            assert result.headers['x-content-type-options'] == 'nosniff'
    for path, method, kwargs in (
        ('drafts/{id}', 'GET', dict(ids={'id': '999999'})),
        ('drafts/{id}/update', 'POST', dict(ids={'id': '999999'}, fields={'title': 'X'})),
        ('drafts/{id}/status', 'POST', dict(ids={'id': '999999'}, fields={'status': 'draft'})),
        ('drafts/{id}/export', 'GET', dict(ids={'id': '999999'})),
    ):
        assert client.call(path, method, **kwargs).status_code == 404
    assert client.call('drafts', query={'secret': 'x'}).status_code == 400
    assert client.call('drafts', query=QueryParams('status=draft&status=ready')).status_code == 400
    assert client.call('drafts/{id}', ids={'id': 'invalid'}).status_code == 400


def test_api_generation_export_and_csrf_host_gate(env):
    client = Client(env)
    app = Starlette(routes=client.routes)

    async def host(request, call_next):
        request.state.password_principal = SimpleNamespace(username='fictional-owner')
        request.state.password_recheck = lambda: True
        request.state.session = SimpleNamespace(authenticated=True)
        if request.method == 'POST':
            parsed = parse_qs((await request.body()).decode('utf8'), keep_blank_values=True)
            if parsed.get('csrf') != ['fictional-csrf']:
                return JSONResponse({'error': '表单校验失败'}, status_code=403)
            request.state.form = parsed
        return await call_next(request)

    app.add_middleware(BaseHTTPMiddleware, dispatch=host)
    with TestClient(app) as http:
        denied = http.post('/api/v1/listing/drafts/generate', data={'product_id': str(env.product), 'channel': 'alibaba'})
        assert denied.status_code == 403
        generated = http.post('/api/v1/listing/drafts/generate', data={
            'csrf': 'fictional-csrf', 'product_id': str(env.product), 'channel': 'alibaba'})
        assert generated.status_code == 200, generated.text
        draft = generated.json()
        assert draft['status'] == 'draft' and draft['generation_source'] == 'ai'
        text = http.get(f"/api/v1/listing/drafts/{draft['id']}/export?format=text")
        csv = http.get(f"/api/v1/listing/drafts/{draft['id']}/export?format=csv")
        assert text.status_code == csv.status_code == 200
        assert '101.23' not in text.text + csv.text
        assert 'FICTIONAL_FACTORY_SECRET' not in text.text + csv.text
        assert http.get(f"/api/v1/listing/drafts/{draft['id']}/export?format=json").status_code == 400
        assert http.post(f"/api/v1/listing/drafts/{draft['id']}/update", data={
            'csrf': 'fictional-csrf', 'unknown': 'value'}).status_code == 400


def test_description_form_has_separate_64k_allowance(env):
    client = Client(env)
    draft = env.generate()
    # The application field limit is characters; URL form encoding can be larger in octets.
    description = '描写' * 30000
    response = client.call('drafts/{id}/update', 'POST', ids={'id': str(draft['id'])},
                           fields={'description': description})
    assert response.status_code == 200
    assert json.loads(response.body)['description'] == description
    too_long = client.call('drafts/{id}/update', 'POST', ids={'id': str(draft['id'])},
                           fields={'description': '描写' * 32769})
    assert too_long.status_code == 400

