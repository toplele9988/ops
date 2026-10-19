export const SEVERITY_ORDER = Object.freeze({ fuse: 0, heavy: 1, warn: 2, info: 3 });
export const OVERRIDE_REASON_MIN = 10;
export const SLIDER_MIN = -0.10;
export const SLIDER_MAX = 0.60;
export const SLIDER_STEP = 0.005;

const ACTIONS = ['save', 'pdf', 'whatsapp', 'zip', 'letter'];
const TERMS = Object.freeze({
  sea_lcl: Object.freeze(['FOB', 'CIF', 'DDP']),
  sea_fcl: Object.freeze(['FOB', 'CIF', 'DDP']),
  express: Object.freeze(['DAP', 'DDP']),
});
const SEA_CHARGES = [
  'inland_cny_per_cbm', 'inland_min_cny', 'origin_fixed_usd',
  'origin_per_cbm_usd', 'lcl_rate_usd_per_wm',
  'fcl_origin_fixed_usd_per_container', 'fcl_dest_fixed_usd_per_container',
  'dest_fixed_usd', 'dest_per_cbm_usd', 'last_mile_usd',
];
const owns = (value, key) => Object.prototype.hasOwnProperty.call(value, key);
const clamp = (value, min, max) => Math.min(max, Math.max(min, value));
const compareText = (a, b) => a < b ? -1 : a > b ? 1 : 0;
const finiteOrZero = value => typeof value === 'number' && Number.isFinite(value) ? value : 0;

function finite(value, name) {
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    throw new TypeError(`${name} must be a finite number`);
  }
  return value;
}

function identifier(value, name) {
  if (typeof value !== 'string' || !value || value.trim() !== value) {
    throw new TypeError(`${name} must be a nonempty trimmed string`);
  }
  return value;
}

function nonnegative(value, name) {
  if (finite(value, name) < 0) throw new RangeError(`${name} must be nonnegative`);
  return value;
}

export function sortAlerts(alerts = []) {
  return alerts.map((alert, index) => ({ alert, index })).sort((a, b) => {
    const rank = severity => owns(SEVERITY_ORDER, severity) ? SEVERITY_ORDER[severity] : Infinity;
    const left = rank(a.alert.severity);
    const right = rank(b.alert.severity);
    return (left === right ? 0 : left < right ? -1 : 1)
      || compareText(String(a.alert.code ?? ''), String(b.alert.code ?? ''))
      || a.index - b.index;
  }).map(item => item.alert);
}

export function actionStates(gate, alerts = [], options = {}) {
  const { acks = [], overrideReason = '', permissions = [] } = options;
  const all = title => Object.fromEntries(ACTIONS.map(key => [key, { disabled: true, title }]));
  if (gate == null) return all('尚未计算');
  const ackable = new Set(gate.requires_ack ?? []);
  const soft = new Set([...ackable, 'MARGIN_BELOW_MIN', 'IMAGE_MISSING']);
  const blocked = [...new Set([
    ...(gate.blocked_by ?? []),
    ...sortAlerts(alerts).filter(alert => alert.severity === 'fuse').map(alert => alert.code),
  ])].filter(code => !soft.has(code));
  if (blocked.length || gate.can_save === false) return all(`阻断:${blocked.join(',') || '当前计算不可保存'}`);

  const acknowledged = new Set(acks);
  const allowed = new Set(permissions);
  const missing = [...new Set(gate.requires_ack ?? [])].filter(code => !acknowledged.has(code));
  const reasons = missing.map(code => `请先确认:${code}`);
  if ((gate.blocked_by ?? []).includes('IMAGE_MISSING')) reasons.push('缺少可对客图片');
  if (gate.requires_override) {
    if (!allowed.has('cpq.override_margin')) {
      reasons.push('毛利低于最低线,需主管放行');
    } else if (typeof overrideReason !== 'string' || overrideReason.trim().length < OVERRIDE_REASON_MIN) {
      reasons.push(`请填写至少 ${OVERRIDE_REASON_MIN} 字放行原因`);
    }
  }
  return Object.fromEntries(ACTIONS.map(key => {
    if (key === 'save') {
      const disabled = gate.can_save === false;
      return [key, { disabled, title: disabled ? '当前计算不可保存' : '' }];
    }
    return [key, { disabled: reasons.length > 0, title: reasons.join(';') }];
  }));
}

