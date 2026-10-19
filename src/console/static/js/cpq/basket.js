const STORAGE_KEY = 'xl.basket.v1';
const TERMS = {
  sea_lcl: ['FOB', 'CIF', 'DDP'],
  sea_fcl: ['FOB', 'CIF', 'DDP'],
  express: ['DAP', 'DDP'],
};
const owns = (value, key) => Object.prototype.hasOwnProperty.call(value, key);

function object(value, name) {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) {
    throw new TypeError(`${name} must be an object`);
  }
  return value;
}

function identifier(value, name) {
  if (typeof value !== 'string' || !value || value.trim() !== value) {
    throw new TypeError(`${name} must be a nonempty, trimmed string`);
  }
  return value;
}

function quantity(value) {
  if (!Number.isSafeInteger(value) || value <= 0) {
    throw new RangeError('qty must be a positive safe integer');
  }
  return value;
}

function nonnegative(value, name) {
  if (typeof value !== 'number' || !Number.isFinite(value) || value < 0) {
    throw new RangeError(`${name} must be a finite nonnegative number`);
  }
  return value;
}

function margin(value) {
  if (typeof value !== 'number' || !Number.isFinite(value) || value <= -1 || value >= 1) {
    throw new RangeError('target_margin must be a finite number between -1 and 1');
  }
  return value;
}

function packing(value) {
  if (value !== 'carton' && value !== 'flight_case') {
    throw new RangeError('Invalid packing_mode');
  }
  return value;
}

function pricing(value) {
  if (value !== 'margin' && value !== 'price') {
    throw new RangeError('Invalid pricing_mode');
  }
  return value;
}

function cleanShipping(value) {
  object(value, 'shipping');
  if (typeof value.mode !== 'string' || !owns(TERMS, value.mode) ||
      !TERMS[value.mode].includes(value.incoterm)) {
    throw new RangeError('Invalid shipping mode or incoterm');
  }
  return {
    mode: value.mode,
    incoterm: value.incoterm,
    lane_id: value.lane_id === null ? null : identifier(value.lane_id, 'lane_id'),
  };
}

function cleanState(value) {
  object(value, 'state');
  if (!Array.isArray(value.lines)) throw new TypeError('lines must be an array');
  const models = new Set();
  const lines = value.lines.map(raw => {
    object(raw, 'line');
    const model_id = identifier(raw.model_id, 'model_id');
    if (models.has(model_id)) throw new RangeError('Duplicate model_id');
    models.add(model_id);
    if (!Array.isArray(raw.accessories)) throw new TypeError('accessories must be an array');
    const accessoryIds = new Set();
    const accessories = raw.accessories.map(item => {
      object(item, 'accessory');
      const accessory_id = identifier(item.accessory_id, 'accessory_id');
      if (accessoryIds.has(accessory_id)) throw new RangeError('Duplicate accessory_id');
      accessoryIds.add(accessory_id);
      return {
        accessory_id,
        qty_per_unit: nonnegative(item.qty_per_unit, 'qty_per_unit'),
      };
    });
    return {
      model_id,
      qty: quantity(raw.qty),
      packing_mode: packing(raw.packing_mode),
      accessories,
      unit_price_usd: raw.unit_price_usd === null
        ? null : nonnegative(raw.unit_price_usd, 'unit_price_usd'),
    };
  });
  return {
    lines,
    shipping: cleanShipping(value.shipping),
    target_margin: margin(value.target_margin),
    pricing_mode: pricing(value.pricing_mode),
  };
}

/**
 * defaults.target_margin is required and must come from caller configuration.
 * Omit storage (or pass undefined) to use globalThis.localStorage when available.
 * State snapshots are detached. count() returns the total number of units.
 * setShipping(patch) merges shipping fields; setAccessory(id, accessoryId, 0)
 * removes that accessory. Subscribers receive snapshots after changes and
 * restore; subscribe returns an unsubscribe function.
 */
