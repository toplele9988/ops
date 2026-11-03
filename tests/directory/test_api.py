"""Directory HTTP contract: fictional services only, with networking disabled."""
import sys
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))
from rehearsal_isolation import isolate_builders

import asyncio
from contextlib import ExitStack
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
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

from console import v3_directory_api as api
from console.directory import RefreshFailed, RefreshLimited


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError('network forbidden')
    monkeypatch.setattr('socket.socket.connect', denied)


def request(*, fields=None, form=None, body=b'', query=None, principal=True,
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
        headers=headers, query_params=query or {}, path_params={}, body=read_body)


class Client:
    def __init__(self, **options):
        self.service = Mock()
        self.service.list.return_value = {'items': [], 'total': 0, 'page': 1, 'page_size': 50}
        self.service.summary.return_value = {'total': 0, 'conclusion': '通讯录里没有群'}
        self.service.refresh.return_value = {'refreshed': True, 'contacts': 0}
        self.gate = SimpleNamespace(error_response=lambda req, code:
            JSONResponse({'error': '请先完成身份认证'}, status_code=code))
        self.routes = api.build(self.gate, service=self.service, **options)
        self.handlers = {route.name.removeprefix('directory_wechat_'): route.endpoint for route in self.routes}

    def call(self, action, **kwargs):
        return asyncio.run(self.handlers[action](request(**kwargs)))


def test_exact_route_methods_and_sanitized_headers():
    client = Client()
    assert {(route.path, frozenset(route.methods)) for route in client.routes} == {
        ('/api/v1/directory/wechat', frozenset(('GET', 'HEAD'))),
        ('/api/v1/directory/wechat/summary', frozenset(('GET', 'HEAD'))),
        ('/api/v1/directory/wechat/refresh', frozenset(('POST',))),
    }
    for action in client.handlers:
        result = client.call(action, fields={'confirm': '1'})
        assert result.status_code == 200
        assert result.headers['cache-control'] == 'no-store'
        assert result.headers['x-content-type-options'] == 'nosniff'


@pytest.mark.parametrize('action', ('list', 'summary', 'refresh'))
@pytest.mark.parametrize('denied', ({'principal': False}, {'authenticated': False}, {'recheck': False}))
def test_every_endpoint_authentication_and_recheck(action, denied):
    client = Client()
    result = client.call(action, fields={'confirm': '1'}, **denied)
    assert result.status_code == 403
    assert result.headers['cache-control'] == 'no-store'
    assert result.headers['x-content-type-options'] == 'nosniff'
    client.service.refresh.assert_not_called()
    if denied.get('principal') is False or denied.get('authenticated') is False:
        assert not client.service.mock_calls


def test_post_rechecks_before_reading_body_or_refreshing():
    client = Client()
    req = request(recheck=False)
    async def prohibited():
        pytest.fail('read body before recheck')
    req.body = prohibited
    assert asyncio.run(client.handlers['refresh'](req)).status_code == 403
    client.service.refresh.assert_not_called()


@pytest.mark.parametrize('action', ('list', 'summary'))
def test_reads_in_threadpool_then_rechecks_even_on_source_failure(action):
    client = Client()
    main_thread = threading.get_ident()
    events = []
    def read(*args):
        assert threading.get_ident() != main_thread
        events.append('read')
        raise RuntimeError('/fictional/private/API-SECRET')
    getattr(client.service, action).side_effect = read
    req = request()
    req.state.password_recheck = lambda: events.append('recheck') or False
    result = asyncio.run(client.handlers[action](req))
    assert result.status_code == 403 and events == ['read', 'recheck']
    assert b'API-SECRET' not in result.body
    assert client.call(action, query={'unexpected': 'x'}, recheck=False).status_code == 403


def test_filter_and_page_values_are_validated_before_service():
    client = Client()
    assert client.call('list').status_code == 200
    client.service.list.assert_called_with(dict(kind='group', state='', q='', page=1, page_size=50))
    assert client.call('list', query={'kind': 'private', 'state': 'not_enrolled', 'q': ' 虚构 ',
                                     'page': '2', 'page_size': '10'}).status_code == 200
    client.service.list.assert_called_with(dict(kind='private', state='not_enrolled', q='虚构', page=2, page_size=10))
    for kind in ('', 'all', 'group', 'private', 'official'):
        assert client.call('list', query={'kind': kind}).status_code == 200
    for state in ('', 'collecting', 'enrolled_silent', 'enrolled_no_messages', 'not_enrolled'):
        assert client.call('list', query={'state': state}).status_code == 200


