"""Rehearsal drafts in the outbox database. No bridge is invoked; live senders cannot consume these rows."""
import json
import time
from console.archive.validation import Conflict
from .store import Store, sha

SCHEMA = '''CREATE TABLE IF NOT EXISTS inbox_dry_run_drafts (
 id INTEGER PRIMARY KEY, request_id TEXT UNIQUE NOT NULL, fingerprint TEXT NOT NULL,
 conversation_key TEXT NOT NULL, source_key TEXT NOT NULL, channel TEXT NOT NULL,
 text TEXT NOT NULL, state TEXT NOT NULL CHECK(state IN ('draft','queued')),
 dry_run INTEGER NOT NULL DEFAULT 1 CHECK(dry_run=1), created_at REAL NOT NULL,
 created_by TEXT NOT NULL)'''


class DryRun:
    def __init__(self, path, clock=None):
        self.store = Store(path)
        self.clock = clock or time.time

    def save(self, key, channel, source, text, request_id, who, state='queued'):
        fingerprint = sha(json.dumps([key, channel, source, text, state], ensure_ascii=False))
        with self.store._write() as db:
            db.execute(SCHEMA)
            old = db.execute('SELECT * FROM inbox_dry_run_drafts WHERE request_id=?', (request_id,)).fetchone()
            if old:
                if old['fingerprint'] != fingerprint:
                    raise Conflict('这次操作已保存，请核对记录后再发起新操作')
                return dict(old)
            row_id = db.execute('INSERT INTO inbox_dry_run_drafts '
                '(request_id,fingerprint,conversation_key,source_key,channel,text,state,created_at,created_by) '
                'VALUES(?,?,?,?,?,?,?,?,?)',
                (request_id, fingerprint, key, source, channel, text, state, self.clock(), who)).lastrowid
            self.store._audit(db, self.clock(), 'inbox_dry_run_' + state, key, request_id, dry_run=True)
            return dict(db.execute('SELECT * FROM inbox_dry_run_drafts WHERE id=?', (row_id,)).fetchone())

    def recent(self, key):
        if not self.store.path.exists():
            return []
        from console.inbox.store import read_database
        import sqlite3
        with read_database(self.store.path) as db:
            db.row_factory = sqlite3.Row
            if not db.execute("SELECT 1 FROM sqlite_master WHERE name='inbox_dry_run_drafts'").fetchone():
                return []
            return [dict(r) for r in db.execute('SELECT * FROM inbox_dry_run_drafts '
                'WHERE conversation_key=? OR source_key=? ORDER BY id DESC LIMIT 50', (key, key))]
