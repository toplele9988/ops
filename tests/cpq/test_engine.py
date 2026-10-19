"""Arithmetic and purity regressions."""
from __future__ import annotations
import ast
from dataclasses import replace
from pathlib import Path
import pytest
from console.cpq import compute, engine, load_config
from console.cpq.engine import _calculate, _costs_at, best_fcl_plan, round2, solve_ddp
from console.cpq.models import PackingProfiles
from .engine_helpers import CONFIG_PATH, TEST_LANES, compute_case, make_inputs


def test_cbm_and_packages() -> None:
    r = compute_case(qty=3, units_per_pkg=2, pkg_cm=(60, 50, 40))
    assert r.totals.packages == 2
    assert r.totals.cbm == pytest.approx(0.24)


@pytest.mark.parametrize(('cm', 'gw', 'expected'), [((60, 50, 40), 18, 24), ((60, 50, 40), 25, 25), ((60, 50, 40.333333333333336), 18, 24.5)])
def test_express_volumetric_weight(cm: tuple[float, float, float], gw: float, expected: float) -> None:
    assert compute_case(mode='express', incoterm='DAP', pkg_cm=cm, pkg_gw=gw).totals.chargeable_kg == pytest.approx(expected)


def test_lcl_min_wm() -> None:
    r = compute_case(qty=3, units_per_pkg=2, pkg_gw=25, incoterm='CIF')
    assert r.totals.cbm == pytest.approx(0.24)
    assert r.totals.gw_kg == 50
    assert r.breakdown.main_carriage_usd == 80


def test_lcl_weight_dominates() -> None:
    r = compute_case(pkg_cm=(100, 100, 100), pkg_gw=1800, incoterm='CIF')
    assert r.totals.cbm == 1
    assert r.breakdown.main_carriage_usd == 1.8 * 80


def test_fcl_plan_choice() -> None:
    p = best_fcl_plan(60, 1000, load_config(CONFIG_PATH)['containers'], TEST_LANES['TEST-FCL'].fcl_rate_usd)
    assert dict(p.containers) == {'20GP': 0, '40GP': 0, '40HQ': 1}
    assert p.utilization_cbm == pytest.approx(60 / 68)


def test_fcl_weight_limit() -> None:
    containers = load_config(CONFIG_PATH)['containers']
    p = best_fcl_plan(20, 30000, containers, TEST_LANES['TEST-FCL'].fcl_rate_usd)
    assert dict(p.containers) != {'20GP': 1, '40GP': 0, '40HQ': 0}
    assert sum(n * containers[k]['max_kg'] for k, n in p.containers.items()) >= 30000


def test_flight_case_increases_cost() -> None:
    r = compute_case(qty=10, incoterm='CIF')
    assert r.packing_compare.flight_case.total_cost_usd > r.packing_compare.carton.total_cost_usd


def test_ddp_converges() -> None:
    inputs = make_inputs(qty=10, incoterm='DDP')
    raw = _calculate(inputs)
    p = raw.equilibrium_price
    assert abs(_costs_at(p, raw.goods, raw.freight, inputs).total / (1 - inputs.target_margin) - p) < 1e-4


def test_unit_price_rounds_up_only() -> None:
    inputs = make_inputs(qty=3, incoterm='DDP')
    raw = _calculate(inputs)
    for unit, unrounded in zip(raw.units, raw.unit_raw):
        assert unit >= unrounded
        assert unit - unrounded < inputs.config['pricing']['unit_price_step_usd']


@pytest.mark.parametrize(('mode', 'term'), [('sea_lcl', 'FOB'), ('sea_lcl', 'DDP'), ('sea_fcl', 'CIF'), ('express', 'DDP')])
def test_actual_margin_not_below_target_after_rounding(mode: str, term: str) -> None:
    assert compute_case(mode=mode, incoterm=term, margin=0.2).totals.actual_margin >= 0.2 - 1e-6


def test_price_mode_margin() -> None:
    r = compute_case(pricing_mode='price', unit_price_usd=200)
    assert r.lines[0].unit_price_usd == 200
    assert r.totals.actual_margin == pytest.approx((200 - (700 / 7 + 70 / 7 + 20)) / 200, abs=1e-6)


def test_bad_incoterm() -> None:
    with pytest.raises(ValueError, match='^BAD_INCOTERM$'):
        compute_case(mode='express', incoterm='FOB')


def test_engine_is_pure() -> None:
    tree = ast.parse(Path(engine.__file__).read_text(encoding='utf-8'))
    banned = {'sqlite3', 'os', 'requests', 'httpx', 'open'}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert not {a.name.split('.')[0] for a in node.names} & banned
        if isinstance(node, ast.ImportFrom):
            assert (node.module or '').split('.')[0] not in banned
            assert not {a.name for a in node.names} & banned
        if isinstance(node, ast.Call):
            assert not (isinstance(node.func, ast.Name) and node.func.id == 'open')
            assert not (isinstance(node.func, ast.Attribute) and node.func.attr == 'open')


def test_deterministic_and_non_mutating() -> None:
    inputs = make_inputs(incoterm='DDP')
    before = repr(inputs)
    assert compute(inputs) == compute(inputs)
    assert repr(inputs) == before
    with pytest.raises(TypeError):
        inputs.config['pricing']['insurance_rate'] = 0


def test_round2_half_up() -> None:
    assert round2(2.675) == 2.68
    assert round2(-2.675) == -2.68


def test_ddp_fob_basis_without_vat() -> None:
    lane = replace(TEST_LANES['TEST-LCL'], duty_basis='FOB', include_import_vat=False)
    duty, vat = solve_ddp(1000, 50, 80, 3, lane)
    assert vat == 0
    assert duty == pytest.approx(0.1 * (1000 - 50 - duty - 80 - 3), abs=1e-6)


def test_estimated_packing_and_missing_cost() -> None:
    inputs = make_inputs(packing='flight_case', units_per_pkg=2, net_weight_kg=10)
    line = replace(inputs.lines[0], packing=PackingProfiles(inputs.lines[0].packing.carton), case_cost_cny=None)
    r = compute(replace(inputs, lines=(line,)))
    assert r.totals.cbm == pytest.approx(70 * 60 * 50 / 1_000_000)
    assert r.totals.gw_kg == 32
    assert {'PACKING_ESTIMATED', 'PACKING_COST_MISSING'} <= {a.code for a in r.alerts}
    assert r.packing_compare.flight_case.total_cost_usd is None
    assert not r.gate.can_save
