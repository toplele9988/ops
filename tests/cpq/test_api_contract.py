from copy import deepcopy
from dataclasses import replace
from datetime import date
from types import SimpleNamespace
import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.testclient import TestClient
from console.routes.strobe_routes import build_routes,settings_from_env
from console.security.roles import ALL_PERMISSIONS,permissions_for
from console.strobe.repository import SUPPLIER_LEGAL_NAME,open_catalog


def keys(value):
    if isinstance(value,dict):
        for k,v in value.items():
            yield k
            yield from keys(v)
    elif isinstance(value,list):
        for v in value:
            yield from keys(v)


def no_financial(value):
    if isinstance(value,dict):
        for k,v in value.items():
            if k=='has_cost_basis':
                assert isinstance(v,bool)
            else:
                assert not any(w in k.lower() for w in ('cost','exw','supplier','offer','margin'))
                no_financial(v)
    elif isinstance(value,list):
        for v in value:
            no_financial(v)


def test_strobes_list_count(viewer,snapshot_db):
    sql = '''SELECT COUNT(DISTINCT e.equipment_id) FROM equipment_specs e JOIN cat_supplier s ON s.legal_name=e.supplier
    JOIN cat_supplier_model sm ON sm.supplier_id=s.supplier_id AND sm.factory_model_code=e.model
    JOIN cat_supplier_offer o ON o.supplier_id=s.supplier_id AND o.supplier_model_id=sm.supplier_model_id
    JOIN cat_review r ON r.subject_kind='offer' AND r.subject_id=o.offer_id
    WHERE e.supplier=? AND e.fixture_type='strobe' AND (o.valid_until IS NULL OR o.valid_until>=?) AND r.decision='accept'
    AND NOT EXISTS (SELECT 1 FROM cat_review n WHERE n.subject_kind='offer' AND n.subject_id=o.offer_id AND n.review_id>r.review_id)'''
    with open_catalog(snapshot_db) as conn:
        count = conn.execute(sql,(SUPPLIER_LEGAL_NAME,date.today().isoformat())).fetchone()[0]
    response = viewer.get('/api/strobes')
    assert response.status_code==200,response.text
    body = response.json()
    assert body['count']==len(body['items'])==count
    assert all(isinstance(i['model_id'],str) for i in body['items'])
    assert len({i['model_id'] for i in body['items']})==count


def test_strobes_list_has_no_cost_keys(viewer):
    response = viewer.get('/api/strobes')
    assert response.status_code==200
    no_financial(response.json())


def test_unknown_values_are_null(viewer):
    response = viewer.get('/api/strobes')
    assert response.status_code==200
    for item in response.json()['items']:
        assert item['net_weight_kg'] is None
        assert item['dimensions_cm']=={'l':None,'w':None,'h':None}
        for field in ('carton','flight_case'):
            assert item[field]=={'source':'none','units_per_pkg':None,'l_cm':None,'w_cm':None,'h_cm':None,'gw_kg':None}
        for field in item['spec_completeness']['missing']:
            value = item['light_source'][field] if field in ('rgb_count','white_count') else item[field]
            assert all(v is None for k,v in value.items() if k!='source') if isinstance(value,dict) else value is None


def test_auth_required(anon,viewer):
    for path in ('/api/strobes','/api/strobes/equipment:missing','/media/a/a_0000000000000000','/api/cpq/context?model_ids=x'):
        response = anon.get(path)
        assert response.status_code==401
        assert response.json()=={'error':{'code':'UNAUTHENTICATED'}}
    assert anon.post('/api/cpq/calculate',json={}).status_code==401
    assert viewer.post('/api/cpq/calculate',json={}).status_code==403


def test_test_headers_ignored_in_prod_mode(settings):
    with TestClient(Starlette(routes=build_routes(replace(settings,testing=False))),headers={'X-Test-User':'admin','X-Test-Roles':'admin'}) as client:
        for path in ('/api/strobes','/api/cpq/context?model_ids=x'):
            response = client.get(path)
            assert response.status_code==401
            assert response.json()=={'error':{'code':'UNAUTHENTICATED'}}
        assert client.post('/api/cpq/calculate',json={}).status_code==401


