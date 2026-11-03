"""Allowlisted local directory metadata. Source databases are always read-only."""
import json
from pathlib import Path
import time

from console.channels.common import ROLES, timestamp
from console.channels.wechat import names, schema
from console.inbox.store import read_database

DISCOVERY_PATH = Path('/var/lib/imgw/wechat/contact-discovery-v1/index.sqlite3')
CONFIG_PATH = Path('/var/lib/imgw/wechat/api_config.json')
STATE_PATH = Path('/var/lib/imgw/wechat/api_state.sqlite3')
CACHE_PATH = Path('/var/lib/imgw/ops-app/wechat-directory.sqlite3')
KINDS = ('group', 'private', 'official')


def text(value, limit=1000):
    if not isinstance(value, str):
        return None
    value = value.strip()
    try:
        value.encode('utf8')
    except UnicodeError:
        return None
    if not value or len(value) > limit or any(ord(c) < 32 for c in value):
        return None
    return value


def identity_kind(identity):
    return 'group' if identity.endswith('@chatroom') else 'official' if identity.startswith('gh_') else 'private'


def read_config(path):
    # Only this projection leaves the JSON reader. Never retain/return the raw
    # object, include it in exceptions, log it, or copy it to another file.
    with Path(path).open(encoding='utf8') as handle:
        groups = json.load(handle).get('groups')
    if not isinstance(groups, dict):
        raise ValueError('群登记结构不可用')
    result = {}
    for identity, group in groups.items():
        if not text(identity) or not identity.endswith('@chatroom') or not isinstance(group, dict):
            continue
        role = group.get('business_role')
        result[identity] = dict(enabled=group.get('enabled') if type(group.get('enabled')) is bool else None,
            business_role=role if isinstance(role, str) and role in ROLES else 'unknown',
            auto_collection_source=text(group.get('auto_collection_source'), 100),
            group_name=text(group.get('group_name')))
    return result


def read_discovery(path, *, database=read_database):
    with database(path) as db:
        return [dict(id=identity, kind=kind, first_seen=timestamp(first), last_seen=timestamp(last))
                for identity, kind, first, last in db.execute(
                    'SELECT identity,kind,first_seen,last_seen FROM identities')
                if text(identity) and kind in KINDS and identity_kind(identity) == kind]


def _unique_identity(db, table, identity_columns):
    columns = [row for row in db.execute('PRAGMA table_info(' + table + ')') if row[5]]
    primary = {row[1] for row in columns}
    # SQLite permits NULLs in many non-integer primary keys. Such schemas still
    # need explicit deduplication and must not use this fast path.
    return bool(primary) and primary <= set(identity_columns) and all(row[3] for row in columns)


def read_state(path, now=None, *, database=read_database, metadata_fast=False):
    """Count archived rows without reading message bodies or configuration secrets.

    De-duplicate by app/group/message, matching the inbox identity boundary.
    Invalid timestamps still count as received records, but have no latest time.
    """
    now = time.time() if now is None else now
    with database(path) as db:
        tables = schema(db)
        columns = tables.get('messages', set())
        if not {'group_id', 'message_id', 'created_at'} <= columns:
            raise ValueError('消息统计结构不可用')
        app = 'app_id' if 'app_id' in columns else "''"
        result = {}
        query = ('SELECT group_id, MIN(at), MAX(at), COUNT(*), '
                 'SUM(CASE WHEN at BETWEEN ? AND ? THEN 1 ELSE 0 END) FROM (SELECT ' + app + ',group_id,message_id,'
                 "MAX(CASE WHEN typeof(created_at) IN ('integer','real') THEN created_at END) AS at "
                 'FROM messages GROUP BY ' + app + ',group_id,message_id) GROUP BY group_id')
        if metadata_fast and _unique_identity(db, 'messages', {'app_id', 'group_id', 'message_id'}):
            # The deployed primary key already enforces the dedup boundary.
            query = ("SELECT group_id,MIN(CASE WHEN typeof(created_at) IN ('integer','real') THEN created_at END),"
                     "MAX(CASE WHEN typeof(created_at) IN ('integer','real') THEN created_at END),COUNT(*),"
                     "SUM(CASE WHEN typeof(created_at) IN ('integer','real') AND created_at BETWEEN ? AND ? "
                     'THEN 1 ELSE 0 END) FROM messages GROUP BY group_id')
        for identity, first, last, count, recent in db.execute(query, (now - 7 * 86400, now)):
            if text(identity):
                result[identity] = dict(n_messages=count, recent_messages=recent,
                                       first_message_at=timestamp(first), last_message_at=timestamp(last))
        labels = names(db, tables, ('group_id', 'room_id', 'chatroom_id'),
                       ('group_name', 'room_name', 'chatroom_name'), 'onboarding_candidates')
        contacts = names(db, tables, ('sender_id', 'wxid', 'member_id', 'user_id', 'contact_id'),
                         ('nickname', 'nick_name', 'display_name', 'member_name', 'contact_name'))
        labels.update({key: value for key, value in contacts.items() if key not in labels})
        return dict(messages=dict(result), names=labels)


def read_role_metadata(path, *, database=read_database):
    """Group scope majorities for audits; never fetch message bodies into memory.

    This is source metadata, not proof of a valid inbox conversation. It uses
    the adapter's app/message boundary and its stable role tie breaking.
    """
    with database(path) as db:
        tables = schema(db)
        if not {'group_id', 'message_id'} <= tables.get('messages', set()) or not {
                'message_id', 'scope_id', 'business_role'} <= tables.get('message_scopes', set()):
            raise ValueError('群角色统计结构不可用')
        scoped = 'app_id' in tables['messages'] and 'app_id' in tables['message_scopes']
        app = 'app_id' if scoped else "''"
        roles = ','.join('?' for _ in ROLES)
        query = ("WITH m AS (SELECT DISTINCT " + app + " AS app,group_id,message_id FROM messages "
                 "WHERE group_id LIKE '%@chatroom'), s AS (SELECT DISTINCT " + app +
                 " AS app,message_id,business_role FROM message_scopes WHERE business_role IN (" + roles +
                 ")) SELECT m.group_id,s.business_role,COUNT(*) FROM m "
                 "LEFT JOIN s ON s.app=m.app AND s.message_id=m.message_id "
                 "GROUP BY m.group_id,s.business_role ORDER BY m.group_id,COUNT(*) DESC,s.business_role")
        identity = {'app_id', 'message_id'} if scoped else {'message_id'}
        if (_unique_identity(db, 'messages', identity | {'group_id'}) and
                _unique_identity(db, 'message_scopes', identity)):
            join = 's.message_id=m.message_id' + (' AND s.app_id=m.app_id' if scoped else '')
            query = ('SELECT m.group_id,s.business_role,COUNT(*) FROM messages m LEFT JOIN message_scopes s ON '
                     + join + ' AND s.business_role IN (' + roles + ") WHERE m.group_id LIKE '%@chatroom' "
                     'GROUP BY m.group_id,s.business_role ORDER BY m.group_id,COUNT(*) DESC,s.business_role')
        result, chosen = {}, set()
        for identity, role, _ in db.execute(query, tuple(sorted(ROLES))):
            # Unscoped rows do not cast votes when an explicit scope exists.
            result.setdefault(identity, dict(buyer_name=None, counterpart_role='unclassified'))
            if role is not None and identity not in chosen:
                result[identity]['counterpart_role'] = role
                chosen.add(identity)
        return result
