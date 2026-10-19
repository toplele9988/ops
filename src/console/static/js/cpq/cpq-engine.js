export const ENGINE_VERSION = 'cpq-2026.10.1';

function sum(values) {
  let total = 0;
  for (const value of values) total += value;
  return total;
}

function makeFclPlan(containers, utilizationCbm, costUsd) {
  const plan = { containers, utilization_cbm: utilizationCbm, cost_usd: costUsd };
  Object.defineProperty(plan, 'total_containers', {
    get() { return sum(Object.values(this.containers)); },
  });
  return plan;
}

function money(value) {
  return Object.is(value, -0) ? '-0.00' : value.toFixed(2);
}

export function round2(value) {
  if (!Number.isFinite(value)) throw new RangeError('round2: invalid number');
  const fixed = value.toFixed(10);
  const negative = fixed.startsWith('-') || Object.is(value, -0);
  const unsigned = fixed.startsWith('-') ? fixed.slice(1) : fixed;
  const [coefficient, exponent = '0'] = unsigned.split('e');
  const [whole, fraction = ''] = coefficient.split('.');
  const digits = whole + fraction;
  const cut = whole.length + Number(exponent) + 2;
  let cents;
  if (cut <= 0) {
    cents = cut === 0 && digits[0] >= '5' ? 1n : 0n;
  } else {
    cents = BigInt(digits.slice(0, cut).padEnd(cut, '0'));
    if (cut < digits.length && digits[cut] >= '5') cents += 1n;
  }
  const rounded = Number(cents) / 100;
  return negative ? -rounded : rounded;
}

export function ceilTo(value, step) {
  const count = Math.ceil((value - 1e-9) / step);
  return (Object.is(count, -0) ? 0 : count) * step;
}

export function _number(value, name, positive = false) {
  if (typeof value !== 'number' || !Number.isFinite(value) || value < 0 || (positive && value === 0)) {
    throw new Error(`${name}: invalid number`);
  }
}

export function _fx(inputs) {
  const buffer = inputs.fx_buffer == null ? inputs.config.pricing.fx_buffer_pct : inputs.fx_buffer;
  return inputs.fx_rate * (1 - buffer);
}

