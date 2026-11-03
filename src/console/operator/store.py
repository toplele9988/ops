"""Private durable workflow ledger. All admission decisions use BEGIN IMMEDIATE."""
from contextlib import contextmanager, closing
import json
from pathlib import Path
import hashlib
import os
import sqlite3
import stat
import threading

_LOCK = threading.RLock()


def sha(value):
    return hashlib.sha256(value.encode("utf8")).hexdigest()

DEFAULTS = dict(enabled=False, dry_run=True, levels={}, types={}, contacts={})
TYPES = ('customer_project', 'customer_wholesale', 'customer_end', 'customer_other',
         'supplier_product', 'supplier_logistics', 'supplier_support')
SCHEMA = (
    '''CREATE TABLE IF NOT EXISTS operator_cursors (name TEXT PRIMARY KEY,
       generation TEXT NOT NULL, position INTEGER NOT NULL)''',
    '''CREATE TABLE IF NOT EXISTS operator_ingested (channel TEXT NOT NULL,
       conversation_key TEXT NOT NULL, stable_id TEXT NOT NULL, at REAL NOT NULL,
       PRIMARY KEY(channel,conversation_key,stable_id))''',
    '''CREATE TABLE IF NOT EXISTS operator_health (component TEXT PRIMARY KEY,
       ok INTEGER NOT NULL, at REAL NOT NULL, error_type TEXT, failures INTEGER NOT NULL DEFAULT 0)''',
    'CREATE TABLE IF NOT EXISTS operator_settings (id INTEGER PRIMARY KEY CHECK(id=1), value TEXT NOT NULL)',
    '''CREATE TABLE IF NOT EXISTS operator_profiles (conversation_key TEXT PRIMARY KEY,
       value TEXT NOT NULL)''',
    '''CREATE TABLE IF NOT EXISTS operator_events (id TEXT PRIMARY KEY, digest TEXT NOT NULL,
       payload TEXT NOT NULL, state TEXT NOT NULL, created_at REAL NOT NULL)''',
    '''CREATE TABLE IF NOT EXISTS operator_actions (id INTEGER PRIMARY KEY, event_id TEXT NOT NULL,
       kind TEXT NOT NULL, contact_key TEXT, level INTEGER NOT NULL, state TEXT NOT NULL,
       reason TEXT NOT NULL, due_at REAL, approved_by TEXT, dry_run INTEGER NOT NULL,
       data TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL)''',
    '''CREATE TABLE IF NOT EXISTS operator_decisions (id INTEGER PRIMARY KEY, event_id TEXT,
       action_id INTEGER, at REAL NOT NULL, phase TEXT NOT NULL, data TEXT NOT NULL)''',
    'CREATE INDEX IF NOT EXISTS operator_action_event ON operator_actions(event_id)',
    '''CREATE TABLE IF NOT EXISTS operator_attempts (id INTEGER PRIMARY KEY,
       action_id INTEGER NOT NULL, part INTEGER NOT NULL, contact_key TEXT NOT NULL,
       digest TEXT NOT NULL, idempotency_key TEXT NOT NULL UNIQUE, state TEXT NOT NULL,
       created_at REAL NOT NULL, UNIQUE(action_id,part))''',
    'CREATE INDEX IF NOT EXISTS operator_contact_time ON operator_attempts(contact_key,created_at)',
    '''CREATE TABLE IF NOT EXISTS operator_receipts (operation_key TEXT PRIMARY KEY,
       digest TEXT NOT NULL, result TEXT NOT NULL)''',
)


def dumps(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


class Store:
    def __init__(self, path):
        self.path = Path(path)

    @contextmanager
    def _transaction(self):
        with _LOCK:
            if any(p.is_symlink() for p in (self.path, *self.path.absolute().parents)):
                raise ValueError('运营数据库路径无效')
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            fd = os.open(self.path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            try:
                info = os.fstat(fd)
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise ValueError('运营数据库路径无效')
                os.fchmod(fd, 0o600)
            finally:
                os.close(fd)
            with closing(sqlite3.connect(self.path, timeout=10)) as db:
                db.row_factory = sqlite3.Row
                db.execute('PRAGMA journal_mode=WAL')
                db.execute('PRAGMA synchronous=FULL')
                db.execute('BEGIN IMMEDIATE')
                with db:
                    yield db

    @contextmanager
    def write(self):
        with self._transaction() as db:
            for sql in SCHEMA:
                db.execute(sql)
            db.execute('INSERT OR IGNORE INTO operator_settings VALUES(1,?)', (dumps(DEFAULTS),))
            yield db

    @contextmanager
    def read(self):
        if not self.path.exists():
            yield None
            return
        with closing(sqlite3.connect(self.path.resolve().as_uri() + '?mode=ro', uri=True)) as db:
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA query_only=ON')
            yield db

    def initialize(self):
        with self.write():
            pass

    def health(self, component, now, error=None):
        # Exception messages may contain private text/paths. Record only the type.
        with self.write() as db:
            db.execute('INSERT INTO operator_health(component,ok,at,error_type,failures) VALUES(?,?,?,?,?) '
                       'ON CONFLICT(component) DO UPDATE SET ok=excluded.ok,at=excluded.at,'
                       'error_type=excluded.error_type,failures=failures+excluded.failures',
                       (component, int(error is None), now, type(error).__name__ if error else None, int(error is not None)))

    @staticmethod
    def settings(db):
        return json.loads(db.execute('SELECT value FROM operator_settings WHERE id=1').fetchone()[0])

    @staticmethod
    def decision(db, now, phase, data, event_id=None, action_id=None):
        db.execute('INSERT INTO operator_decisions(event_id,action_id,at,phase,data) VALUES(?,?,?,?,?)',
                   (event_id, action_id, now, phase, dumps(data)))

    @staticmethod
    def action(row):
        return dict(row, data=json.loads(row['data']))
