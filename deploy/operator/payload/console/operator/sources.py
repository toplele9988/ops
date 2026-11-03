"""Read-only, append-cursor inputs matching the console's actual source schemas.

Never opens a channel client, signs media URLs, migrates, or writes a source.
Row positions drive ingestion; (channel, conversation, stable ID) drives dedup.
"""
from contextlib import contextmanager
import hashlib
import json
import logging
import math
from pathlib import Path
import sqlite3

from console.inbox.model import make_key, parse_message, decode_key
from .store import dumps, sha


@contextmanager
def readonly(path):
    path = Path(path).resolve(strict=True)
    wal = Path(str(path) + '-wal')
    live = wal.exists() and wal.stat().st_size > 0
    if live and not Path(str(path) + '-shm').exists():
        raise ValueError('source_shm_unavailable')
    # Static databases must not create SHM on a read-only mount.
    db = sqlite3.connect(path.as_uri() + '?mode=ro' + ('' if live else '&immutable=1'), uri=True, timeout=3)
    try:
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        db.execute('PRAGMA temp_store=MEMORY')
        db.execute('BEGIN')
        yield db
    finally:
        db.close()


class Source:
    def __init__(self, name, path, kind, *, authorize=None):
        self.name, self.path, self.kind = name, Path(path), kind
        self.authorize = authorize

    def scan(self, after=0, limit=None):
        """Return all physical rows, including explicit skip/error records.

        The source transaction closes before any model invocation. No open source
        transaction is held while the operator writes its private ledger.
        """
        with readonly(self.path) as db:
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if self.kind == 'private' and 'wechat_private_messages' not in tables:
                return None  # Not installed yet: do not create or advance a cursor.
            table = 'inbound' if self.kind == 'alibaba' else 'wechat_private_messages' if self.kind == 'private' else 'messages'
            high = db.execute('SELECT coalesce(max(rowid),0) FROM ' + table).fetchone()[0]
            info = self.path.stat()
            generation = dumps([info.st_dev, info.st_ino])
            if after > high:
                after = 0  # Source replacement/truncation; durable dedup still applies.
            params = [after]
            suffix = ' WHERE rowid>? ORDER BY rowid'
            if limit is not None:
                suffix += ' LIMIT ?'
                params.append(limit)
            if self.kind == 'private':
                sql = ('SELECT p.rowid AS position,p.*,m.payload,m.created_at FROM wechat_private_messages p '
                       'JOIN messages m ON m.app_id=p.app_id AND m.message_id=p.message_id '
                       'WHERE p.rowid>? ORDER BY p.rowid')
                if limit is not None:
                    sql += ' LIMIT ?'
                rows = db.execute(sql, params).fetchall()
            else:
                rows = db.execute('SELECT rowid AS position,* FROM ' + table + suffix, params).fetchall()
            binding = None
            if self.kind == 'whatsapp':
                bindings = list(db.execute('SELECT session,account_id,owner,scope FROM binding'))
                if len(bindings) != 1:
                    raise ValueError('whatsapp_binding_required')
                binding = tuple(bindings[0])
                if self.authorize is None or self.authorize() != (binding[0], binding[2]):
                    raise ValueError('whatsapp_binding_mismatch')
                conflicts = {r[0] for r in db.execute('SELECT message_key FROM message_conflicts')} if 'message_conflicts' in tables else set()
            result = []
            for row in rows:
                try:
                    record = self._parse(dict(row), binding)
                    if record is not None:
                        decode_key(record['key'],'conversation')
                        record['text'].encode('utf8')
                    if self.kind == 'whatsapp' and row['message_key'] in conflicts:
                        raise ValueError('conflicting_message')
                    result.append(dict(position=row['position'], message=record, error=None))
                except (ValueError, KeyError, TypeError, UnicodeError, AttributeError, RecursionError, OverflowError):
                    result.append(dict(position=row['position'], message=None, error='source_row_invalid'))
            if self.kind == 'whatsapp' and self.authorize() != (binding[0], binding[2]):
                raise ValueError('whatsapp_authorization_changed')
        return dict(generation=generation, high=high, rows=result)

    def _parse(self, row, binding):
        if self.kind == 'alibaba':
            m = parse_message(row['payload'], row['id'])
            if m is None:
                raise ValueError('invalid_inbound')
            key = make_key('conversation', 'alibaba', m['account'], m['conversation_id'])
            stable = dumps(list(m['dedup']))
            return dict(key=key, channel='alibaba', stable_id=stable, name=m['buyer_name'],
                        role=m['role'], text=m['text'], sent_at=m['sent_at'])
        if self.kind in ('wechat', 'private'):
            p = json.loads(row['payload'])
            own, peer, sender = (p[k] for k in ('own_wxid', 'group_id', 'sender_id'))
            mid = p['message_id']
            if not all(isinstance(v, str) and v for v in (own, peer, sender)) or mid is None:
                raise ValueError('invalid_wechat')
            if self.kind == 'wechat':
                if not peer.endswith('@chatroom'):
                    return None  # Private messages have their own account-scoped stream.
                account, role = '微信', 'seller' if sender == own else 'buyer'
            else:
                if (p['group_id'] != row['peer_id'] or own != row['own_wxid']
                        or row['direction'] not in ('inbound', 'outbound')):
                    raise ValueError('invalid_private_identity')
                account = '微信私聊:' + hashlib.sha256((row['app_id']+'\0'+own).encode()).hexdigest()
                role = 'buyer' if row['direction'] == 'inbound' else 'seller'
            text = p.get('text') or ('[附件待核对]' if p.get('attachments') else '')
            at = p['created_at']
            if not isinstance(text, str) or type(at) not in (int, float) or not math.isfinite(at) or at <= 0:
                raise ValueError('invalid_wechat_body')
            return dict(key=make_key('conversation', 'wechat', account, peer), channel='wechat',
                        stable_id=dumps([row.get('app_id', ''), str(mid)]), name='微信会话',
                        role=role, text=text, sent_at=at)
        if self.kind == 'whatsapp':
            if tuple(row[k] for k in ('session', 'account_id', 'owner', 'scope')) != binding:
                raise ValueError('whatsapp_row_binding_mismatch')
            if row['direction'] not in ('inbound', 'outbound') or not row['message_id'] or not row['chat_id']:
                raise ValueError('invalid_whatsapp_identity')
            if not isinstance(row['content'], str):
                raise ValueError('invalid_whatsapp_body')
            at = row['sent_at'] if row['sent_at'] is not None else row['received']
            if type(at) not in (int,float) or not math.isfinite(at) or at <= 0:
                raise ValueError('invalid_whatsapp_time')
            # Same display account/key as channels/whatsapp.py; the stable ID also
            # retains the full account and session so masked suffixes cannot dedup.
            account = 'WhatsApp · ****' + binding[1].split('@')[0][-4:]
            return dict(key=make_key('conversation', 'whatsapp', account, row['chat_id']), channel='whatsapp',
                        stable_id=dumps([binding[0], binding[1], row['message_id']]), name='WhatsApp 会话',
                        role='buyer' if row['direction'] == 'inbound' else 'seller',
                        text=row['content'] or '[附件待核对]', sent_at=at,
                        time_basis='message' if row['sent_at'] is not None else 'received')
        raise ValueError('source_kind_invalid')


