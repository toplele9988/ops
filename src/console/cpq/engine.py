"""Pure, deterministic CPQ arithmetic."""
from __future__ import annotations
from dataclasses import dataclass, replace
from decimal import Decimal, ROUND_HALF_UP
from math import ceil, isfinite
from typing import Any, Mapping
from .models import Breakdown, CalcInputs, CalcResult, FCLPlan, Gate, GateContext, Lane, LineInput, LineResult, Packing, PackingCompare, PackingComparison, Totals

ENGINE_VERSION = 'cpq-2026.10.1'


@dataclass(frozen=True, slots=True)
class _Line:
    packages: int
    cbm: float
    gw: float
    chg: float
    exw: float
    pack: float
    acc: float
    missing: bool

    @property
    def goods(self) -> float:
        return self.exw + self.pack + self.acc


@dataclass(frozen=True, slots=True)
class _Costs:
    insurance: float
    fee: float
    duty: float
    vat: float
    total: float


@dataclass(frozen=True, slots=True)
class _Raw:
    lines: tuple[_Line, ...]
    freight: tuple[float, float, float, float]
    plan: FCLPlan | None
    goods: float
    equilibrium_price: float
    unit_raw: tuple[float, ...]
    units: tuple[float, ...]
    shares: tuple[float, ...]
    price: float
    costs: _Costs


def round2(value: float) -> float:
    return float(Decimal(str(value)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))


def ceil_to(value: float, step: float) -> float:
    return ceil((value - 1e-9) / step) * step


def _number(value: Any, name: str, positive: bool = False) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value) or value < 0 or (positive and value == 0):
        raise ValueError(f'{name}: invalid number')


def _fx(inputs: CalcInputs) -> float:
    buffer = inputs.config['pricing']['fx_buffer_pct'] if inputs.fx_buffer is None else inputs.fx_buffer
    return inputs.fx_rate * (1 - buffer)