export function _validate(inputs) {
  const shipping = inputs.shipping;
  const allowed = {
    sea_lcl: ['FOB', 'CIF', 'DDP'],
    sea_fcl: ['FOB', 'CIF', 'DDP'],
    express: ['DAP', 'DDP'],
  };
  if (!Object.hasOwn(allowed, shipping.mode)) throw new Error('BAD_MODE');
  if (!allowed[shipping.mode].includes(shipping.incoterm)) throw new Error('BAD_INCOTERM');
  const lane = shipping.lane;
  if (lane.mode !== (shipping.mode === 'express' ? 'express' : 'sea')) {
    throw new Error('LANE_MODE_MISMATCH');
  }
  if (lane.currency !== 'USD' || !['FOB', 'CIF'].includes(lane.duty_basis)) {
    throw new Error('BAD_LANE_CURRENCY_OR_DUTY_BASIS');
  }
  for (const key of [
    'inland_cny_per_cbm', 'inland_min_cny', 'origin_fixed_usd', 'origin_per_cbm_usd',
    'lcl_rate_usd_per_wm', 'lcl_min_wm', 'fcl_origin_fixed_usd_per_container',
    'fcl_dest_fixed_usd_per_container', 'dest_fixed_usd', 'dest_per_cbm_usd',
    'last_mile_usd', 'duty_rate', 'import_vat_rate', 'min_charge_usd',
    'fuel_surcharge_pct', 'ddp_fee_usd',
  ]) {
    _number(lane[key], key);
  }
  if (lane.duty_rate > 1 || lane.import_vat_rate > 1 || typeof lane.include_import_vat !== 'boolean') {
    throw new Error('BAD_TAX_PARAMETERS');
  }
  const rateNames = Object.keys(lane.fcl_rate_usd);
  const containerNames = Object.keys(inputs.config.containers);
  if (rateNames.length !== containerNames.length || !containerNames.every(name => Object.hasOwn(lane.fcl_rate_usd, name))) {
    throw new Error('FCL_RATES_REQUIRED');
  }
  for (const rate of Object.values(lane.fcl_rate_usd)) _number(rate, 'fcl_rate_usd');
  if (shipping.mode === 'express') {
    _number(lane.volumetric_divisor, 'volumetric_divisor', true);
    _number(lane.increment_kg, 'increment_kg', true);
    if (!lane.bands.length) throw new Error('EXPRESS_BANDS_REQUIRED');
    let previous = 0.0;
    for (const band of lane.bands) {
      _number(band.upto_kg, 'upto_kg', true);
      _number(band.usd_per_kg, 'usd_per_kg');
      if (band.upto_kg <= previous) throw new Error('BANDS_NOT_INCREASING');
      previous = band.upto_kg;
    }
  }
  _number(inputs.fx_rate, 'fx_rate', true);
  const buffer = inputs.fx_buffer == null ? inputs.config.pricing.fx_buffer_pct : inputs.fx_buffer;
  _number(buffer, 'fx_buffer');
  if (buffer >= 1) throw new Error('BAD_FX_BUFFER');
  if (!Number.isInteger(inputs.fx_age_days) || inputs.fx_age_days < 0) throw new Error('BAD_FX_AGE');
  if (typeof inputs.target_margin !== 'number' || !Number.isFinite(inputs.target_margin) || !(inputs.target_margin > -1 && inputs.target_margin < 1)) {
    throw new Error('BAD_TARGET_MARGIN');
  }
  if (!['margin', 'price'].includes(inputs.pricing_mode) || !inputs.lines.length) {
    throw new Error('BAD_PRICING_MODE_OR_EMPTY_LINES');
  }
  for (const line of inputs.lines) {
    if (!Number.isInteger(line.qty) || line.qty <= 0) throw new Error('BAD_QTY');
    if (!line.model_id || !['carton', 'flight_case'].includes(line.packing_mode)) throw new Error('BAD_LINE');
    for (const profile of [line.packing.carton, line.packing.flight_case, line.packing.override]) {
      if (profile == null) continue;
      if (!Number.isInteger(profile.units_per_pkg) || profile.units_per_pkg <= 0) {
        throw new Error('BAD_UNITS_PER_PKG');
      }
      for (const key of ['l_cm', 'w_cm', 'h_cm']) _number(profile[key], key, true);
      _number(profile.gw_kg, 'gw_kg');
      if (profile.case_cost_cny != null) _number(profile.case_cost_cny, 'case_cost_cny');
    }
    for (const key of ['cost_basis_cny', 'net_weight_kg', 'case_cost_cny', 'unit_price_usd']) {
      if (line[key] != null) _number(line[key], key);
    }
    if (inputs.pricing_mode === 'price') _number(line.unit_price_usd, 'unit_price_usd', true);
    for (const accessory of line.accessories) {
      _number(accessory.cost_cny, 'accessory.cost_cny');
      _number(accessory.qty_per_unit, 'accessory.qty_per_unit');
    }
  }
}

export function _resolve(line, inputs, mode = null) {
  const selected = mode === null || mode === line.packing_mode;
  mode = mode || line.packing_mode;
  let estimated = selected && line.packing_estimated;
  let profile;
  if (selected && line.packing.override != null) {
    profile = line.packing.override;
    estimated = true;
  } else if (mode === 'carton') {
    profile = line.packing.carton;
  } else if (line.packing.flight_case != null) {
    profile = line.packing.flight_case;
  } else {
    if (line.net_weight_kg == null) throw new Error('NET_WEIGHT_REQUIRED_FOR_PACKING_ESTIMATE');
    const carton = line.packing.carton;
    const estimate = inputs.config.packing_estimate;
    const pad = estimate.fc_pad_cm;
    profile = {
      units_per_pkg: carton.units_per_pkg,
      l_cm: carton.l_cm + pad,
      w_cm: carton.w_cm + pad,
      h_cm: carton.h_cm + pad,
      gw_kg: carton.units_per_pkg * line.net_weight_kg + estimate.fc_tare_kg,
      case_cost_cny: line.case_cost_cny,
    };
    estimated = true;
  }
  const missing = mode === 'flight_case' && (profile.case_cost_cny == null || (selected && line.case_cost_missing));
  return [profile, estimated, missing];
}