export function createBasket(storage, defaults) {
  object(defaults, 'defaults');
  const defaultMargin = margin(defaults.target_margin);
  const emptyState = () => ({
    lines: [],
    shipping: { mode: 'sea_lcl', incoterm: 'DDP', lane_id: null },
    target_margin: defaultMargin,
    pricing_mode: 'margin',
  });
  let state = emptyState();
  let persistence = storage;
  const listeners = new Set();

  if (persistence === undefined) {
    try {
      persistence = globalThis.localStorage;
    } catch {
      persistence = undefined;
    }
  }

  function parse(text) {
    try {
      if (typeof text !== 'string') return emptyState();
      return cleanState(JSON.parse(text));
    } catch {
      return emptyState();
    }
  }

  try {
    state = parse(persistence.getItem(STORAGE_KEY));
  } catch {
    state = emptyState();
  }

  const snapshot = () => cleanState(state);

  function commit(next) {
    state = cleanState(next);
    const result = snapshot();
    try {
      persistence.setItem(STORAGE_KEY, JSON.stringify(state));
    } catch {
      // The in-memory state remains authoritative when storage is unavailable.
    }
    for (const listener of [...listeners]) {
      try {
        listener(cleanState(result));
      } catch {
        // A subscriber failure must not interrupt storage fallback or restore.
      }
    }
    return result;
  }

  function findLine(next, model_id) {
    identifier(model_id, 'model_id');
    const line = next.lines.find(item => item.model_id === model_id);
    if (!line) throw new RangeError('Unknown basket model_id');
    return line;
  }

  return {
    get state() {
      return snapshot();
    },

    add(model_id, qty) {
      identifier(model_id, 'model_id');
      quantity(qty);
      const next = snapshot();
      const line = next.lines.find(item => item.model_id === model_id);
      const merged = Boolean(line);
      if (line) {
        line.qty = quantity(line.qty + qty);
      } else {
        next.lines.push({
          model_id,
          qty,
          packing_mode: 'carton',
          accessories: [],
          unit_price_usd: null,
        });
      }
      commit(next);
      return { merged };
    },

    remove(model_id) {
      identifier(model_id, 'model_id');
      const next = snapshot();
      next.lines = next.lines.filter(line => line.model_id !== model_id);
      return commit(next);
    },

    setQty(model_id, qty) {
      quantity(qty);
      const next = snapshot();
      findLine(next, model_id).qty = qty;
      return commit(next);
    },

    setPacking(model_id, packing_mode) {
      packing(packing_mode);
      const next = snapshot();
      findLine(next, model_id).packing_mode = packing_mode;
      return commit(next);
    },

    setAccessory(model_id, accessory_id, qty_per_unit) {
      identifier(accessory_id, 'accessory_id');
      nonnegative(qty_per_unit, 'qty_per_unit');
      const next = snapshot();
      const line = findLine(next, model_id);
      const item = line.accessories.find(entry => entry.accessory_id === accessory_id);
      if (qty_per_unit === 0) {
        line.accessories = line.accessories.filter(entry => entry.accessory_id !== accessory_id);
      } else if (item) {
        item.qty_per_unit = qty_per_unit;
      } else {
        line.accessories.push({ accessory_id, qty_per_unit });
      }
      return commit(next);
    },

    setShipping(patch) {
      object(patch, 'shipping patch');
      const next = snapshot();
      for (const key of ['mode', 'incoterm', 'lane_id']) {
        if (owns(patch, key)) next.shipping[key] = patch[key];
      }
      next.shipping = cleanShipping(next.shipping);
      return commit(next);
    },

    setMargin(target_margin) {
      margin(target_margin);
      const next = snapshot();
      next.target_margin = target_margin;
      return commit(next);
    },

    setPricingMode(pricing_mode) {
      pricing(pricing_mode);
      const next = snapshot();
      next.pricing_mode = pricing_mode;
      return commit(next);
    },

    setUnitPrice(model_id, unit_price_usd) {
      if (unit_price_usd !== null) nonnegative(unit_price_usd, 'unit_price_usd');
      const next = snapshot();
      findLine(next, model_id).unit_price_usd = unit_price_usd;
      return commit(next);
    },

    count() {
      return state.lines.reduce((total, line) => total + line.qty, 0);
    },

    toRequest() {
      const current = snapshot();
      if (!current.lines.length) throw new RangeError('Cannot calculate an empty basket');
      if (current.shipping.lane_id === null) {
        throw new RangeError('Select a shipping lane before calculating');
      }
      if (current.pricing_mode === 'price' && current.lines.some(line =>
        line.unit_price_usd === null || line.unit_price_usd <= 0)) {
        throw new RangeError('Price mode requires a positive unit price for every line');
      }
      return {
        lines: current.lines.map(line => ({
          model_id: line.model_id,
          qty: line.qty,
          packing_mode: line.packing_mode,
          packing_override: null,
          accessories: line.accessories.map(item => ({
            accessory_id: item.accessory_id,
            qty_per_unit: item.qty_per_unit,
          })),
          unit_price_usd: line.unit_price_usd,
        })),
        shipping: {
          mode: current.shipping.mode,
          incoterm: current.shipping.incoterm,
          lane_id: current.shipping.lane_id,
        },
        target_margin: current.target_margin,
        pricing_mode: current.pricing_mode,
      };
    },

    serialize() {
      return JSON.stringify(state);
    },

    restore(text) {
      return commit(parse(text));
    },

    subscribe(fn) {
      if (typeof fn !== 'function') throw new TypeError('Subscriber must be a function');
      listeners.add(fn);
      return () => listeners.delete(fn);
    },
  };
}