def _validate(inputs: CalcInputs) -> None:
    shipping = inputs.shipping
    allowed = {'sea_lcl': {'FOB', 'CIF', 'DDP'}, 'sea_fcl': {'FOB', 'CIF', 'DDP'}, 'express': {'DAP', 'DDP'}}
    if shipping.mode not in allowed:
        raise ValueError('BAD_MODE')
    if shipping.incoterm not in allowed[shipping.mode]:
        raise ValueError('BAD_INCOTERM')
    lane = shipping.lane
    if lane.mode != ('express' if shipping.mode == 'express' else 'sea'):
        raise ValueError('LANE_MODE_MISMATCH')
    if lane.currency != 'USD' or lane.duty_basis not in {'FOB', 'CIF'}:
        raise ValueError('BAD_LANE_CURRENCY_OR_DUTY_BASIS')
    for key in ('inland_cny_per_cbm', 'inland_min_cny', 'origin_fixed_usd', 'origin_per_cbm_usd', 'lcl_rate_usd_per_wm', 'lcl_min_wm', 'fcl_origin_fixed_usd_per_container', 'fcl_dest_fixed_usd_per_container', 'dest_fixed_usd', 'dest_per_cbm_usd', 'last_mile_usd', 'duty_rate', 'import_vat_rate', 'min_charge_usd', 'fuel_surcharge_pct', 'ddp_fee_usd'):
        _number(getattr(lane, key), key)
    if lane.duty_rate > 1 or lane.import_vat_rate > 1 or not isinstance(lane.include_import_vat, bool):
        raise ValueError('BAD_TAX_PARAMETERS')
    if set(lane.fcl_rate_usd) != set(inputs.config['containers']):
        raise ValueError('FCL_RATES_REQUIRED')
    for rate in lane.fcl_rate_usd.values():
        _number(rate, 'fcl_rate_usd')
    if shipping.mode == 'express':
        _number(lane.volumetric_divisor, 'volumetric_divisor', True)
        _number(lane.increment_kg, 'increment_kg', True)
        if not lane.bands:
            raise ValueError('EXPRESS_BANDS_REQUIRED')
        previous = 0.0
        for band in lane.bands:
            _number(band.upto_kg, 'upto_kg', True)
            _number(band.usd_per_kg, 'usd_per_kg')
            if band.upto_kg <= previous:
                raise ValueError('BANDS_NOT_INCREASING')
            previous = band.upto_kg
    _number(inputs.fx_rate, 'fx_rate', True)
    buffer = inputs.config['pricing']['fx_buffer_pct'] if inputs.fx_buffer is None else inputs.fx_buffer
    _number(buffer, 'fx_buffer')
    if buffer >= 1:
        raise ValueError('BAD_FX_BUFFER')
    if isinstance(inputs.fx_age_days, bool) or not isinstance(inputs.fx_age_days, int) or inputs.fx_age_days < 0:
        raise ValueError('BAD_FX_AGE')
    if isinstance(inputs.target_margin, bool) or not isinstance(inputs.target_margin, (int, float)) or not isfinite(inputs.target_margin) or not -1 < inputs.target_margin < 1:
        raise ValueError('BAD_TARGET_MARGIN')
    if inputs.pricing_mode not in {'margin', 'price'} or not inputs.lines:
        raise ValueError('BAD_PRICING_MODE_OR_EMPTY_LINES')
    for line in inputs.lines:
        if isinstance(line.qty, bool) or not isinstance(line.qty, int) or line.qty <= 0:
            raise ValueError('BAD_QTY')
        if not line.model_id or line.packing_mode not in {'carton', 'flight_case'}:
            raise ValueError('BAD_LINE')
        for profile in (line.packing.carton, line.packing.flight_case, line.packing.override):
            if profile is None:
                continue
            if isinstance(profile.units_per_pkg, bool) or not isinstance(profile.units_per_pkg, int) or profile.units_per_pkg <= 0:
                raise ValueError('BAD_UNITS_PER_PKG')
            for key in ('l_cm', 'w_cm', 'h_cm'):
                _number(getattr(profile, key), key, True)
            _number(profile.gw_kg, 'gw_kg')
            if profile.case_cost_cny is not None:
                _number(profile.case_cost_cny, 'case_cost_cny')
        for key in ('cost_basis_cny', 'net_weight_kg', 'case_cost_cny', 'unit_price_usd'):
            value = getattr(line, key)
            if value is not None:
                _number(value, key)
        if inputs.pricing_mode == 'price':
            _number(line.unit_price_usd, 'unit_price_usd', True)
        for accessory in line.accessories:
            _number(accessory.cost_cny, 'accessory.cost_cny')
            _number(accessory.qty_per_unit, 'accessory.qty_per_unit')


def _resolve(line: LineInput, inputs: CalcInputs, mode: str | None = None) -> tuple[Packing, bool, bool]:
    selected = mode is None or mode == line.packing_mode
    mode = mode or line.packing_mode
    estimated = selected and line.packing_estimated
    if selected and line.packing.override is not None:
        profile = line.packing.override
        estimated = True
    elif mode == 'carton':
        profile = line.packing.carton
    elif line.packing.flight_case is not None:
        profile = line.packing.flight_case
    else:
        if line.net_weight_kg is None:
            raise ValueError('NET_WEIGHT_REQUIRED_FOR_PACKING_ESTIMATE')
        carton = line.packing.carton
        estimate = inputs.config['packing_estimate']
        pad = estimate['fc_pad_cm']
        profile = Packing(carton.units_per_pkg, carton.l_cm + pad, carton.w_cm + pad, carton.h_cm + pad, carton.units_per_pkg * line.net_weight_kg + estimate['fc_tare_kg'], line.case_cost_cny)
        estimated = True
    missing = mode == 'flight_case' and (profile.case_cost_cny is None or (selected and line.case_cost_missing))
    return profile, estimated, missing


def packing_status(inputs: CalcInputs) -> tuple[bool, bool]:
    profiles = tuple(_resolve(line, inputs) for line in inputs.lines)
    return any(p[1] for p in profiles), any(p[2] for p in profiles)