export function packingStatus(inputs) {
  const profiles = inputs.lines.map(line => _resolve(line, inputs));
  return [profiles.some(profile => profile[1]), profiles.some(profile => profile[2])];
}

export function _line(line, inputs, mode = null) {
  const [profile, , missing] = _resolve(line, inputs, mode);
  const packages = Math.ceil(line.qty / profile.units_per_pkg);
  const volume = profile.l_cm * profile.w_cm * profile.h_cm;
  let chg = 0.0;
  if (inputs.shipping.mode === 'express') {
    const lane = inputs.shipping.lane;
    chg = packages * ceilTo(Math.max(profile.gw_kg, volume / lane.volumetric_divisor), lane.increment_kg);
  }
  const fx = _fx(inputs);
  const pack = (mode || line.packing_mode) === 'flight_case' ? packages * (profile.case_cost_cny || 0.0) / fx : 0.0;
  const fact = {
    packages,
    cbm: packages * volume / 1000000,
    gw: packages * profile.gw_kg,
    chg,
    exw: (line.cost_basis_cny || 0.0) * line.qty / fx,
    pack,
    acc: line.qty * sum(line.accessories.map(accessory => accessory.cost_cny * accessory.qty_per_unit)) / fx,
    missing,
  };
  Object.defineProperty(fact, 'goods', {
    get() { return this.exw + this.pack + this.acc; },
  });
  return fact;
}

export function bestFclPlan(cbm, gw, containers, rates) {
  const limit = Math.min(10, Math.ceil(cbm / containers['20GP'].usable_cbm) + 1);
  const names = ['20GP', '40GP', '40HQ'];
  let best = null;
  let bestKey = null;
  for (let n20 = 0; n20 <= limit; n20 += 1) {
    for (let n40 = 0; n40 <= limit; n40 += 1) {
      for (let nhq = 0; nhq <= limit; nhq += 1) {
        const counts = [n20, n40, nhq];
        const number = sum(counts);
        const capacity = sum(names.map((name, index) => counts[index] * containers[name].usable_cbm));
        const weight = sum(names.map((name, index) => counts[index] * containers[name].max_kg));
        if (!number || capacity < cbm || weight < gw) continue;
        const ocean = sum(names.map((name, index) => counts[index] * rates[name]));
        const key = [ocean, number, n20];
        if (bestKey === null || key[0] < bestKey[0] || (key[0] === bestKey[0] && (key[1] < bestKey[1] || (key[1] === bestKey[1] && key[2] < bestKey[2])))) {
          bestKey = key;
          best = makeFclPlan(Object.fromEntries(names.map((name, index) => [name, counts[index]])), cbm / capacity, ocean);
        }
      }
    }
  }
  if (best === null) throw new Error('FCL_CAPACITY_EXCEEDED');
  return best;
}

export function _freight(inputs, lines) {
  const shipping = inputs.shipping;
  const lane = shipping.lane;
  const cbm = sum(lines.map(line => line.cbm));
  const gw = sum(lines.map(line => line.gw));
  let plan = null;
  if (shipping.mode === 'express') {
    const chg = sum(lines.map(line => line.chg));
    let rate = lane.bands[lane.bands.length - 1].usd_per_kg;
    for (const band of lane.bands) {
      if (chg <= band.upto_kg) {
        rate = band.usd_per_kg;
        break;
      }
    }
    const main = Math.max(lane.min_charge_usd, chg * rate) * (1 + lane.fuel_surcharge_pct);
    return [[0.0, 0.0, main, shipping.incoterm === 'DDP' ? lane.ddp_fee_usd : 0.0], null];
  }
  const inland = Math.max(lane.inland_min_cny, lane.inland_cny_per_cbm * cbm) / _fx(inputs);
  let origin;
  let main;
  let dest;
  if (shipping.mode === 'sea_lcl') {
    origin = lane.origin_fixed_usd + lane.origin_per_cbm_usd * cbm;
    main = Math.max(cbm, gw / 1000, lane.lcl_min_wm) * lane.lcl_rate_usd_per_wm;
    dest = lane.dest_fixed_usd + lane.dest_per_cbm_usd * cbm + lane.last_mile_usd;
  } else {
    plan = bestFclPlan(cbm, gw, inputs.config.containers, lane.fcl_rate_usd);
    origin = plan.total_containers * lane.fcl_origin_fixed_usd_per_container;
    main = plan.cost_usd;
    dest = plan.total_containers * lane.fcl_dest_fixed_usd_per_container + lane.last_mile_usd;
  }
  return [[inland, origin, shipping.incoterm !== 'FOB' ? main : 0.0, shipping.incoterm === 'DDP' ? dest : 0.0], plan];
}

