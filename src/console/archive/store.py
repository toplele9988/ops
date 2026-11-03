"""SQLite storage with versioned migrations and transactional, value-free audits."""
from contextlib import closing, contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import threading

ARCHIVE_PATH = Path('/var/lib/imgw/ops-app/archive.sqlite3')
_LOCK = threading.RLock()
TABLES = frozenset(('parties', 'products', 'product_sources', 'offers', 'freight_quotes', 'terms', 'product_merges',
    'identities', 'deals', 'deal_participants', 'deal_conversations', 'deal_items', 'deal_tasks', 'deal_events',
    'quote_rounds', 'quote_lines', 'quote_costs', 'quote_negotiations',
    'role_runs', 'role_alerts', 'triage_suggestions', 'role_ai_reservations', 'extraction_suggestions',
    'deal_payments', 'fulfilment_nodes', 'deal_issues', 'deal_issue_notes', 'listing_drafts'))

SCHEMA = (
    """CREATE TABLE parties (
      id INTEGER PRIMARY KEY, kind TEXT NOT NULL CHECK(kind IN ('supplier','forwarder')),
      name TEXT NOT NULL, short_name TEXT, aliases_json TEXT, conversation_ref TEXT, notes TEXT,
      status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','paused','archived')),
      created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(kind,name))""",
    """CREATE TABLE products (
      id INTEGER PRIMARY KEY, supplier_id INTEGER REFERENCES parties(id), name TEXT NOT NULL,
      model TEXT, category TEXT, wattage REAL, description TEXT,
      status TEXT NOT NULL DEFAULT 'candidate' CHECK(status IN ('candidate','confirmed','hidden')),
      product_l_cm REAL CHECK(product_l_cm BETWEEN 2 AND 300),
      product_w_cm REAL CHECK(product_w_cm BETWEEN 2 AND 300),
      product_h_cm REAL CHECK(product_h_cm BETWEEN 2 AND 300),
      carton_l_cm REAL CHECK(carton_l_cm BETWEEN 2 AND 300),
      carton_w_cm REAL CHECK(carton_w_cm BETWEEN 2 AND 300),
      carton_h_cm REAL CHECK(carton_h_cm BETWEEN 2 AND 300),
      gross_kg REAL CHECK(gross_kg BETWEEN 0.05 AND 500),
      net_kg REAL CHECK(net_kg BETWEEN 0.05 AND 500),
      units_per_carton INTEGER CHECK(units_per_carton IS NULL OR (typeof(units_per_carton)='integer' AND units_per_carton BETWEEN 1 AND 10000)),
      field_state_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""",
    """CREATE TABLE product_sources (
      id INTEGER PRIMARY KEY, product_id INTEGER NOT NULL REFERENCES products(id),
      kind TEXT NOT NULL CHECK(kind IN ('equipment_spec','asset','chunk','offer','manual')),
      ref TEXT NOT NULL, title TEXT, note TEXT, UNIQUE(product_id,kind,ref))""",
    """CREATE TABLE offers (
      id INTEGER PRIMARY KEY, product_id INTEGER NOT NULL REFERENCES products(id),
      price_fen INTEGER NOT NULL CHECK(typeof(price_fen)='integer' AND price_fen BETWEEN 0 AND 999999999999),
      currency TEXT NOT NULL DEFAULT 'CNY', unit TEXT, price_basis TEXT, tax_basis TEXT,
      includes_flightcase INTEGER CHECK(includes_flightcase IN (0,1)),
      moq_units INTEGER CHECK(moq_units BETWEEN 1 AND 1000000),
      lead_days_min INTEGER CHECK(lead_days_min BETWEEN 0 AND 3650),
      lead_days_max INTEGER CHECK(lead_days_max BETWEEN 0 AND 3650),
      quoted_on TEXT NOT NULL, valid_until TEXT, corrects_offer_id INTEGER REFERENCES offers(id),
      source_ref TEXT, status TEXT NOT NULL DEFAULT 'suggested' CHECK(status IN ('suggested','confirmed','rejected')),
      created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
      CHECK(valid_until IS NULL OR valid_until >= quoted_on),
      CHECK(lead_days_min IS NULL OR lead_days_max IS NULL OR lead_days_max >= lead_days_min),
      CHECK(corrects_offer_id IS NULL OR corrects_offer_id != id))""",
    """CREATE TABLE freight_quotes (
      id INTEGER PRIMARY KEY, forwarder_id INTEGER NOT NULL REFERENCES parties(id), origin TEXT NOT NULL,
      destination_country TEXT NOT NULL, destination_detail TEXT,
      mode TEXT NOT NULL CHECK(mode IN ('air','sea','express','rail','truck')), service TEXT NOT NULL,
      unit TEXT NOT NULL CHECK(unit IN ('kg','cbm','ticket','container')),
      price_fen INTEGER NOT NULL CHECK(typeof(price_fen)='integer' AND price_fen BETWEEN 0 AND 999999999999),
      currency TEXT NOT NULL, min_charge_fen INTEGER CHECK(min_charge_fen IS NULL OR (typeof(min_charge_fen)='integer' AND min_charge_fen BETWEEN 0 AND 999999999999)),
      transit_days_min INTEGER CHECK(transit_days_min BETWEEN 0 AND 3650),
      transit_days_max INTEGER CHECK(transit_days_max BETWEEN 0 AND 3650),
      quoted_on TEXT NOT NULL, valid_until TEXT, source_ref TEXT, notes TEXT,
      status TEXT NOT NULL DEFAULT 'suggested' CHECK(status IN ('suggested','confirmed','rejected')),
      created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
      CHECK(valid_until IS NULL OR valid_until >= quoted_on),
      CHECK(transit_days_min IS NULL OR transit_days_max IS NULL OR transit_days_max >= transit_days_min))""",
    """CREATE TABLE terms (
      id INTEGER PRIMARY KEY, party_id INTEGER NOT NULL REFERENCES parties(id),
      field TEXT NOT NULL CHECK(field IN ('lead_time','moq','payment','shipping_origin','warranty','contact_note')),
      value TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('suggested','confirmed','rejected')),
      source_ref TEXT, updated_at TEXT NOT NULL)""",
    """CREATE TABLE product_merges (
      id INTEGER PRIMARY KEY, product_id INTEGER NOT NULL UNIQUE REFERENCES products(id),
      into_id INTEGER NOT NULL REFERENCES products(id), CHECK(product_id != into_id))""",
    """CREATE TABLE audit (
      id INTEGER PRIMARY KEY, at TEXT NOT NULL, who TEXT NOT NULL, action TEXT NOT NULL,
      table_name TEXT NOT NULL, row_id INTEGER NOT NULL, detail TEXT NOT NULL)""",
    'CREATE INDEX products_supplier ON products(supplier_id,status)',
    'CREATE INDEX sources_ref ON product_sources(kind,ref)',
    'CREATE INDEX offers_product ON offers(product_id,status,quoted_on)',
    'CREATE INDEX offers_source ON offers(source_ref)',
    'CREATE INDEX freight_forwarder ON freight_quotes(forwarder_id,status,quoted_on)',
    'CREATE INDEX terms_party ON terms(party_id)',
)


