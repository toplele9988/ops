"""Configuration-driven draft and export gates."""
from __future__ import annotations
from .models import Alert, CalcInputs, CalcResult, Gate, GateContext, Lane


def _unpriced(lane: Lane) -> bool:
    if lane.mode == 'express':
        charges = [lane.min_charge_usd, lane.ddp_fee_usd, *(b.usd_per_kg for b in lane.bands)]
    else:
        charges = [lane.inland_cny_per_cbm, lane.inland_min_cny, lane.origin_fixed_usd, lane.origin_per_cbm_usd, lane.lcl_rate_usd_per_wm, *lane.fcl_rate_usd.values(), lane.fcl_origin_fixed_usd_per_container, lane.fcl_dest_fixed_usd_per_container, lane.dest_fixed_usd, lane.dest_per_cbm_usd, lane.last_mile_usd]
    return not any(value > 0 for value in charges)


def evaluate(result: CalcResult, inputs: CalcInputs, ctx: GateContext) -> tuple[tuple[Alert, ...], Gate]:
    from .engine import freight_suggestions, lcl_fcl_saving, packing_status
    t = inputs.config['thresholds']
    alerts: list[Alert] = []

    def add(code: str, level: str, message: str, suggestions: tuple[str, ...] = ()) -> None:
        alerts.append(Alert(code, level, message, suggestions))

    if any(line.cost_basis_cny is None or line.cost_basis_cny <= 0 for line in inputs.lines):
        add('NO_COST_BASIS', 'fuse', '缺少有效出厂价。')
    if _unpriced(inputs.shipping.lane):
        add('LANE_UNPRICED', 'fuse', '航线运价未填写。')
    if inputs.shipping.lane.valid_until < inputs.today:
        add('LANE_EXPIRED', 'fuse', '航线报价已过期。')
    if inputs.fx_age_days > t['fx_expired_days']:
        add('FX_EXPIRED', 'fuse', '汇率已过期。')
    margin = result.totals.actual_margin
    if margin is not None and margin <= t['fuse_margin']:
        add('MARGIN_FUSE', 'fuse', '实际毛利触及熔断线。')
    values = result._raw_goods_values or tuple(line.unit_goods_value_cny for line in result.lines)
    if any(line.category in t['price_floor_cny_per_unit'] and value < t['price_floor_cny_per_unit'][line.category] for line, value in zip(inputs.lines, values)):
        add('PRICE_FLOOR_FUSE', 'fuse', '单台货值低于品类底价。')
    estimated, missing = packing_status(inputs)
    if missing:
        add('PACKING_COST_MISSING', 'fuse', '航空箱成本未录入。')
    if ctx.images_ok is not None and any(not ctx.images_ok.get(line.model_id, False) for line in inputs.lines):
        add('IMAGE_MISSING', 'fuse', '缺少可对客图片。')
    if margin is not None and t['fuse_margin'] < margin < t['min_margin']:
        add('MARGIN_BELOW_MIN', 'warn', '毛利低于最低要求，需要授权和原因。')
    ratio = result.totals.freight_ratio
    bands = t['freight_ratio']
    if ratio is not None:
        if ratio > bands['inverted']:
            add('FREIGHT_EXCEEDS_GOODS', 'fuse', '运费超过出厂货值，需要确认。', freight_suggestions(inputs, result))
        elif ratio >= bands['heavy']:
            add('FREIGHT_HEAVY', 'heavy', '运费占比较重。')
        elif ratio >= bands['sensitive']:
            add('FREIGHT_SENSITIVE', 'warn', '运费占比进入敏感区间。')
    if estimated:
        add('PACKING_ESTIMATED', 'warn', '使用估算或手工包装，需要确认。')
    if t['fx_stale_days'] < inputs.fx_age_days <= t['fx_expired_days']:
        add('FX_STALE', 'warn', '汇率较旧，建议更新。')
    if any(not line.spec_complete for line in inputs.lines):
        add('SPEC_INCOMPLETE', 'warn', '关键规格缺失。')
    saving = lcl_fcl_saving(inputs, result)
    if saving is not None and saving > 0:
        add('LCL_FCL_SUGGEST', 'info', '同航线整柜主运费与起运费更低。')
    ack = [a.code for a in alerts if a.code in {'FREIGHT_EXCEEDS_GOODS', 'PACKING_ESTIMATED'}]
    hard = [a.code for a in alerts if a.level == 'fuse' and a.code not in {'IMAGE_MISSING', 'FREIGHT_EXCEEDS_GOODS'}]
    override = any(a.code == 'MARGIN_BELOW_MIN' for a in alerts)
    allowed = 'cpq.override_margin' in ctx.permissions and len(ctx.override_reason.strip()) >= t['override_reason_min_chars']
    blocked = [a.code for a in alerts if a.code in hard or a.code == 'IMAGE_MISSING' or (a.code in ack and a.code not in ctx.acks) or (a.code == 'MARGIN_BELOW_MIN' and not allowed)]
    return tuple(alerts), Gate(not hard, not blocked, ack, override, blocked)