export function solveDdp(price, post, main, insurance, lane) {
  let duty = 0.0;
  let vat = 0.0;
  let dutyNew = 0.0;
  let vatNew = 0.0;
  for (let iteration = 0; iteration < 50; iteration += 1) {
    const cif = price - post - duty - vat;
    const customs = lane.duty_basis === 'CIF' ? cif : cif - main - insurance;
    dutyNew = lane.duty_rate * customs;
    vatNew = lane.include_import_vat ? lane.import_vat_rate * (cif + dutyNew) : 0.0;
    if (Math.abs(dutyNew - duty) + Math.abs(vatNew - vat) < 1e-6) break;
    duty = dutyNew;
    vat = vatNew;
  }
  return [dutyNew, vatNew];
}

export function _costsAt(price, goods, freight, inputs) {
  const pricing = inputs.config.pricing;
  const shipping = inputs.shipping;
  const insurance = shipping.mode !== 'express' && ['CIF', 'DDP'].includes(shipping.incoterm)
    ? price * pricing.insured_markup * pricing.insurance_rate : 0.0;
  const fee = price * pricing.payment_fee_pct;
  const [duty, vat] = shipping.incoterm === 'DDP'
    ? solveDdp(price, freight[3], freight[2], insurance, shipping.lane) : [0.0, 0.0];
  return { insurance, fee, duty, vat, total: goods + sum(freight) + insurance + fee + duty + vat };
}

export function _calculate(inputs, mode = null) {
  const lines = inputs.lines.map(line => _line(line, inputs, mode));
  const [freight, plan] = _freight(inputs, lines);
  const goods = sum(lines.map(line => line.goods));
  const weights = lines.map(line => inputs.shipping.mode === 'express' ? line.chg : line.cbm);
  const shares = weights.map(weight => weight / sum(weights));
  const denominator = 1 - inputs.target_margin;
  let price = (goods + sum(freight)) / denominator;
  if (inputs.pricing_mode === 'margin') {
    for (let iteration = 0; iteration < 50; iteration += 1) {
      const updated = _costsAt(price, goods, freight, inputs).total / denominator;
      if (!Number.isFinite(updated)) throw new Error('NONFINITE_PRICE');
      if (Math.abs(updated - price) < 1e-6) break;
      price = updated;
    }
  } else {
    price = sum(inputs.lines.map(line => Number(line.unit_price_usd) * line.qty));
  }
  const logistics = _costsAt(price, goods, freight, inputs).total - goods;
  const raw = lines.map((fact, index) => (fact.goods + logistics * shares[index]) / denominator / inputs.lines[index].qty);
  const units = inputs.pricing_mode === 'margin'
    ? raw.map(unit => ceilTo(unit, inputs.config.pricing.unit_price_step_usd))
    : inputs.lines.map(line => Number(line.unit_price_usd));
  const final = sum(units.map((unit, index) => unit * inputs.lines[index].qty));
  return {
    lines, freight, plan, goods, equilibrium_price: price, unit_raw: raw,
    units, shares, price: final, costs: _costsAt(final, goods, freight, inputs),
  };
}

