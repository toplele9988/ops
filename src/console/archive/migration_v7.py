"""Extraction suggestions and role registration, preserving deployed migrations."""
import re


def _role(db):
    row = db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='role_runs'").fetchone()
    if row is None:
        raise ValueError('岗位运行表不存在')
    sql = row[0]
    pattern = r"CHECK\s*\(\s*role\s+IN\s*\(([^)]*)\)\s*\)"
    match = re.search(pattern, sql, re.I)
    if match is None or not re.fullmatch(r"\s*'[a-z_]+'(?:\s*,\s*'[a-z_]+')*\s*", match[1]):
        raise ValueError('岗位运行约束不兼容')
    if 'extract' in re.findall(r"'([a-z_]+)'", match[1]):
        return
    sql = sql[:match.start()] + "CHECK(role IN (" + match[1] + ",'extract'))" + sql[match.end():]
    sql, count = re.subn(r'^CREATE\s+TABLE\s+["`\[]?role_runs["`\]]?',
                        'CREATE TABLE role_runs_extract_v7', sql, count=1, flags=re.I)
    if count != 1:
        raise ValueError('岗位运行结构不兼容')
    objects = [r[0] for r in db.execute("SELECT sql FROM sqlite_master WHERE tbl_name='role_runs' "
               "AND type IN ('index','trigger') AND sql IS NOT NULL")]
    columns = ','.join('"' + r[1].replace('"', '""') + '"' for r in db.execute('PRAGMA table_xinfo(role_runs)') if not r[6])
    db.execute(sql)
    db.execute(f'INSERT INTO role_runs_extract_v7 ({columns}) SELECT {columns} FROM role_runs')
    db.execute('DROP TABLE role_runs')
    legacy = db.execute('PRAGMA legacy_alter_table').fetchone()[0]
    try:
        # Existing views/child references must keep referring to role_runs.
        # Modern ALTER validates views in the brief drop/rename interval.
        db.execute('PRAGMA legacy_alter_table=ON')
        db.execute('ALTER TABLE role_runs_extract_v7 RENAME TO role_runs')
    finally:
        db.execute('PRAGMA legacy_alter_table=' + ('ON' if legacy else 'OFF'))
    for statement in objects:
        db.execute(statement)


SCHEMA = (
    """CREATE TABLE extraction_suggestions (
      id INTEGER PRIMARY KEY,
      kind TEXT NOT NULL CHECK(kind IN ('offer','freight')),
      conversation_key TEXT NOT NULL, source_message_refs TEXT NOT NULL,
      party_id INTEGER REFERENCES parties(id), party_hint TEXT,
      product_text TEXT, matched_product_id INTEGER REFERENCES products(id),
      price_fen INTEGER CHECK(price_fen IS NULL OR (typeof(price_fen)='integer' AND price_fen BETWEEN 0 AND 999999999999)),
      currency TEXT, unit TEXT, moq_units INTEGER CHECK(moq_units BETWEEN 1 AND 1000000),
      lead_time_text TEXT, tax_text TEXT, route TEXT, mode TEXT,
      origin TEXT, destination_country TEXT, destination_detail TEXT,
      min_charge_fen INTEGER CHECK(min_charge_fen IS NULL OR (typeof(min_charge_fen)='integer' AND min_charge_fen BETWEEN 0 AND 999999999999)),
      validity_text TEXT, suggested_valid_until TEXT, quoted_on TEXT,
      excerpt TEXT NOT NULL CHECK(length(excerpt)<=200),
      confidence REAL NOT NULL CHECK(confidence BETWEEN 0 AND 1),
      source TEXT NOT NULL CHECK(source IN ('rules','ai')),
      status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','accepted','dismissed')),
      decided_at TEXT, decided_by TEXT, created_at TEXT NOT NULL,
      input_fingerprint TEXT NOT NULL UNIQUE,
      has_attachment INTEGER NOT NULL DEFAULT 0 CHECK(has_attachment IN (0,1)),
      run_id INTEGER REFERENCES role_runs(id), accepted_record_id INTEGER,
      CHECK((status='pending' AND decided_at IS NULL AND decided_by IS NULL AND accepted_record_id IS NULL)
         OR (status='dismissed' AND decided_at IS NOT NULL AND decided_by IS NOT NULL AND accepted_record_id IS NULL)
         OR (status='accepted' AND decided_at IS NOT NULL AND decided_by IS NOT NULL AND accepted_record_id IS NOT NULL)))""",
    'CREATE INDEX extraction_pending ON extraction_suggestions(status,kind,created_at,id)',
    'CREATE INDEX extraction_conversation ON extraction_suggestions(conversation_key,created_at,id)',
)


def upgrade(db):
    _role(db)
    for statement in SCHEMA:
        db.execute(statement)


def migrate(db):
    from .store import migrate as migrate_store
    migrate_store(db, target_version=7)
