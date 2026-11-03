"""Private, durable admissions: deduplication and rate checks share one transaction."""
from contextlib import closing, contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import stat
import threading

from console.inbox.store import read_database

OUTBOX_PATH = Path('/var/lib/imgw/ops-app/outbox.sqlite3')
_WRITE_LOCK = threading.RLock()  # Also bounds the process-wide umask window.
_SCHEMA = '''
CREATE TABLE IF NOT EXISTS send_settings (
    channel TEXT PRIMARY KEY, enabled INTEGER NOT NULL DEFAULT 0, updated_at REAL);
CREATE TABLE IF NOT EXISTS outbox (
    id INTEGER PRIMARY KEY, request_id TEXT UNIQUE NOT NULL, idempotency_key TEXT UNIQUE NOT NULL,
    conversation_key TEXT NOT NULL, channel TEXT NOT NULL, chat_ref TEXT NOT NULL,
    text TEXT NOT NULL, text_sha TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('sending','sent','failed','unknown')),
    error_code TEXT, draft_id INTEGER, created_at REAL, updated_at REAL);
CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY, at REAL, action TEXT, conversation_key TEXT, request_id TEXT, detail TEXT);
CREATE INDEX IF NOT EXISTS outbox_time ON outbox(created_at);
CREATE INDEX IF NOT EXISTS outbox_conversation_time ON outbox(conversation_key,created_at);
'''


def sha(value):
    return hashlib.sha256(value.encode('utf8')).hexdigest()


class Store:
    def __init__(self, path=OUTBOX_PATH):
        self.path = Path(path)

    @contextmanager
    def _write(self):
        with _WRITE_LOCK:
            previous = os.umask(0o077)
            try:
                self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                fd = os.open(self.path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
                try:
                    info = os.fstat(fd)
                    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                        raise OSError('发送记录不可用')
                    os.fchmod(fd, 0o600)
                finally:
                    os.close(fd)
                with closing(sqlite3.connect(self.path, timeout=10)) as db:
                    db.row_factory = sqlite3.Row
                    db.execute('PRAGMA journal_mode=WAL')
                    db.execute('PRAGMA synchronous=FULL')
                    db.executescript(_SCHEMA)
                    db.execute('BEGIN IMMEDIATE')
                    with db:
                        yield db
            finally:
                os.umask(previous)

    @staticmethod
    def _audit(db, now, action, key=None, request_id=None, **detail):
        db.execute('INSERT INTO audit(at,action,conversation_key,request_id,detail) VALUES(?,?,?,?,?)',
                   (now, action, key, request_id, json.dumps(detail, ensure_ascii=False, sort_keys=True)))

    def audit(self, now, action, key=None, request_id=None, **detail):
        with self._write() as db:
            self._audit(db, now, action, key, request_id, **detail)

    def enabled(self):
        if not self.path.exists():
            return False
        with read_database(self.path) as db:
            row = db.execute("SELECT enabled FROM send_settings WHERE channel='whatsapp'").fetchone()
        return bool(row and row[0] == 1)

    def set_enabled(self, enabled, now):
        with self._write() as db:
            db.execute("INSERT INTO send_settings VALUES('whatsapp',?,?) ON CONFLICT(channel) "
                       'DO UPDATE SET enabled=excluded.enabled,updated_at=excluded.updated_at', (int(enabled), now))
            self._audit(db, now, 'settings_changed', channel='whatsapp', enabled=enabled, actor='owner_explicit')

    def find_request(self, request_id):
        if not self.path.exists():
            return None
        with read_database(self.path) as db:
            db.row_factory = sqlite3.Row
            row = db.execute('SELECT * FROM outbox WHERE request_id=?', (request_id,)).fetchone()
            return dict(row) if row else None

    def reserve(self, key, chat, text, request_id, draft_id, now, *, attachment_marker=None):
        """Return (row, new, rejection, retry_after); duplicates do not consume quota."""
        with self._write() as db:
            old = db.execute('SELECT * FROM outbox WHERE request_id=?', (request_id,)).fetchone()
            if old:
                conflict = old['conversation_key'] != key or (attachment_marker is not None and old['text'] != attachment_marker)
                self._audit(db, now, 'send_rejected' if conflict else 'duplicate', key, request_id,
                            code='request_id_conflict' if conflict else 'existing_request')
                return dict(old), False, 'request_id_conflict' if conflict else None, 0
            # Recheck under the admission lock so disabling and sending cannot race.
            enabled = db.execute("SELECT enabled FROM send_settings WHERE channel='whatsapp'").fetchone()
            if not enabled or enabled[0] != 1:
                self._audit(db, now, 'send_rejected', key, request_id, code='disabled')
                return None, False, 'disabled', 0
            retries = []
            for query, args, limit, window in (
                ('SELECT created_at FROM outbox WHERE conversation_key=? AND created_at>? ORDER BY created_at',
                 (key, now - 60), 5, 60),
                ('SELECT created_at FROM outbox WHERE created_at>? ORDER BY created_at', (now - 3600,), 60, 3600),
            ):
                times = [r[0] for r in db.execute(query, args)]
                if len(times) >= limit:
                    retries.append(max(1, math.ceil(times[len(times) - limit] + window - now)))
            if retries:
                retry = max(retries)
                self._audit(db, now, 'send_rejected', key, request_id, code='rate_limited', retry_after=retry)
                return None, False, 'rate_limited', retry
            idem = 'ops-' + sha(request_id + key)[:40]
            cursor = db.execute('INSERT INTO outbox(request_id,idempotency_key,conversation_key,channel,chat_ref,'
                'text,text_sha,state,draft_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT DO NOTHING',
                (request_id, idem, key, 'whatsapp', sha(chat)[:16], text, sha(text), 'sending', draft_id, now, now))
            if cursor.rowcount == 0:
                old = db.execute('SELECT * FROM outbox WHERE request_id=? OR idempotency_key=?',
                                 (request_id, idem)).fetchone()
                conflict = attachment_marker is not None and old['text'] != attachment_marker
                self._audit(db, now, 'send_rejected' if conflict else 'duplicate', key, request_id, code='request_id_conflict' if conflict else 'unique_conflict')
                return dict(old), False, 'request_id_conflict' if conflict else None, 0
            self._audit(db, now, 'send_attempt', key, request_id, actor='owner_explicit', chat_ref=sha(chat)[:16])
            row = dict(db.execute('SELECT * FROM outbox WHERE id=?', (cursor.lastrowid,)).fetchone())
        return row, True, None, 0

    def finish(self, row, state, code, now):
        with self._write() as db:
            db.execute("UPDATE outbox SET state=?,error_code=?,updated_at=? WHERE id=? AND state='sending'",
                       (state, code, now, row['id']))
            self._audit(db, now, 'send_result', row['conversation_key'], row['request_id'], state=state, code=code)
            return dict(db.execute('SELECT * FROM outbox WHERE id=?', (row['id'],)).fetchone())

    def recent(self, key=None):
        if not self.path.exists():
            return []
        with read_database(self.path) as db:
            db.row_factory = sqlite3.Row
            query = 'SELECT * FROM outbox' + (' WHERE conversation_key=?' if key is not None else '')
            return [dict(r) for r in db.execute(query + ' ORDER BY created_at DESC,id DESC LIMIT ?',
                                               (key, 30) if key is not None else (50,))]
