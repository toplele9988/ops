"""Read bridge's archived bytes without modifying its database or media directory."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat

from .store import HEX, MAX_BYTES, MIME, MediaError, identity

MEDIA_ROOT = Path(os.environ.get('IMGW_WA_BRIDGE_MEDIA', '/var/lib/imgw/whatsapp/media'))


class BridgeArchive:
    def __init__(self, db, root=MEDIA_ROOT):
        self.db, self.root = Path(db).absolute(), Path(root).absolute()

    @contextmanager
    def connect(self):
        try:
            db = sqlite3.connect(self.db.as_uri() + '?mode=ro', uri=True, timeout=10)
        except sqlite3.Error:
            raise MediaError('archive_source') from None
        try:
            db.execute('PRAGMA query_only=ON')
            yield db
        except sqlite3.Error:
            raise MediaError('archive_source') from None
        finally:
            db.close()

    def available(self, db):
        return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='media_jobs'").fetchone() is not None

    def _directory(self):
        if '..' in self.root.parts:
            raise MediaError('archive_path')
        directory = os.open(self.root.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            for part in self.root.parts[1:]:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
                os.close(directory)
                directory = child
            return directory
        except BaseException:
            os.close(directory)
            raise

    def load(self, session, message_id):
        identity(session, message_id)
        with self.connect() as db:
            if not self.available(db):
                return None
            row = db.execute("SELECT j.metadata_json FROM messages m JOIN media_jobs j ON j.message_key=m.message_key "
                             "WHERE m.session=? AND m.message_id=? AND m.has_media=1 AND j.status='archived' "
                             "ORDER BY j.updated DESC,m.rowid DESC LIMIT 1", (session, message_id)).fetchone()
        if row is None:
            return None
        raw = row[0]
        try:
            metadata = json.loads(raw) if isinstance(raw, str) and len(raw) <= 16384 else None
        except (ValueError, TypeError):
            metadata = None
        if not isinstance(metadata, dict):
            raise MediaError('archive_metadata')
        digest, size, mime = metadata.get('sha256'), metadata.get('bytes'), metadata.get('mimetype')
        if not isinstance(digest, str) or not HEX.fullmatch(digest) or type(size) is not int or not 0 < size <= MAX_BYTES:
            raise MediaError('archive_metadata')
        if not isinstance(mime, str) or mime not in MIME:
            raise MediaError('media_type')
        try:
            directory = self._directory()
            try:
                fd = os.open(digest, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
            finally:
                os.close(directory)
            with os.fdopen(fd, 'rb') as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise MediaError('archive_path')
                if info.st_size != size:
                    raise MediaError('archive_size')
                body = stream.read(size + 1)
        except FileNotFoundError:
            raise MediaError('archive_missing') from None
        except OSError:
            raise MediaError('archive_path') from None
        if len(body) != size:
            raise MediaError('archive_size')
        if hashlib.sha256(body).hexdigest() != digest:
            raise MediaError('archive_hash')
        return body, mime
