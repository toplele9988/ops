"""Private local draft storage and process-wide admission control. No sends."""
from collections import deque
from contextlib import closing
import json
import math
import os
from pathlib import Path
import sqlite3
import threading

from .store import read_database

_WRITE_LOCK = threading.Lock()
_SCHEMA = """CREATE TABLE IF NOT EXISTS drafts (
    id INTEGER PRIMARY KEY, conversation_key TEXT, created_at REAL, model TEXT,
    language TEXT, text TEXT, needs_json TEXT,
    status TEXT DEFAULT 'new' CHECK(status IN ('new','used','discarded')))"""


def _public(row, status=False):
    result = dict(id=row[0], text=row[5], language=row[4], needs_confirmation=json.loads(row[6]),
                  model=row[3], created_at=row[2], sources=json.loads(row[8] or '[]'))
    if status:
        result['status'] = row[7]
    return result


def read_drafts(path):
    path = Path(path)
    if not path.exists():
        return {}, 0
    latest, count = {}, 0
    with read_database(path) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='drafts'").fetchone():
            return {}, 0
        columns = {row[1] for row in db.execute('PRAGMA table_info(drafts)')}
        sources = 'sources_json' if 'sources_json' in columns else "'[]'"
        for row in db.execute('SELECT id, conversation_key, created_at, model, language, text, needs_json, status, '
                              + sources + ' FROM drafts ORDER BY created_at DESC, id DESC'):
            count += row[7] == 'new'
            if row[7] != 'discarded' and row[1] not in latest:
                latest[row[1]] = _public(row, status=True)
    return latest, count


def ensure_schema(db):
    db.execute(_SCHEMA)
    if 'sources_json' not in {row[1] for row in db.execute('PRAGMA table_info(drafts)')}:
        db.execute("ALTER TABLE drafts ADD COLUMN sources_json TEXT DEFAULT '[]'")
    columns = {row[1] for row in db.execute('PRAGMA table_info(drafts)')}
    if 'request_id' not in columns:
        db.execute('ALTER TABLE drafts ADD COLUMN request_id TEXT')
    db.execute('CREATE UNIQUE INDEX IF NOT EXISTS drafts_request ON drafts(request_id) WHERE request_id IS NOT NULL')


def save_draft(path, key, draft, now, request_id=None):
    path = Path(path)
    # umask is process-wide: serialize and keep its window as short as possible.
    with _WRITE_LOCK:
        previous = os.umask(0o077)
        try:
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            try:
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                pass
            else:
                os.close(fd)
            path.chmod(0o600)
        finally:
            os.umask(previous)
        with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=rw', uri=True)) as db:
            db.execute('PRAGMA journal_mode=WAL')
            with db:
                db.execute('BEGIN IMMEDIATE')
                ensure_schema(db)
                if request_id:
                    old = db.execute('SELECT id,conversation_key,created_at,model,language,text,needs_json,status,sources_json '
                                     'FROM drafts WHERE request_id=?', (request_id,)).fetchone()
                    if old:
                        if old[1] != key:
                            from .model import BadRequest
                            raise BadRequest('起草请求已用于其他会话')
                        return _public(old, status=True)
                cursor = db.execute('INSERT INTO drafts '
                    '(conversation_key, created_at, model, language, text, needs_json, sources_json) VALUES (?, ?, ?, ?, ?, ?, ?)',
                    (key, now, draft['model'], draft['language'], draft['text'],
                     json.dumps(draft['needs_confirmation'], ensure_ascii=False),
                     json.dumps(draft.get('sources', []), ensure_ascii=False)))
                if request_id:
                    db.execute('UPDATE drafts SET request_id=? WHERE id=?', (request_id, cursor.lastrowid))
                result = dict(draft, sources=draft.get('sources', []), id=cursor.lastrowid, created_at=now)
    return result


def set_status(path, draft_id, status):
    if not Path(path).exists():
        return None
    with _WRITE_LOCK, closing(sqlite3.connect(Path(path).resolve().as_uri() + '?mode=rw', uri=True)) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='drafts'").fetchone():
            return None
        with db:
            changed = db.execute('UPDATE drafts SET status=? WHERE id=?', (status, draft_id)).rowcount
    return dict(id=draft_id, status=status) if changed else None


class DraftLimiter:
    """Atomic reservations count attempts, including failures; never automatic retries."""
    def __init__(self):
        self.lock = threading.Lock()
        self.calls = deque()
        self.latest = {}
        self.active = set()

    def reserve(self, key, now):
        with self.lock:
            while self.calls and self.calls[0] <= now - 3600:
                self.calls.popleft()
            self.latest = {k: t for k, t in self.latest.items() if now - t < 60}
            if key in self.active:
                return 60
            if key in self.latest:
                return max(1, math.ceil(60 - (now - self.latest[key])))
            if len(self.calls) >= 30:
                return max(1, math.ceil(3600 - (now - self.calls[0])))
            self.calls.append(now)
            self.latest[key] = now
            self.active.add(key)
            return 0

    def finish(self, key):
        with self.lock:
            self.active.discard(key)


LIMITER = DraftLimiter()


def find_request(path, key, request_id):
    if not request_id or not Path(path).exists():
        return None
    with read_database(path) as db:
        if 'request_id' not in {r[1] for r in db.execute('PRAGMA table_info(drafts)')}:
            return None
        row = db.execute('SELECT id,conversation_key,created_at,model,language,text,needs_json,status,sources_json '
                         'FROM drafts WHERE request_id=?', (request_id,)).fetchone()
        if row and row[1] != key:
            from .model import BadRequest
            raise BadRequest('起草请求已用于其他会话')
        return _public(row, status=True) if row else None


def initialize(path):
    """Explicit additive migration, without generating a draft or contacting AI."""
    path = Path(path)
    with _WRITE_LOCK:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            os.fchmod(fd, 0o600)
        finally:
            os.close(fd)
        with closing(sqlite3.connect(path)) as db, db:
            db.execute('BEGIN IMMEDIATE')
            ensure_schema(db)
