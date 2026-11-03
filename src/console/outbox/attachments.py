"""Private, bounded attachment candidates; claiming is durable and one way."""
import base64
from contextlib import closing
import hashlib
import os
from pathlib import Path
import secrets
import sqlite3
import stat
import threading
import time

MAX_SIZE = 20 * 1024 * 1024
MIMES = frozenset(('image/jpeg', 'image/png', 'image/webp', 'video/mp4', 'application/pdf',
    'text/plain', 'application/zip',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    'application/vnd.openxmlformats-officedocument.presentationml.presentation'))
_LOCK = threading.RLock()


class Candidates:
    def __init__(self, path):
        self.path = Path(path)

    def _connect(self):
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise OSError('附件候选库不可用')
            os.fchmod(fd, 0o600)
        finally:
            os.close(fd)
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('PRAGMA synchronous=FULL')
        db.execute('CREATE TABLE IF NOT EXISTS candidates (id INTEGER PRIMARY KEY, request_id TEXT UNIQUE NOT NULL, '
                   'conversation_key TEXT NOT NULL, filename TEXT NOT NULL, mimetype TEXT NOT NULL, '
                   'data BLOB NOT NULL, digest TEXT NOT NULL, marker TEXT NOT NULL, created_at REAL NOT NULL, send_request_id TEXT)')
        return db

    def create(self, key, request_id, filename, mimetype, data):
        if not isinstance(filename, str) or not 1 <= len(filename) < 250 or any(
                c in '/\\' or ord(c) < 32 or ord(c) == 127 for c in filename) or filename in ('.', '..'):
            raise ValueError('文件名无效，请重新选择文件')
        if mimetype not in MIMES:
            raise ValueError('文件类型不支持，请选择常见图片、MP4 视频或文档')
        if not isinstance(data, str) or len(data) > 4 * ((MAX_SIZE + 2) // 3):
            raise ValueError('文件超过 20 MiB，请选择较小的文件')
        try:
            binary = base64.b64decode(data, validate=True)
        except (ValueError, TypeError):
            raise ValueError('文件内容无效，请重新选择文件') from None
        if not 0 < len(binary) <= MAX_SIZE:
            raise ValueError('文件为空或超过 20 MiB，请重新选择文件')
        digest = hashlib.sha256(binary).hexdigest()
        with _LOCK, closing(self._connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM candidates WHERE request_id=?', (request_id,)).fetchone()
            if row:
                if (row['conversation_key'], row['filename'], row['mimetype'], row['digest']) != (key, filename, mimetype, digest):
                    raise ValueError('候选标识已用于其他文件，请重新选择文件')
            else:
                cur = db.execute('INSERT INTO candidates(request_id,conversation_key,filename,mimetype,data,digest,marker,created_at) '
                                 'VALUES(?,?,?,?,?,?,?,?)', (request_id, key, filename, mimetype, binary, digest, secrets.token_hex(32), time.time()))
                row = db.execute('SELECT * FROM candidates WHERE id=?', (cur.lastrowid,)).fetchone()
        return dict(id=row['id'], filename=row['filename'], mimetype=row['mimetype'], size=len(row['data']))

    def candidate_metadata(self, candidate_id, key):
        if not self.path.exists():
            return None
        with _LOCK, closing(self._connect()) as db:
            row = db.execute('SELECT send_request_id,marker FROM candidates WHERE id=? AND conversation_key=?',
                             (candidate_id, key)).fetchone()
            return dict(row) if row else None

    def claim(self, candidate_id, key, request_id, *, replace_request=None):
        if not self.path.exists():
            return None
        with _LOCK, closing(self._connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM candidates WHERE id=? AND conversation_key=?', (candidate_id, key)).fetchone()
            if not row or (row['send_request_id'] and row['send_request_id'] not in (request_id, replace_request)):
                return None
            if row['send_request_id'] != request_id:
                db.execute('UPDATE candidates SET send_request_id=? WHERE id=?', (request_id, candidate_id))
            return dict(row)