export function _comparison(inputs, mode) {
  let raw;
  try {
    raw = _calculate(inputs, mode);
  } catch (error) {
    if (!['NET_WEIGHT_REQUIRED_FOR_PACKING_ESTIMATE', 'FCL_CAPACITY_EXCEEDED'].includes(error.message)) throw error;
    return { packages: null, cbm: null, gw_kg: null, total_cost_usd: null };
  }
  const known = inputs.lines.every(line => line.cost_basis_cny != null && line.cost_basis_cny > 0)
    && !raw.lines.some(line => line.missing);
  return {
    packages: sum(raw.lines.map(line => line.packages)),
    cbm: sum(raw.lines.map(line => line.cbm)),
    gw_kg: sum(raw.lines.map(line => line.gw)),
    total_cost_usd: known ? round2(raw.costs.total) : null,
  };
}

export function lclFclSaving(inputs, result) {
  if (inputs.shipping.mode !== 'sea_lcl') return null;
  const lane = inputs.shipping.lane;
  const cbm = result.totals.cbm;
  const gw = result.totals.gw_kg;
  let plan;
  try {
    plan = bestFclPlan(cbm, gw, inputs.config.containers, lane.fcl_rate_usd);
  } catch (error) {
    if (error.message !== 'FCL_CAPACITY_EXCEEDED') throw error;
    return null;
  }
  const lcl = Math.max(cbm, gw / 1000, lane.lcl_min_wm) * lane.lcl_rate_usd_per_wm
    + lane.origin_fixed_usd + lane.origin_per_cbm_usd * cbm;
  return lcl - plan.cost_usd - plan.total_containers * lane.fcl_origin_fixed_usd_per_container;
}

export function freightSuggestions(inputs, result) {
  const carton = result.packing_compare.carton.total_cost_usd;
  const flight = result.packing_compare.flight_case.total_cost_usd;
  const packing = carton === null || flight === null
    ? '包装差额待确认：请补齐包装档案、净重和航空箱成本。'
    : `纸箱 ${money(carton)} USD，航空箱 ${money(flight)} USD；航空箱减纸箱差额 ${money(round2(flight - carton))} USD。`;
  const saving = lclFclSaving(inputs, result);
  const fcl = saving === null
    ? '当前方式或柜容限制不适用 LCL 改 FCL。'
    : `LCL 减 FCL 的主运费与起运费差额 ${money(round2(saving))} USD；其他费用按新方案重算。`;
  const terms = inputs.shipping.mode === 'express' ? ['DAP'] : ['FOB', 'CIF'];
  const prices = [];
  for (const term of terms) {
    const alternate = { ...inputs, shipping: { ...inputs.shipping, incoterm: term }, pricing_mode: 'margin' };
    prices.push(`${term} ${money(round2(_calculate(alternate).price))} USD`);
  }
  const alternative = (inputs.shipping.mode === 'express' ? '快件不适用 FOB/CIF。' : '')
    + '按当前目标毛利改报：' + prices.join('；') + '，由买家安排进口清关及税费。';
  return [packing, fcl, alternative, '建议提高起订量以摊薄起运港固定费；具体起订量需业务确认。'];
}

export function _unpriced(lane) {
  const charges = lane.mode === 'express'
    ? [lane.min_charge_usd, lane.ddp_fee_usd, ...lane.bands.map(band => band.usd_per_kg)]
    : [lane.inland_cny_per_cbm, lane.inland_min_cny, lane.origin_fixed_usd,
      lane.origin_per_cbm_usd, lane.lcl_rate_usd_per_wm, ...Object.values(lane.fcl_rate_usd),
      lane.fcl_origin_fixed_usd_per_container, lane.fcl_dest_fixed_usd_per_container,
      lane.dest_fixed_usd, lane.dest_per_cbm_usd, lane.last_mile_usd];
  return !charges.some(value => value > 0);
}

