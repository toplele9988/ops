"""Quote terms and reviewed documents, separate from the published archive schema."""
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import threading

STATE_PATH = Path('/var/lib/imgw/ops-app/quote-state.sqlite3')
_LOCK = threading.RLock()


class State:
    def __init__(self, path):
        self.path = Path(path) if str(path) != ':memory:' else None
        self.memory = sqlite3.connect(':memory:', check_same_thread=False) if self.path is None else None

    def _check_path(self):
        path = self.path.absolute()
        if '..' in path.parts or any(part.is_symlink() for part in (path, *path.parents)):
            raise ValueError('报价状态路径无效')

    def get(self, key):
        with _LOCK:
            if self.memory is not None:
                return self._get(self.memory, key)
            self._check_path()
            if not self.path.exists():
                return {}
            with closing(sqlite3.connect(self.path.resolve().as_uri() + '?mode=ro', uri=True)) as db:
                return self._get(db, key)

    @staticmethod
    def _get(db, key):
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='quote_state'").fetchone():
            return {}
        row = db.execute('SELECT payload FROM quote_state WHERE quote_key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else {}

    def save(self, key, payload, who, operation, stamp):
        with _LOCK:
            if self.memory is not None:
                return self._save(self.memory, key, payload, who, operation, stamp)
            self._check_path()
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            fd = os.open(self.path, os.O_CREAT | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
            try:
                os.fchmod(fd, 0o600)
            finally:
                os.close(fd)
            with closing(sqlite3.connect(self.path.resolve().as_uri() + '?mode=rw', uri=True, timeout=10)) as db:
                self._save(db, key, payload, who, operation, stamp)

    @staticmethod
    def _save(db, key, payload, who, operation, stamp):
        db.execute('BEGIN IMMEDIATE')
        with db:
            db.execute('CREATE TABLE IF NOT EXISTS quote_state(quote_key TEXT PRIMARY KEY,payload TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS quote_state_audit(id INTEGER PRIMARY KEY,quote_key TEXT NOT NULL,who TEXT NOT NULL,operation TEXT NOT NULL,at TEXT NOT NULL,fields TEXT NOT NULL)')
            encoded = json.dumps(payload, ensure_ascii=False)
            old = db.execute('SELECT payload FROM quote_state WHERE quote_key=?', (key,)).fetchone()
            if old and old[0] == encoded:
                return
            db.execute('INSERT INTO quote_state VALUES(?,?) ON CONFLICT(quote_key) DO UPDATE SET payload=excluded.payload',
                       (key, encoded))
            db.execute('INSERT INTO quote_state_audit(quote_key,who,operation,at,fields) VALUES(?,?,?,?,?)',
                       (key, who, operation, stamp, json.dumps(sorted(payload))))
