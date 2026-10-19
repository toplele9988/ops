"""Synthetic inputs; these lanes are not business quotations."""
from __future__ import annotations
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Any
from console.cpq import compute, config_from_dict, load_config
from console.cpq.models import Accessory, CalcInputs, CalcResult, ExpressBand, Lane, LineInput, Packing, PackingProfiles, ShippingInput

CONFIG_PATH = Path(__file__).resolve().parents[2] / 'config' / 'cpq.yaml'
TODAY = date(2026, 10, 19)
SEA = Lane('TEST-LCL', 'sea', date(2026, 11, 30), inland_min_cny=70, origin_fixed_usd=20, lcl_rate_usd_per_wm=80, fcl_rate_usd={'20GP': 1000, '40GP': 1600, '40HQ': 1800}, fcl_origin_fixed_usd_per_container=30, fcl_dest_fixed_usd_per_container=40, dest_fixed_usd=30, last_mile_usd=10, duty_rate=0.1, import_vat_rate=0.2)
TEST_LANES = {
    'TEST-LCL': SEA,
    'TEST-FCL': replace(SEA, lane_id='TEST-FCL'),
    'TEST-FAR-DDP': replace(SEA, lane_id='TEST-FAR-DDP', inland_min_cny=140, origin_fixed_usd=120, lcl_rate_usd_per_wm=500, dest_fixed_usd=600, last_mile_usd=100),
    'TEST-EXPRESS': Lane('TEST-EXPRESS', 'express', date(2026, 11, 30), volumetric_divisor=5000, increment_kg=0.5, bands=(ExpressBand(21, 4), ExpressBand(45, 3), ExpressBand(100, 2)), min_charge_usd=10, fuel_surcharge_pct=0.1, ddp_fee_usd=5, duty_rate=0.1, import_vat_rate=0.2),
}


def make_inputs(exw_cny: float | None = 700, qty: int = 1, packing: str = 'carton', units_per_pkg: int = 1, pkg_cm: tuple[float, float, float] = (60, 50, 40), pkg_gw: float = 18, case_cost_cny: float | None = 150, mode: str = 'sea_lcl', incoterm: str = 'FOB', lane: str | Lane | None = None, margin: float = 0.2, **options: Any) -> CalcInputs:
    if lane is None:
        lane = {'sea_lcl': 'TEST-LCL', 'sea_fcl': 'TEST-FCL', 'express': 'TEST-EXPRESS'}[mode]
    selected = TEST_LANES[lane] if isinstance(lane, str) else lane
    carton = Packing(units_per_pkg, *pkg_cm, pkg_gw, 0)
    flight = Packing(units_per_pkg, *pkg_cm, pkg_gw, case_cost_cny) if packing == 'flight_case' else Packing(units_per_pkg, *(v + 10 for v in pkg_cm), pkg_gw + 12, case_cost_cny)
    line_options = {k: options.pop(k) for k in ('unit_price_usd', 'accessories', 'net_weight_kg', 'packing_estimated', 'case_cost_missing', 'spec_complete') if k in options}
    line_options.setdefault('net_weight_kg', 10)
    line = LineInput('MODEL-1', qty, packing, PackingProfiles(carton, flight), exw_cny, **line_options)
    values = dict(fx_rate=7, fx_age_days=0, fx_buffer=0, target_margin=margin, pricing_mode='margin', config=load_config(CONFIG_PATH), today=TODAY)
    values.update(options)
    return CalcInputs((line,), ShippingInput(mode, incoterm, selected), **values)


def compute_case(*args: Any, **kwargs: Any) -> CalcResult:
    return compute(make_inputs(*args, **kwargs))


def inputs_from_dict(data: dict[str, Any]) -> CalcInputs:
    values = dict(data)
    lines: list[LineInput] = []
    for item in values['lines']:
        line = dict(item)
        line['packing'] = PackingProfiles(**{k: Packing(**v) if v is not None else None for k, v in line['packing'].items()})
        line['accessories'] = tuple(Accessory(**a) for a in line['accessories'])
        lines.append(LineInput(**line))
    shipping = values['shipping']
    lane = dict(shipping['lane'])
    lane['valid_until'] = date.fromisoformat(lane['valid_until'])
    lane['bands'] = tuple(ExpressBand(**b) for b in lane.get('bands', []))
    values['lines'] = tuple(lines)
    values['shipping'] = ShippingInput(shipping['mode'], shipping['incoterm'], Lane(**lane))
    values['config'] = config_from_dict(values['config'])
    values['today'] = date.fromisoformat(values['today'])
    return CalcInputs(**values)
