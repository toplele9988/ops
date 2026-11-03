import asyncio
import importlib.util
import json
import threading
from types import SimpleNamespace
from urllib.parse import parse_qs, urlencode
from unittest.mock import patch

import pytest
from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from starlette.testclient import TestClient

from console import v3_audit_api as api, v3_roles_api
from console.audit.common import finding
from console.audit.service import Audit
from console.roles.service import Roles
from console.roles.storage import RateLimited


def request(*, fields=None, form=None, body=b'', query=None, channel=None, principal=True,
            authenticated=True, recheck=True, headers=None):
    async def read_body():
        return body
    if headers is None:
        headers = {'content-length': str(len(urlencode(fields if fields is not None else form, doseq=True).encode())
                    if fields is not None or form is not None else len(body))}
    return SimpleNamespace(state=SimpleNamespace(password_principal=SimpleNamespace(username='fictional-owner') if principal else None,
        session=SimpleNamespace(authenticated=authenticated), password_recheck=lambda: recheck,
        form={k: [v] for k, v in fields.items()} if fields is not None else form),
        path_params={'channel': channel} if channel is not None else {}, query_params=query or {}, headers=headers, body=read_body)


@pytest.fixture
def client(tmp_path):
    gate = SimpleNamespace(error_response=lambda req, code: JSONResponse({'error': '请先登录'}, status_code=code))
    checks = {'wechat': [('wechat.test', '虚构项', lambda ctx: finding('wechat.test', '虚构项', 'ok', 0, '完整', '系统后台'))]}
    audit = Audit(tmp_path / 'archive.db', checks=checks)
    routes = api.build(gate, service=audit)
    handlers = {r.name: r.endpoint for r in routes}
    def call(name, **kwargs):
        return asyncio.run(handlers[name](request(**kwargs)))
    return SimpleNamespace(gate=gate, service=audit, routes=routes, handlers=handlers, call=call)


@pytest.mark.parametrize('denied', [{'principal': False}, {'authenticated': False}, {'recheck': False}])
def test_all_routes_authentication_and_headers(client, denied):
    for route in client.routes:
        response = asyncio.run(route.endpoint(request(fields={}, **denied)))
        assert response.status_code == 403
        assert response.headers['cache-control'] == 'no-store'
        assert response.headers['x-content-type-options'] == 'nosniff'
    assert not client.service.archive_path.exists()


def test_post_recheck_before_body_get_recheck_after_io_and_errors(client):
    req = request(recheck=False)
    async def bad_body():
        pytest.fail('body before recheck')
    req.body = bad_body
    assert asyncio.run(client.handlers['audit_run'](req)).status_code == 403
    calls, main_thread = [], threading.get_ident()
    req = request()
    req.state.password_recheck = lambda: calls.append('recheck') or True
    def read(*args):
        assert threading.get_ident() != main_thread
        calls.append('read')
        return {'items': []}
    with patch.object(client.service, 'read', read):
        assert asyncio.run(client.handlers['audit_all'](req)).status_code == 200
    assert calls == ['read', 'recheck']
    assert client.call('audit_channel', channel='unknown', recheck=False).status_code == 403


@pytest.mark.parametrize('kwargs', [
    dict(form={'x': ['a', 'b']}), dict(form={'x': 'a'}), dict(body=b'x=\xff'),
    dict(body=b'csrf=a&csrf=b'), dict(body=b'x=' + b'x' * 8192),
    dict(fields={}, headers={}), dict(fields={}, headers={'content-length': '8193'}),
    dict(fields={'channel': 'wechat'}),
    dict(body=b'{}', headers={'content-length': '2', 'content-type': 'application/json'}),
])
def test_invalid_forms_never_write(client, kwargs):
    result = client.call('audit_run', **kwargs)
    assert result.status_code == 400
    assert not client.service.archive_path.exists()


def test_scan_post_persists_and_roles_filter_reads_run(client):
    assert client.call('audit_channel', channel='alibaba').status_code == 200
    assert client.call('audit_channel', channel='unknown').status_code == 404
    assert client.call('audit_all', query={'secret': 'fictional-secret'}).status_code == 400
    assert not client.service.archive_path.exists()
    result = client.call('audit_run', body=b'csrf=fictional-csrf')
    assert result.status_code == 200
    run = json.loads(result.body)['run']
    assert run['created_by'] == 'fictional-owner' and run['role'] == 'audit'
    roles = Roles(client.service.archive_path, runner=None)
    routes = v3_roles_api.build(client.gate, service=roles)
    endpoint = next(r.endpoint for r in routes if r.path == '/api/v1/roles/runs')
    result = asyncio.run(endpoint(request(query={'role': 'audit'})))
    assert result.status_code == 200 and len(json.loads(result.body)['items']) == 1


def test_csrf_host_gate_and_valid_post(client):
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
        assert http.post('/api/v1/audit/run', data={}).status_code == 403
        assert http.post('/api/v1/audit/run', data={'csrf': 'wrong'}).status_code == 403
        assert not client.service.archive_path.exists()
        assert http.post('/api/v1/audit/run', data={'csrf': 'fictional-csrf'}).status_code == 200


def test_rate_limit_and_raw_error_are_handled_like_roles(client):
    with patch.object(client.service, 'run', side_effect=RateLimited(33)):
        response = client.call('audit_run', fields={})
        assert response.status_code == 429 and response.headers['retry-after'] == '33'
    with patch.object(client.service, 'read', side_effect=RuntimeError('fictional-token')):
        response = client.call('audit_all')
        assert response.status_code == 503 and b'fictional-token' not in response.body


def test_rehearsal_mount_uses_temporary_role_store_and_explicit_read_sources(tmp_path, monkeypatch):
    from pathlib import Path
    monkeypatch.setenv('IMGW_REHEARSAL', '1')
    monkeypatch.setenv('REAL_ARCHIVE', str(tmp_path / 'nonexistent.db'))
    spec = importlib.util.spec_from_file_location('audit_rehearsal_test', Path(__file__).resolve().parents[2] / 'scripts/rehearsal_server.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    from console import v3_icbu_api
    monkeypatch.setattr(v3_icbu_api, 'build', lambda *a, **kw: [])
    for name in ('v3_inspiration_api', 'v3_packing_api', 'v3_inbox_api', 'v3_customers_api', 'v3_channels_api',
                 'v3_outbox_api', 'v3_media_api', 'v3_kb_api', 'v3_archive_api', 'v3_deals_api', 'v3_app'):
        monkeypatch.setattr(getattr(module, name), 'build', lambda *a, **kw: [])
    with patch.object(api, 'build', wraps=api.build) as build:
        routes = module.build_routes(str(tmp_path))
    assert {'/api/v1/audit', '/api/v1/audit/{channel}', '/api/v1/audit/run'} <= {r.path for r in routes}
    assert build.call_args.args[1] == str(tmp_path / 'archive.sqlite3')
    assert set(build.call_args.kwargs) == {'inbound_path','probe_path','wechat_path','discovery_path',
        'wechat_config_path','whatsapp_path','whatsapp_config_path'}
    assert not list(tmp_path.iterdir())