def _line(line: LineInput, inputs: CalcInputs, mode: str | None) -> _Line:
    profile, _, missing = _resolve(line, inputs, mode)
    packages = ceil(line.qty / profile.units_per_pkg)
    volume = profile.l_cm * profile.w_cm * profile.h_cm
    chg = 0.0
    if inputs.shipping.mode == 'express':
        lane = inputs.shipping.lane
        assert lane.volumetric_divisor is not None and lane.increment_kg is not None
        chg = packages * ceil_to(max(profile.gw_kg, volume / lane.volumetric_divisor), lane.increment_kg)
    fx = _fx(inputs)
    pack = packages * (profile.case_cost_cny or 0.0) / fx if (mode or line.packing_mode) == 'flight_case' else 0.0
    return _Line(packages, packages * volume / 1_000_000, packages * profile.gw_kg, chg, (line.cost_basis_cny or 0.0) * line.qty / fx, pack, line.qty * sum(a.cost_cny * a.qty_per_unit for a in line.accessories) / fx, missing)


def best_fcl_plan(cbm: float, gw: float, containers: Mapping[str, Any], rates: Mapping[str, float]) -> FCLPlan:
    """Enumerate the specified bounded search deterministically."""
    limit = min(10, ceil(cbm / containers['20GP']['usable_cbm']) + 1)
    names = ('20GP', '40GP', '40HQ')
    best: FCLPlan | None = None
    best_key: tuple[float, int, int] | None = None
    for n20 in range(limit + 1):
        for n40 in range(limit + 1):
            for nhq in range(limit + 1):
                counts = (n20, n40, nhq)
                number = sum(counts)
                capacity = sum(n * containers[name]['usable_cbm'] for name, n in zip(names, counts))
                weight = sum(n * containers[name]['max_kg'] for name, n in zip(names, counts))
                if not number or capacity < cbm or weight < gw:
                    continue
                ocean = sum(n * rates[name] for name, n in zip(names, counts))
                key = (ocean, number, n20)
                if best_key is None or key < best_key:
                    best_key = key
                    best = FCLPlan(dict(zip(names, counts)), cbm / capacity, ocean)
    if best is None:
        raise ValueError('FCL_CAPACITY_EXCEEDED')
    return best


def _freight(inputs: CalcInputs, lines: tuple[_Line, ...]) -> tuple[tuple[float, float, float, float], FCLPlan | None]:
    shipping = inputs.shipping
    lane = shipping.lane
    cbm, gw = sum(line.cbm for line in lines), sum(line.gw for line in lines)
    plan = None
    if shipping.mode == 'express':
        chg = sum(line.chg for line in lines)
        rate = next((b.usd_per_kg for b in lane.bands if chg <= b.upto_kg), lane.bands[-1].usd_per_kg)
        main = max(lane.min_charge_usd, chg * rate) * (1 + lane.fuel_surcharge_pct)
        return (0.0, 0.0, main, lane.ddp_fee_usd if shipping.incoterm == 'DDP' else 0.0), None
    inland = max(lane.inland_min_cny, lane.inland_cny_per_cbm * cbm) / _fx(inputs)
    if shipping.mode == 'sea_lcl':
        origin = lane.origin_fixed_usd + lane.origin_per_cbm_usd * cbm
        main = max(cbm, gw / 1000, lane.lcl_min_wm) * lane.lcl_rate_usd_per_wm
        dest = lane.dest_fixed_usd + lane.dest_per_cbm_usd * cbm + lane.last_mile_usd
    else:
        plan = best_fcl_plan(cbm, gw, inputs.config['containers'], lane.fcl_rate_usd)
        origin = plan.total_containers * lane.fcl_origin_fixed_usd_per_container
        main = plan.cost_usd
        dest = plan.total_containers * lane.fcl_dest_fixed_usd_per_container + lane.last_mile_usd
    return (inland, origin, main if shipping.incoterm != 'FOB' else 0.0, dest if shipping.incoterm == 'DDP' else 0.0), plan


