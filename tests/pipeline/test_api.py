import asyncio
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse

from console import v3_pipeline_api
from conftest import NOW, add


def request(**changes):
    state = dict(password_principal=SimpleNamespace(username='fictional-user'),
                 password_recheck=lambda: True, session=SimpleNamespace(authenticated=True))
    state.update(changes)
    return SimpleNamespace(state=SimpleNamespace(**state), query_params={})


def endpoint(paths, **options):
    app = SimpleNamespace(error_response=lambda request, code: JSONResponse({'error': '无权查看，请重新登录'}, status_code=code))
    return v3_pipeline_api.build(app, paths=paths, clock=lambda: NOW, **options)[0].endpoint


def test_authenticated_metadata_response(paths):
    result = asyncio.run(endpoint(paths)(request()))
    assert result.status_code == 200
    assert result.headers['cache-control'] == 'no-store'
    assert result.headers['x-content-type-options'] == 'nosniff'
    data = json.loads(result.body)
    assert len(data['channels']) == 3 and len(data['sources']) == 7
    assert len(data['scorecard']) == 3
    assert data['failed_jobs']['status'] == 'not_connected'


def test_session_and_failed_jobs_metadata_in_authenticated_api(paths):
    add(paths, 'whatsapp_session', NOW - 600,
        raw='{"payload":{"status":"FAILED"},"me":null,"ignored":"fictional-private-content"}')
    reader = Mock(return_value=[dict(unit='fictional-worker.service', failed_at=NOW - 60)])
    result = asyncio.run(endpoint(paths, failed_units_reader=reader)(request()))
    data = json.loads(result.body)
    assert data['scorecard'][2]['result'] == 'fail'
    assert data['failed_jobs']['items'] == [dict(unit='fictional-worker.service', failed_at=NOW - 60)]
    assert 'fictional-private' not in result.body.decode()
    reader.assert_called_once_with(NOW)


def test_unauthorised_request_cannot_call_failed_units_provider(paths):
    reader = Mock(side_effect=AssertionError('无权读取后台作业'))
    result = asyncio.run(endpoint(paths, failed_units_reader=reader)(request(password_principal=None)))
    assert result.status_code == 403
    reader.assert_not_called()


@pytest.mark.parametrize('change', [dict(password_principal=None), dict(password_recheck=None),
    dict(session=SimpleNamespace(authenticated=False)), dict(session=None)])
def test_authorisation_before_any_source_read(paths, change):
    service = SimpleNamespace(read=Mock(side_effect=AssertionError('不应读库')))
    result = asyncio.run(endpoint(paths, health=service)(request(**change)))
    assert result.status_code == 403
    service.read.assert_not_called()


def test_recheck_after_read_blocks_revoked_session(paths):
    result = asyncio.run(endpoint(paths)(request(password_recheck=lambda: False)))
    assert result.status_code == 403


def test_unexpected_error_does_not_leak_and_rechecks(paths):
    service = SimpleNamespace(read=Mock(side_effect=RuntimeError('fictional-private-error')))
    check = Mock(return_value=True)
    result = asyncio.run(endpoint(paths, health=service)(request(password_recheck=check)))
    assert result.status_code == 503 and 'fictional-private' not in result.body.decode()
    check.assert_called_once()


def test_query_paths_and_sql_are_rejected_without_read(paths):
    service = SimpleNamespace(read=Mock())
    req = request()
    req.query_params = {'path': '../../private', 'sql': 'DELETE FROM inbound'}
    result = asyncio.run(endpoint(paths, health=service)(req))
    assert result.status_code == 400
    service.read.assert_not_called()


def test_route_methods_and_starlette_dispatch(paths):
    app_module = SimpleNamespace(error_response=lambda request, code: JSONResponse({'error': '无权查看'}, status_code=code))
    route = v3_pipeline_api.build(app_module, paths=paths, clock=lambda: NOW)[0]
    app = Starlette(routes=[route])

    async def dispatch(method):
        responses = []
        async def receive():
            return {'type': 'http.request', 'body': b'', 'more_body': False}
        async def send(message):
            responses.append(message)
        state = vars(request().state)
        await app(dict(type='http', asgi={'version': '3.0'}, http_version='1.1', method=method,
                       scheme='http', path='/api/v1/pipeline/health', raw_path=b'/api/v1/pipeline/health',
                       query_string=b'', root_path='', headers=[], server=('fictional', 80),
                       client=('fictional', 1), state=state), receive, send)
        return responses[0]['status']

    assert asyncio.run(dispatch('GET')) == 200
    assert asyncio.run(dispatch('POST')) == 405
