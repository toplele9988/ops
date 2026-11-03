"""Passive business private-chat intake. No send client or work queue calls."""
from copy import deepcopy
from dataclasses import asdict
import json
from pathlib import Path
import time

from console.channels.wechat_private import (ARCHIVE_PATH, CONTACT_PATH, broad_role, contact_role,
                                           digest, scope_key)

SCHEMA = '''
CREATE TABLE IF NOT EXISTS wechat_private_contacts(
 app_id TEXT NOT NULL, own_wxid TEXT NOT NULL, peer_id TEXT NOT NULL,
 business_role TEXT NOT NULL CHECK(business_role IN ('customer','supplier','forwarder','vendor','unknown')),
 display_name TEXT NOT NULL, role_basis TEXT NOT NULL, updated_at REAL NOT NULL,
 PRIMARY KEY(app_id,own_wxid,peer_id));
CREATE TABLE IF NOT EXISTS wechat_private_messages(
 app_id TEXT NOT NULL, message_id TEXT NOT NULL, own_wxid TEXT NOT NULL, peer_id TEXT NOT NULL,
 direction TEXT NOT NULL CHECK(direction IN ('inbound','outbound')), scope_id TEXT NOT NULL,
 callback_sha256 TEXT NOT NULL, created_at REAL NOT NULL,
 PRIMARY KEY(app_id,message_id));
CREATE TABLE IF NOT EXISTS wechat_private_receipts(
 account_sha256 TEXT NOT NULL, callback_sha256 TEXT NOT NULL, app_id TEXT NOT NULL,
 message_id TEXT NOT NULL, state TEXT NOT NULL, recorded_at REAL NOT NULL,
 PRIMARY KEY(account_sha256,callback_sha256));
CREATE INDEX IF NOT EXISTS wechat_private_peer ON wechat_private_messages(app_id,own_wxid,peer_id);
'''


def initialize(store):
    with store.lock, store.db:
        if not store.db.in_transaction:
            store.db.execute('BEGIN IMMEDIATE')
        # Execute statements individually: executescript would commit an outer transaction.
        for statement in SCHEMA.split(';'):
            if statement.strip():
                store.db.execute(statement)


def text(value):
    if isinstance(value, dict):
        value = value.get('string')
    return value if isinstance(value, str) and 0 < len(value) <= 256 and not any(
        c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value) else None


def identify(config, payload):
    if not isinstance(payload, dict) or payload.get('TypeName') != 'AddMsg':
        return None
    own = config.get('owner_control', {}).get('own_wxid')
    if not text(own) or payload.get('Appid') != config.get('app_id') or payload.get('Wxid') != own:
        return None
    data = payload.get('Data')
    if not isinstance(data, dict):
        return None
    origin, target = text(data.get('FromUserName')), text(data.get('ToUserName'))
    direction = 'outbound' if origin == own else 'inbound'
    peer = target if direction == 'outbound' else origin
    internal = {own, config.get('owner_control', {}).get('owner_wxid'),
                *config.get('owner_control', {}).get('group_member_wxids', [])}
    if (not peer or peer in internal or '@' in peer or peer.startswith('gh_') or
            peer.lower() in ('filehelper', 'fmessage', 'notification', 'system') or
            direction == 'inbound' and target != own):
        return None
    return own, peer, direction


def normalize(config, payload, now):
    identity = identify(config, payload)
    if identity is None:
        return None
    from wtapi_events import GroupMessage, normalize_private_message, normalize_owner_media_message
    from outbound_callback import canonical_id
    own, peer, direction = identity
    wire = deepcopy(payload)
    data = wire['Data']
    mid = canonical_id(data.get('NewMsgId'))
    created = data.get('CreateTime')
    if (mid is None or mid == '0' or type(created) is not int or not 0 < created <= now + 60
            or type(data.get('MsgType')) is not int):
        return None
    # Reuse the inbound parser for provider-asserted outbound content, without
    # altering the retained envelope or pretending it was received from the peer.
    data.update(FromUserName={'string': peer}, ToUserName={'string': own}, NewMsgId=mid)
    private = normalize_private_message(wire) or normalize_owner_media_message(wire)
    if private is None:
        if data['MsgType'] in (1, 3, 34, 43, 49, 62):
            # Malformed known media is retained as an observable placeholder.
            label = '[微信消息，待解析]'
        else:
            label = '[微信消息，类型 %d，待解析]' % data['MsgType']
        return GroupMessage(config['app_id'], own, peer, own if direction == 'outbound' else peer,
                            mid, label, created, ()), direction
    return GroupMessage(private.app_id, own, peer, own if direction == 'outbound' else peer,
                        mid, private.text, created, (), private.attachments), direction


