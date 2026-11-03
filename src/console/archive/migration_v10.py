"""Add owner-reviewed listing drafts and the listing role/quota lane."""
import re


def _rebuild_role_runs(db):
    row = db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='role_runs'").fetchone()
    if row is None:
        raise ValueError('岗位运行表不存在')
    sql = row[0]
    replacements = (
        (r"CHECK\s*\(\s*role\s+IN\s*\(([^)]*)\)\s*\)", 'listing', 'role'),
        (r"CHECK\s*\(\s*subject_kind\s+IN\s*\(([^)]*)\)\s*\)", 'product', 'subject_kind'),
    )
    changed = False
    for pattern, value, label in replacements:
        match = re.search(pattern, sql, re.I)
        if match is None or not re.fullmatch(r"\s*'[a-z_]+'(?:\s*,\s*'[a-z_]+'\s*)*", match[1]):
            raise ValueError('岗位运行结构不兼容')
        values = re.findall(r"'([a-z_]+)'", match[1])
        if value not in values:
            sql = sql[:match.start()] + 'CHECK(' + label + ' IN (' + match[1] + ",'" + value + "'))" + sql[match.end():]
            changed = True
    if not changed:
        return
    sql, count = re.subn(r'^CREATE\s+TABLE\s+["`\[]?role_runs["`\]]?',
                         'CREATE TABLE role_runs_listing_v10', sql, count=1, flags=re.I)
    if count != 1:
        raise ValueError('岗位运行结构不兼容')
    objects = [r[0] for r in db.execute("SELECT sql FROM sqlite_master WHERE tbl_name='role_runs' "
                                        "AND type IN ('index','trigger') AND sql IS NOT NULL")]
    columns = ','.join('"' + r[1].replace('"', '""') + '"'
                       for r in db.execute('PRAGMA table_xinfo(role_runs)') if not r[6])
    db.execute(sql)
    db.execute(f'INSERT INTO role_runs_listing_v10 ({columns}) SELECT {columns} FROM role_runs')
    db.execute('DROP TABLE role_runs')
    legacy = db.execute('PRAGMA legacy_alter_table').fetchone()[0]
    try:
        db.execute('PRAGMA legacy_alter_table=ON')
        db.execute('ALTER TABLE role_runs_listing_v10 RENAME TO role_runs')
    finally:
        db.execute('PRAGMA legacy_alter_table=' + ('ON' if legacy else 'OFF'))
    for statement in objects:
        db.execute(statement)


def upgrade(db):
    _rebuild_role_runs(db)

    reservation_columns = {r[1] for r in db.execute('PRAGMA table_xinfo(role_ai_reservations)')}
    if 'bucket' not in reservation_columns:
        db.execute("ALTER TABLE role_ai_reservations ADD COLUMN bucket TEXT NOT NULL DEFAULT 'shared' "
                   "CHECK(bucket IN ('shared','listing'))")
    db.execute('CREATE INDEX IF NOT EXISTS role_ai_bucket_time ON role_ai_reservations(bucket,at)')

    db.execute("""CREATE TABLE IF NOT EXISTS listing_drafts (
      id INTEGER PRIMARY KEY,
      product_id INTEGER NOT NULL REFERENCES products(id),
      channel TEXT NOT NULL CHECK(channel IN ('alibaba','website')),
      status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','ready','published','archived')),
      title TEXT NOT NULL DEFAULT '', keywords_json TEXT NOT NULL DEFAULT '[]',
      category_hint TEXT NOT NULL DEFAULT '', attributes_json TEXT NOT NULL DEFAULT '{}',
      moq INTEGER, unit TEXT NOT NULL DEFAULT '', price_tiers_json TEXT NOT NULL DEFAULT '[]',
      lead_time_text TEXT NOT NULL DEFAULT '', packaging_json TEXT NOT NULL DEFAULT '{}',
      selling_points_json TEXT NOT NULL DEFAULT '[]', description TEXT NOT NULL DEFAULT '' CHECK(length(description)<=65536),
      image_refs_json TEXT NOT NULL DEFAULT '[]', chinese_review TEXT NOT NULL DEFAULT '',
      compliance_json TEXT NOT NULL DEFAULT '{}', sources_json TEXT NOT NULL DEFAULT '[]',
      generation_source TEXT NOT NULL DEFAULT 'rules' CHECK(generation_source IN ('ai','rules')),
      internal_pricing_json TEXT NOT NULL DEFAULT '{}', input_fingerprint TEXT NOT NULL,
      created_at TEXT NOT NULL, updated_at TEXT NOT NULL, created_by TEXT NOT NULL,
      decided_at TEXT, decided_by TEXT, published_ref TEXT)""")
    db.execute('CREATE INDEX IF NOT EXISTS listing_product_channel ON listing_drafts(product_id,channel,created_at DESC,id DESC)')
    db.execute('CREATE INDEX IF NOT EXISTS listing_status_updated ON listing_drafts(status,updated_at DESC,id DESC)')


def migrate(db):
    from .store import migrate as migrate_store
    migrate_store(db, target_version=10)
