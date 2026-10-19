"""Calculate-layer gates; quote freezing belongs to batch C."""
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from math import nextafter,inf
import sqlite3
import pytest
from console.cpq.config import config_from_dict
from console.cpq.models import Lane,json_value
from console.security.roles import ROLE_PERMISSIONS


def calculate(client,payload):
    response = client.post('/api/cpq/calculate',json=payload)
    assert response.status_code==200,response.text
    return response.json()


def codes(body):
    return {a['code'] for a in body['alerts']}


def priced(seeded,app,margin):
    seeded.store.put_lane(replace(seeded.store.lane('TEST-SEA-LCL'),inland_cny_per_cbm=0,inland_min_cny=0,origin_fixed_usd=0,origin_per_cbm_usd=0))
    payload = deepcopy(seeded.payload)
    payload['pricing_mode']='price'
    payload['shipping']['incoterm']='FOB'
    payload['lines'][0].update(qty=1,accessories=[],unit_price_usd=1)
    inputs,_ = app.state.cpq_service.prepare_calculation(payload,ROLE_PERMISSIONS['sales_cost'])
    cost = inputs.lines[0].cost_basis_cny/(inputs.fx_rate*(1-inputs.config['pricing']['fx_buffer_pct']))
    unit = cost/(1-margin)
    for _ in range(20):
        actual = (unit-cost)/unit
        if (margin>=0 and actual>=margin) or (margin<0 and actual<=margin):
            break
        unit = nextafter(unit,inf if margin>=0 else -inf)
    payload['lines'][0]['unit_price_usd']=unit
    return payload


def test_margin_ok_at_15(sales_cost,seeded,app):
    minimum = app.state.cpq_service.config['thresholds']['min_margin']
    body = calculate(sales_cost,priced(seeded,app,minimum))
    assert body['totals']['actual_margin']==pytest.approx(minimum,abs=1e-12)
    assert not any(code.startswith('MARGIN') for code in codes(body))


def test_margin_below_min_requires_override(sales,seeded,app):
    body = calculate(sales,priced(seeded,app,0.10))
    assert 'MARGIN_BELOW_MIN' in codes(body) and body['gate']['requires_override'] is True


def test_margin_fuse_hard_block(manager,seeded,app):
    payload = priced(seeded,app,-0.03)
    payload['override_reason']='Synthetic manager reason exceeding minimum length'
    body = calculate(manager,payload)
    assert 'MARGIN_FUSE' in codes(body) and body['gate']['can_save'] is False


def test_margin_fuse_boundary(manager,seeded,app):
    fuse = app.state.cpq_service.config['thresholds']['fuse_margin']
    body = calculate(manager,priced(seeded,app,fuse))
    assert body['totals']['actual_margin']==pytest.approx(fuse,abs=1e-12)
    assert 'MARGIN_FUSE' in codes(body)


def test_price_floor_fuse(sales_cost,seeded,app):
    payload = priced(seeded,app,0.18)
    floor = app.state.cpq_service.config['thresholds']['price_floor_cny_per_unit']['strobe']
    payload['lines'][0]['unit_price_usd']=(floor-0.01)/7.12
    assert 'PRICE_FLOOR_FUSE' in codes(calculate(sales_cost,payload))
    price = floor/7.12
    while price*7.12<floor:
        price = nextafter(price,inf)
    payload['lines'][0]['unit_price_usd']=price
    assert 'PRICE_FLOOR_FUSE' not in codes(calculate(sales_cost,payload))


def test_no_cost_basis_blocks(sales,seeded):
    with sqlite3.connect(seeded.settings.db_path) as conn:
        for smid in seeded.supplier_models[seeded.model_ids[0]]:
            conn.execute('UPDATE cat_supplier_offer SET valid_until=? WHERE supplier_model_id=?',((seeded.today-timedelta(days=1)).isoformat(),smid))
    body = calculate(sales,seeded.payload)
    assert 'NO_COST_BASIS' in codes(body) and body['gate']['can_save'] is False


def test_lane_expired_blocks(sales,seeded):
    seeded.store.put_lane(replace(seeded.store.lane('TEST-SEA-LCL'),valid_until=seeded.today-timedelta(days=1)))
    body = calculate(sales,seeded.payload)
    assert 'LANE_EXPIRED' in codes(body) and body['gate']['can_save'] is False


def test_lane_unpriced_blocks(sales,seeded):
    seeded.store.put_lane(Lane('TEST-SEA-LCL','sea',seeded.today+timedelta(days=1)))
    body = calculate(sales,seeded.payload)
    assert 'LANE_UNPRICED' in codes(body) and body['gate']['can_save'] is False


def test_fx_expired_blocks(sales,seeded,app):
    age = app.state.cpq_service.config['thresholds']['fx_expired_days']+1
    with sqlite3.connect(seeded.settings.db_path) as conn:
        conn.execute('UPDATE cat_fx_rate SET as_of=?',(seeded.epoch-age*86400,))
    body = calculate(sales,seeded.payload)
    assert 'FX_EXPIRED' in codes(body) and body['gate']['can_save'] is False


def test_packing_estimate_requires_ack(sales,seeded,monkeypatch):
    import console.cpq.store as module
    original = module.parse_specs
    def parser(*args,**kwargs):
        result = original(*args,**kwargs)
        result['values']['net_weight_kg']=5.0  # Synthetic parser evidence, test only.
        return result
    monkeypatch.setattr(module,'parse_specs',parser)
    with sqlite3.connect(seeded.settings.db_path) as conn:
        conn.execute('DELETE FROM cat_packing WHERE packing=?',('flightcase',))
    payload = deepcopy(seeded.payload)
    payload['lines'][0]['packing_mode']='flight_case'
    assert 'PACKING_ESTIMATED' in calculate(sales,payload)['gate']['requires_ack']


def test_thresholds_from_config(sales,seeded,app):
    service = app.state.cpq_service
    config = json_value(service.config)
    config['thresholds']['min_margin']=0.20
    service._config=config_from_dict(config)
    assert 'MARGIN_BELOW_MIN' in codes(calculate(sales,priced(seeded,app,0.18)))
