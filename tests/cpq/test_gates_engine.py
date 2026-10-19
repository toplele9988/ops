"""Engine gate regressions without HTTP or database fixtures."""
from __future__ import annotations
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
import pytest
import yaml
from console.cpq import ConfigError, compute, evaluate, load_config
from console.cpq.models import CalcInputs, CalcResult, GateContext, Lane
from .engine_helpers import CONFIG_PATH, make_inputs


def _margin(value: float) -> tuple[CalcInputs, CalcResult]:
    inputs = make_inputs()
    result = compute(inputs)
    return inputs, replace(result, totals=replace(result.totals, actual_margin=value))


def test_margin_ok_at_15(engine_ctx: GateContext) -> None:
    inputs, result = _margin(0.15)
    alerts, gate = evaluate(result, inputs, engine_ctx)
    assert not any(a.code.startswith('MARGIN') for a in alerts)
    assert gate.can_export


def test_margin_below_min_requires_override(engine_ctx: GateContext) -> None:
    inputs, result = _margin(0.10)
    alerts, gate = evaluate(result, inputs, engine_ctx)
    assert 'MARGIN_BELOW_MIN' in {a.code for a in alerts}
    assert gate.requires_override and gate.can_save and not gate.can_export
    manager = replace(engine_ctx, permissions=frozenset({'cpq.override_margin'}), override_reason='0123456789')
    assert evaluate(result, inputs, manager)[1].can_export
    assert not evaluate(result, inputs, replace(manager, override_reason='123456789'))[1].can_export
    assert not evaluate(result, inputs, replace(manager, permissions=frozenset()))[1].can_export


def test_margin_fuse_hard_block(engine_ctx: GateContext) -> None:
    inputs, result = _margin(-0.03)
    ctx = replace(engine_ctx, permissions=frozenset({'cpq.override_margin'}), override_reason='0123456789')
    alerts, gate = evaluate(result, inputs, ctx)
    assert 'MARGIN_FUSE' in {a.code for a in alerts}
    assert not gate.can_save and not gate.can_export


def test_margin_fuse_boundary(engine_ctx: GateContext) -> None:
    inputs, result = _margin(-0.021)
    assert 'MARGIN_FUSE' in evaluate(result, inputs, engine_ctx)[1].blocked_by


def test_price_floor_fuse(engine_ctx: GateContext) -> None:
    inputs, result = _margin(0.2)
    for value, blocked in ((259.99, True), (260.00, False), (259.999, True)):
        modified = replace(result, lines=(replace(result.lines[0], unit_goods_value_cny=round(value, 2)),), _raw_goods_values=(value,))
        gate = evaluate(modified, inputs, engine_ctx)[1]
        assert ('PRICE_FLOOR_FUSE' in gate.blocked_by) == blocked
        assert gate.can_save == (not blocked)


def test_no_cost_basis_blocks(engine_ctx: GateContext) -> None:
    inputs = make_inputs(exw_cny=None)
    gate = evaluate(compute(inputs), inputs, engine_ctx)[1]
    assert 'NO_COST_BASIS' in gate.blocked_by
    assert not gate.can_save


def test_lane_expired_blocks(engine_ctx: GateContext) -> None:
    inputs = make_inputs()
    lane = replace(inputs.shipping.lane, valid_until=inputs.today - timedelta(days=1))
    inputs = replace(inputs, shipping=replace(inputs.shipping, lane=lane))
    assert 'LANE_EXPIRED' in evaluate(compute(inputs), inputs, engine_ctx)[1].blocked_by


def test_lane_unpriced_blocks(engine_ctx: GateContext) -> None:
    inputs = make_inputs()
    inputs = replace(inputs, shipping=replace(inputs.shipping, lane=Lane('ZERO', 'sea', inputs.today)))
    assert 'LANE_UNPRICED' in evaluate(compute(inputs), inputs, engine_ctx)[1].blocked_by


def test_fx_expired_blocks(engine_ctx: GateContext) -> None:
    inputs = make_inputs(fx_age_days=15)
    assert 'FX_EXPIRED' in evaluate(compute(inputs), inputs, engine_ctx)[1].blocked_by


def test_packing_estimate_requires_ack(engine_ctx: GateContext) -> None:
    inputs = make_inputs(packing_estimated=True)
    result = compute(inputs)
    gate = evaluate(result, inputs, engine_ctx)[1]
    assert 'PACKING_ESTIMATED' in gate.requires_ack
    assert gate.can_save and not gate.can_export
    assert evaluate(result, inputs, replace(engine_ctx, acks=frozenset({'PACKING_ESTIMATED'})))[1].can_export


def test_thresholds_from_config(tmp_path: Path, engine_ctx: GateContext) -> None:
    data = yaml.safe_load(CONFIG_PATH.read_text(encoding='utf-8'))
    data['thresholds']['min_margin'] = 0.20
    data['thresholds']['fuse_margin'] = -0.05
    data['thresholds']['override_reason_min_chars'] = 12
    data['pricing']['unit_price_step_usd'] = 3
    path = tmp_path / 'cpq.yaml'
    path.write_text(yaml.safe_dump(data), encoding='utf-8')
    inputs, result = _margin(0.18)
    inputs = replace(inputs, config=load_config(path))
    assert 'MARGIN_BELOW_MIN' in {a.code for a in evaluate(result, inputs, engine_ctx)[0]}
    ctx = replace(engine_ctx, permissions=frozenset({'cpq.override_margin'}), override_reason='0123456789')
    assert not evaluate(result, inputs, ctx)[1].can_export
    assert evaluate(result, inputs, replace(ctx, override_reason='012345678901'))[1].can_export
    assert compute(inputs).lines[0].unit_price_usd % 3 == 0
    result = replace(result, totals=replace(result.totals, actual_margin=-0.03))
    assert 'MARGIN_FUSE' not in {a.code for a in evaluate(result, inputs, engine_ctx)[0]}


def test_image_missing_only_blocks_export(engine_ctx: GateContext) -> None:
    inputs = make_inputs()
    gate = evaluate(compute(inputs), inputs, replace(engine_ctx, images_ok={}))[1]
    assert gate.can_save and not gate.can_export
    assert 'IMAGE_MISSING' in gate.blocked_by


def test_stale_specs_and_lcl_suggestion(engine_ctx: GateContext) -> None:
    inputs = make_inputs(fx_age_days=8, spec_complete=False, pkg_cm=(600, 200, 500))
    assert {'FX_STALE', 'SPEC_INCOMPLETE', 'LCL_FCL_SUGGEST'} <= {a.code for a in evaluate(compute(inputs), inputs, engine_ctx)[0]}


@pytest.mark.parametrize(('section', 'field', 'value'), [('thresholds', 'min_margin', 1), ('pricing', 'insurance_rate', -1), ('pricing', 'fx_buffer_pct', 1)])
def test_config_errors_identify_field(tmp_path: Path, section: str, field: str, value: float) -> None:
    data = yaml.safe_load(CONFIG_PATH.read_text(encoding='utf-8'))
    data[section][field] = value
    path = tmp_path / 'bad.yaml'
    path.write_text(yaml.safe_dump(data), encoding='utf-8')
    with pytest.raises(ConfigError, match=field):
        load_config(path)
