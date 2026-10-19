from dataclasses import replace
from datetime import date
from types import SimpleNamespace
import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.testclient import TestClient
from console.routes.strobe_routes import build_routes, settings_from_env
from console.security.roles import ALL_PERMISSIONS, permissions_for
from console.strobe.repository import SUPPLIER_LEGAL_NAME, open_catalog


def test_strobes_list_count(viewer, snapshot_db):
    sql = '''
    SELECT COUNT(DISTINCT e.equipment_id) FROM equipment_specs e
    JOIN cat_supplier s ON s.legal_name = e.supplier
    JOIN cat_supplier_model sm ON sm.supplier_id = s.supplier_id AND sm.factory_model_code = e.model
    JOIN cat_supplier_offer o ON o.supplier_id = s.supplier_id AND o.supplier_model_id = sm.supplier_model_id
    JOIN cat_review r ON r.subject_kind = 'offer' AND r.subject_id = o.offer_id
    WHERE e.supplier = ? AND e.fixture_type = 'strobe'
      AND (o.valid_until IS NULL OR o.valid_until >= ?) AND r.decision = 'accept'
      AND NOT EXISTS (SELECT 1 FROM cat_review newer WHERE newer.subject_kind = 'offer'
                      AND newer.subject_id = o.offer_id AND newer.review_id > r.review_id)
    '''
    with open_catalog(snapshot_db) as conn:
        expected = conn.execute(sql, (SUPPLIER_LEGAL_NAME, date.today().isoformat())).fetchone()[0]
    response = viewer.get('/api/strobes')
    assert response.status_code == 200, response.text
    body = response.json()
    assert body['count'] == len(body['items']) == expected
    assert all(isinstance(item['model_id'], str) for item in body['items'])
    assert len({item['model_id'] for item in body['items']}) == expected


def _no_financial_keys(value):
    if isinstance(value, dict):
        for key, child in value.items():
            # Explicit contract conflict: the required has_cost_basis flag
            # contains cost. It is the sole exception, and must be boolean.
            if key == 'has_cost_basis':
                assert isinstance(child, bool)
                continue
            assert not any(word in key.lower() for word in ('cost', 'exw', 'supplier', 'offer', 'margin')), key
            _no_financial_keys(child)
    elif isinstance(value, list):
        for child in value:
            _no_financial_keys(child)


def test_strobes_list_has_no_cost_keys(viewer):
    response = viewer.get('/api/strobes')
    assert response.status_code == 200, response.text
    _no_financial_keys(response.json())


def test_unknown_values_are_null(viewer):
    response = viewer.get('/api/strobes')
    assert response.status_code == 200, response.text
    for item in response.json()['items']:
        assert item['net_weight_kg'] is None
        assert item['dimensions_cm'] == {'l': None, 'w': None, 'h': None}
        for field in ('carton', 'flight_case'):
            assert item[field] == {'source': 'none', 'units_per_pkg': None,
                                   'l_cm': None, 'w_cm': None, 'h_cm': None, 'gw_kg': None}
        for field in item['spec_completeness']['missing']:
            value = item['light_source'][field] if field in ('rgb_count', 'white_count') else item[field]
            if isinstance(value, dict):
                assert all(part is None for key, part in value.items() if key != 'source')
            else:
                assert value is None, (item['model_id'], field, value)


def test_auth_required(anon):
    response = anon.get('/api/strobes')
    assert response.status_code == 401
    assert response.json() == {'error': {'code': 'UNAUTHENTICATED'}}
    assert anon.get('/api/strobes/equipment:missing').status_code == 401


def test_test_headers_ignored_in_prod_mode(settings):
    application = Starlette(routes=build_routes(replace(settings, testing=False)))
    with TestClient(application) as client:
        response = client.get('/api/strobes', headers={'X-Test-User': 'admin', 'X-Test-Roles': 'admin'})
    assert response.status_code == 401
    assert response.json() == {'error': {'code': 'UNAUTHENTICATED'}}


def test_missing_permission_is_forbidden(app):
    with TestClient(app) as client:
        response = client.get('/api/strobes', headers={'X-Test-User': 'unknown', 'X-Test-Roles': 'unknown'})
    assert response.status_code == 403
    assert response.json() == {'error': {'code': 'FORBIDDEN', 'details': {'required': 'strobe.view'}}}


def test_detail_has_full_specs(viewer):
    response = viewer.get('/api/strobes')
    assert response.status_code == 200, response.text
    items = response.json()['items']
    assert items, 'Expected a nonempty catalog snapshot'
    response = viewer.get('/api/strobes/' + items[0]['model_id'])
    assert response.status_code == 200
    body = response.json()
    assert body['model_id'] == items[0]['model_id']
    assert body['specs_full']
    assert all({'label', 'value', 'source'} <= set(row) for row in body['specs_full'])
    _no_financial_keys(body)


def test_production_session_and_proxy_have_all_permissions():
    for state in ({'session': SimpleNamespace(authenticated=True)}, {'proxy_trusted': True}):
        request = Request({'type': 'http', 'headers': [], 'state': state})
        assert permissions_for(request, False) == frozenset(ALL_PERMISSIONS)


def test_settings_require_secret(monkeypatch):
    monkeypatch.delenv('MEDIA_UID_SECRET', raising=False)
    with pytest.raises(ValueError, match='MEDIA_UID_SECRET'):
        settings_from_env()
