import asyncio
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse

from console import v3_overview_api
from conftest import NOW, PRIVATE


def request(**changes):
    state = dict(password_principal=SimpleNamespace(username='fake-user'),
                 password_recheck=lambda: True, session=SimpleNamespace(authenticated=True))
    state.update(changes)
    return SimpleNamespace(state=SimpleNamespace(**state), query_params={})


def route(paths, **options):
    gate = SimpleNamespace(error_response=lambda request, code: JSONResponse({'error': '无权查看'}, status_code=code))
    return v3_overview_api.build(gate, paths=paths, clock=lambda: NOW, **options)[0]


def test_authenticated_contract_and_headers(healthy):
    response = asyncio.run(route(healthy).endpoint(request()))
    assert response.status_code == 200
    assert response.headers['cache-control'] == 'no-store'
    assert response.headers['x-content-type-options'] == 'nosniff'
    assert 'generated_at' in json.loads(response.body)
    assert PRIVATE not in response.body.decode()


@pytest.mark.parametrize('changes', [dict(password_principal=None), dict(password_recheck=None),
    dict(session=None), dict(session=SimpleNamespace(authenticated=False))])
def test_authorisation_precedes_every_source(paths, changes):
    service = SimpleNamespace(read=Mock(side_effect=AssertionError('不应读来源')))
    response = asyncio.run(route(paths, overview=service).endpoint(request(**changes)))
    assert response.status_code == 403
    service.read.assert_not_called()


def test_revocation_after_read(paths):
    service = SimpleNamespace(read=Mock(return_value={'fake': 'ok'}))
    response = asyncio.run(route(paths, overview=service).endpoint(request(password_recheck=lambda: False)))
    assert response.status_code == 403
    service.read.assert_called_once()


def test_query_injection_rejected_before_read_and_rechecked(paths):
    service = SimpleNamespace(read=Mock())
    check = Mock(return_value=True)
    req = request(password_recheck=check)
    req.query_params = {'path': '/etc/imgw/secret', 'sql': 'DELETE FROM messages'}
    response = asyncio.run(route(paths, overview=service).endpoint(req))
    assert response.status_code == 400
    service.read.assert_not_called()
    check.assert_called_once()


def test_failure_is_sanitised_and_rechecked(paths):
    service = SimpleNamespace(read=Mock(side_effect=RuntimeError(PRIVATE)))
    check = Mock(return_value=True)
    response = asyncio.run(route(paths, overview=service).endpoint(request(password_recheck=check)))
    assert response.status_code == 503 and PRIVATE not in response.body.decode()
    assert response.headers['cache-control'] == 'no-store'
    check.assert_called_once()


def test_asgi_only_get_head_and_no_write_methods(paths):
    mounted = route(paths)
    assert mounted.path == '/api/v1/overview'
    assert mounted.methods == {'GET', 'HEAD'}
    app = Starlette(routes=[mounted])

    async def dispatch(method):
        messages = []
        async def receive():
            return dict(type='http.request', body=b'', more_body=False)
        async def send(message):
            messages.append(message)
        await app(dict(type='http', asgi={'version': '3.0'}, http_version='1.1', method=method,
                       scheme='http', path=mounted.path, raw_path=mounted.path.encode(), query_string=b'',
                       root_path='', headers=[], server=('fake', 80), client=('fake', 1), state=vars(request().state)),
                  receive, send)
        return messages[0]['status']

    assert asyncio.run(dispatch('GET')) == 200
    assert asyncio.run(dispatch('HEAD')) == 200
    for method in ['POST', 'PUT', 'PATCH', 'DELETE']:
        assert asyncio.run(dispatch(method)) == 405
