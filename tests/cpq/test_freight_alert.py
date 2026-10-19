'''Freight alerts and quote acknowledgement enforcement.'''
from dataclasses import replace
import pytest
from console.cpq import compute, evaluate
from console.cpq.models import GateContext
from .engine_helpers import TEST_LANES, compute_case, make_inputs
from .test_quotes import _images


def test_freight_exceeds_goods_alert(engine_ctx: GateContext) -> None:
    res = compute_case(exw_cny=280, qty=10, packing='flight_case', units_per_pkg=1, pkg_cm=(70, 55, 52), pkg_gw=26, case_cost_cny=150, mode='sea_lcl', incoterm='DDP', lane='TEST-FAR-DDP', margin=0.20)
    assert res.totals.freight_ratio > 1.0
    codes = {a.code: a for a in res.alerts}
    assert 'FREIGHT_EXCEEDS_GOODS' in codes
    assert 'FREIGHT_EXCEEDS_GOODS' in res.gate.requires_ack
    assert codes['FREIGHT_EXCEEDS_GOODS'].suggestions
    assert len(codes['FREIGHT_EXCEEDS_GOODS'].suggestions) == 4
    inputs = make_inputs(exw_cny=280, qty=10, packing='flight_case', units_per_pkg=1, pkg_cm=(70, 55, 52), pkg_gw=26, case_cost_cny=150, mode='sea_lcl', incoterm='DDP', lane='TEST-FAR-DDP', margin=0.20)
    gate = evaluate(res, inputs, engine_ctx)[1]
    assert gate.can_save and not gate.can_export
    assert evaluate(res, inputs, replace(engine_ctx, acks=frozenset({'FREIGHT_EXCEEDS_GOODS'})))[1].can_export


def test_freight_ratio_bands(engine_ctx: GateContext) -> None:
    freight_codes = {'FREIGHT_SENSITIVE', 'FREIGHT_HEAVY', 'FREIGHT_EXCEEDS_GOODS'}
    for ratio, expected in ((0.25, set()), (0.50, {'FREIGHT_SENSITIVE'}), (0.85, {'FREIGHT_HEAVY'})):
        lane = replace(TEST_LANES['TEST-LCL'], inland_min_cny=0, inland_cny_per_cbm=0, origin_fixed_usd=100 * ratio, origin_per_cbm_usd=0)
        inputs = make_inputs(exw_cny=700, lane=lane, incoterm='FOB', fx_rate=7, fx_buffer=0)
        result = compute(inputs)
        assert result.totals.freight_ratio == pytest.approx(ratio, abs=1e-9)
        alerts, gate = evaluate(result, inputs, engine_ctx)
        assert {a.code for a in alerts} & freight_codes == expected
        assert gate.can_export


def test_freight_ratio_uses_exw_only(engine_ctx: GateContext) -> None:
    cheap = compute_case(exw_cny=700, packing='flight_case', case_cost_cny=70, incoterm='FOB')
    expensive = compute_case(exw_cny=700, packing='flight_case', case_cost_cny=700, incoterm='FOB')
    assert cheap.totals.packaging_cost_usd != expensive.totals.packaging_cost_usd
    assert cheap.totals.goods_cost_usd == expensive.totals.goods_cost_usd == 100
    for result in (cheap, expensive):
        assert result.totals.freight_ratio == pytest.approx(result.totals.freight_usd / 100)
    assert cheap.totals.freight_ratio == expensive.totals.freight_ratio


def test_export_blocked_without_freight_ack(sales_cost,freight_heavy_payload,app,monkeypatch):
    _images(app,monkeypatch)
    payload = freight_heavy_payload|{'payment_terms_id':'TT_30_70'}
    r = sales_cost.post('/api/cpq/quotes',json=payload|{'acks':[]})
    assert r.status_code==409,r.text
    assert r.json()['error']['code']=='ACK_REQUIRED'
    r2 = sales_cost.post('/api/cpq/quotes',json=payload|{'acks':['FREIGHT_EXCEEDS_GOODS']})
    assert r2.status_code==201,r2.text
