import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

// Synthetic identifiers and quantities are test fixtures, not catalog data.
// The configured margin below comes from the supplied cpq.yaml.
const defaults = { target_margin: 0.15 };
const STORAGE_KEY = 'xl.basket.v1';

function memoryStorage() {
  const values = new Map();
  return {
    getItem(key) { return values.get(key) ?? null; },
    setItem(key, value) { values.set(key, String(value)); },
  };
}

function assertNoCostKeys(value) {
  if (value === null || typeof value !== 'object') return;
  for (const [key, child] of Object.entries(value)) {
    assert.equal(/cost|exw|supplier|offer/i.test(key), false, `Forbidden key: ${key}`);
    assertNoCostKeys(child);
  }
}

async function main() {
  // Loading the dependency-free source as an ES module also works in repositories
  // whose package.json does not declare type=module; the production file stays .js.
  const source = await readFile(new URL('../../src/console/static/js/cpq/basket.js', import.meta.url), 'utf8');
  const { createBasket } = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);
  const results = {};

  function scenario(name, run) {
    try {
      results[name] = { ok: true, ...run() };
    } catch (error) {
      results[name] = { ok: false, error: String(error.stack || error) };
      process.exitCode = 1;
    }
  }

  scenario('test_basket_merge_quantity', () => {
    const storage = memoryStorage();
    const basket = createBasket(storage, defaults);
    let notifications = 0;
    const unsubscribe = basket.subscribe(state => {
      notifications += 1;
      state.lines.length = 0;
    });
    assert.deepEqual(basket.add('fixture-model', 2), { merged: false });
    assert.deepEqual(basket.add('fixture-model', 3), { merged: true });
    assert.equal(notifications, 2);
    unsubscribe();
    assert.equal(basket.count(), 5);
    assert.equal(basket.state.lines.length, 1);
    basket.setShipping({ lane_id: 'fixture-sea-lane' });
    assert.equal(notifications, 2);
    const request = basket.toRequest();
    assert.equal(request.lines[0].qty, 5);
    assert.deepEqual(createBasket(storage, defaults).toRequest(), request);
    assert.deepEqual(JSON.parse(storage.getItem(STORAGE_KEY)), basket.state);
    const detached = basket.state;
    detached.lines[0].qty = 1;
    assert.equal(basket.count(), 5);
    return { count: basket.count(), line_count: request.lines.length, qty: request.lines[0].qty };
  });

  scenario('test_basket_rejects_bad_qty', () => {
    const basket = createBasket(memoryStorage(), defaults);
    basket.add('fixture-model', 2);
    const before = basket.serialize();
    const rejected = [];
    for (const qty of [0, -1, 1.5, '3']) {
      assert.throws(() => basket.add('fixture-model', qty), RangeError);
      assert.throws(() => basket.add('fixture-new-model', qty), RangeError);
      assert.throws(() => basket.setQty('fixture-model', qty), RangeError);
      assert.equal(basket.serialize(), before);
      rejected.push(qty);
    }
    basket.setQty('fixture-model', Number.MAX_SAFE_INTEGER);
    const maximum = basket.serialize();
    assert.throws(() => basket.add('fixture-model', 1), RangeError);
    assert.equal(basket.serialize(), maximum);
    return { rejected };
  });

  scenario('test_basket_request_has_no_cost_keys', () => {
    const basket = createBasket(memoryStorage(), defaults);
    assert.throws(() => basket.toRequest(), RangeError);
    basket.add('fixture-model', 2);
    assert.throws(() => basket.toRequest(), RangeError);
    basket.setShipping({ lane_id: 'fixture-sea-lane' });
    basket.setPacking('fixture-model', 'flight_case');
    basket.setAccessory('fixture-model', 'fixture-accessory', 1);
    basket.setAccessory('fixture-model', 'fixture-accessory', 2);
    assert.equal(basket.state.lines[0].accessories.length, 1);
    const beforeShipping = basket.serialize();
    assert.throws(() => basket.setShipping({ mode: 'express', incoterm: 'FOB' }), RangeError);
    assert.throws(() => basket.setShipping({ mode: 'sea_fcl', incoterm: 'DAP' }), RangeError);
    assert.equal(basket.serialize(), beforeShipping);
    basket.setShipping({ mode: 'express', incoterm: 'DAP', lane_id: 'fixture-express-lane' });

    const polluted = JSON.parse(basket.serialize());
    polluted.cost_total = 1;
    polluted.lines[0].exw_cny = 1;
    polluted.lines[0].packing_override = { case_cost_cny: 1 };
    polluted.lines[0].accessories[0].supplier_id = 'fixture-supplier';
    polluted.shipping.offer_info = { nested_cost: 1 };
    basket.restore(JSON.stringify(polluted));
    const request = basket.toRequest();
    assertNoCostKeys(request);
    assert.deepEqual(request, {
      lines: [{
        model_id: 'fixture-model',
        qty: 2,
        packing_mode: 'flight_case',
        packing_override: null,
        accessories: [{ accessory_id: 'fixture-accessory', qty_per_unit: 2 }],
        unit_price_usd: null,
      }],
      shipping: { mode: 'express', incoterm: 'DAP', lane_id: 'fixture-express-lane' },
      target_margin: defaults.target_margin,
      pricing_mode: 'margin',
    });
    const roundtrip = createBasket(memoryStorage(), defaults);
    roundtrip.restore(basket.serialize());
    assert.deepEqual(roundtrip.toRequest(), request);
    request.lines[0].accessories[0].qty_per_unit = 1;
    assert.equal(basket.toRequest().lines[0].accessories[0].qty_per_unit, 2);

    basket.setPricingMode('price');
    assert.throws(() => basket.toRequest(), RangeError);
    basket.setUnitPrice('fixture-model', 0);
    assert.throws(() => basket.toRequest(), RangeError);
    basket.setUnitPrice('fixture-model', 1); // Synthetic price for validation only.
    assert.equal(basket.toRequest().lines[0].unit_price_usd, 1);
    basket.setAccessory('fixture-model', 'fixture-accessory', 0);
    assert.deepEqual(basket.toRequest().lines[0].accessories, []);
    basket.setMargin(defaults.target_margin);
    assert.throws(() => basket.setMargin(Number.NaN), RangeError);
    basket.remove('fixture-model');
    assert.equal(basket.count(), 0);
    assert.throws(() => basket.toRequest(), RangeError);
    return { request: roundtrip.toRequest(), cost_keys_found: [] };
  });

  scenario('test_basket_survives_storage_failure', () => {
    let reads = 0;
    let writes = 0;
    const storage = {
      getItem() { reads += 1; throw new Error('fixture read failure'); },
      setItem() { writes += 1; throw new Error('fixture write failure'); },
    };
    const basket = createBasket(storage, defaults);
    basket.subscribe(() => { throw new Error('fixture subscriber failure'); });
    basket.add('fixture-model', 2);
    basket.setShipping({ lane_id: 'fixture-sea-lane' });
    const request = basket.toRequest();
    assert.equal(request.lines[0].qty, 2);
    assert.equal(reads, 1);
    assert.ok(writes > 0);
    const serialized = basket.serialize();
    basket.restore(serialized);
    assert.deepEqual(basket.toRequest(), request);
    const invalidState = JSON.parse(serialized);
    invalidState.lines[0].qty = '3';
    for (const text of ['{broken', 'null', '{}', JSON.stringify(invalidState), undefined]) {
      assert.doesNotThrow(() => basket.restore(text));
      assert.deepEqual(basket.state, {
        lines: [],
        shipping: { mode: 'sea_lcl', incoterm: 'DDP', lane_id: null },
        target_margin: defaults.target_margin,
        pricing_mode: 'margin',
      });
    }
    basket.add('fixture-model', 2);
    basket.setShipping({ lane_id: 'fixture-sea-lane' });
    assert.deepEqual(basket.toRequest(), request);
    return { reads, writes, request };
  });

  process.stdout.write(`${JSON.stringify(results)}\n`);
}

main().catch(error => {
  process.exitCode = 1;
  process.stdout.write(`${JSON.stringify({ runner_error: String(error.stack || error) })}\n`);
});
