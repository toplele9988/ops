import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const moduleURL = new URL('../../src/console/static/js/cpq/drawer-model.js', import.meta.url);
const basketURL = new URL('../../src/console/static/js/cpq/basket.js', import.meta.url);
const source = await readFile(moduleURL, 'utf8');
// Data URLs force ES module parsing without changing repository package settings.
const importSource = text => import(`data:text/javascript;base64,${Buffer.from(text).toString('base64')}`);
const model = await importSource(source);
const {
  SEVERITY_ORDER, OVERRIDE_REASON_MIN, SLIDER_MIN, SLIDER_MAX, SLIDER_STEP,
  sortAlerts, actionStates, sliderZones, snapMargin, marginZone, freightLevel,
  incotermOptions, coerceIncoterm, laneState, lanesForMode, buildCalcRequest,
  costSeries, formatUsd, formatPct, packingCompareRows,
} = model;

function freeze(value) {
  if (value && typeof value === 'object') {
    Object.values(value).forEach(freeze);
    Object.freeze(value);
  }
  return value;
}

const close = (actual, expected) => assert.ok(Math.abs(actual - expected) < 1e-9, `${actual} != ${expected}`);
const gate = patch => ({ can_save: true, can_export: true, requires_ack: [], requires_override: false, blocked_by: [], ...patch });
const exportKeys = ['pdf', 'whatsapp', 'zip', 'letter'];
function exportsAre(states, disabled, title = '') {
  for (const key of exportKeys) assert.deepEqual(states[key], { disabled, title });
}

// Dates, rates and quantities below are synthetic test fixtures.
const todayISO = '2030-06-10';
const seaLane = patch => ({ lane_id: 'SEA_TEST', mode: 'sea', valid_until: todayISO, lcl_rate_usd_per_wm: 2, fcl_rate_usd: {}, ...patch });
const expressLane = patch => ({ lane_id: 'EXPRESS_TEST', mode: 'express', valid_until: todayISO, bands: [], min_charge_usd: 0, ddp_fee_usd: 0, ...patch });