def now():
    return datetime.now(timezone.utc).isoformat(timespec='microseconds')


def configure(db):
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    db.execute('PRAGMA busy_timeout=10000')


def migrate(db, *, target_version=2):
    """Atomic archive migrations; old callers keep their requested baseline.

    Foreign keys must be disabled outside the transaction for the parent-table
    rebuild, then validated before commit. Never use executescript (implicit commit).
    Quote/archive writes request target_version=9; listing requests v10. Every caller accepts a newer
    database without downgrading it; GET never migrates a filesystem database.
    """
    from .migration_v2 import upgrade
    if target_version not in (2, 3, 4, 6, 7, 8, 9, 10):
        raise ValueError('档案数据库版本不兼容')
    if db.in_transaction:
        raise ValueError('迁移必须在独立事务中执行')
    foreign_keys = db.execute('PRAGMA foreign_keys').fetchone()[0]
    db.execute('PRAGMA foreign_keys=OFF')
    try:
        db.execute('BEGIN IMMEDIATE')
        db.execute('CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)')
        versions = [r[0] for r in db.execute('SELECT version FROM schema_migrations ORDER BY version')]
        baselines = ([], [1], [2], [1, 2], [2, 3], [1, 2, 3], [2, 3, 4], [1, 2, 3, 4])
        # Preserve optional installed v5 history without manufacturing it;
        # fulfilment adds tables in v8 and freight channels in v9.
        later = tuple(base + suffix for base in ([2, 3, 4], [1, 2, 3, 4])
                      for suffix in ([5], [6], [5, 6], [6, 7], [5, 6, 7], [6, 7, 8],
                                     [5, 6, 7, 8], [6, 7, 8, 9], [5, 6, 7, 8, 9],
                                     [6, 7, 8, 9, 10], [5, 6, 7, 8, 9, 10]))
        if versions not in baselines + later:
            raise ValueError('档案数据库版本不兼容')
        if 2 not in versions:
            if not versions:
                for statement in SCHEMA:
                    db.execute(statement)
            upgrade(db)
            if db.execute('PRAGMA foreign_key_check').fetchone() is not None:
                raise ValueError('档案关联校验失败')
            db.execute('INSERT INTO schema_migrations VALUES (2,?)', (now(),))
            audit(db, 'migration', 'migrate', 'schema_migrations', 2, ('version',))
        if target_version >= 3 and 3 not in versions:
            from .migration_v3 import upgrade as upgrade_quotes
            upgrade_quotes(db)
            if db.execute('PRAGMA foreign_key_check').fetchone() is not None:
                raise ValueError('档案关联校验失败')
            db.execute('INSERT INTO schema_migrations VALUES (3,?)', (now(),))
            audit(db, 'migration', 'migrate', 'schema_migrations', 3, ('version',))
        if target_version >= 4 and 4 not in versions:
            from .migration_v4 import upgrade as upgrade_roles
            upgrade_roles(db)
            if db.execute('PRAGMA foreign_key_check').fetchone() is not None:
                raise ValueError('档案关联校验失败')
            db.execute('INSERT INTO schema_migrations VALUES (4,?)', (now(),))
            audit(db, 'migration', 'migrate', 'schema_migrations', 4, ('version',))
        if target_version >= 6 and 6 not in versions:
            from .migration_v6 import upgrade as upgrade_audit
            upgrade_audit(db)
            if db.execute('PRAGMA foreign_key_check').fetchone() is not None:
                raise ValueError('档案关联校验失败')
            db.execute('INSERT INTO schema_migrations VALUES (6,?)', (now(),))
            audit(db, 'migration', 'migrate', 'schema_migrations', 6, ('version',))
        if target_version >= 7 and 7 not in versions:
            from .migration_v7 import upgrade as upgrade_extract
            upgrade_extract(db)
            if db.execute('PRAGMA foreign_key_check').fetchone() is not None:
                raise ValueError('档案关联校验失败')
            db.execute('INSERT INTO schema_migrations VALUES (7,?)', (now(),))
            audit(db, 'migration', 'migrate', 'schema_migrations', 7, ('version',))
        if target_version >= 8 and 8 not in versions:
            from .migration_v8 import upgrade as upgrade_fulfilment
            upgrade_fulfilment(db)
            if db.execute('PRAGMA foreign_key_check').fetchone() is not None:
                raise ValueError('档案关联校验失败')
            db.execute('INSERT INTO schema_migrations VALUES (8,?)', (now(),))
            audit(db, 'migration', 'migrate', 'schema_migrations', 8, ('version',))
        if target_version >= 9 and 9 not in versions:
            from .migration_v9 import upgrade as upgrade_freight_channel
            upgrade_freight_channel(db)
            if db.execute('PRAGMA foreign_key_check').fetchone() is not None:
                raise ValueError('档案关联校验失败')
            db.execute('INSERT INTO schema_migrations VALUES (9,?)', (now(),))
            audit(db, 'migration', 'migrate', 'schema_migrations', 9, ('version',))
        if target_version >= 10 and 10 not in versions:
            from .migration_v10 import upgrade as upgrade_listing
            upgrade_listing(db)
            if db.execute('PRAGMA foreign_key_check').fetchone() is not None:
                raise ValueError('档案关联校验失败')
            db.execute('INSERT INTO schema_migrations VALUES (10,?)', (now(),))
            audit(db, 'migration', 'migrate', 'schema_migrations', 10, ('version',))
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.execute('PRAGMA foreign_keys=' + ('ON' if foreign_keys else 'OFF'))