@pytest.mark.parametrize('query', [
    {'kind': 'invalid'}, {'state': 'invalid'}, {'page': '0'}, {'page': '-1'}, {'page': '1.0'},
    {'page': '9' * 100}, {'page_size': '101'}, {'page_size': '0'}, {'q': '字' * 201},
    {'q': '\x00'}, {'page': 1}, {'arbitrary': 'x'}, QueryParams('kind=group&kind=private'),
])
def test_invalid_filters_never_read_service(query):
    client = Client()
    result = client.call('list', query=query)
    assert result.status_code == 400
    client.service.list.assert_not_called()


@pytest.mark.parametrize('kwargs', [
    dict(fields={}), dict(fields={'confirm': '0'}), dict(fields={'confirm': 'true'}),
    dict(fields={'confirm': '1', 'group_id': 'fictional@chatroom'}),
    dict(form={'confirm': ['1', '1']}), dict(form={'confirm': '1'}),
    dict(body=b'confirm=\xff'), dict(body=b'confirm=1&confirm=1'),
    dict(body=b'x=' + b'x' * 8192), dict(fields={'confirm': '1'}, headers={}),
    dict(fields={}, headers={'content-length': '8193'}),
    dict(body=b'{}', headers={'content-type': 'application/json', 'content-length': '2'}),
    dict(fields={'confirm': '1'}, query={'force': '1'}),
])
def test_invalid_forms_never_refresh(kwargs):
    client = Client()
    result = client.call('refresh', **kwargs)
    assert result.status_code == 400
    assert any('\u4e00' <= char <= '\u9fff' for char in json.loads(result.body)['error'])
    client.service.refresh.assert_not_called()


def test_cached_form_and_stream_limit():
    client = Client()
    req = request(fields={'csrf': 'fictional-csrf', 'confirm': '1'})
    async def consumed():
        pytest.fail('must not reread middleware-consumed body')
    req.body = consumed
    assert asyncio.run(client.handlers['refresh'](req)).status_code == 200
    client.service.refresh.assert_called_once_with()
    client.service.refresh.reset_mock()
    req = request()
    async def stream():
        yield b'confirm=1&x='
        yield b'x' * 8192
    req.stream = stream
    assert asyncio.run(client.handlers['refresh'](req)).status_code == 400
    client.service.refresh.assert_not_called()


def test_refresh_runs_once_in_threadpool():
    client = Client()
    main_thread = threading.get_ident()
    def refresh():
        assert threading.get_ident() != main_thread
        return {'refreshed': True}
    client.service.refresh.side_effect = refresh
    result = client.call('refresh', body=b'confirm=1&csrf=fictional-csrf')
    assert result.status_code == 200
    client.service.refresh.assert_called_once_with()


def test_refresh_limit_and_failure_responses_without_retry():
    client = Client()
    client.service.refresh.side_effect = RefreshLimited(600)
    limited = client.call('refresh', fields={'confirm': '1'})
    assert limited.status_code == 429
    assert limited.headers['retry-after'] == '600'
    assert json.loads(limited.body)['retry_after'] == 600
    client.service.refresh.side_effect = RefreshFailed('通讯录拉取超时，结果不明，不会自动重试', status_code=504)
    failed = client.call('refresh', fields={'confirm': '1'})
    assert failed.status_code == 504
    assert '不会自动重试' in json.loads(failed.body)['error']
    assert client.service.refresh.call_count == 2


@pytest.mark.parametrize('action', ('list', 'summary', 'refresh'))
def test_unexpected_errors_do_not_expose_paths_or_secrets(action):
    client = Client()
    getattr(client.service, action).side_effect = RuntimeError('/fictional/private/API-SECRET')
    result = client.call(action, fields={'confirm': '1'})
    assert result.status_code == 503
    assert b'private' not in result.body and b'API-SECRET' not in result.body


def test_host_csrf_is_required_and_validated_before_refresh():
    client = Client()
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
        assert http.post('/api/v1/directory/wechat/refresh', data={'confirm': '1'}).status_code == 403
        assert http.post('/api/v1/directory/wechat/refresh', data={'confirm': '1', 'csrf': 'wrong'}).status_code == 403
        client.service.refresh.assert_not_called()
        assert http.post('/api/v1/directory/wechat/refresh',
                         data={'confirm': '1', 'csrf': 'fictional-csrf'}).status_code == 200
        client.service.refresh.assert_called_once_with()


