# Review snapshot only: identity and content generalized; not a deployed service.
from __future__ import annotations
import math
import hashlib
import json
import os
if os.environ.get('EXAMPLE_POLICY_AUDIT_ONLY') != '1':
    raise RuntimeError('Reference snapshot is for audit only.')
from pathlib import Path
import stat
from urllib.parse import urlsplit
PUBLIC_AUTH_NAME = 'r34-community-public.json'
PUBLIC_FIELDS = frozenset(('enabled', 'active_after', 'app_id', 'assistant_profile_id', 'group_id', 'own_wxid', 'memory_scope_id'))
PUBLIC_POLICY = {'public_group_count': 1, 'private_targets_unchanged': True, 'group_tasks': False, 'daily_limit': 5, 'ordinary_cooldown_seconds': 600, 'direct_cooldown_seconds': 30, 'digest_daily_limit': 24, 'digest_interval_seconds': 1800, 'buffer_min_seconds': 8, 'buffer_max_seconds': 18}
_UNKNOWN_PUBLIC_FDS = set()

class PublicReplyError(ValueError):
    pass

def _need(value, code='public_reply_invalid'):
    if not value:
        raise PublicReplyError(code)

def _encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')

def _sha(value):
    return hashlib.sha256(value if isinstance(value, bytes) else _encoded(value)).hexdigest()

def _ident(value):
    return type(value) is str and 0 < len(value) <= 256 and (not any((c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value)))

def _public_shape(config):
    setting = config.get('community_public_reply')
    if setting is None:
        return None
    _need(type(setting) is dict and set(setting) == PUBLIC_FIELDS and (type(setting['enabled']) is bool) and _stamp(setting['active_after']) and all((_ident(setting[k]) for k in PUBLIC_FIELDS - {'enabled', 'active_after'})), 'public_selection_invalid')
    return dict(setting)

def public_selection(config):
    setting = _public_shape(config)
    if setting is None or not enabled(config) or (not setting['enabled']):
        return None
    try:
        origin = urlsplit(config.get('base_url', ''))
        valid_origin = type(config.get('base_url')) is str and (not any((c.isspace() or ord(c) < 32 or ord(c) == 127 for c in config['base_url']))) and (origin.scheme == 'https') and (origin.hostname == os.environ.get('EXAMPLE_API_HOST', 'api.fixture.invalid')) and (origin.port in (None, 443)) and (not origin.username) and (not origin.password) and (origin.path in ('', '/')) and (not origin.query) and (not origin.fragment)
    except Exception:
        valid_origin = False
    owner = config.get('owner_control', {})
    if not valid_origin or type(owner) is not dict or setting['app_id'] != config.get('app_id') or (setting['assistant_profile_id'] != config.get('assistant_profile_id')) or (setting['own_wxid'] != owner.get('own_wxid')) or (not setting['group_id'].endswith('@chatroom')):
        return None
    groups = config.get('groups', {})
    group = groups.get(setting['group_id']) if type(groups) is dict else None
    if not (type(group) is dict and group.get('enabled') is True and (group.get('reply_mode') == 'participate') and (group.get('business_role') == 'learning') and (group.get('group_name') == os.environ.get('EXAMPLE_GROUP_NAME', '示例业务群')) and (group.get('own_wxid') == setting['own_wxid']) and (group.get('memory_scope_id') == setting['memory_scope_id']) and (group.get('daily_limit') == 5) and (type(group.get('daily_limit')) is int) and (group.get('group_cooldown_seconds') == 600) and (group.get('sender_cooldown_seconds') == 600)):
        return None
    ai = group.get('ai', {})
    if not (type(ai) is dict and ai.get('enabled') is True and (ai.get('mode') == 'support') and (ai.get('participation_mode') == 'community') and (ai.get('digest_enabled') is True) and (ai.get('digest_daily_limit') == 24) and (type(ai.get('digest_daily_limit')) is int) and (ai.get('digest_interval_seconds') == 1800)):
        return None
    if any((key in group for key in ('lifecycle_closed_at', 'onboarding_pending_activation', 'management_pending_activation'))):
        return None
    return setting