export function evaluateGates(result, inputs, ctx = {}) {
  const t = inputs.config.thresholds;
  const alerts = [];
  const add = (code, level, message, suggestions = []) => {
    alerts.push({ code, level, message, suggestions });
  };
  if (inputs.lines.some(line => line.cost_basis_cny == null || line.cost_basis_cny <= 0)) {
    add('NO_COST_BASIS', 'fuse', '缺少有效出厂价。');
  }
  if (_unpriced(inputs.shipping.lane)) add('LANE_UNPRICED', 'fuse', '航线运价未填写。');
  if (inputs.shipping.lane.valid_until < inputs.today) add('LANE_EXPIRED', 'fuse', '航线报价已过期。');
  if (inputs.fx_age_days > t.fx_expired_days) add('FX_EXPIRED', 'fuse', '汇率已过期。');
  const margin = result.totals.actual_margin;
  if (margin !== null && margin <= t.fuse_margin) add('MARGIN_FUSE', 'fuse', '实际毛利触及熔断线。');
  const values = result._raw_goods_values?.length
    ? result._raw_goods_values : result.lines.map(line => line.unit_goods_value_cny);
  if (inputs.lines.some((line, index) => index < values.length && Object.hasOwn(t.price_floor_cny_per_unit, line.category)
    && values[index] < t.price_floor_cny_per_unit[line.category])) {
    add('PRICE_FLOOR_FUSE', 'fuse', '单台货值低于品类底价。');
  }
  const [estimated, missing] = packingStatus(inputs);
  if (missing) add('PACKING_COST_MISSING', 'fuse', '航空箱成本未录入。');
  if (ctx.images_ok != null && inputs.lines.some(line => !Object.hasOwn(ctx.images_ok, line.model_id) || !ctx.images_ok[line.model_id])) {
    add('IMAGE_MISSING', 'fuse', '缺少可对客图片。');
  }
  if (margin !== null && t.fuse_margin < margin && margin < t.min_margin) {
    add('MARGIN_BELOW_MIN', 'warn', '毛利低于最低要求，需要授权和原因。');
  }
  const ratio = result.totals.freight_ratio;
  const bands = t.freight_ratio;
  if (ratio !== null) {
    if (ratio > bands.inverted) {
      add('FREIGHT_EXCEEDS_GOODS', 'fuse', '运费超过出厂货值，需要确认。', freightSuggestions(inputs, result));
    } else if (ratio >= bands.heavy) {
      add('FREIGHT_HEAVY', 'heavy', '运费占比较重。');
    } else if (ratio >= bands.sensitive) {
      add('FREIGHT_SENSITIVE', 'warn', '运费占比进入敏感区间。');
    }
  }
  if (estimated) add('PACKING_ESTIMATED', 'warn', '使用估算或手工包装，需要确认。');
  if (t.fx_stale_days < inputs.fx_age_days && inputs.fx_age_days <= t.fx_expired_days) {
    add('FX_STALE', 'warn', '汇率较旧，建议更新。');
  }
  if (inputs.lines.some(line => !line.spec_complete)) add('SPEC_INCOMPLETE', 'warn', '关键规格缺失。');
  const saving = lclFclSaving(inputs, result);
  if (saving !== null && saving > 0) add('LCL_FCL_SUGGEST', 'info', '同航线整柜主运费与起运费更低。');
  const ack = alerts.filter(alert => ['FREIGHT_EXCEEDS_GOODS', 'PACKING_ESTIMATED'].includes(alert.code)).map(alert => alert.code);
  const hard = alerts.filter(alert => alert.level === 'fuse' && !['IMAGE_MISSING', 'FREIGHT_EXCEEDS_GOODS'].includes(alert.code)).map(alert => alert.code);
  const override = alerts.some(alert => alert.code === 'MARGIN_BELOW_MIN');
  const permissions = new Set(ctx.permissions ?? []);
  const acks = new Set(ctx.acks ?? []);
  const reason = (ctx.override_reason ?? '').replace(/^[\u0009-\u000d\u001c-\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]+|[\u0009-\u000d\u001c-\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]+$/gu, '');
  const allowed = permissions.has('cpq.override_margin') && [...reason].length >= t.override_reason_min_chars;
  const blocked = alerts.filter(alert => hard.includes(alert.code) || alert.code === 'IMAGE_MISSING'
    || (ack.includes(alert.code) && !acks.has(alert.code))
    || (alert.code === 'MARGIN_BELOW_MIN' && !allowed)).map(alert => alert.code);
  return [alerts, {
    can_save: hard.length === 0,
    can_export: blocked.length === 0,
    requires_ack: ack,
    requires_override: override,
    blocked_by: blocked,
  }];
}