class LocalInbox:
    """Same conversation keys as the unified inbox, entirely local and read-only.

    Bounded UI history is never used as the ingestion cursor. detail_at selects
    the triggering message and earlier history even during historical catch-up.
    """
    def __init__(self, sources):
        self.sources = sources
        self._cache = {}

    def _histories(self):
        grouped = {}
        for source in self.sources:
            if not source.path.exists():
                continue
            wal = Path(str(source.path)+'-wal')
            stamp = tuple((p.stat().st_mtime_ns, p.stat().st_size, p.stat().st_ino) if p.exists() else None for p in (source.path, wal))
            cached = self._cache.get(source.name)
            if cached is None or cached[0] != stamp:
                try:
                    snapshot = source.scan()
                except Exception as error:
                    logging.getLogger(__name__).error('operator history unavailable source=%s type=%s', source.name, type(error).__name__)
                    continue
                cached = (stamp, [] if snapshot is None else [r['message'] for r in snapshot['rows'] if r['message']])
                self._cache[source.name] = cached
            for m in cached[1]:
                grouped.setdefault(m['key'], {})[m['stable_id']] = m
        return {k: sorted(ms.values(), key=lambda m: (m['sent_at'], m['stable_id'])) for k, ms in grouped.items()}

    def detail(self, key, now=None):
        messages = self._histories().get(key, [])
        return dict(key=key, buyer_name=messages[-1]['name'], messages=messages) if messages else None

    def detail_at(self, key, stable_id, now):
        conv = self.detail(key, now)
        if not conv:
            raise ValueError('trigger_message_unavailable')
        index = next((i for i, m in enumerate(conv['messages']) if m['stable_id'] == stable_id), None)
        if index is None:
            raise ValueError('trigger_message_unavailable')
        return dict(conv, messages=conv['messages'][:index+1])