def capture(store, config, payload, now, *, archive_path=ARCHIVE_PATH, contact_path=CONTACT_PATH):
    """None means existing routing. Otherwise the caller terminates private business routing."""
    identity = identify(config, payload)
    if identity is None:
        return None
    normalized = normalize(config, payload, now)
    if normalized is None:
        return 'invalid'
    message, direction = normalized
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    if len(raw.encode('utf8')) > 4 * 1024 * 1024:
        raise ValueError('private_callback_too_large')
    callback = digest(raw)
    account = digest(message.app_id + '\0' + message.own_wxid)
    role, name, basis = contact_role(message.app_id, message.own_wxid, message.group_id,
                                   archive_path=archive_path, contact_path=contact_path)
    encoded = json.dumps(asdict(message), ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    scope = scope_key(message.app_id, message.own_wxid, message.group_id)
    with store.transaction() as db:
        if not db.in_transaction:
            db.execute('BEGIN IMMEDIATE')
        existing = db.execute('SELECT state FROM wechat_private_receipts WHERE account_sha256=? AND callback_sha256=?',
                              (account, callback)).fetchone()
        if existing:
            return 'duplicate'
        prior = db.execute('SELECT payload FROM messages WHERE app_id=? AND message_id=?',
                           (message.app_id, message.message_id)).fetchone()
        state = 'inserted'
        if prior:
            binding = db.execute('SELECT * FROM wechat_private_messages WHERE app_id=? AND message_id=?',
                                 (message.app_id, message.message_id)).fetchone()
            previous = json.loads(prior['payload'])
            pending = previous.get('attachments') or []
            upgrade = (binding is not None and binding['direction'] == direction and binding['peer_id'] == message.group_id
                       and binding['own_wxid'] == message.own_wxid and len(pending) == 1
                       and pending[0].get('unsupported_reason') == 'file_pending'
                       and len(message.attachments) == 1 and message.attachments[0].get('appmsg_type') == 6
                       and previous['created_at'] == message.created_at)
            if upgrade and db.execute("SELECT 1 FROM sqlite_master WHERE name='company_media'").fetchone():
                upgrade = not db.execute('''SELECT 1 FROM company_media WHERE app_id=? AND message_id=?
                    AND (original IS NOT NULL OR sha256<>'' OR archive_sha256<>'' OR archive_status='ready')''',
                    (message.app_id, message.message_id)).fetchone()
            if upgrade:
                # A pending upload is deferred by media_auth, so it has no original to overwrite.
                db.execute('UPDATE messages SET payload=?,updated_at=? WHERE app_id=? AND message_id=?',
                           (encoded, now, message.app_id, message.message_id))
                db.execute('UPDATE wechat_private_messages SET callback_sha256=? WHERE app_id=? AND message_id=?',
                           (callback,message.app_id,message.message_id))
                # Existing group completion machinery also reclaims this exact source.
                encode = lambda v: json.dumps(v, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
                db.execute('''INSERT OR IGNORE INTO media_completion_pending(
                    app_id,message_id,own_wxid,group_id,sender_id,ordinal,previous_descriptor_sha256,
                    current_descriptor_sha256,source_payload_sha256,status,created_at,updated_at)
                    VALUES(?,?,?,?,?,0,?,?,?,'pending',?,?)''',
                    (message.app_id,message.message_id,message.own_wxid,message.group_id,message.sender_id,
                     digest(encode(pending[0])),digest(encode(message.attachments[0])),digest(encoded),now,now))
                state = 'upgraded'
            else:
                state = 'duplicate' if prior['payload'] == encoded and binding is not None else 'conflict'
        else:
            # This terminal status is outside the reply/task admission states.
            db.execute('INSERT INTO messages(app_id,message_id,group_id,sender_id,created_at,payload,status,updated_at) '
                       "VALUES(?,?,?,?,?,?,'collected_private',?)", (message.app_id, message.message_id,
                       message.group_id, message.sender_id, message.created_at, encoded, now))
            db.execute('INSERT INTO wechat_private_messages VALUES(?,?,?,?,?,?,?,?)',
                       (message.app_id, message.message_id, message.own_wxid, message.group_id, direction, scope, callback, now))
            db.execute('INSERT INTO message_scopes VALUES(?,?,?,?,?,?)',
                       (message.app_id, message.message_id, config['assistant_profile_id'], scope, message.own_wxid, broad_role(role)))
        if state != 'conflict':
            db.execute('INSERT INTO wechat_private_contacts VALUES(?,?,?,?,?,?,?) '
                       'ON CONFLICT(app_id,own_wxid,peer_id) DO UPDATE SET business_role=excluded.business_role, '
                       'display_name=excluded.display_name,role_basis=excluded.role_basis,updated_at=excluded.updated_at',
                       (message.app_id, message.own_wxid, message.group_id, role, name, basis, now))
        db.execute('INSERT INTO wechat_private_receipts VALUES(?,?,?,?,?,?)',
                   (account, callback, message.app_id, message.message_id, state, now))
    return state


def media_auth(db, row, payload, config, now):
    """The company media worker calls this before every claim/download/save."""
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='wechat_private_messages'").fetchone():
        return None
    p = db.execute('SELECT * FROM wechat_private_messages WHERE app_id=? AND message_id=?',
                   (row['app_id'], row['message_id'])).fetchone()
    if p is None:
        return None
    own = config.get('owner_control', {}).get('own_wxid')
    if (row.get('app_id') != config.get('app_id') or row.get('own_wxid') != own or p['own_wxid'] != own
            or row.get('profile_id') != config.get('assistant_profile_id') or row.get('scope_id') != p['scope_id']
            or row.get('scope_id') != scope_key(row['app_id'], own, p['peer_id'])
            or row.get('group_id') != p['peer_id'] or row.get('status') != 'collected_private'
            or row.get('sender_id') != (own if p['direction'] == 'outbound' else p['peer_id'])
            or not 0 < row.get('created_at', 0) <= now + 60
            or any(payload.get(k) != row.get(k) for k in ('app_id','message_id','group_id','sender_id','created_at','own_wxid'))):
        return None
    from company_materials import _workspace
    return ('wechat-private-v1', row['app_id'], own, p['peer_id'], p['direction'], p['scope_id'],
            str(_workspace(config)), config.get('token_file'), config.get('base_url'))


def replay(store, config, *, limit=200, now=None, **sources):
    """One bounded batch. Re-runs skip receipts; original archives stay immutable."""
    initialize(store)
    counts = dict(scanned=0, inserted=0, upgraded=0, duplicate=0, conflict=0, invalid=0, skipped=0)
    account = digest(config['app_id'] + '\0' + config['owner_control']['own_wxid'])
    with store.lock:
        tables = {r[0] for r in store.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        rows = []
        for table in ('received_callback_archive', 'outbound_callback_archive'):
            if table in tables:
                rows.extend(store.db.execute('SELECT a.payload_json FROM ' + table + ''' a
                    WHERE a.account_sha256=? AND a.channel_kind='private' AND NOT EXISTS(
                    SELECT 1 FROM wechat_private_receipts r WHERE r.account_sha256=a.account_sha256
                    AND r.callback_sha256=a.callback_sha256) ORDER BY a.received_at,a.rowid LIMIT ?''', (account, limit)).fetchall())
    for row in rows[:limit]:
        payload = json.loads(row['payload_json'])
        state = capture(store, config, payload, time.time() if now is None else now, **sources)
        if state is None or state == 'invalid':
            # A receipt makes excluded/malformed envelopes observable without stalling subsequent batches.
            raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
            with store.transaction() as db:
                db.execute('INSERT OR IGNORE INTO wechat_private_receipts VALUES(?,?,?,?,?,?)',
                           (account, digest(raw), config['app_id'], '', state or 'skipped', time.time() if now is None else now))
        counts[state or 'skipped'] += 1
        counts['scanned'] += 1
    return counts
