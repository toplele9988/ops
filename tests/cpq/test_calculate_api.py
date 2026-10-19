from copy import deepcopy
import sqlite3
import pytest
from console.cpq import engine
from console.cpq.models import Accessory,CalcInputs,LineInput,Packing,PackingProfiles,ShippingInput


def post(client,payload):
    response = client.post('/api/cpq/calculate',json=payload)
    assert response.status_code==200,response.text
    return response.json()


@pytest.mark.parametrize('mode,lane,term',[('sea_lcl','TEST-SEA-LCL','DDP'),('sea_fcl','TEST-SEA-FCL','CIF'),('express','TEST-EXP','DAP')])
def test_calculate_matches_engine(sales_cost,seeded,app,mode,lane,term):
    payload = deepcopy(seeded.payload)
    payload['shipping'] = {'mode':mode,'lane_id':lane,'incoterm':term}
    line = LineInput(seeded.model_ids[0],40,'carton',PackingProfiles(Packing(2,60,40,30,12),Packing(2,70,50,40,20,180)),1000,accessories=(Accessory(6.5,2),),spec_complete=False,case_cost_cny=180)
    inputs = CalcInputs((line,),ShippingInput(mode,term,seeded.store.lane(lane)),7.12,0,None,0.18,'margin',app.state.cpq_service.config,seeded.today)
    expected = engine.compute(inputs).to_dict()
    actual = post(sales_cost,payload)
    for section in ('totals','breakdown'):
        for key,value in expected[section].items():
            assert actual[section][key]==(pytest.approx(value,abs=0.01) if value is not None else None)
    for key,value in expected['lines'][0].items():
        assert actual['lines'][0][key]==(value if isinstance(value,str) else pytest.approx(value,abs=0.01))


def test_missing_packing(sales,seeded):
    with sqlite3.connect(seeded.settings.db_path) as conn:
        conn.execute('DELETE FROM cat_packing')
    response = sales.post('/api/cpq/calculate',json=seeded.payload)
    assert response.status_code==400
    error = response.json()['error']
    assert error['code']=='PACKING_UNKNOWN'
    assert error['details'][0]['model_id']==seeded.model_ids[0]
    assert 'carton.l_cm' in error['details'][0]['missing']
    context = sales.get('/api/cpq/context',params={'model_ids':seeded.model_ids[0]}).json()
    assert context['models'][0]['packing']=={'carton':None,'flight_case':None}


def test_missing_fx_never_calls_engine(sales,seeded,monkeypatch):
    with sqlite3.connect(seeded.settings.db_path) as conn:
        conn.execute('DELETE FROM cat_fx_rate')
    with sqlite3.connect(seeded.settings.work_db) as conn:
        conn.execute('DELETE FROM cpq_fx_rate')
    def forbidden(*args):
        raise AssertionError('engine must not run without FX')
    monkeypatch.setattr(engine,'compute',forbidden)
    body = post(sales,seeded.payload)
    assert body['lines'] is body['totals'] is body['breakdown'] is None
    assert body['gate']['can_save'] is False and body['gate']['blocked_by']==['FX_EXPIRED']
    context = sales.get('/api/cpq/context',params={'model_ids':seeded.model_ids[0]}).json()
    assert context['fx'] is None and context['fx_missing'] is True


def test_price_mode_and_multiple_lines(sales_cost,seeded):
    payload = deepcopy(seeded.payload)
    payload.update(pricing_mode='price',target_margin={'ignored':True})
    payload['lines'][0]['unit_price_usd']=200
    for mid in seeded.model_ids[1:]:
        payload['lines'].append({'model_id':mid,'qty':3,'packing_mode':'flight_case','unit_price_usd':250})
    body = post(sales_cost,payload)
    assert len(body['lines'])==3 and body['totals']['price_usd']==9500
    payload['lines'][1].pop('unit_price_usd')
    assert sales_cost.post('/api/cpq/calculate',json=payload).status_code==400


def test_masked_packing_comparison(sales,sales_cost,seeded):
    full = post(sales_cost,seeded.payload)['packing_compare']
    masked = post(sales,seeded.payload)['packing_compare']
    base = full['carton']['total_cost_usd']
    for mode in ('carton','flight_case'):
        assert 'total_cost_usd' not in masked[mode]
        assert masked[mode]['delta_pct']==pytest.approx((full[mode]['total_cost_usd']-base)/base)


@pytest.mark.parametrize('mode,term,lane',[('express','FOB','TEST-EXP'),('sea_lcl','DAP','TEST-SEA-LCL'),('sea_fcl','DDP','TEST-EXP')])
def test_bad_incoterm(sales,seeded,mode,term,lane):
    payload = deepcopy(seeded.payload)
    payload['shipping']={'mode':mode,'incoterm':term,'lane_id':lane}
    response = sales.post('/api/cpq/calculate',json=payload)
    assert response.status_code==400
    assert response.json()['error']['code']=='BAD_INCOTERM'