def test_missing_permission_is_forbidden(app):
    with TestClient(app) as client:
        response = client.get('/api/strobes',headers={'X-Test-User':'unknown','X-Test-Roles':'unknown'})
    assert response.status_code==403
    assert response.json()=={'error':{'code':'FORBIDDEN','details':{'required':'strobe.view'}}}


def test_detail_has_full_specs(viewer):
    items = viewer.get('/api/strobes').json()['items']
    assert items
    response = viewer.get('/api/strobes/'+items[0]['model_id'])
    assert response.status_code==200
    body = response.json()
    assert body['model_id']==items[0]['model_id'] and body['specs_full']
    assert all({'label','value','source'}<=set(r) for r in body['specs_full'])
    no_financial(body)


def test_production_session_and_proxy_have_all_permissions():
    for state in ({'session':SimpleNamespace(authenticated=True)},{'proxy_trusted':True}):
        assert permissions_for(Request({'type':'http','headers':[],'state':state}),False)==frozenset(ALL_PERMISSIONS)


def test_settings_require_secret(monkeypatch):
    monkeypatch.delenv('MEDIA_UID_SECRET',raising=False)
    with pytest.raises(ValueError,match='MEDIA_UID_SECRET'):
        settings_from_env()


def context(client,seeded):
    response = client.get('/api/cpq/context',params={'model_ids':','.join(seeded.model_ids)})
    assert response.status_code==200,response.text
    return response.json()


def test_context_masked_for_sales(sales,seeded):
    body = context(sales,seeded)
    assert body['cost_masked'] is True and body['fx']['usd_cny']==7.12
    assert all('cost_basis_cny' not in m and 'offers' not in m for m in body['models'])
    assert all('cost' not in k or k=='cost_masked' for k in keys(body))


def test_context_cost_for_view_cost(sales_cost,seeded):
    body = context(sales_cost,seeded)
    assert all(m['cost_basis_cny']==1000 and 'offers' not in m for m in body['models'])
    assert all('supplier' not in k for k in keys(body))


def test_context_suppliers_for_admin(admin,seeded):
    assert all(m['offers'] and m['offers'][0]['supplier_name'] for m in context(admin,seeded)['models'])


@pytest.mark.parametrize('key',['cost_basis_cny','exw_cny','cost_custom','supplier_id','offer_id'])
@pytest.mark.parametrize('nested',[False,True])
def test_calculate_rejects_client_cost(sales,seeded,key,nested):
    body = deepcopy(seeded.payload)
    target = body['lines'][0] if nested else body
    target['extra'] = [{'nested':{key:1}}]
    response = sales.post('/api/cpq/calculate',json=body)
    assert response.status_code==400
    assert response.json()['error']['code']=='CLIENT_COST_REJECTED'


def test_calculate_strips_cost_for_sales(sales,freight_heavy_payload):
    response = sales.post('/api/cpq/calculate',json=freight_heavy_payload)
    assert response.status_code==200,response.text
    body = response.json()
    assert body['cost_masked'] is True
    assert all(('cost' not in k or k=='cost_masked') and k!='unit_goods_value_cny' for k in keys(body))
    assert all('suggestions' not in a for a in body['alerts'])


def test_case_cost_override_permission(sales,sales_cost,seeded):
    body = deepcopy(seeded.payload)
    body['lines'][0].update(packing_mode='flight_case',packing_override={'units_per_pkg':2,'l_cm':70,'w_cm':50,'h_cm':40,'gw_kg':20,'case_cost_cny':180})
    assert sales.post('/api/cpq/calculate',json=body).status_code==403
    assert sales_cost.post('/api/cpq/calculate',json=body).status_code==200


def test_body_limit(sales):
    assert sales.post('/api/cpq/calculate',content=b' '*65537,headers={'Content-Type':'application/json'}).status_code==413
    assert sales.post('/api/cpq/calculate',content=iter([b' '*40000,b' '*40000]),headers={'Content-Type':'application/json'}).status_code==413
