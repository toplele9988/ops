"""Alibaba projection over the existing shared, mtime/WAL-aware inbound cache."""
from pathlib import Path
import sqlite3

from console.inbox.store import INBOUND_PATH, inbound_cache, read_database
from console.media import tokens
from console.media import alibaba as media
from . import alibaba_rich
from .common import CachedAdapter, file_stamp


class AlibabaAdapter(CachedAdapter):
    name, label = 'alibaba', '阿里国际站'

    def __init__(self, path=INBOUND_PATH, key_path=tokens.KEY_PATH, *, media_tokens=True,
                 attachments_db_path=media.DB_PATH):
        super().__init__()
        self.cache = inbound_cache(path)
        self._source = None
        self.key_path, self.media_tokens = key_path, media_tokens
        self.attachments_db_path = Path(attachments_db_path)
        self._has_rich, self._ready_media, self._media_stamp = False, set(), None

    def _read_ready_media(self):
        path = self.attachments_db_path.absolute()
        if '..' in path.parts or any(p.is_symlink() for p in (path, *path.parents)):
            return set()
        try:
            with read_database(path) as db:
                rows = db.execute('SELECT a.inbound_id,a.attachment_id,a.sha256,a.size,a.mime,b.size '
                                  'FROM attachments a JOIN blobs b ON b.sha256=a.sha256 '
                                  'WHERE a.inbound_id IS NOT NULL')
                grouped = {}
                for inbound, identity, digest, size, mime, blob_size in rows:
                    if (not all(isinstance(v, str) and alibaba_rich.IDENTIFIER.fullmatch(v)
                                for v in (inbound, identity)) or not isinstance(digest, str)
                            or not media.SHA256.fullmatch(digest) or type(size) is not int
                            or not 0 < size <= media.MAX_BYTES or size != blob_size):
                        continue
                    grouped.setdefault((inbound, identity), set()).add((digest, size, mime))
                return {key for key, variants in grouped.items() if len(variants) == 1}
        except (OSError, sqlite3.Error, ValueError, TypeError):
            return set()

    def _message(self, message):
        result = dict(message, sender_label=None)
        payload, inbound_id = result.pop('_rich', None), result.pop('_inbound_id', None)
        if payload:
            blocks = alibaba_rich.blocks_for(payload, result['text'], lambda identity:
                tokens.make(['ali', inbound_id, identity], self.key_path)
                if self.media_tokens and (inbound_id, identity) in self._ready_media else None)
            if blocks:
                result['blocks'] = blocks
                result['text'] = alibaba_rich.summary(blocks, result['text'])
        return result

    def _read(self, now):
        try:
            snapshot = self.cache.read()  # Preserve existing invalid-database 503 semantics.
        except sqlite3.OperationalError:
            with read_database(self.cache.path) as db:
                columns = {row[1] for row in db.execute('PRAGMA table_info(inbound)')}
            if not {'id', 'payload'} <= columns:
                self._source, self._raw = None, {}
                return 'missing-schema', False, {}
            raise
        if not snapshot['ready'] and self._source is not None and not self._source['ready']:
            return self._stamp, False, self._raw
        changed = snapshot is not self._source
        if changed:
            self._has_rich = any(m.get('_rich') for c in snapshot['conversations'].values() for m in c['messages'])
        try:
            stamp = file_stamp(self.attachments_db_path) if self._has_rich and self.media_tokens else None
        except OSError:
            stamp = None
        if changed or stamp != self._media_stamp:
            self._source = snapshot
            self._media_stamp = stamp
            self._ready_media = self._read_ready_media() if stamp is not None else set()
            self._stamp = object()
            self._raw = {key: dict(conv, channel_label=self.label, counterpart_role='customer', is_group=False,
                                   messages=tuple(self._message(m) for m in conv['messages']))
                         for key, conv in snapshot['conversations'].items()}
        return self._stamp, snapshot['ready'], self._raw

    def health(self, now):
        try:
            return super().health(now)
        except Exception:
            self.ready = False
            return dict(channel=self.name, label=self.label, ready=False, conversations=0,
                        waiting=0, messages_24h=0, last_message_at=None)