def solve_ddp(price: float, post: float, main: float, insurance: float, lane: Lane) -> tuple[float, float]:
    duty = vat = duty_new = vat_new = 0.0
    for _ in range(50):
        cif = price - post - duty - vat
        customs = cif if lane.duty_basis == 'CIF' else cif - main - insurance
        duty_new = lane.duty_rate * customs
        vat_new = lane.import_vat_rate * (cif + duty_new) if lane.include_import_vat else 0.0
        if abs(duty_new - duty) + abs(vat_new - vat) < 1e-6:
            break
        duty, vat = duty_new, vat_new
    return duty_new, vat_new


def _costs_at(price: float, goods: float, freight: tuple[float, float, float, float], inputs: CalcInputs) -> _Costs:
    pricing = inputs.config['pricing']
    shipping = inputs.shipping
    insurance = price * pricing['insured_markup'] * pricing['insurance_rate'] if shipping.mode != 'express' and shipping.incoterm in {'CIF', 'DDP'} else 0.0
    fee = price * pricing['payment_fee_pct']
    duty, vat = solve_ddp(price, freight[3], freight[2], insurance, shipping.lane) if shipping.incoterm == 'DDP' else (0.0, 0.0)
    return _Costs(insurance, fee, duty, vat, goods + sum(freight) + insurance + fee + duty + vat)


def _calculate(inputs: CalcInputs, mode: str | None = None) -> _Raw:
    lines = tuple(_line(line, inputs, mode) for line in inputs.lines)
    freight, plan = _freight(inputs, lines)
    goods = sum(line.goods for line in lines)
    weights = tuple(line.chg if inputs.shipping.mode == 'express' else line.cbm for line in lines)
    shares = tuple(weight / sum(weights) for weight in weights)
    denominator = 1 - inputs.target_margin
    price = (goods + sum(freight)) / denominator
    if inputs.pricing_mode == 'margin':
        for _ in range(50):
            updated = _costs_at(price, goods, freight, inputs).total / denominator
            if not isfinite(updated):
                raise ValueError('NONFINITE_PRICE')
            if abs(updated - price) < 1e-6:
                break
            price = updated
    else:
        price = sum(float(line.unit_price_usd) * line.qty for line in inputs.lines)
    logistics = _costs_at(price, goods, freight, inputs).total - goods
    raw = tuple((fact.goods + logistics * share) / denominator / line.qty for fact, share, line in zip(lines, shares, inputs.lines))
    units = tuple(ceil_to(unit, inputs.config['pricing']['unit_price_step_usd']) for unit in raw) if inputs.pricing_mode == 'margin' else tuple(float(line.unit_price_usd) for line in inputs.lines)
    final = sum(unit * line.qty for unit, line in zip(units, inputs.lines))
    return _Raw(lines, freight, plan, goods, price, raw, units, shares, final, _costs_at(final, goods, freight, inputs))


def _comparison(inputs: CalcInputs, mode: str) -> PackingComparison:
    try:
        raw = _calculate(inputs, mode)
    except ValueError as exc:
        if str(exc) not in {'NET_WEIGHT_REQUIRED_FOR_PACKING_ESTIMATE', 'FCL_CAPACITY_EXCEEDED'}:
            raise
        return PackingComparison(None, None, None, None)
    known = all(line.cost_basis_cny is not None and line.cost_basis_cny > 0 for line in inputs.lines) and not any(line.missing for line in raw.lines)
    return PackingComparison(sum(line.packages for line in raw.lines), sum(line.cbm for line in raw.lines), sum(line.gw for line in raw.lines), round2(raw.costs.total) if known else None)