def public_authorization_record(config, confirmed_utc=None):
    selected = public_selection(config)
    _need(selected is not None, 'public_authorization_invalid')
    if confirmed_utc is None:
        confirmed_utc = selected['active_after']
    _need(_stamp(confirmed_utc) and confirmed_utc == selected['active_after'], 'public_authorization_invalid')
    projection = {'selection': selected, 'group': config['groups'][selected['group_id']], 'policy': PUBLIC_POLICY}
    return {'schema_version': 1, 'authorization_kind': 'desktop_user_confirmation', 'confirmed_instruction': 'only_example_group_limited_public_suggestions', 'authority_sha256': _sha(projection), 'policy': dict(PUBLIC_POLICY), 'confirmed_utc': confirmed_utc, 'active_after': confirmed_utc}

def _pin(st):
    return (st.st_dev, st.st_ino, st.st_mode, st.st_uid, st.st_nlink, st.st_size, st.st_mtime_ns, st.st_ctime_ns)

def _private_bytes(path, maximum=16384):
    _need(not _UNKNOWN_PUBLIC_FDS, 'public_file_quarantined')
    path = Path(path)
    _need(path.is_absolute() and '..' not in path.parts, 'public_file_unsafe')
    (fds, chain) = ([], [])
    fd = None
    try:
        fds.append(os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW))
        for part in path.parent.parts[1:]:
            parent = fds[-1]
            before = os.stat(part, dir_fd=parent, follow_symlinks=False)
            _need(stat.S_ISDIR(before.st_mode), 'public_file_unsafe')
            current = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            fds.append(current)
            _need(_pin(os.fstat(current)) == _pin(before), 'public_parent_changed')
            chain.append((parent, part, current, _pin(before)[:4]))
        last = os.fstat(fds[-1])
        _need(last.st_uid == os.geteuid() and stat.S_IMODE(last.st_mode) == 448, 'public_file_unsafe')
        before = os.stat(path.name, dir_fd=fds[-1], follow_symlinks=False)
        _need(stat.S_ISREG(before.st_mode) and before.st_uid == os.geteuid() and (before.st_nlink == 1) and (stat.S_IMODE(before.st_mode) == 384) and (0 < before.st_size <= maximum), 'public_file_unsafe')
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fds[-1])
        if _pin(os.fstat(fd)) != _pin(before):
            _UNKNOWN_PUBLIC_FDS.add(fd)
            fd = None
            raise PublicReplyError('public_file_changed')
        raw = bytearray()
        while len(raw) <= maximum:
            chunk = os.read(fd, min(65536, maximum + 1 - len(raw)))
            if not chunk:
                break
            raw.extend(chunk)
        _need(len(raw) == before.st_size and _pin(os.fstat(fd)) == _pin(before) and (_pin(os.stat(path.name, dir_fd=fds[-1], follow_symlinks=False)) == _pin(before)), 'public_file_changed')
        for (parent, name, current, expected) in chain:
            _need(_pin(os.stat(name, dir_fd=parent, follow_symlinks=False))[:4] == expected and _pin(os.fstat(current))[:4] == expected, 'public_parent_changed')
        return bytes(raw)
    finally:
        if fd is not None:
            os.close(fd)
        for current in reversed(fds):
            os.close(current)

def _grant(config):
    import wechat_outbound_queue as queue
    workspace = queue._workspace(config)
    raw = _private_bytes(workspace / 'desktop_approvals' / PUBLIC_AUTH_NAME)
    record = queue._json(raw)
    _need(type(record) is dict and _encoded(record) == _encoded(public_authorization_record(config, record.get('confirmed_utc'))), 'public_authorization_changed')
    return (record, _sha(raw))

def _fingerprint(config, group):
    keys = ('app_id', 'send_enabled', 'enabled', 'token_file', 'base_url', 'active_after', 'max_message_age_seconds', 'assistant_profile_id', 'collection_only_groups', 'wechat_task_relay', 'community_public_reply')
    return _sha(dict([(key, config.get(key)) for key in keys], group=group))