const tests = {
  test_action_states_fuse_blocks_all() {
    assert.deepEqual(SEVERITY_ORDER, { fuse: 0, heavy: 1, warn: 2, info: 3 });
    const alerts = freeze([
      { code: 'B', severity: 'warn', marker: 1 },
      { code: 'A', severity: 'warn', marker: 2 },
      { code: 'Z', severity: 'fuse' },
      { code: 'A', severity: 'warn', marker: 3 },
      { code: 'H', severity: 'heavy' },
      { code: 'I', severity: 'info' },
    ]);
    const before = JSON.stringify(alerts);
    const sorted = sortAlerts(alerts);
    assert.deepEqual(sorted.map(a => a.code), ['Z', 'H', 'A', 'A', 'B', 'I']);
    assert.deepEqual(sorted.filter(a => a.code === 'A').map(a => a.marker), [2, 3]);
    assert.equal(JSON.stringify(alerts), before);
    for (const states of [
      actionStates(freeze(gate({})), alerts),
      actionStates(freeze(gate({ blocked_by: ['Z'] })), []),
    ]) {
      assert.deepEqual(Object.keys(states), ['save', ...exportKeys]);
      for (const state of Object.values(states)) {
        assert.equal(state.disabled, true);
        assert.ok(state.title.includes('Z'));
      }
    }
    const combined = actionStates(gate({ blocked_by: ['A', 'Z'] }), alerts);
    assert.equal(combined.save.title, '阻断:A,Z');
    for (const state of Object.values(actionStates(null, []))) {
      assert.deepEqual(state, { disabled: true, title: '尚未计算' });
    }
  },

  test_action_states_ack_and_override() {
    assert.equal(OVERRIDE_REASON_MIN, 10);
    const needsAck = freeze(gate({ requires_ack: ['PACKING_ESTIMATED', 'OTHER_ACK'] }));
    let states = actionStates(needsAck, [], { acks: ['OTHER_ACK'] });
    assert.deepEqual(states.save, { disabled: false, title: '' });
    exportsAre(states, true, '请先确认:PACKING_ESTIMATED');
    exportsAre(actionStates(needsAck, [], { acks: new Set(needsAck.requires_ack) }), false);
    const needsOverride = freeze(gate({ requires_override: true, can_export: false }));
    exportsAre(actionStates(needsOverride, []), true, '毛利低于最低线,需主管放行');
    const permissions = freeze(['cpq.override_margin']);
    exportsAre(actionStates(needsOverride, [], { permissions, overrideReason: '  123456789  ' }), true, '请填写至少 10 字放行原因');
    exportsAre(actionStates(needsOverride, [], { permissions, overrideReason: '  1234567890  ' }), false);
    states = actionStates(gate({ can_save: false }), []);
    assert.equal(states.save.disabled, true);
    assert.ok(states.save.title);
    exportsAre(states, true, '阻断:当前计算不可保存');
    states = actionStates(gate({ requires_ack: ['ACK'], requires_override: true }), []);
    assert.ok(states.pdf.title.includes('请先确认:ACK'));
    assert.ok(states.pdf.title.includes('毛利低于最低线,需主管放行'));
  },

  test_slider_zones_and_margin_zone() {
    const thresholds = freeze({ fuse_margin: -0.021, min_margin: 0.15 });
    assert.equal(SLIDER_MIN, -0.10);
    assert.equal(SLIDER_MAX, 0.60);
    assert.equal(SLIDER_STEP, 0.005);
    const zones = sliderZones(thresholds, { min: SLIDER_MIN, max: SLIDER_MAX });
    close(zones.fuse_pct, (-0.021 + 0.10) / 0.70 * 100);
    close(zones.min_pct, (0.15 + 0.10) / 0.70 * 100);
    assert.deepEqual(sliderZones({ fuse_margin: -2, min_margin: 3 }, { min: 0, max: 1 }), { fuse_pct: 0, min_pct: 100 });
    assert.throws(() => sliderZones(thresholds, { min: 1, max: 1 }), RangeError);
    assert.equal(marginZone(-0.022, thresholds), 'fuse');
    assert.equal(marginZone(-0.021, thresholds), 'fuse');
    assert.equal(marginZone(-0.020, thresholds), 'warn');
    assert.equal(marginZone(0.149, thresholds), 'warn');
    assert.equal(marginZone(0.15, thresholds), 'ok');
    assert.equal(snapMargin(-2), SLIDER_MIN);
    assert.equal(snapMargin(2), SLIDER_MAX);
    assert.equal(snapMargin(0.123), 0.125);
    assert.equal(snapMargin(-0.023), -0.025);
    assert.equal(snapMargin(0), 0);
    assert.throws(() => snapMargin(NaN), TypeError);
  },

  test_freight_level_bands() {
    const thresholds = freeze({ sensitive: 0.30, heavy: 0.70, inverted: 1.00 });
    for (const [ratio, expected] of [
      [0, 'ok'], [0.299, 'ok'], [0.30, 'warn'], [0.699, 'warn'],
      [0.70, 'heavy'], [1.00, 'heavy'], [1.001, 'fuse'],
    ]) assert.equal(freightLevel(ratio, thresholds), expected);
  },

  test_incoterm_rules() {
    for (const mode of ['sea_lcl', 'sea_fcl']) {
      assert.deepEqual(incotermOptions(mode), ['FOB', 'CIF', 'DDP']);
      assert.equal(coerceIncoterm(mode, 'CIF'), 'CIF');
      assert.equal(coerceIncoterm(mode, 'DAP'), 'DDP');
    }
    assert.deepEqual(incotermOptions('express'), ['DAP', 'DDP']);
    assert.equal(coerceIncoterm('express', 'DAP'), 'DAP');
    assert.equal(coerceIncoterm('express', 'FOB'), 'DDP');
    const options = incotermOptions('express');
    options.pop();
    assert.deepEqual(incotermOptions('express'), ['DAP', 'DDP']);
    assert.deepEqual(incotermOptions('unknown'), []);
    assert.throws(() => coerceIncoterm('unknown', 'DDP'), RangeError);
  },

  test_lane_state() {
    const usable = { usable: true, reason: '' };
    const unpriced = { usable: false, reason: 'unpriced' };
    assert.deepEqual(laneState(freeze(seaLane({})), todayISO), usable);
    assert.deepEqual(laneState(seaLane({ valid_until: '2030-06-09', lcl_rate_usd_per_wm: 0 }), todayISO), { usable: false, reason: 'expired' });
    assert.deepEqual(laneState(seaLane({ lcl_rate_usd_per_wm: 0 }), todayISO), unpriced);
    assert.deepEqual(laneState(seaLane({ lcl_rate_usd_per_wm: null, fcl_rate_usd: { '20GP': 0, '40HQ': 4 } }), todayISO), usable);
    for (const key of [
      'inland_cny_per_cbm', 'inland_min_cny', 'origin_fixed_usd', 'origin_per_cbm_usd',
      'fcl_origin_fixed_usd_per_container', 'fcl_dest_fixed_usd_per_container',
      'dest_fixed_usd', 'dest_per_cbm_usd', 'last_mile_usd',
    ]) assert.deepEqual(laneState(seaLane({ lcl_rate_usd_per_wm: 0, [key]: 1 }), todayISO), usable);
    assert.deepEqual(laneState(expressLane({}), todayISO), unpriced);
    assert.deepEqual(laneState(expressLane({ bands: [{ usd_per_kg: 0 }, { usd_per_kg: 2 }] }), todayISO), usable);
    assert.deepEqual(laneState(expressLane({ bands: [{ rate: 2 }] }), todayISO), usable);
    assert.deepEqual(laneState(expressLane({ min_charge_usd: 2 }), todayISO), usable);
    assert.deepEqual(laneState(expressLane({ ddp_fee_usd: 2 }), todayISO), usable);
    assert.deepEqual(laneState(expressLane({ bands: [{ usd_per_kg: null }, { rate: '' }] }), todayISO), unpriced);
    const lanes = freeze([seaLane({}), expressLane({ min_charge_usd: 1 })]);
    assert.deepEqual(lanesForMode(lanes, 'sea_lcl'), [lanes[0]]);
    assert.deepEqual(lanesForMode(lanes, 'sea_fcl'), [lanes[0]]);
    assert.deepEqual(lanesForMode(lanes, 'express'), [lanes[1]]);
    assert.deepEqual(lanesForMode(lanes, 'unknown'), []);
  },

  async test_build_calc_request_guards() {
    const { createBasket } = await importSource(await readFile(basketURL, 'utf8'));
    const basket = createBasket({ getItem: () => null, setItem: () => {} }, { target_margin: 0.20 });
    const options = freeze({ lanes: [seaLane({})], todayISO });
    assert.deepEqual(buildCalcRequest(basket.state, options), { ok: false, reason: 'EMPTY_BASKET' });
    basket.add('MODEL_TEST', 2);
    assert.deepEqual(buildCalcRequest(basket.state, options), { ok: false, reason: 'NO_LANE' });
    basket.setShipping({ lane_id: 'SEA_TEST' });
    assert.deepEqual(buildCalcRequest(basket.state, { lanes: [], todayISO }), { ok: false, reason: 'NO_LANE' });
    assert.deepEqual(buildCalcRequest(basket.state, { lanes: [seaLane({ valid_until: '2030-06-09' })], todayISO }), { ok: false, reason: 'LANE_UNUSABLE' });
    assert.deepEqual(buildCalcRequest(basket.state, { lanes: [seaLane({ lcl_rate_usd_per_wm: 0 })], todayISO }), { ok: false, reason: 'LANE_UNUSABLE' });
    assert.deepEqual(buildCalcRequest(basket.state, { lanes: [expressLane({ lane_id: 'SEA_TEST', min_charge_usd: 1 })], todayISO }), { ok: false, reason: 'LANE_UNUSABLE' });
    basket.setAccessory('MODEL_TEST', 'ACCESSORY_TEST', 1);
    basket.setPacking('MODEL_TEST', 'flight_case');
    const state = basket.state;
    state.cost_debug = { exw: 99 };
    state.shipping.supplier_name = 'ignored';
    state.lines[0].packing_override = { case_cost_cny: 99 };
    state.lines[0].offer_price = 99;
    state.lines[0].accessories[0].cost_cny = 99;
    freeze(state);
    const before = JSON.stringify(state);
    const result = buildCalcRequest(state, options);
    assert.equal(result.ok, true);
    assert.deepEqual(result.request, basket.toRequest());
    assert.equal(JSON.stringify(state), before);
    assert.notEqual(result.request.lines[0].accessories, state.lines[0].accessories);
    function keyPaths(value, path = '') {
      if (!value || typeof value !== 'object') return [];
      if (Array.isArray(value)) return value.flatMap(item => keyPaths(item, `${path}[]`));
      return Object.entries(value).flatMap(([key, child]) => {
        assert.equal(/cost|exw|supplier|offer/i.test(key), false, key);
        const next = `${path}.${key}`;
        return [next, ...keyPaths(child, next)];
      }).sort();
    }
    assert.deepEqual(keyPaths(result.request), keyPaths(basket.toRequest()));
    result.request.lines[0].accessories[0].qty_per_unit = 5;
    assert.equal(state.lines[0].accessories[0].qty_per_unit, 1);
    basket.setPricingMode('price');
    assert.deepEqual(buildCalcRequest(basket.state, options), { ok: false, reason: 'INVALID_BASKET' });
    basket.setUnitPrice('MODEL_TEST', 8);
    assert.deepEqual(buildCalcRequest(basket.state, options), { ok: true, request: basket.toRequest() });
    const malformed = basket.state;
    malformed.lines[0].model_id = { supplier_secret: 1 };
    assert.deepEqual(buildCalcRequest(malformed, options), { ok: false, reason: 'INVALID_BASKET' });
  },

  test_cost_series_masked() {
    const totals = freeze({ price_usd: 100, cost_usd: 94, goods_cost_usd: 10, packaging_cost_usd: 2, accessory_cost_usd: 3, freight_usd: 4, duty_vat_usd: 5 });
    assert.deepEqual(costSeries(totals, true), [
      { key: 'freight_usd', label: '运费', value: 4 },
      { key: 'duty_vat_usd', label: '关税增值税', value: 5 },
      { key: 'other_usd', label: '其余', value: 91 },
    ]);
    assert.deepEqual(costSeries(totals, false).map(row => row.label), ['货值', '包装', '配件', '运费', '关税增值税', '毛利']);
    assert.deepEqual(costSeries(totals, false).map(row => row.value), [10, 2, 3, 4, 5, 6]);
    assert.ok(costSeries(null, false).every(row => row.value === 0));
    assert.ok(costSeries({}, true).every(row => row.value === 0));
    const guarded = { price_usd: 100, freight_usd: 4, duty_vat_usd: 5 };
    for (const key of ['goods_cost_usd', 'packaging_cost_usd', 'accessory_cost_usd', 'cost_usd']) {
      Object.defineProperty(guarded, key, { get() { throw new Error('Masked field accessed'); } });
    }
    assert.deepEqual(costSeries(guarded, true), costSeries(totals, true));
    const compare = freeze({ carton: { total_cost_usd: 20, delta_pct: 0 }, flight_case: { total_cost_usd: 24, delta_pct: 0.20 } });
    assert.deepEqual(packingCompareRows(compare, false), [
      { key: 'carton', label: '纸箱', value: 20 }, { key: 'flight_case', label: '航空箱', value: 24 },
    ]);
    assert.deepEqual(packingCompareRows(compare, true), [
      { key: 'carton', label: '纸箱', value: 0 }, { key: 'flight_case', label: '航空箱', value: 0.20 },
    ]);
    assert.deepEqual(packingCompareRows(null, true), []);
    assert.equal(packingCompareRows({ carton: { delta_pct: null } }, true)[0].value, null);
    assert.equal(formatUsd(null), '—');
    assert.equal(formatUsd(1234.5), '1,234.50');
    assert.equal(formatUsd(0), '0.00');
    assert.equal(formatUsd(-12.5), '-12.50');
    assert.equal(formatPct(null), '—');
    assert.equal(formatPct(0.1234), '12.3%');
    assert.equal(formatPct(0.1234, 2), '12.34%');
  },

  test_model_has_no_dom_or_business_literals() {
    for (const forbidden of ['document', 'window', 'fetch(', 'localStorage', 'innerHTML', 'eval(', '0.15', '0.021', '260']) {
      assert.equal(source.includes(forbidden), false, `Forbidden substring: ${forbidden}`);
    }
  },
};

const requested = process.argv.slice(2);
const names = requested.length ? requested : Object.keys(tests);
for (const name of names) {
  if (!Object.prototype.hasOwnProperty.call(tests, name)) throw new Error(`Unknown test: ${name}`);
  await tests[name]();
  process.stdout.write(`PASS ${name}\n`);
}