def test_builder_preserves_injected_dependencies(tmp_path):
    client = Client()
    options = dict(discovery_path=tmp_path / 'fictional-discovery.sqlite3',
                   state_path=tmp_path / 'fictional-state.sqlite3', config_path=tmp_path / 'fictional-config.json',
                   cache_path=tmp_path / 'fictional-cache.sqlite3', clock=Mock(), contact_fetcher=Mock())
    with patch.object(api, 'WechatDirectory', return_value=client.service) as constructor:
        assert len(api.build(client.gate, **options)) == 3
    constructor.assert_called_once_with(**options)


def test_rehearsal_automount_uses_throwaway_cache_and_disables_refresh(tmp_path):
    from console import (v3_audit_api, v3_extract_api, v3_fulfil_api, v3_icbu_api, v3_lang_api,
                         v3_listing_api, v3_pipeline_api, v3_quotes_api, v3_roles_api, v3_whatsapp_login_api)
    with ExitStack() as stack:
        stack.enter_context(patch.dict('os.environ', {
            'IMGW_REHEARSAL': '1', 'REAL_ARCHIVE': str(tmp_path / 'missing-fictional.sqlite3')}))
        path = Path(__file__).resolve().parents[2] / 'scripts' / 'rehearsal_server.py'
        spec = importlib.util.spec_from_file_location('directory_rehearsal_test', path)
        rehearsal = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(rehearsal)
        isolate_builders(stack, rehearsal, 'v3_directory_api')
        client = Client()
        build = api.build
        def isolated_build(gate, **options):
            assert options == {'cache_path': str(tmp_path / 'directory.sqlite3'), 'allow_refresh': False}
            return build(gate, service=client.service, **options)
        mounted = stack.enter_context(patch.object(api, 'build', side_effect=isolated_build))
        routes = rehearsal.build_routes(str(tmp_path))
        mounted.assert_called_once()
        assert {route.path for route in routes} == {route.path for route in client.routes}
        route = next(route for route in routes if route.path.endswith('/refresh'))
        result = asyncio.run(route.endpoint(request(fields={'confirm': '1'})))
        assert result.status_code == 403 and '演练' in json.loads(result.body)['error']
        client.service.refresh.assert_not_called()


@pytest.fixture
def integrated_http(tmp_path):
    """Real directory service and cache; all source files and contacts are fictional."""
    discovery, config, state, cache = (tmp_path / name for name in
        ('fictional-discovery.sqlite3', 'fictional-config.json', 'fictional-state.sqlite3', 'fictional-cache.sqlite3'))
    with sqlite3.connect(discovery) as db:
        db.execute('CREATE TABLE identities(identity TEXT,kind TEXT,first_seen REAL,last_seen REAL)')
    config.write_text('{"groups":{}}')
    with sqlite3.connect(state) as db:
        db.execute('CREATE TABLE messages(group_id TEXT,message_id TEXT,created_at REAL)')
    fetcher = Mock(return_value={'ret': 200, 'data': {'friends': ['fictional-friend'],
        'chatrooms': ['fictional-new@chatroom'], 'ghs': ['gh_fictional-news']}})
    service = api.WechatDirectory(discovery_path=discovery, config_path=config, state_path=state,
        cache_path=cache, clock=lambda: 1_900_000_000, contact_fetcher=fetcher,
        conversation_reader=lambda path, now: {})
    gate = SimpleNamespace(error_response=lambda req, code:
        JSONResponse({'error': '请先完成身份认证'}, status_code=code))
    app = Starlette(routes=api.build(gate, service=service))
    env = SimpleNamespace(cache=cache, fetcher=fetcher, recheck=True,
        sources=(discovery, config, state))

    async def host(request, call_next):
        request.state.password_principal = SimpleNamespace(username='fictional-owner')
        request.state.password_recheck = lambda: env.recheck
        request.state.session = SimpleNamespace(authenticated=True)
        if request.method == 'POST':
            form = parse_qs((await request.body()).decode(), keep_blank_values=True)
            if form.get('csrf') != ['fictional-csrf']:
                return JSONResponse({'error': '表单校验失败'}, status_code=403)
            request.state.form = form
        return await call_next(request)

    app.add_middleware(BaseHTTPMiddleware, dispatch=host)
    with TestClient(app) as http:
        env.http = http
        yield env