export function sliderZones(thresholds, { min = SLIDER_MIN, max = SLIDER_MAX } = {}) {
  finite(min, 'min');
  finite(max, 'max');
  if (max <= min) throw new RangeError('max must exceed min');
  const position = value => clamp((finite(value, 'threshold') - min) / (max - min) * 100, 0, 100);
  return { fuse_pct: position(thresholds.fuse_margin), min_pct: position(thresholds.min_margin) };
}

export function snapMargin(x) {
  const bounded = clamp(finite(x, 'margin'), SLIDER_MIN, SLIDER_MAX);
  const snapped = SLIDER_MIN + Math.round((bounded - SLIDER_MIN) / SLIDER_STEP) * SLIDER_STEP;
  return clamp(Number(snapped.toFixed(12)), SLIDER_MIN, SLIDER_MAX);
}

export function marginZone(m, thresholds) {
  finite(m, 'margin');
  const fuse = finite(thresholds.fuse_margin, 'fuse_margin');
  const minimum = finite(thresholds.min_margin, 'min_margin');
  return m <= fuse ? 'fuse' : m < minimum ? 'warn' : 'ok';
}

export function freightLevel(ratio, thresholds) {
  finite(ratio, 'ratio');
  const sensitive = finite(thresholds.sensitive, 'sensitive');
  const heavy = finite(thresholds.heavy, 'heavy');
  const inverted = finite(thresholds.inverted, 'inverted');
  return ratio < sensitive ? 'ok' : ratio < heavy ? 'warn' : ratio <= inverted ? 'heavy' : 'fuse';
}

export function incotermOptions(mode) {
  return owns(TERMS, mode) ? [...TERMS[mode]] : [];
}

export function coerceIncoterm(mode, incoterm) {
  const options = incotermOptions(mode);
  if (!options.length) throw new RangeError('Invalid shipping mode');
  return options.includes(incoterm) ? incoterm : options.includes('DDP') ? 'DDP' : options[0];
}

function positiveCharge(value) {
  if (typeof value !== 'number' && typeof value !== 'string') return false;
  const number = Number(value);
  return Number.isFinite(number) && number > 0;
}

export function laneState(lane, todayISO) {
  if (!lane || typeof lane !== 'object') return { usable: false, reason: 'unpriced' };
  if (typeof lane.valid_until === 'string' && lane.valid_until < todayISO) {
    return { usable: false, reason: 'expired' };
  }
  let charges;
  if (lane.mode === 'sea') {
    charges = [
      ...SEA_CHARGES.map(key => lane[key]),
      ...Object.values(lane.fcl_rate_usd ?? {}),
    ];
  } else if (lane.mode === 'express') {
    charges = [
      lane.min_charge_usd, lane.ddp_fee_usd,
      ...(lane.bands ?? []).flatMap(band => [band.usd_per_kg, band.rate]),
    ];
  } else {
    return { usable: false, reason: 'unpriced' };
  }
  return charges.some(positiveCharge)
    ? { usable: true, reason: '' }
    : { usable: false, reason: 'unpriced' };
}

export function lanesForMode(lanes, mode) {
  const laneMode = mode === 'sea_lcl' || mode === 'sea_fcl' ? 'sea' : mode === 'express' ? 'express' : null;
  return laneMode === null ? [] : (lanes ?? []).filter(lane => lane.mode === laneMode);
}

function requestFromState(state) {
  const pricing_mode = state.pricing_mode;
  if (pricing_mode !== 'margin' && pricing_mode !== 'price') throw new RangeError('Invalid pricing_mode');
  const target_margin = finite(state.target_margin, 'target_margin');
  if (target_margin <= -1 || target_margin >= 1) throw new RangeError('Invalid target_margin');
  const { mode, incoterm, lane_id } = state.shipping;
  if (!incotermOptions(mode).includes(incoterm)) throw new RangeError('Invalid incoterm');
  identifier(lane_id, 'lane_id');
  const models = new Set();
  const lines = state.lines.map(line => {
    const model_id = identifier(line.model_id, 'model_id');
    if (models.has(model_id)) throw new RangeError('Duplicate model_id');
    models.add(model_id);
    if (!Number.isSafeInteger(line.qty) || line.qty <= 0) throw new RangeError('Invalid qty');
    if (!['carton', 'flight_case'].includes(line.packing_mode)) throw new RangeError('Invalid packing_mode');
    if (!Array.isArray(line.accessories)) throw new TypeError('Invalid accessories');
    const accessoryIds = new Set();
    const accessories = line.accessories.map(item => {
      const accessory_id = identifier(item.accessory_id, 'accessory_id');
      if (accessoryIds.has(accessory_id)) throw new RangeError('Duplicate accessory_id');
      accessoryIds.add(accessory_id);
      return { accessory_id, qty_per_unit: nonnegative(item.qty_per_unit, 'qty_per_unit') };
    });
    const unit_price_usd = line.unit_price_usd === null ? null : nonnegative(line.unit_price_usd, 'unit_price_usd');
    if (pricing_mode === 'price' && (unit_price_usd === null || unit_price_usd <= 0)) {
      throw new RangeError('Price mode requires positive unit prices');
    }
    return {
      model_id, qty: line.qty, packing_mode: line.packing_mode,
      packing_override: null, accessories, unit_price_usd,
    };
  });
  return { lines, shipping: { mode, incoterm, lane_id }, target_margin, pricing_mode };
}