def lcl_fcl_saving(inputs: CalcInputs, result: CalcResult) -> float | None:
    if inputs.shipping.mode != 'sea_lcl':
        return None
    lane = inputs.shipping.lane
    cbm, gw = result.totals.cbm, result.totals.gw_kg
    try:
        plan = best_fcl_plan(cbm, gw, inputs.config['containers'], lane.fcl_rate_usd)
    except ValueError as exc:
        if str(exc) != 'FCL_CAPACITY_EXCEEDED':
            raise
        return None
    lcl = max(cbm, gw / 1000, lane.lcl_min_wm) * lane.lcl_rate_usd_per_wm + lane.origin_fixed_usd + lane.origin_per_cbm_usd * cbm
    return lcl - plan.cost_usd - plan.total_containers * lane.fcl_origin_fixed_usd_per_container


def freight_suggestions(inputs: CalcInputs, result: CalcResult) -> tuple[str, ...]:
    carton, flight = result.packing_compare.carton.total_cost_usd, result.packing_compare.flight_case.total_cost_usd
    packing = '包装差额待确认：请补齐包装档案、净重和航空箱成本。' if carton is None or flight is None else f'纸箱 {carton:.2f} USD，航空箱 {flight:.2f} USD；航空箱减纸箱差额 {round2(flight - carton):.2f} USD。'
    saving = lcl_fcl_saving(inputs, result)
    fcl = '当前方式或柜容限制不适用 LCL 改 FCL。' if saving is None else f'LCL 减 FCL 的主运费与起运费差额 {round2(saving):.2f} USD；其他费用按新方案重算。'
    terms = ('DAP',) if inputs.shipping.mode == 'express' else ('FOB', 'CIF')
    prices: list[str] = []
    for term in terms:
        alternate = replace(inputs, shipping=replace(inputs.shipping, incoterm=term), pricing_mode='margin')
        prices.append(f'{term} {round2(_calculate(alternate).price):.2f} USD')
    alternative = ('快件不适用 FOB/CIF。' if inputs.shipping.mode == 'express' else '') + '按当前目标毛利改报：' + '；'.join(prices) + '，由买家安排进口清关及税费。'
    return packing, fcl, alternative, '建议提高起订量以摊薄起运港固定费；具体起订量需业务确认。'


def compute(inputs: CalcInputs) -> CalcResult:
    """Calculate without I/O or input mutation."""
    _validate(inputs)
    raw = _calculate(inputs)
    logistics = raw.costs.total - raw.goods
    lines: list[LineResult] = []
    values: list[float] = []
    for source, fact, unit, share in zip(inputs.lines, raw.lines, raw.units, raw.shares):
        allocation = logistics * share
        value = (unit - allocation / source.qty) * inputs.fx_rate
        values.append(value)
        lines.append(LineResult(source.model_id, source.qty, fact.packages, fact.cbm, fact.gw, fact.chg, round2(unit), round2(unit * source.qty), round2(allocation), round2(value), round2(fact.goods + allocation)))
    exw = sum(line.exw for line in raw.lines)
    freight = sum(raw.freight) + raw.costs.insurance
    totals = Totals(sum(line.packages for line in raw.lines), sum(line.cbm for line in raw.lines), sum(line.gw for line in raw.lines), sum(line.chg for line in raw.lines), round2(raw.price), round2(raw.costs.total), (raw.price - raw.costs.total) / raw.price if raw.price else None, round2(exw), round2(sum(line.pack for line in raw.lines)), round2(sum(line.acc for line in raw.lines)), round2(freight), round2(raw.costs.duty + raw.costs.vat), round2(raw.costs.fee), freight / exw if exw else None)
    breakdown = Breakdown(*(round2(v) for v in (raw.freight[0], raw.freight[1], raw.freight[2], raw.costs.insurance, raw.freight[3], raw.costs.duty, raw.costs.vat)))
    plan = replace(raw.plan, cost_usd=round2(raw.plan.cost_usd)) if raw.plan else None
    result = CalcResult(tuple(lines), totals, breakdown, plan, PackingCompare(_comparison(inputs, 'carton'), _comparison(inputs, 'flight_case')), (), Gate(True, True, [], False, []), ENGINE_VERSION, tuple(values))
    from .gates import evaluate
    alerts, gate = evaluate(result, inputs, GateContext())
    return replace(result, alerts=alerts, gate=gate)
