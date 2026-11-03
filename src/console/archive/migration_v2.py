"""Deal foundation schema; called only inside Store's migration transaction."""

PARTIES = """CREATE TABLE parties_v2 (
 id INTEGER PRIMARY KEY, kind TEXT NOT NULL CHECK(kind IN ('supplier','forwarder','customer','vendor')),
 name TEXT NOT NULL, short_name TEXT, aliases_json TEXT, conversation_ref TEXT, notes TEXT,
 status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','paused','archived')),
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL, country TEXT, UNIQUE(kind,name))"""

SCHEMA = (
    """CREATE TABLE identities (
      id INTEGER PRIMARY KEY, party_id INTEGER NOT NULL REFERENCES parties(id),
      conversation_key TEXT NOT NULL UNIQUE, channel TEXT, label TEXT,
      created_at TEXT NOT NULL, created_by TEXT NOT NULL)""",
    """CREATE TABLE deals (
      id INTEGER PRIMARY KEY, title TEXT NOT NULL, customer_party_id INTEGER REFERENCES parties(id),
      stage TEXT NOT NULL DEFAULT 'inquiry' CHECK(stage IN
      ('inquiry','sourcing','quoting','negotiating','awaiting_payment','paid','inspection','shipped','delivered','aftersales','reorder')),
      status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','won','lost','paused')),
      value_fen INTEGER CHECK(value_fen IS NULL OR (typeof(value_fen)='integer' AND value_fen BETWEEN 0 AND 999999999999)),
      currency TEXT DEFAULT 'USD', next_action TEXT, next_action_due TEXT, lost_reason TEXT, notes TEXT,
      source_conversation_key TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
      stage_changed_at TEXT NOT NULL, won_at TEXT, closed_at TEXT)""",
    """CREATE TABLE deal_participants (
      id INTEGER PRIMARY KEY, deal_id INTEGER NOT NULL REFERENCES deals(id) ON DELETE CASCADE,
      party_id INTEGER NOT NULL REFERENCES parties(id),
      role TEXT NOT NULL CHECK(role IN ('customer','supplier','forwarder','vendor')), note TEXT,
      created_at TEXT NOT NULL, UNIQUE(deal_id,party_id))""",
    """CREATE TABLE deal_conversations (
      id INTEGER PRIMARY KEY, deal_id INTEGER NOT NULL REFERENCES deals(id) ON DELETE CASCADE,
      conversation_key TEXT NOT NULL, party_id INTEGER REFERENCES parties(id), note TEXT,
      created_at TEXT NOT NULL, UNIQUE(deal_id,conversation_key))""",
    """CREATE TABLE deal_items (
      id INTEGER PRIMARY KEY, deal_id INTEGER NOT NULL REFERENCES deals(id) ON DELETE CASCADE,
      product_id INTEGER REFERENCES products(id), name TEXT NOT NULL,
      qty INTEGER CHECK(qty IS NULL OR (typeof(qty)='integer' AND qty BETWEEN 1 AND 1000000)),
      target_price_fen INTEGER CHECK(target_price_fen IS NULL OR
      (typeof(target_price_fen)='integer' AND target_price_fen BETWEEN 0 AND 999999999999)),
      currency TEXT DEFAULT 'USD', note TEXT)""",
    """CREATE TABLE deal_tasks (
      id INTEGER PRIMARY KEY, deal_id INTEGER NOT NULL REFERENCES deals(id) ON DELETE CASCADE,
      stage TEXT, title TEXT NOT NULL, due TEXT,
      status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','done','skipped')),
      auto INTEGER NOT NULL DEFAULT 0 CHECK(auto IN (0,1)), template_key TEXT,
      created_at TEXT NOT NULL, done_at TEXT)""",
    """CREATE TABLE deal_events (
      id INTEGER PRIMARY KEY, deal_id INTEGER NOT NULL REFERENCES deals(id) ON DELETE CASCADE,
      at TEXT NOT NULL, kind TEXT NOT NULL CHECK(kind IN
      ('created','stage','status','note','link','participant','item','task','system')),
      summary TEXT NOT NULL CHECK(length(summary) BETWEEN 1 AND 500), who TEXT)""",
    'CREATE INDEX identities_party ON identities(party_id)',
    'CREATE INDEX deals_status_stage ON deals(status,stage)',
    'CREATE INDEX deals_customer ON deals(customer_party_id)',
    'CREATE INDEX participants_party ON deal_participants(party_id,deal_id)',
    'CREATE INDEX conversations_key ON deal_conversations(conversation_key,deal_id)',
    'CREATE INDEX items_deal ON deal_items(deal_id)',
    'CREATE INDEX tasks_deal ON deal_tasks(deal_id,status,due)',
    'CREATE UNIQUE INDEX tasks_template ON deal_tasks(deal_id,template_key) WHERE template_key IS NOT NULL',
    'CREATE INDEX events_deal ON deal_events(deal_id,at DESC,id DESC)',
)


def upgrade(db):
    # Rebuild without renaming the old parent: SQLite must keep child REFERENCES
    # pointing to "parties". The caller disables FK enforcement before BEGIN and
    # runs foreign_key_check before committing, restoring enforcement afterwards.
    extras = [r[0] for r in db.execute("SELECT sql FROM sqlite_master WHERE tbl_name='parties' "
                                      "AND type IN ('index','trigger') AND sql IS NOT NULL")]
    db.execute(PARTIES)
    columns = ','.join(r[1] for r in db.execute('PRAGMA table_info(parties)'))
    db.execute(f'INSERT INTO parties_v2 ({columns}) SELECT {columns} FROM parties')
    db.execute('DROP TABLE parties')
    db.execute('ALTER TABLE parties_v2 RENAME TO parties')
    for statement in (*extras, *SCHEMA):
        db.execute(statement)