def audit(db, who, action, table, row_id, fields=()):
    # Never include values (notes, credentials, references, prices, bodies).
    detail = json.dumps({'fields': sorted(fields)}, ensure_ascii=False)
    db.execute('INSERT INTO audit(at,who,action,table_name,row_id,detail) VALUES (?,?,?,?,?,?)',
               (now(), who, action, table, row_id, detail))


def insert(db, table, values, who, action='create'):
    if table not in TABLES:
        raise ValueError('无效数据表')
    columns = ','.join(values)
    cur = db.execute(f'INSERT INTO {table} ({columns}) VALUES ({",".join("?" for _ in values)})', tuple(values.values()))
    audit(db, who, action, table, cur.lastrowid, values)
    return cur.lastrowid


def update(db, table, row_id, values, who, action='update'):
    if table not in TABLES:
        raise ValueError('无效数据表')
    if not values:
        return
    cur = db.execute(f'UPDATE {table} SET ' + ','.join(f'{k}=?' for k in values) + ' WHERE id=?',
                     (*values.values(), row_id))
    if cur.rowcount != 1:
        raise ValueError('记录不存在')
    audit(db, who, action, table, row_id, values)


class Store:
    def __init__(self, path=ARCHIVE_PATH, *, connection=None, target_version=2):
        self.path = Path(path)
        self.connection = connection  # Only for in-memory dry runs.
        self.target_version = target_version

    @contextmanager
    def read(self):
        if self.connection is not None:
            yield self.connection
        elif not self.path.exists():
            with closing(sqlite3.connect(':memory:')) as db:
                configure(db)
                migrate(db, target_version=self.target_version)
                yield db
        else:
            with closing(sqlite3.connect(self.path.resolve().as_uri() + '?mode=ro', uri=True)) as db:
                configure(db)
                db.execute('PRAGMA query_only=ON')
                db.execute('BEGIN')
                yield db

    @contextmanager
    def write(self):
        if self.connection is not None:
            db = self.connection
            if self.target_version >= 3:
                migrate(db, target_version=self.target_version)
            db.execute('BEGIN IMMEDIATE')
            with db:
                yield db
            return
        # Serialize this module's short process-wide umask window. New WAL/SHM
        # inherit the database's 0600 mode; explicitly tighten existing sidecars.
        with _LOCK:
            previous = os.umask(0o077)
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                if self.path.is_symlink():
                    raise ValueError('档案路径无效')
                fd = os.open(self.path, os.O_CREAT | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
                try:
                    os.fchmod(fd, 0o600)
                finally:
                    os.close(fd)
                for suffix in ('-wal', '-shm'):
                    side = Path(str(self.path) + suffix)
                    if side.exists():
                        if side.is_symlink():
                            raise ValueError('档案路径无效')
                        side.chmod(0o600)
            finally:
                os.umask(previous)
        with _LOCK, closing(sqlite3.connect(self.path.resolve().as_uri() + '?mode=rw', uri=True)) as db:
            configure(db)
            db.execute('PRAGMA journal_mode=WAL')
            migrate(db, target_version=self.target_version)
            db.execute('BEGIN IMMEDIATE')
            with db:
                yield db

    def initialize(self):
        with self.write():
            pass
