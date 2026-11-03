"""Quote snapshots. Store calls upgrade inside its single migration transaction."""

SCHEMA = (
    """CREATE TABLE quote_rounds (
      id INTEGER PRIMARY KEY, deal_id INTEGER NOT NULL REFERENCES deals(id) ON DELETE CASCADE,
      round_no INTEGER NOT NULL CHECK(round_no>0),
      status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','sent','accepted','rejected','superseded')),
      currency TEXT NOT NULL DEFAULT 'USD', fx_x10000 INTEGER CHECK(fx_x10000 IS NULL OR
        (typeof(fx_x10000)='integer' AND fx_x10000>0)), fx_source TEXT,
      incoterm TEXT NOT NULL DEFAULT 'DDP' CHECK(incoterm IN ('EXW','FOB','CIF','DAP','DDP')),
      destination_country TEXT, target_margin_bp INTEGER NOT NULL DEFAULT 1500
        CHECK(typeof(target_margin_bp)='integer' AND target_margin_bp BETWEEN 0 AND 9000),
      valid_until TEXT, notes TEXT, sent_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
      copied_from_round_id INTEGER REFERENCES quote_rounds(id), UNIQUE(deal_id,round_no))""",
    """CREATE TABLE quote_lines (
      id INTEGER PRIMARY KEY, round_id INTEGER NOT NULL REFERENCES quote_rounds(id) ON DELETE CASCADE,
      deal_item_id INTEGER REFERENCES deal_items(id) ON DELETE SET NULL,
      product_id INTEGER REFERENCES products(id), name TEXT NOT NULL,
      qty INTEGER NOT NULL CHECK(typeof(qty)='integer' AND qty BETWEEN 1 AND 1000000), unit TEXT,
      offer_id INTEGER REFERENCES offers(id), cost_unit_fen INTEGER CHECK(cost_unit_fen IS NULL OR
        (typeof(cost_unit_fen)='integer' AND cost_unit_fen BETWEEN 0 AND 999999999999)),
      cost_currency TEXT DEFAULT 'CNY', cost_valid_until TEXT, cost_validity TEXT,
      units_per_carton INTEGER, carton_l_cm REAL, carton_w_cm REAL, carton_h_cm REAL, gross_kg REAL,
      sell_unit_minor INTEGER CHECK(sell_unit_minor IS NULL OR
        (typeof(sell_unit_minor)='integer' AND sell_unit_minor BETWEEN 0 AND 999999999999)), note TEXT,
      lead_days_min INTEGER, lead_days_max INTEGER, qty_confirmed INTEGER NOT NULL DEFAULT 1 CHECK(qty_confirmed IN (0,1)))""",
    """CREATE TABLE quote_costs (
      id INTEGER PRIMARY KEY, round_id INTEGER NOT NULL REFERENCES quote_rounds(id) ON DELETE CASCADE,
      kind TEXT NOT NULL CHECK(kind IN ('freight','vendor','domestic','other')),
      party_id INTEGER REFERENCES parties(id), freight_quote_id INTEGER REFERENCES freight_quotes(id),
      description TEXT, amount_fen INTEGER CHECK(amount_fen IS NULL OR
        (typeof(amount_fen)='integer' AND amount_fen BETWEEN 0 AND 999999999999)),
      manual INTEGER NOT NULL DEFAULT 0 CHECK(manual IN (0,1)), valid_until TEXT, validity TEXT,
      source_note TEXT, calc_json TEXT)""",
    """CREATE TABLE quote_negotiations (
      id INTEGER PRIMARY KEY, round_id INTEGER NOT NULL REFERENCES quote_rounds(id) ON DELETE CASCADE,
      side TEXT NOT NULL CHECK(side IN ('customer','supplier','forwarder','vendor','us')),
      party_id INTEGER REFERENCES parties(id), note TEXT NOT NULL,
      price_minor INTEGER CHECK(price_minor IS NULL OR
        (typeof(price_minor)='integer' AND price_minor BETWEEN 0 AND 999999999999)),
      currency TEXT, at TEXT NOT NULL, who TEXT NOT NULL)""",
    'CREATE INDEX quote_lines_round ON quote_lines(round_id)',
    'CREATE INDEX quote_costs_round ON quote_costs(round_id)',
    'CREATE INDEX quote_negotiations_round ON quote_negotiations(round_id,at,id)',
)


def upgrade(db):
    for statement in SCHEMA:
        db.execute(statement)


def migrate(db):
    from .store import migrate as migrate_store
    migrate_store(db, target_version=3)