export function buildCalcRequest(basketState, { lanes = [], todayISO } = {}) {
  if (!Array.isArray(basketState?.lines)) return { ok: false, reason: 'INVALID_BASKET' };
  if (!basketState.lines.length) return { ok: false, reason: 'EMPTY_BASKET' };
  const shipping = basketState.shipping;
  if (shipping?.lane_id == null || shipping.lane_id === '') return { ok: false, reason: 'NO_LANE' };
  const lane = lanes.find(item => item.lane_id === shipping.lane_id);
  if (!lane) return { ok: false, reason: 'NO_LANE' };
  if (!lanesForMode([lane], shipping.mode).length || !laneState(lane, todayISO).usable) {
    return { ok: false, reason: 'LANE_UNUSABLE' };
  }
  try {
    return { ok: true, request: requestFromState(basketState) };
  } catch (error) {
    if (!(error instanceof TypeError) && !(error instanceof RangeError)) throw error;
    return { ok: false, reason: 'INVALID_BASKET' };
  }
}

/**
 * 待确认：附件没有给出 totals 的金额字段定义。
 * 此处定义展示层输入契约，不能声称它就是服务端 totals：
 * goods_usd, packing_usd, accessories_usd, freight_usd, taxes_usd,
 * profit_usd, other_usd。taxes_usd 是关税与增值税之和。
 * 调用层须在确认真实字段后映射；other_usd 也须来自明确的聚合口径。
 * masked 分支只读取允许展示的三个字段。
 */
export function costSeries(totals, masked) {
  const t = totals || {};
  const price = finiteOrZero(t.price_usd), freight = finiteOrZero(t.freight_usd), taxes = finiteOrZero(t.duty_vat_usd);
  if (masked) {
    return [
      {key: 'freight_usd', label: '运费', value: freight},
      {key: 'duty_vat_usd', label: '关税增值税', value: taxes},
      {key: 'other_usd', label: '其余', value: price - freight - taxes},
    ];
  }
  return [
    {key: 'goods_cost_usd', label: '货值', value: finiteOrZero(t.goods_cost_usd)},
    {key: 'packaging_cost_usd', label: '包装', value: finiteOrZero(t.packaging_cost_usd)},
    {key: 'accessory_cost_usd', label: '配件', value: finiteOrZero(t.accessory_cost_usd)},
    {key: 'freight_usd', label: '运费', value: freight},
    {key: 'duty_vat_usd', label: '关税增值税', value: taxes},
    {key: 'profit_usd', label: '毛利', value: price - finiteOrZero(t.cost_usd)},
  ];
}

export function formatUsd(n) {
  if (n == null || typeof n !== 'number' || !Number.isFinite(n)) return '—';
  return new Intl.NumberFormat('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(n);
}

export function formatPct(x, digits = 1) {
  if (x == null || typeof x !== 'number' || !Number.isFinite(x)) return '—';
  return new Intl.NumberFormat('en-US', {
    style: 'percent', minimumFractionDigits: digits, maximumFractionDigits: digits,
  }).format(x);
}

export function packingCompareRows(compare, masked) {
  const labels = { carton: '纸箱', flight_case: '航空箱' };
  return Object.entries(compare ?? {}).map(([key, item]) => {
    const value = item?.[masked ? 'delta_pct' : 'total_cost_usd'];
    return {
      key, label: owns(labels, key) ? labels[key] : key,
      value: typeof value === 'number' && Number.isFinite(value) ? value : null,
    };
  });
}