export function compute(inputs) {
  _validate(inputs);
  const raw = _calculate(inputs);
  const logistics = raw.costs.total - raw.goods;
  const lines = [];
  const values = [];
  for (let index = 0; index < inputs.lines.length; index += 1) {
    const source = inputs.lines[index];
    const fact = raw.lines[index];
    const unit = raw.units[index];
    const share = raw.shares[index];
    const allocation = logistics * share;
    const value = (unit - allocation / source.qty) * inputs.fx_rate;
    values.push(value);
    lines.push({
      model_id: source.model_id,
      qty: source.qty,
      packages: fact.packages,
      cbm: fact.cbm,
      gw_kg: fact.gw,
      chargeable_kg: fact.chg,
      unit_price_usd: round2(unit),
      amount_usd: round2(unit * source.qty),
      logistics_alloc_usd: round2(allocation),
      unit_goods_value_cny: round2(value),
      cost_usd: round2(fact.goods + allocation),
    });
  }
  const exw = sum(raw.lines.map(line => line.exw));
  const freight = sum(raw.freight) + raw.costs.insurance;
  const totals = {
    packages: sum(raw.lines.map(line => line.packages)),
    cbm: sum(raw.lines.map(line => line.cbm)),
    gw_kg: sum(raw.lines.map(line => line.gw)),
    chargeable_kg: sum(raw.lines.map(line => line.chg)),
    price_usd: round2(raw.price),
    cost_usd: round2(raw.costs.total),
    actual_margin: raw.price ? (raw.price - raw.costs.total) / raw.price : null,
    goods_cost_usd: round2(exw),
    packaging_cost_usd: round2(sum(raw.lines.map(line => line.pack))),
    accessory_cost_usd: round2(sum(raw.lines.map(line => line.acc))),
    freight_usd: round2(freight),
    duty_vat_usd: round2(raw.costs.duty + raw.costs.vat),
    payment_fee_usd: round2(raw.costs.fee),
    freight_ratio: exw ? freight / exw : null,
  };
  const breakdown = {
    inland_usd: round2(raw.freight[0]),
    origin_usd: round2(raw.freight[1]),
    main_carriage_usd: round2(raw.freight[2]),
    insurance_usd: round2(raw.costs.insurance),
    dest_usd: round2(raw.freight[3]),
    duty_usd: round2(raw.costs.duty),
    vat_usd: round2(raw.costs.vat),
  };
  const plan = raw.plan === null ? null
    : makeFclPlan(raw.plan.containers, raw.plan.utilization_cbm, round2(raw.plan.cost_usd));
  const result = {
    lines,
    totals,
    breakdown,
    fcl_plan: plan,
    packing_compare: { carton: _comparison(inputs, 'carton'), flight_case: _comparison(inputs, 'flight_case') },
    alerts: [],
    gate: { can_save: true, can_export: true, requires_ack: [], requires_override: false, blocked_by: [] },
    engine_version: ENGINE_VERSION,
  };
  Object.defineProperty(result, '_raw_goods_values', { value: Object.freeze(values) });
  const [alerts, gate] = evaluateGates(result, inputs, {});
  result.alerts = alerts;
  result.gate = gate;
  return result;
}

export const py = Object.freeze({
  round2,
  ceil_to: ceilTo,
  _number,
  _fx,
  _validate,
  _resolve,
  packing_status: packingStatus,
  _line,
  best_fcl_plan: bestFclPlan,
  _freight,
  solve_ddp: solveDdp,
  _costs_at: _costsAt,
  _calculate,
  _comparison,
  lcl_fcl_saving: lclFclSaving,
  freight_suggestions: freightSuggestions,
  compute,
  _unpriced,
  evaluate: evaluateGates,
});
