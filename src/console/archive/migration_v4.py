"""Internal role outputs only. No ALTER/UPDATE of any existing business table."""

SCHEMA = (
    """CREATE TABLE role_runs (
      id INTEGER PRIMARY KEY,
      role TEXT NOT NULL CHECK(role IN ('secretary','triage','patrol','brief')),
      subject_kind TEXT NOT NULL CHECK(subject_kind IN ('deal','conversation','day')),
      subject_key TEXT NOT NULL, input_fingerprint TEXT NOT NULL,
      status TEXT NOT NULL CHECK(status IN ('ok','fallback','failed')),
      source TEXT NOT NULL CHECK(source IN ('ai','rules')), model TEXT,
      output_json TEXT NOT NULL, output_text TEXT NOT NULL CHECK(length(output_text)>0),
      evidence_json TEXT NOT NULL, error TEXT, created_at TEXT NOT NULL, created_by TEXT NOT NULL,
      ai_success INTEGER CHECK(ai_success IN (0,1)))""",
    """CREATE TABLE role_alerts (
      id INTEGER PRIMARY KEY, kind TEXT NOT NULL,
      deal_id INTEGER NOT NULL REFERENCES deals(id) ON DELETE CASCADE,
      ref_kind TEXT NOT NULL, ref_id TEXT NOT NULL, message TEXT NOT NULL,
      severity TEXT NOT NULL CHECK(severity IN ('high','medium','low')),
      first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL,
      resolved_at TEXT, dismissed_at TEXT, dismissed_by TEXT)""",
    """CREATE UNIQUE INDEX role_alert_active ON role_alerts(kind,ref_kind,ref_id)
      WHERE resolved_at IS NULL""",
    """CREATE TABLE triage_suggestions (
      id INTEGER PRIMARY KEY, conversation_key TEXT NOT NULL UNIQUE,
      suggested_deal_id INTEGER REFERENCES deals(id),
      suggested_party_id INTEGER REFERENCES parties(id),
      suggested_party_kind TEXT CHECK(suggested_party_kind IN ('customer','supplier','forwarder','vendor')),
      action TEXT NOT NULL CHECK(action IN ('link','create','identity','ignore')),
      reason TEXT NOT NULL, confidence REAL NOT NULL CHECK(confidence BETWEEN 0 AND 1),
      source TEXT NOT NULL CHECK(source IN ('rules','ai')),
      status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','accepted','dismissed')),
      run_id INTEGER NOT NULL REFERENCES role_runs(id), summary_json TEXT NOT NULL,
      created_at TEXT NOT NULL, updated_at TEXT NOT NULL, decided_at TEXT, decided_by TEXT,
      decided_deal_id INTEGER REFERENCES deals(id), decided_party_id INTEGER REFERENCES parties(id))""",
    # official_runner owns up to three gateway attempts; reserve that upper bound
    # atomically before invoking it. Reservations survive worker/restart boundaries.
    """CREATE TABLE role_ai_reservations (
      id INTEGER PRIMARY KEY, at REAL NOT NULL,
      calls INTEGER NOT NULL CHECK(calls BETWEEN 1 AND 3))""",
    'CREATE INDEX role_runs_latest ON role_runs(role,subject_key,created_at,id)',
    'CREATE INDEX role_alerts_deal ON role_alerts(deal_id,resolved_at,severity)',
    'CREATE INDEX triage_status ON triage_suggestions(status,updated_at)',
    'CREATE INDEX role_ai_time ON role_ai_reservations(at)',
)


def upgrade(db):
    for statement in SCHEMA:
        db.execute(statement)


def migrate(db):
    from .store import migrate as migrate_store
    migrate_store(db, target_version=4)
