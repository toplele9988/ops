"""Add payment, fulfilment and feedback tables without altering deployed schemas."""

SCHEMA = (
    """CREATE TABLE deal_payments (
      id INTEGER PRIMARY KEY, deal_id INTEGER NOT NULL REFERENCES deals(id) ON DELETE CASCADE,
      kind TEXT NOT NULL CHECK(kind IN ('deposit','balance','full','refund')),
      amount_fen INTEGER NOT NULL CHECK(typeof(amount_fen)='integer' AND amount_fen BETWEEN 1 AND 999999999999),
      currency TEXT NOT NULL, received_on TEXT NOT NULL,
      method TEXT NOT NULL CHECK(method IN ('wire','paypal','lc','other')),
      quote_round_id INTEGER REFERENCES quote_rounds(id), note TEXT, evidence TEXT, currency_note TEXT,
      status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','confirmed','void')),
      qualifies INTEGER NOT NULL DEFAULT 0 CHECK(qualifies IN (0,1)),
      created_at TEXT NOT NULL, created_by TEXT NOT NULL, updated_at TEXT NOT NULL,
      confirmed_at TEXT, confirmed_by TEXT, voided_at TEXT, voided_by TEXT, void_note TEXT,
      CHECK((confirmed_at IS NULL)=(confirmed_by IS NULL)),
      CHECK(status!='confirmed' OR confirmed_at IS NOT NULL),
      CHECK((voided_at IS NULL)=(voided_by IS NULL)),
      CHECK(status!='void' OR voided_at IS NOT NULL),
      CHECK(kind!='refund' OR qualifies=0))""",
    """CREATE TABLE fulfilment_nodes (
      id INTEGER PRIMARY KEY, deal_id INTEGER NOT NULL REFERENCES deals(id) ON DELETE CASCADE,
      node TEXT NOT NULL CHECK(node IN ('po','supplier_confirmation','production','inspection','booking','transit','customs','signoff')),
      status TEXT NOT NULL DEFAULT 'todo' CHECK(status IN ('todo','doing','done','blocked')),
      planned_on TEXT, completed_on TEXT,
      responsible TEXT NOT NULL DEFAULT 'us' CHECK(responsible IN ('us','supplier','forwarder','vendor')),
      party_id INTEGER REFERENCES parties(id), note TEXT, promised_on TEXT,
      inspection_result TEXT CHECK(inspection_result IN ('pass','fail','needs_fix')),
      inspector TEXT, carrier TEXT, tracking_number TEXT, etd TEXT, eta TEXT, departed_on TEXT, signed_on TEXT,
      created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(deal_id,node))""",
    """CREATE TABLE deal_issues (
      id INTEGER PRIMARY KEY, deal_id INTEGER NOT NULL REFERENCES deals(id) ON DELETE CASCADE,
      kind TEXT NOT NULL CHECK(kind IN ('quality','shortage','damage','delay','documents','feedback','other')),
      quote_line_id INTEGER REFERENCES quote_lines(id), product_id INTEGER REFERENCES products(id),
      responsible TEXT NOT NULL DEFAULT 'unknown' CHECK(responsible IN ('supplier','forwarder','vendor','us','customer','unknown')),
      party_id INTEGER REFERENCES parties(id), severity TEXT NOT NULL DEFAULT 'medium' CHECK(severity IN ('low','medium','high','critical')),
      status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','waiting_party','resolved','closed')),
      description TEXT NOT NULL, resolution TEXT,
      impact_kind TEXT CHECK(impact_kind IN ('compensation','discount','refund')),
      impact_fen INTEGER CHECK(impact_fen IS NULL OR (typeof(impact_fen)='integer' AND impact_fen BETWEEN 0 AND 999999999999)),
      currency TEXT, created_at TEXT NOT NULL, created_by TEXT NOT NULL, updated_at TEXT NOT NULL,
      resolved_at TEXT, closed_at TEXT)""",
    """CREATE TABLE deal_issue_notes (
      id INTEGER PRIMARY KEY, issue_id INTEGER NOT NULL REFERENCES deal_issues(id) ON DELETE CASCADE,
      note TEXT NOT NULL, at TEXT NOT NULL, who TEXT NOT NULL)""",
    'CREATE INDEX payments_deal ON deal_payments(deal_id,status,currency)',
    'CREATE INDEX payments_pending ON deal_payments(status,created_at,id)',
    'CREATE INDEX fulfilment_due ON fulfilment_nodes(status,planned_on,deal_id)',
    'CREATE INDEX issues_deal ON deal_issues(deal_id,status,created_at)',
    'CREATE INDEX issues_product ON deal_issues(product_id,created_at,status)',
    'CREATE INDEX issues_party ON deal_issues(party_id,created_at,status)',
    'CREATE INDEX issue_notes_issue ON deal_issue_notes(issue_id,at,id)',
)


def upgrade(db):
    for statement in SCHEMA:
        db.execute(statement)


def migrate(db):
    from .store import migrate as migrate_store
    migrate_store(db, target_version=8)
