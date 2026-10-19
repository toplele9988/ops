import { readFileSync } from 'node:fs';
import { compute } from '../../src/console/static/js/cpq/cpq-engine.js';

function resolve(value, fixture, trail = []) {
  if (Array.isArray(value)) return value.map(item => resolve(item, fixture, trail));
  if (value === null || typeof value !== 'object') return value;
  let base = {};
  if (Object.hasOwn(value, '$ref')) {
    const reference = value.$ref;
    if (typeof reference !== 'string' || !reference.startsWith('#/') || trail.includes(reference)) {
      throw new Error(`Invalid or cyclic reference: ${reference}`);
    }
    let target = fixture;
    for (const key of reference.slice(2).split('/')) {
      if (target === null || typeof target !== 'object' || !Object.hasOwn(target, key)) {
        throw new Error(`Unresolved reference: ${reference}`);
      }
      target = target[key];
    }
    base = resolve(target, fixture, [...trail, reference]);
    if (base === null || typeof base !== 'object' || Array.isArray(base)) {
      throw new Error(`Reference must resolve to an object: ${reference}`);
    }
  }
  const siblings = Object.fromEntries(Object.entries(value)
    .filter(([key]) => key !== '$ref')
    .map(([key, item]) => [key, resolve(item, fixture, trail)]));
  return { ...base, ...siblings };
}

function packingFromDict(value) {
  return value == null ? null : { case_cost_cny: null, ...value };
}

function lineFromDict(value) {
  return {
    unit_price_usd: null,
    accessories: [],
    net_weight_kg: null,
    packing_estimated: false,
    case_cost_missing: false,
    category: 'strobe',
    spec_complete: true,
    case_cost_cny: null,
    ...value,
    packing: {
      carton: packingFromDict(value.packing.carton),
      flight_case: packingFromDict(value.packing.flight_case),
      override: packingFromDict(value.packing.override),
    },
    accessories: (value.accessories ?? []).map(accessory => ({ ...accessory })),
  };
}

function laneFromDict(value) {
  return {
    dest_country: '',
    named_place: '',
    currency: 'USD',
    inland_cny_per_cbm: 0.0,
    inland_min_cny: 0.0,
    origin_fixed_usd: 0.0,
    origin_per_cbm_usd: 0.0,
    lcl_rate_usd_per_wm: 0.0,
    lcl_min_wm: 1.0,
    fcl_rate_usd: { '20GP': 0.0, '40GP': 0.0, '40HQ': 0.0 },
    fcl_origin_fixed_usd_per_container: 0.0,
    fcl_dest_fixed_usd_per_container: 0.0,
    dest_fixed_usd: 0.0,
    dest_per_cbm_usd: 0.0,
    last_mile_usd: 0.0,
    duty_rate: 0.0,
    duty_basis: 'CIF',
    import_vat_rate: 0.0,
    include_import_vat: true,
    volumetric_divisor: null,
    increment_kg: null,
    bands: [],
    min_charge_usd: 0.0,
    fuel_surcharge_pct: 0.0,
    ddp_fee_usd: 0.0,
    ...value,
    bands: (value.bands ?? []).map(band => ({ ...band })),
  };
}

function inputsFromDict(value) {
  return {
    ...value,
    lines: value.lines.map(lineFromDict),
    shipping: { ...value.shipping, lane: laneFromDict(value.shipping.lane) },
  };
}

const fixturePath = process.argv[2];
if (!fixturePath) throw new Error('Usage: node tests/cpq/js_parity_runner.mjs <vectors.json>');
const fixture = JSON.parse(readFileSync(fixturePath, 'utf8'));
const results = Object.create(null);
for (const vector of fixture.vectors) {
  if (Object.hasOwn(results, vector.id)) throw new Error(`Duplicate vector id: ${vector.id}`);
  results[vector.id] = compute(inputsFromDict(resolve(vector.input, fixture)));
}
process.stdout.write(JSON.stringify(results));