def test_real_service_refresh_then_lists_cached_contacts_without_touching_sources(integrated_http):
    env = integrated_http
    before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in env.sources}
    assert env.http.get('/api/v1/directory/wechat').json()['total'] == 0
    assert not env.cache.exists()
    result = env.http.post('/api/v1/directory/wechat/refresh',
        data={'confirm': '1', 'csrf': 'fictional-csrf'})
    assert result.status_code == 200 and result.json()['received'] == 3
    env.fetcher.assert_called_once_with()
    group, = env.http.get('/api/v1/directory/wechat').json()['items']
    assert group['id'] == 'fictional-new@chatroom' and group['name'] == '未命名群'
    assert group['collect_state'] == 'not_enrolled' and group['sources'] == ['refresh_cache']
    assert group['n_messages'] == 0 and group['inbox_visible'] is False
    assert env.http.get('/api/v1/directory/wechat?kind=all').json()['total'] == 3
    summary = env.http.get('/api/v1/directory/wechat/summary').json()
    assert summary['groups']['directory_total'] == 1
    assert summary['groups']['directory_not_enrolled'] == 1
    assert before == {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in env.sources}


def test_real_service_csrf_and_recheck_rejections_never_create_cache_or_fetch(integrated_http):
    env = integrated_http
    for data in ({'confirm': '1'}, {'confirm': '1', 'csrf': 'wrong'}):
        assert env.http.post('/api/v1/directory/wechat/refresh', data=data).status_code == 403
        assert not env.cache.exists()
        env.fetcher.assert_not_called()
    env.recheck = False
    assert env.http.post('/api/v1/directory/wechat/refresh',
        data={'confirm': '1', 'csrf': 'fictional-csrf'}).status_code == 403
    assert not env.cache.exists()
    env.fetcher.assert_not_called()


@pytest.fixture
def default_mount(tmp_path):
    """Production builder with explicitly isolated, entirely fictional sources."""
    discovery, config, state, cache = (tmp_path / name for name in
        ('discovery.sqlite3', 'api_config.json', 'state.sqlite3', 'cache.sqlite3'))
    groups = ['fictional-supplier@chatroom', 'fictional-customer@chatroom']
    with sqlite3.connect(discovery) as db:
        db.execute('CREATE TABLE identities(identity TEXT,kind TEXT,first_seen REAL,last_seen REAL)')
        db.executemany('INSERT INTO identities VALUES (?, ?, ?, ?)',
                       [(identity, 'group', 1_900_000_000, 1_900_000_000) for identity in groups])
    config.write_text(json.dumps({'app_id': 'fictional-app', 'token_file': 'fictional-token.txt', 'groups': {}}))
    token = tmp_path / 'fictional-token.txt'
    token.write_text('FICTIONAL-DEFAULT-MOUNT-SECRET\n')
    token.chmod(0o600)
    with sqlite3.connect(state) as db:
        db.execute('CREATE TABLE messages(group_id TEXT,message_id TEXT,created_at REAL)')
    gate = SimpleNamespace(error_response=lambda req, code:
        JSONResponse({'error': '请先完成身份认证'}, status_code=code))
    options = dict(discovery_path=discovery, config_path=config, state_path=state,
                   cache_path=cache, clock=lambda: 1_900_000_000)

    def mount(**overrides):
        routes = api.build(gate, **dict(options, **overrides))
        handlers = {route.name.removeprefix('directory_wechat_'): route.endpoint for route in routes}
        def call(action, **kwargs):
            return asyncio.run(handlers[action](request(**kwargs)))
        return call

    return SimpleNamespace(mount=mount, groups=groups, config=config, token=token, cache=cache,
                           sources=(discovery, config, state, token))


def test_default_mount_constructs_real_adapter_lazily_and_refreshes_names(default_mount, monkeypatch, caplog):
    env, calls = default_mount, []
    class FakeWTAPIClient:
        def __init__(self, token, base_url, timeout):
            assert token == 'FICTIONAL-DEFAULT-MOUNT-SECRET'
            assert base_url == 'https://wx.chuapi.com' and timeout == 20
            calls.append('constructed')

        def fetch_contacts_list(self, app_id):
            assert app_id == 'fictional-app'
            calls.append('contacts')
            return {'ret': 200, 'data': {'friends': ['fictional-friend'], 'chatrooms': env.groups, 'ghs': []}}

        def get_brief_info(self, app_id, wxids):
            assert app_id == 'fictional-app' and set(wxids) == set(env.groups)
            calls.append('brief')
            return {'ret': 200, 'data': [
                {'userName': env.groups[0], 'nickName': 'SUP001 虚构灯具'},
                {'userName': env.groups[1], 'nickName': 'CUS002 虚构酒店'},
            ]}

        def send_text(self, *args):
            pytest.fail('directory must never send')

    monkeypatch.setitem(sys.modules, 'wtapi_client', SimpleNamespace(WTAPIClient=FakeWTAPIClient))
    before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in env.sources}
    call = env.mount()
    assert calls == []
    assert call('list').status_code == 200
    summary = call('summary')
    assert summary.status_code == 200
    assert json.loads(summary.body)['not_enrolled_suspected'] == {'supplier': 0, 'customer': 0}
    assert call('refresh', fields={'confirm': '1'}, recheck=False).status_code == 403
    assert call('refresh', fields={'confirm': '0'}).status_code == 400
    assert calls == [] and not env.cache.exists()
    refreshed = call('refresh', fields={'confirm': '1'})
    assert refreshed.status_code == 200 and json.loads(refreshed.body)['received'] == 3
    assert calls == ['constructed', 'contacts', 'brief']
    rows = {item['id']: item for item in json.loads(call('list').body)['items']}
    assert rows[env.groups[0]]['name'] == 'SUP001 虚构灯具'
    assert rows[env.groups[1]]['name'] == 'CUS002 虚构酒店'
    summary = call('summary')
    assert json.loads(summary.body)['not_enrolled_suspected'] == {'supplier': 1, 'customer': 1}
    assert b'FICTIONAL-DEFAULT-MOUNT-SECRET' not in refreshed.body + summary.body
    assert 'FICTIONAL-DEFAULT-MOUNT-SECRET' not in caplog.text
    assert call('refresh', fields={'confirm': '1'}).status_code == 429
    assert calls == ['constructed', 'contacts', 'brief']
    assert before == {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in env.sources}


