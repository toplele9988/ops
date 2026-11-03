import asyncio
import json
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from starlette.datastructures import QueryParams
from starlette.responses import JSONResponse

from console import v3_deal_workflow_api as api
from console.archive.validation import BadRequest, Conflict, Missing
from console.lang.storage import RateLimited
from test_api import request


@pytest.fixture
def workflow():
    offers = SimpleNamespace(read=Mock(return_value={'items': []}), choose=Mock(return_value={'ok': True}))
    demands = SimpleNamespace(read=Mock(return_value={'items': []}), scan=Mock(return_value={'items': []}),
        accept=Mock(return_value={'ok': True}), dismiss=Mock(return_value={'ok': True}))
    followups = SimpleNamespace(read=Mock(return_value={'items': []}), write=Mock(return_value={'ok': True}))
    gate = SimpleNamespace(error_response=lambda req, code: JSONResponse({'error': '请先完成身份认证'}, status_code=code))
    routes = api.build(gate, offers_service=offers, demands_service=demands, followups_service=followups)
    handlers = {(r.path.removeprefix('/api/v1/deals/{id}/'), 'POST' if 'POST' in r.methods else 'GET'): r.endpoint for r in routes}
    return SimpleNamespace(offers=offers, demands=demands, followups=followups, routes=routes, handlers=handlers, gate=gate)


def call(workflow, path='offers', method='GET', **kwargs):
    kwargs.setdefault('ids', {'id': '1', 'child': '2'})
    return asyncio.run(workflow.handlers[path, method](request(**kwargs)))


def test_workflow_auth_and_recheck_precede_body_and_effects(workflow):
    assert len(workflow.routes) == 11
    for route in workflow.routes:
        for denied in ({'principal': False}, {'authenticated': False}, {'recheck': False}):
            req = request(ids={'id': '1', 'child': '2'}, fields={}, **denied)
            response = asyncio.run(route.endpoint(req))
            assert response.status_code == 403
            assert response.headers['cache-control'] == 'no-store'
            assert response.headers['x-content-type-options'] == 'nosniff'
    req = request(ids={'id': '1'}, recheck=False)
    async def forbidden_body():
        pytest.fail('失效认证不得读取表单')
    req.body = forbidden_body
    workflow.followups.write.reset_mock()
    assert asyncio.run(workflow.handlers['waiting', 'POST'](req)).status_code == 403
    workflow.followups.write.assert_not_called()


def test_dispatch_scopes_child_and_actor_and_runs_off_event_loop(workflow):
    threads = []
    workflow.offers.choose.side_effect = lambda *args: threads.append(threading.get_ident()) or {'ok': True}
    assert call(workflow, 'items/{child}/offer', 'POST', fields={'offer_id': '3', 'reason': '虚构选择理由'}).status_code == 200
    workflow.offers.choose.assert_called_once_with(1, 2, {'offer_id': '3', 'reason': '虚构选择理由'}, 'fictional-owner')
    assert threads and threads[0] != threading.get_ident()
    for path, method, target, args in [
        ('demands', 'GET', workflow.demands.read, (1,)),
        ('demands/scan', 'POST', workflow.demands.scan, (1, {}, 'fictional-owner')),
        ('demands/{child}/accept', 'POST', workflow.demands.accept, (1, 2, {}, 'fictional-owner')),
        ('demands/{child}/dismiss', 'POST', workflow.demands.dismiss, (1, 2, 'fictional-owner')),
        ('waiting', 'GET', workflow.followups.read, (1,)),
        ('waiting', 'POST', workflow.followups.write, ('create', 1, {}, 'fictional-owner', 2)),
        ('waiting/{child}/draft', 'POST', workflow.followups.write, ('draft', 1, {}, 'fictional-owner', 2)),
    ]:
        assert call(workflow, path, method, fields={} if method == 'POST' else None).status_code == 200
        target.assert_called_with(*args)


@pytest.mark.parametrize('kwargs', [
    {'query': QueryParams('x=1&x=2')}, {'ids': {'id': '../2'}},
    {'fields': {'path': '/fictional/db'}, 'query': {'secret': 'x'}},
    {'form': {'reason': ['a', 'b']}}, {'body': b'\xff'},
    {'body': b'x' * 8193}, {'headers': {'content-type': 'application/json'}, 'body': b'{}'},
])
def test_rejects_invalid_input_before_mutation(workflow, kwargs):
    assert call(workflow, 'items/{child}/offer', 'POST', **kwargs).status_code == 400
    workflow.offers.choose.assert_not_called()


def test_error_mapping_no_private_exception_or_get_recheck_leak(workflow):
    for error, code in ((BadRequest('请核对字段'), 400), (Missing('未找到案子'), 404),
                        (Conflict('记录已变化，请重新读取'), 409), (RuntimeError('/fictional/private'), 503)):
        workflow.offers.read.side_effect = error
        response = call(workflow)
        assert response.status_code == code
        assert '/fictional/private' not in response.body.decode()
        assert call(workflow, recheck=False).status_code == 403
    workflow.followups.write.side_effect = RateLimited(30)
    response = call(workflow, 'waiting/{child}/draft', 'POST', fields={})
    assert response.status_code == 429 and response.headers['retry-after'] == '30'
    assert json.loads(response.body)['retry_after'] == 30
    assert call(workflow, 'demands/{child}/dismiss', 'POST', fields={'who': '伪造操作人'}).status_code == 400
    workflow.demands.dismiss.assert_not_called()


def test_custom_archive_keeps_all_sidecars_in_fixture_directory(tmp_path, monkeypatch, workflow):
    inbox = SimpleNamespace()
    seen = {}
    def factory(name):
        def construct(*args, **kwargs):
            seen[name] = (args, kwargs)
            return SimpleNamespace()
        return construct
    for name in ('Offers', 'Demands', 'Followups', 'Lang'):
        monkeypatch.setattr(api, name, factory(name))
    api.build(workflow.gate, tmp_path / 'archive.sqlite3', inbox=inbox, runner=lambda _: '')
    assert seen['Offers'][1]['path'] == tmp_path / 'deal-offers.sqlite3'
    assert seen['Demands'][1]['path'] == tmp_path / 'deal-demands.sqlite3'
    assert seen['Followups'][1]['path'] == tmp_path / 'deal-followups.sqlite3'
    assert seen['Lang'][0][0] == tmp_path / 'lang.sqlite3'
    assert all(value[1]['inbox'] is inbox for value in seen.values())
    assert not list(tmp_path.iterdir())
