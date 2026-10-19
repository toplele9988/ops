CREATE TABLE IF NOT EXISTS cpq_packing_profile (
  model_id TEXT NOT NULL,
  packing_mode TEXT NOT NULL CHECK (packing_mode IN ('carton','flight_case')),
  units_per_pkg INTEGER NOT NULL CHECK (units_per_pkg > 0),
  l_cm REAL NOT NULL, w_cm REAL NOT NULL, h_cm REAL NOT NULL, gw_kg REAL NOT NULL,
  case_cost_cny REAL, source TEXT NOT NULL DEFAULT 'manual',
  updated_by TEXT, updated_at TEXT NOT NULL,
  PRIMARY KEY (model_id, packing_mode)
);
CREATE TABLE IF NOT EXISTS cpq_freight_lane (
  lane_id TEXT PRIMARY KEY, mode TEXT NOT NULL, payload_json TEXT NOT NULL,
  valid_until TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cpq_fx_rate (
  id INTEGER PRIMARY KEY AUTOINCREMENT, pair TEXT NOT NULL DEFAULT 'USDCNY',
  rate REAL NOT NULL, effective_at TEXT NOT NULL, entered_by TEXT
);
CREATE TABLE IF NOT EXISTS cpq_accessory (
  accessory_id TEXT PRIMARY KEY, name_en TEXT NOT NULL, name_zh TEXT,
  cost_cny REAL NOT NULL, active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS cpq_public_sku (
  model_id TEXT PRIMARY KEY, public_sku TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cpq_asset_flag (
  asset_id TEXT PRIMARY KEY, customer_safe INTEGER NOT NULL DEFAULT 1,
  reason TEXT, updated_by TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS cpq_spec_override (
  model_id TEXT NOT NULL, field TEXT NOT NULL, value_json TEXT NOT NULL,
  updated_by TEXT, updated_at TEXT, PRIMARY KEY (model_id, field)
);
CREATE TABLE IF NOT EXISTS cpq_quote (
  quote_token TEXT PRIMARY KEY, quote_no TEXT NOT NULL UNIQUE,
  status TEXT NOT NULL CHECK (status IN ('draft','final')),
  engine_version TEXT NOT NULL, inputs_json TEXT NOT NULL, result_json TEXT NOT NULL,
  internal_snapshot_json TEXT NOT NULL, customer_dto_json TEXT,
  acks_json TEXT, override_reason TEXT, created_by TEXT NOT NULL,
  created_at TEXT NOT NULL, valid_until TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cpq_quote_letter (
  id INTEGER PRIMARY KEY AUTOINCREMENT, quote_token TEXT NOT NULL,
  version INTEGER NOT NULL, subject TEXT, body TEXT,
  status TEXT NOT NULL DEFAULT 'draft', created_by TEXT, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cpq_audit_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, actor TEXT,
  action TEXT NOT NULL, quote_token TEXT, detail_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_cpq_quote_created ON cpq_quote(created_at);
CREATE INDEX IF NOT EXISTS idx_cpq_audit_at ON cpq_audit_log(at);