@pytest.mark.parametrize('missing', ('config', 'token_file', 'token_value'))
def test_default_mount_missing_configuration_only_breaks_refresh(default_mount, monkeypatch, caplog, missing):
    env = default_mount
    if missing == 'config':
        env.config.unlink()
    elif missing == 'token_file':
        env.config.write_text('{"app_id":"fictional-app","groups":{}}')
    else:
        env.token.write_text('')
    constructor = Mock(side_effect=AssertionError('must not construct without credentials'))
    monkeypatch.setitem(sys.modules, 'wtapi_client', SimpleNamespace(WTAPIClient=constructor))
    call = env.mount()
    assert call('list').status_code == 200
    assert call('summary').status_code == 200
    result = call('refresh', fields={'confirm': '1'})
    assert result.status_code == 503
    assert any('\u4e00' <= char <= '\u9fff' for char in json.loads(result.body)['error'])
    assert b'FICTIONAL-DEFAULT-MOUNT-SECRET' not in result.body
    assert 'FICTIONAL-DEFAULT-MOUNT-SECRET' not in caplog.text
    assert str(env.config).encode() not in result.body
    assert call('list').status_code == 200
    assert call('summary').status_code == 200
    constructor.assert_not_called()
    assert call('refresh', fields={'confirm': '1'}).status_code == 429


def test_default_mount_rehearsal_does_not_construct_client(default_mount):
    with patch.object(api, 'build_readonly_refresh_client') as builder:
        call = default_mount.mount(allow_refresh=False)
        assert call('list').status_code == 200
        assert call('summary').status_code == 200
        result = call('refresh', fields={'confirm': '1'})
        assert result.status_code == 403 and '演练' in json.loads(result.body)['error']
        assert not default_mount.cache.exists()
        builder.assert_not_called()


def test_default_mount_respects_explicit_readonly_client(default_mount):
    readonly = SimpleNamespace(
        fetch_contacts_list=Mock(return_value={'ret': 200, 'data': {'friends': [], 'chatrooms': [], 'ghs': []}}),
        get_brief_info=Mock(return_value={'ret': 200, 'data': []}))
    with patch.object(api, 'build_readonly_refresh_client') as builder:
        call = default_mount.mount(readonly_client=readonly)
        assert call('refresh', fields={'confirm': '1'}).status_code == 200
        readonly.fetch_contacts_list.assert_called_once_with()
        builder.assert_not_called()


def test_default_mount_factory_uses_service_isolated_config_path(tmp_path):
    gate = SimpleNamespace(error_response=lambda req, code: JSONResponse({}, status_code=code))
    readonly = SimpleNamespace(
        fetch_contacts_list=lambda: {'ret': 200, 'data': {'friends': [], 'chatrooms': [], 'ghs': []}},
        get_brief_info=lambda ids: {'ret': 200, 'data': []})
    with patch.object(api, 'build_readonly_refresh_client', return_value=readonly) as builder:
        routes = api.build(gate, discovery_path=tmp_path / 'isolated.sqlite3')
        builder.assert_not_called()
        refresh = next(route.endpoint for route in routes if route.name == 'directory_wechat_refresh')
        assert asyncio.run(refresh(request(fields={'confirm': '1'}))).status_code == 200
        builder.assert_called_once_with(config_path=tmp_path / 'disabled-api-config.json')
