"""Read-only WTAPI groups, role scopes and best-effort names; no private paths exposed."""
from collections import Counter, defaultdict
import json
from pathlib import Path

from console.inbox.store import WECHAT_PATH, read_database
from console.media import tokens
from console.names import NamesResolver

from . import rich
from . import wechat_private
from .common import CachedAdapter, ROLES, conversation, file_stamp, placeholder, timestamp


def quote(name):
    return '"' + name.replace('"', '""') + '"'


def schema(db):
    return {name: {r[1] for r in db.execute('PRAGMA table_info(' + quote(name) + ')')}
            for name, in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")}


def names(db, tables, identities, labels, preferred=None):
    result = {}
    ordered = sorted(tables, key=lambda name: (name != preferred, name))
    for table in ordered:
        columns = tables[table]
        identity = next((c for c in identities if c in columns), None)
        label = next((c for c in labels if c in columns), None)
        if not identity or not label:
            continue
        for key, value in db.execute('SELECT ' + quote(identity) + ',' + quote(label) + ' FROM ' + quote(table)):
            if isinstance(key, str) and isinstance(value, str) and value.strip():
                try:
                    value.encode('utf8')
                except UnicodeError:
                    continue
                result.setdefault(key, value.strip()[:1000])
    return result


class WechatAdapter(CachedAdapter):
    name, label = 'wechat', '微信'

    def __init__(self, path=WECHAT_PATH, include_roles=('customer', 'supplier', 'unclassified'), key_path=tokens.KEY_PATH,
                 *, media_tokens=True, archive_path=None, contact_path=None, names_resolver=None, database=read_database):
        super().__init__()
        self.path, self.include_roles = Path(path), frozenset(include_roles)
        self.key_path = key_path
        # Metadata-only consumers must never create a media signing key on GET.
        self.media_tokens = media_tokens
        live = self.path == WECHAT_PATH
        self.archive_path = Path(archive_path) if archive_path is not None else (wechat_private.ARCHIVE_PATH if live else self.path.parent / 'disabled-archive.sqlite3')
        self.contact_path = Path(contact_path) if contact_path is not None else (wechat_private.CONTACT_PATH if live else self.path.parent / 'disabled-contacts.sqlite3')
        self.names = names_resolver or NamesResolver(self.path, contacts_path=self.contact_path)
        self.database = database
        if not self.include_roles <= ROLES:
            raise ValueError('群角色无效')
        self._all_raw = {}

    def _load(self):
        with self.database(self.path) as db:
            tables = schema(db)
            if ('payload' not in tables.get('messages', set()) or
                    not {'message_id', 'scope_id', 'business_role'} <= tables.get('message_scopes', set())):
                raise ValueError('微信数据结构不完整')
            labels = self.names.snapshot(state_db=db)
            scoped_app = 'app_id' in tables['messages'] and 'app_id' in tables['message_scopes']
            scopes = defaultdict(Counter)
            app_column = 'app_id' if scoped_app else "''"
            for app, mid, role in db.execute('SELECT DISTINCT ' + app_column + ',message_id,business_role FROM message_scopes'):
                if role in ROLES:
                    scopes[(app, str(mid))][role] += 1
            grouped, roles, seen = defaultdict(list), defaultdict(Counter), set()
            for app, raw in db.execute('SELECT ' + app_column + ',payload FROM messages ORDER BY rowid'):
                try:
                    payload = json.loads(raw)
                    group, mid, sender, own = (payload.get(k) for k in ('group_id', 'message_id', 'sender_id', 'own_wxid'))
                    if (not all(isinstance(v, str) and v for v in (group, sender, own)) or
                            not group.endswith('@chatroom') or len(group) > 1000 or mid is None):
                        continue
                    sent_at = timestamp(payload.get('created_at'))
                    text = payload.get('text') or ''
                    if sent_at is None or not isinstance(text, str):
                        continue
                    blocks = None
                    if payload.get('attachments'):
                        blocks = rich.blocks_for(payload, lambda ordinal, a=app, m=str(mid):
                            tokens.make(['wx', a, m, ordinal], self.key_path) if self.media_tokens else None)
                        text = rich.summary(blocks, text) if blocks else (text if text.strip() else placeholder(payload.get('attachments')))
                    if not text:
                        continue
                    text.encode('utf8')
                    identity = (app, group, str(mid))
                    if identity in seen:
                        continue
                    seen.add(identity)
                    message = dict(role='seller' if sender == own else 'buyer', text=text,
                        sent_at=sent_at, sender_label=labels.member_name(group, sender))
                    if blocks:
                        message['blocks'] = blocks
                    grouped[group].append(message)
                    roles[group].update(scopes[(app, str(mid))])
                except (ValueError, TypeError, AttributeError, UnicodeError, RecursionError):
                    continue
            private = wechat_private.conversations(db, archive_path=self.archive_path, contact_path=self.contact_path, names=labels,
                blocks=lambda p, a, m: rich.blocks_for(p, lambda ordinal:
                    tokens.make(['wx', a, m, ordinal], self.key_path) if self.media_tokens else None))
        result = dict(private)
        for group, messages in grouped.items():
            role = min(roles[group], key=lambda r: (-roles[group][r], r)) if roles[group] else 'unclassified'
            resolved = labels.group(group)
            conv = conversation(self.name, self.label, '微信', group, resolved.value, messages, role, is_group=True)
            conv.update(name_missing=not resolved.named, name_missing_reason=resolved.reason)
            result[conv['key']] = conv
        return result

    def _read(self, now):
        try:
            source = file_stamp(self.path)
            stamp = (source, file_stamp(self.archive_path), file_stamp(self.contact_path), self.names.fingerprint()) if source else None
            if stamp != self._stamp:
                self._all_raw = self._load() if stamp is not None else {}
                self._raw = {k: c for k, c in self._all_raw.items() if c['counterpart_role'] in self.include_roles}
                self._stamp, self.ready = stamp, stamp is not None
        except Exception:
            self._all_raw, self._raw, self.ready = {}, {}, False
            self._stamp = locals().get('stamp')
        return (self._stamp, self.ready), self.ready, self._raw

    def health(self, now):
        result = super().health(now)
        all_convs = self._all_raw.values()
        result.update(groups_by_role=dict(Counter(c['counterpart_role'] for c in all_convs)),
                      conversations=len(self._all_raw),
                      messages=sum(c['n_messages'] for c in all_convs),
                      messages_24h=sum(now - 86400 <= m['sent_at'] <= now for c in all_convs for m in c['messages']),
                      last_message_at=max((c['last_seen'] for c in all_convs), default=None))
        return result