def _source(row):
    import wechat_outbound_queue as queue
    from wtapi_events import GroupMessage
    _need(type(row['payload']) is str and len(row['payload'].encode('utf-8')) <= 256 * 1024)
    data = queue._json(row['payload'].encode('utf-8'))
    _need(set(data) == {'app_id', 'own_wxid', 'group_id', 'sender_id', 'message_id', 'text', 'created_at', 'at_ids', 'attachments'})
    _need(all((_ident(data[k]) for k in ('app_id', 'own_wxid', 'group_id', 'sender_id', 'message_id'))) and type(data['text']) is str and _stamp(data['created_at']) and (type(data['at_ids']) is list) and (len(data['at_ids']) <= 256) and all((_ident(x) for x in data['at_ids'])) and (type(data['attachments']) is list))
    _need(all((row[k] == data[k] for k in ('app_id', 'message_id', 'group_id', 'sender_id', 'created_at'))))
    return (GroupMessage(**data), _sha({k: row[k] for k in ('app_id', 'message_id', 'group_id', 'sender_id', 'created_at', 'payload')}))

def _context(store, path, config, message, now):
    import wechat_outbound_queue as queue
    selected = public_selection(config)
    _need(selected is not None and config.get('enabled', True) is True and (config.get('send_enabled') is True) and (not store.paused()), 'public_inactive')
    _need(queue.active(store, config, path), 'public_private_queue_inactive')
    (app, own, profile, _) = queue._identity(config)
    _need((app, own, profile, message.group_id) == (selected['app_id'], selected['own_wxid'], selected['assistant_profile_id'], selected['group_id']) and message.app_id == app and (message.own_wxid == own) and (not message.attachments), 'public_scope_changed')
    group = group_view(config, message.group_id, db=store.db, app_id=app, own_wxid=own, created_at=message.created_at, public_reply=True)
    _need(group is not None and group.get('reply_mode') == 'participate', 'public_group_closed')
    (record, grant) = _grant(config)
    bindings = store.db.execute('SELECT app_id,own_wxid,group_id,created_at FROM memory_bindings WHERE profile_id=? AND scope_id=?', (profile, selected['memory_scope_id'])).fetchall()
    _need(len(bindings) == 1 and tuple(bindings[0])[:3] == (app, own, message.group_id) and _stamp(bindings[0]['created_at']), 'public_binding_changed')
    scope = store.db.execute('SELECT profile_id,scope_id,own_wxid,business_role FROM message_scopes WHERE app_id=? AND message_id=?', (app, message.message_id)).fetchone()
    _need(scope is not None and tuple(scope) == (profile, selected['memory_scope_id'], own, 'learning'), 'public_source_scope_changed')
    age = config.get('max_message_age_seconds', 120)
    _need(type(age) in (int, float) and math.isfinite(age) and (0 < age <= 3600))
    expires = message.created_at + age
    _need(max(record['active_after'], bindings[0]['created_at'], config.get('active_after', 0), group.get('activation_after', 0)) <= message.created_at <= now + 60 and now < expires, 'public_source_expired')
    return (group, grant, _fingerprint(config, group), expires)

def public_snapshot(store, path, config, message, now, pending_updated_at=None):
    if config.get('community_public_reply') is None:
        return None
    try:
        with store.lock:
            (_, grant, fingerprint, _) = _context(store, path, config, message, now)
        _need(_stamp(pending_updated_at) and config['community_public_reply']['active_after'] <= pending_updated_at <= now, 'public_pending_before_approval')
        return {'grant_sha256': grant, 'fingerprint': fingerprint, 'pending_updated_at': pending_updated_at}
    except Exception:
        return None

def public_admitted(store, path, config, message, now, pending_updated_at=None):
    return public_snapshot(store, path, config, message, now, pending_updated_at) is not None

