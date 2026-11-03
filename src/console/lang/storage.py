"""Independent transactional language settings, caches and invocation quota."""
from contextlib import closing, contextmanager
import json
import math
import os
from pathlib import Path
import sqlite3

from console.roles.storage import run_lock

LANG_PATH = Path(os.environ.get('IMGW_LANG_DB', '/var/lib/imgw/ops-app/lang.sqlite3'))
DEFAULT_TERMS = ('DMX', 'RDM', 'Art-Net', 'sACN', 'beam', 'spot', 'wash', 'moving head', 'IP65')
SCHEMA = '''
CREATE TABLE IF NOT EXISTS settings (
 id INTEGER PRIMARY KEY CHECK(id=1), my_language TEXT NOT NULL, auto_native_send INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS glossary (
 id INTEGER PRIMARY KEY, term TEXT NOT NULL UNIQUE, mode TEXT NOT NULL, translation TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS contacts (
 channel TEXT NOT NULL, conversation_key TEXT NOT NULL, native_language TEXT,
 source TEXT, confidence REAL NOT NULL, updated_at REAL NOT NULL,
 PRIMARY KEY(channel,conversation_key));
CREATE TABLE IF NOT EXISTS translation_cache (
 source_hash TEXT NOT NULL, target_language TEXT NOT NULL, glossary_hash TEXT NOT NULL,
 text TEXT NOT NULL, created_at REAL NOT NULL,
 PRIMARY KEY(source_hash,target_language,glossary_hash));
CREATE TABLE IF NOT EXISTS reservations (id INTEGER PRIMARY KEY, at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS reservations_at ON reservations(at);
CREATE TABLE IF NOT EXISTS audit (id INTEGER PRIMARY KEY, at REAL NOT NULL,
 who TEXT NOT NULL, action TEXT NOT NULL, fields TEXT NOT NULL);
'''


class RateLimited(Exception):
    def __init__(self, retry_after, result=None):
        self.retry_after = max(1, int(retry_after))
        self.result = result
        super().__init__('翻译 AI 的独立小时额度已用完，请稍后重试；已完成的译文已保留')


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.connection = None

    @contextmanager
    def read(self):
        if not self.path.exists():
            yield None
            return
        if self.path.is_symlink():
            raise ValueError('语言数据库路径不可用')
        with closing(sqlite3.connect(self.path.resolve().as_uri() + '?mode=ro', uri=True)) as db:
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA query_only=ON')
            yield db

    @contextmanager
    def write(self):
        with run_lock(self):
            if self.path.is_symlink():
                raise ValueError('语言数据库路径不可用')
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            # New files contain private translations and are owner-readable only.
            fd = os.open(self.path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            os.close(fd)
            with closing(sqlite3.connect(self.path, timeout=10)) as db:
                db.row_factory = sqlite3.Row
                db.executescript('BEGIN IMMEDIATE;\n' + SCHEMA)
                try:
                    db.execute("INSERT OR IGNORE INTO settings VALUES(1,'zh-CN',1)")
                    # Seed once, so deleting an initial term stays deleted.
                    if not db.execute("SELECT 1 FROM audit WHERE action='initialize'").fetchone():
                        db.executemany('INSERT OR IGNORE INTO glossary(id,term,mode,translation) VALUES(?,?,?,?)',
                                       ((i, term, 'keep', '') for i, term in enumerate(DEFAULT_TERMS, 1)))
                        self.audit(db, 0, 'system', 'initialize', ())
                    yield db
                    db.commit()
                except BaseException:
                    db.rollback()
                    raise

    @staticmethod
    def audit(db, now, who, action, fields):
        db.execute('INSERT INTO audit(at,who,action,fields) VALUES(?,?,?,?)',
                   (now, who, action, json.dumps(sorted(fields), ensure_ascii=False)))

    def reserve(self, now, limit):
        with self.write() as db:
            rows = [r[0] for r in db.execute('SELECT at FROM reservations WHERE at>? ORDER BY at', (now - 3600,))]
            if len(rows) >= limit:
                raise RateLimited(math.ceil(rows[len(rows) - limit] + 3600 - now))
            db.execute('DELETE FROM reservations WHERE at<=?', (now - 3600,))
            db.execute('INSERT INTO reservations(at) VALUES(?)', (now,))

    def quota(self, now, limit):
        with self.read() as db:
            used = db.execute('SELECT count(*) FROM reservations WHERE at>?', (now - 3600,)).fetchone()[0] if db else 0
        return dict(hourly_limit=limit, used=used, remaining=max(0, limit - used))