def _limits(db, message, group, now, direct, prepared_at=None):
    rows = db.execute('SELECT a.sender_id,a.planned_at,a.day,m.status FROM actions a\n        LEFT JOIN messages m ON m.app_id=a.app_id AND m.message_id=a.message_id\n        WHERE a.app_id=? AND a.group_id=? AND a.message_id<>?', (message.app_id, message.group_id, message.message_id)).fetchall()
    if prepared_at is not None:
        rows = [r for r in rows if not (r['status'] == 'outbound_queued' and r['planned_at'] > prepared_at)]
    cooldown = 30 if direct else 600
    return sum((r['day'] == int(now // 86400) for r in rows)) < group['daily_limit'] and (not any((now - r['planned_at'] < cooldown for r in rows))) and (not any((r['sender_id'] == message.sender_id and now - r['planned_at'] < cooldown for r in rows)))

def prepare_public(store, path, loader, message, content, source_ids, mention_ids, *, expected, direct=False, now):
    import wechat_outbound_queue as queue
    _need(type(direct) is bool and type(content) is str and (0 < len(content) <= 2000) and (not any((ord(c) < 32 and c not in '\n\t' for c in content))))
    _need(type(source_ids) is list and len(source_ids) <= 12 and all((_ident(x) for x in source_ids)) and (tuple(mention_ids) in ((), (message.sender_id,))))
    config = loader()
    with queue._transaction(store) as db:
        (group, grant, fingerprint, expires) = _context(store, path, config, message, now)
        _need(type(expected) is dict and set(expected) == {'grant_sha256', 'fingerprint', 'pending_updated_at'} and (expected['grant_sha256'] == grant) and (expected['fingerprint'] == fingerprint) and _stamp(expected['pending_updated_at']) and (config['community_public_reply']['active_after'] <= expected['pending_updated_at'] <= now), 'public_authorization_changed')
        row = db.execute('SELECT * FROM messages WHERE app_id=? AND message_id=?', (message.app_id, message.message_id)).fetchone()
        _need(row is not None and row['status'] == 'processing', 'public_source_changed')
        (current, source) = _source(row)
        _need(_encoded(current.__dict__) == _encoded(message.__dict__), 'public_source_changed')
        _need(_limits(db, message, group, now, direct), 'public_reply_limit')
        _need(db.execute('SELECT 1 FROM actions WHERE app_id=? AND message_id=?', (message.app_id, message.message_id)).fetchone() is None)
        analysis = db.execute('SELECT * FROM analysis_archive WHERE app_id=? AND message_id=?', (message.app_id, message.message_id)).fetchone()
        _need(analysis is not None and analysis['group_id'] == message.group_id and (analysis['sender_id'] == message.sender_id) and (analysis['action'] == 'reply') and (not direct or analysis['target'] == 'assistant'), 'public_analysis_changed')
        delay = 8 + int(_sha([message.app_id, message.message_id])[:8], 16) % 11
        meta = {'version': 1, 'kind': 'community_public_reply', 'grant_sha256': grant, 'fingerprint': fingerprint, 'source_sha256': source, 'analysis_sha256': _sha(dict(analysis)), 'reply_sha256': _sha([content, source_ids, list(mention_ids)]), 'mention_ids': list(mention_ids), 'not_before': now + delay, 'expires_at': expires, 'direct': direct, 'pending_updated_at': expected['pending_updated_at']}
        _need(now + delay < expires, 'public_source_expired')
        _need(loader() == config and _grant(config)[1] == grant, 'public_authorization_changed')
        db.execute('INSERT INTO actions VALUES (?,?,?,?,?,?)', (message.app_id, message.message_id, message.group_id, message.sender_id, now, int(now // 86400)))
        db.execute("INSERT INTO reply_archive VALUES (?,?,?,?,?,'queued',?,?)", (message.app_id, message.message_id, message.group_id, message.own_wxid, content, now, _encoded(source_ids).decode('utf-8')))
        _need(db.execute("UPDATE messages SET status='outbound_queued',rule=?,updated_at=? WHERE app_id=? AND message_id=? AND status='processing'", (_encoded(meta).decode('utf-8'), now, message.app_id, message.message_id)).rowcount == 1)
    return {'status': 'queued', 'attempted': False}

def drain_public(store, path, loader, client_factory, *, now):
    import wechat_outbound_queue as queue
    from wtapi_client import SendRejected
    clock = now
    when = queue._when(clock)
    if getattr(store, '_community_public_uncertain', False):
        return {'status': 'blocked', 'attempted': False}
    attempted = False
    row = None
    try:
        with store.lock:
            if store.db.execute("SELECT 1 FROM messages WHERE status='outbound_queued' LIMIT 1").fetchone() is None:
                return {'status': 'idle', 'attempted': False}
        with queue._send_lock(store):
            config = loader()
            config_raw = _private_bytes(Path(path).absolute(), 1024 * 1024)
            from group_lifecycle import apply_state_db
            _need(apply_state_db(store.db, queue._json(config_raw)) == config, 'public_configuration_changed')
            if not queue.active(store, config, path):
                return {'status': 'disabled', 'attempted': False}
            (app, own, profile, _) = queue._identity(config)
            account = queue._sha([app, own])
            with queue._transaction(store) as db:
                row = db.execute("SELECT * FROM messages WHERE app_id=? AND status='outbound_queued' ORDER BY rowid LIMIT 1", (app,)).fetchone()
                if row is None:
                    return {'status': 'idle', 'attempted': False}
                if store.paused() or queue._legacy_unresolved(store, config) or db.execute("SELECT 1 FROM wechat_outbound_queue WHERE account=? AND status IN ('sending','unknown','rejected') LIMIT 1", (account,)).fetchone():
                    return {'status': 'blocked', 'attempted': False}
                if db.execute("SELECT 1 FROM wechat_outbound_queue WHERE account=? AND status='queued' LIMIT 1", (account,)).fetchone():
                    return {'status': 'private_priority', 'attempted': False}
                try:
                    (message, source) = _source(row)
                    (group, grant, fingerprint, expires) = _context(store, path, config, message, when)
                    meta = queue._json(row['rule'].encode('utf-8'))
                    _need(set(meta) == {'version', 'kind', 'grant_sha256', 'fingerprint', 'source_sha256', 'analysis_sha256', 'reply_sha256', 'mention_ids', 'not_before', 'expires_at', 'direct', 'pending_updated_at'} and type(meta['version']) is int and (meta['version'] == 1) and (meta['kind'] == 'community_public_reply') and (type(meta['direct']) is bool) and (meta['source_sha256'] == source) and (meta['fingerprint'] == fingerprint) and (meta['grant_sha256'] == grant) and (meta['expires_at'] == expires) and _stamp(meta['pending_updated_at']) and (config['community_public_reply']['active_after'] <= meta['pending_updated_at'] <= when) and _stamp(meta['not_before']) and (meta['not_before'] < expires) and (type(meta['mention_ids']) is list) and (tuple(meta['mention_ids']) in ((), (message.sender_id,))))
                    reply = db.execute('SELECT * FROM reply_archive WHERE app_id=? AND message_id=?', (app, row['message_id'])).fetchone()
                    _need(reply is not None and reply['status'] == 'queued' and (reply['group_id'] == message.group_id) and (reply['sender_id'] == own) and _stamp(reply['created_at']) and (meta['pending_updated_at'] <= reply['created_at']))
                    ids = json.loads(reply['source_ids'], parse_constant=lambda _: (_ for _ in ()).throw(PublicReplyError('public_reply_invalid')))
                    _need(type(ids) is list and len(ids) <= 12 and all((_ident(x) for x in ids)) and (type(reply['text']) is str) and (0 < len(reply['text']) <= 2000) and (_sha([reply['text'], ids, meta['mention_ids']]) == meta['reply_sha256']))
                    delay = 8 + int(_sha([app, message.message_id])[:8], 16) % 11
                    _need(meta['not_before'] == reply['created_at'] + delay)
                    action = db.execute('SELECT * FROM actions WHERE app_id=? AND message_id=?', (app, message.message_id)).fetchone()
                    _need(action is not None and action['group_id'] == message.group_id and (action['sender_id'] == message.sender_id) and (action['planned_at'] == reply['created_at']) and (action['day'] == int(reply['created_at'] // 86400)))
                    analysis = db.execute('SELECT * FROM analysis_archive WHERE app_id=? AND message_id=?', (app, message.message_id)).fetchone()
                    _need(analysis is not None and _sha(dict(analysis)) == meta['analysis_sha256'])
                    _need(_limits(db, message, group, when, meta['direct'], reply['created_at']), 'public_reply_limit')
                except Exception:
                    db.execute("UPDATE messages SET status='cancelled',updated_at=? WHERE app_id=? AND message_id=? AND status='outbound_queued'", (when, app, row['message_id']))
                    db.execute("UPDATE reply_archive SET status='cancelled' WHERE app_id=? AND message_id=? AND status='queued'", (app, row['message_id']))
                    return {'status': 'cancelled', 'attempted': False}
                timing = db.execute('SELECT next_attempt_at FROM wechat_outbound_accounts WHERE account=?', (account,)).fetchone()
                _need(timing is None or _account_time(timing[0]), 'public_spacing_invalid')
                if when < meta['not_before'] or (timing and when < timing[0]):
                    return {'status': 'queued', 'attempted': False}
            token_path = Path(config['token_file'])
            if not token_path.is_absolute():
                token_path = Path(path).parent / token_path
            token = _private_bytes(token_path).decode('utf-8').strip(' \t\r\n\x0b\x0c')
            _need(bool(token) and (not any((c.isspace() or ord(c) < 33 or ord(c) > 126 for c in token))), 'public_credential_invalid')
            client = client_factory(token=token, base_url=config['base_url'], timeout=15)
            with queue._transaction(store) as db:
                _need(loader() == config and _private_bytes(Path(path).absolute(), 1024 * 1024) == config_raw and (_context(store, path, config, message, queue._when(clock))[1:3] == (grant, fingerprint)), 'public_authorization_changed')
                current = db.execute('SELECT * FROM messages WHERE app_id=? AND message_id=?', (app, message.message_id)).fetchone()
                current_reply = db.execute('SELECT * FROM reply_archive WHERE app_id=? AND message_id=?', (app, message.message_id)).fetchone()
                _need(dict(current) == dict(row) and dict(current_reply) == dict(reply), 'public_source_changed')
                current_analysis = db.execute('SELECT * FROM analysis_archive WHERE app_id=? AND message_id=?', (app, message.message_id)).fetchone()
                _need(current_analysis is not None and dict(current_analysis) == dict(analysis), 'public_analysis_changed')
                _need(not queue._legacy_unresolved(store, config) and (not store.paused()), 'public_send_unresolved')
                _need(db.execute("SELECT 1 FROM wechat_outbound_queue WHERE account=? AND status IN ('queued','sending','unknown','rejected') LIMIT 1", (account,)).fetchone() is None, 'public_private_priority')
                started = queue._when(clock)
                _need(started < expires and started >= meta['not_before'], 'public_source_expired')
                current_timing = db.execute('SELECT next_attempt_at FROM wechat_outbound_accounts WHERE account=?', (account,)).fetchone()
                _need(current_timing is None or _account_time(current_timing[0]), 'public_spacing_invalid')
                _need(current_timing is None or started >= current_timing[0], 'public_spacing_not_due')
                _need(_limits(db, message, group, started, meta['direct'], reply['created_at']), 'public_reply_limit')
                _need(db.execute('UPDATE actions SET planned_at=?,day=? WHERE app_id=? AND message_id=?\n                    AND group_id=? AND sender_id=? AND planned_at=? AND day=?', (started, int(started // 86400), app, message.message_id, message.group_id, message.sender_id, reply['created_at'], int(reply['created_at'] // 86400))).rowcount == 1, 'public_action_changed')
                _need(db.execute("UPDATE messages SET status='sending',updated_at=? WHERE app_id=? AND message_id=? AND status='outbound_queued'", (started, app, message.message_id)).rowcount == 1, 'public_source_changed')
                _need(db.execute("UPDATE reply_archive SET status='sending' WHERE app_id=? AND message_id=? AND status='queued'", (app, message.message_id)).rowcount == 1, 'public_source_changed')
                db.execute('INSERT OR IGNORE INTO wechat_outbound_accounts VALUES (?,0,?)', (account, started))
                db.execute('UPDATE wechat_outbound_accounts SET next_attempt_at=?,updated_at=? WHERE account=?', (started + queue.INTERVAL_SECONDS, started, account))
            attempted = True
            try:
                client.send_text(app, message.group_id, reply['text'], mention_ids=tuple(meta['mention_ids']))
                result = 'accepted'
            except SendRejected:
                result = 'failed'
            except Exception:
                result = 'unknown'
            finally:
                store._community_public_uncertain = True
            with queue._transaction(store) as db:
                finished = queue._when(clock)
                db.execute('UPDATE wechat_outbound_accounts SET next_attempt_at=max(next_attempt_at,?),updated_at=? WHERE account=?', (finished + queue.INTERVAL_SECONDS, finished, account))
                _need(db.execute("UPDATE messages SET status=?,updated_at=? WHERE app_id=? AND message_id=? AND status='sending'", (result, finished, app, message.message_id)).rowcount == 1, 'public_result_uncommitted')
                _need(db.execute("UPDATE reply_archive SET status=? WHERE app_id=? AND message_id=? AND status='sending'", (result, app, message.message_id)).rowcount == 1, 'public_result_uncommitted')
                if result in ('failed', 'unknown'):
                    queue._pause(db)
            store._community_public_uncertain = False
            return {'status': result, 'attempted': True}
    except Exception as error:
        if attempted:
            store._community_public_uncertain = True
            try:
                with queue._transaction(store) as db:
                    queue._pause(db)
            except Exception:
                pass
            return {'status': 'unknown', 'attempted': True}
        return {'status': 'busy' if getattr(error, 'code', None) == 'busy' else 'blocked', 'attempted': False}

def enabled(config):
    return config.get('collection_only_groups') is True

def allows_unclassified_media(config, group):
    setting = config.get('group_onboarding', {})
    return enabled(config) and isinstance(setting, dict) and (setting.get('auto_collect') is True) and isinstance(group, dict) and (group.get('business_role') == 'unclassified') and (group.get('auto_collection_source') == 'standing_group_collection')

def validate(config):
    if type(config.get('collection_only_groups', False)) is not bool:
        raise ValueError('collection_only_groups must be boolean')
    _public_shape(config)

def _stamp(value):
    return type(value) in (int, float) and math.isfinite(value) and (0 < value <= 10 ** 12)

def _account_time(value):
    return type(value) in (int, float) and math.isfinite(value) and (0 <= value <= 10 ** 12)

def group_view(config, group_id, *, db=None, app_id=None, own_wxid=None, created_at=None, public_reply=False):
    group = config.get('groups', {}).get(group_id)
    if not isinstance(group, dict):
        return None
    if group.get('lifecycle_closed_at') or 'onboarding_pending_activation' in group or 'management_pending_activation' in group:
        return None
    if db is not None:
        from group_lifecycle import is_closed_db
        if is_closed_db(db, config, group_id, own_wxid):
            return None
    if group.get('enabled') is True:
        view = dict(group)
        selected = public_selection(config)
        if enabled(config) and (not public_reply or selected is None or selected['group_id'] != group_id):
            view['reply_mode'] = 'silent'
        return view
    if not enabled(config) or group.get('enabled') is not False or db is None:
        return None
    owner = config.get('owner_control', {})
    (profile, scope, own) = (config.get('assistant_profile_id'), group.get('memory_scope_id'), group.get('own_wxid'))
    after = group.get('activation_after')
    if owner.get('enabled') is not True or group_id not in owner.get('group_ids', []) or group.get('business_role', 'internal') != 'internal' or (not all((isinstance(value, str) and value for value in (profile, scope, own)))) or (own != owner.get('own_wxid')) or (app_id != config.get('app_id')) or (own_wxid != own) or (not _stamp(after)) or (not _stamp(created_at)) or (created_at < after) or (sum((isinstance(item, dict) and item.get('memory_scope_id') == scope for item in config.get('groups', {}).values())) != 1):
        return None
    bindings = db.execute('SELECT app_id,own_wxid,group_id,created_at FROM memory_bindings\n        WHERE profile_id=? AND scope_id=?', (profile, scope)).fetchall()
    matches = [row for row in bindings if row['app_id'] == app_id and row['own_wxid'] == own]
    if not bindings or any((row['group_id'] != group_id for row in bindings)) or len(matches) != 1 or (not _stamp(matches[0]['created_at'])) or (created_at < matches[0]['created_at']):
        return None
    return dict(group, enabled=True, business_role='internal', reply_mode='silent')
