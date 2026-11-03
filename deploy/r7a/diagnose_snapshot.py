"""只读私有 SQLite 备份，输出计数；不读取配置、不提供执行或重试入口。"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3
import stat

FILES = ('wechat.sqlite3', 'digest.sqlite3', 'history.sqlite3', 'raw-index.sqlite3', 'whatsapp.sqlite3')
ROLES = frozenset(('learning', 'internal', 'unclassified', 'supplier', 'customer', 'forwarder', 'vendor'))
DIGEST_STATES = frozenset(('prepared', 'calling', 'model_usage_unknown', 'budget_blocked', 'model_failed',
    'generated', 'nas_pending', 'archived_only', 'notification_ready', 'queued', 'accepted',
    'notify_unknown', 'notify_rejected', 'authorization_changed'))
DIGEST_ERRORS = frozenset(('', 'qwen_matrix_unavailable', 'digest_numeric_evidence_rejected',
    'digest_category_representative_rejected', 'model_exception', 'digest_previous_call_uncertain'))
SCAN_REASONS = frozenset(('text_empty', 'unsupported_message_type', 'media_without_text_caption',
    'transient_failure', 'index_damaged', 'indexed_source_changed', 'source_conflict', 'identity_conflict',
    'encoded_or_binary', 'evidence_missing_or_invalid', 'evidence_conflict', 'event_invalid',
    'evidence_addresses_mismatch', 'projection_mismatch', 'history_context_unverified',
    'unsupported_chat_kind', 'history_chat_invalid', 'source_unavailable', 'binding_changed'))


def snapshot_root(value):
    root = Path(value).absolute()
    if '..' in root.parts or not root.is_relative_to('/tmp') or root == Path('/tmp'):
        raise ValueError('仅允许读取 /tmp 下的私有备份目录')
    if any(p.is_symlink() for p in (root, *root.parents)):
        raise ValueError('备份目录不能含符号链接')
    info = root.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise ValueError('备份目录必须属于当前用户且权限为 0700')
    return root


@contextmanager
def read_db(root, name):
    if name not in FILES:
        raise ValueError('备份文件不在允许清单内')
    path = root / name
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or path.is_symlink() or info.st_uid != os.geteuid()
            or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) not in (0o400, 0o600)
            or any(Path(str(path) + suffix).exists() for suffix in ('-wal', '-shm'))):
        raise ValueError('备份须为当前用户的私有独立文件，不能带 WAL 或 SHM')
    db = sqlite3.connect(path.as_uri() + '?mode=ro&immutable=1', uri=True)
    db.row_factory = sqlite3.Row
    try:
        db.execute('PRAGMA query_only=ON')
        yield db
    finally:
        db.close()


def wechat(db):
    rows = db.execute("""SELECT m.payload,m.created_at,m.updated_at,s.business_role
        FROM messages m LEFT JOIN message_scopes s USING(app_id,message_id)
        WHERE m.status='pending'""").fetchall()
    roles, shapes = Counter(), Counter()
    malformed = 0
    for row in rows:
        role = row['business_role'] if row['business_role'] in ROLES else 'unknown'
        roles[role] += 1
        try:
            payload = json.loads(row['payload'])
            if not isinstance(payload, dict) or not isinstance(payload.get('attachments'), list):
                raise ValueError
            shapes['attachments' if payload['attachments'] else 'text_only'] += 1
        except (ValueError, TypeError):
            malformed += 1
    linked = {}
    for table in ('actions', 'analysis_archive', 'reply_archive', 'receive_collection_jobs'):
        linked[table] = db.execute("SELECT count(*) FROM messages m WHERE m.status='pending' "
            'AND EXISTS(SELECT 1 FROM ' + table + ' x WHERE x.app_id=m.app_id AND x.message_id=m.message_id)').fetchone()[0]
    paused = db.execute("SELECT value='true' FROM metadata WHERE key='paused'").fetchone()
    return {'pending': len(rows), 'paused': bool(paused[0]) if paused else None,
        'roles': dict(roles), 'shapes': dict(shapes), 'malformed': malformed, 'linked': linked}


def diagnose(value):
    root = snapshot_root(value)
    result = {}
    with read_db(root, 'wechat.sqlite3') as db:
        result['wechat'] = wechat(db)
    with read_db(root, 'digest.sqlite3') as db:
        grouped = Counter()
        for row in db.execute("SELECT status,error_code,count(*) n FROM digest_runs WHERE kind='daily' GROUP BY 1,2"):
            state = row['status'] if row['status'] in DIGEST_STATES else 'other'
            code = row['error_code'] if row['error_code'] in DIGEST_ERRORS else 'other'
            grouped[(state, code)] += row['n']
        result['daily'] = [dict(status=k[0], error_code=k[1], count=n) for k, n in sorted(grouped.items())]
    with read_db(root, 'history.sqlite3') as db:
        result['history_messages'] = db.execute('SELECT count(*) FROM messages').fetchone()[0]
    with read_db(root, 'raw-index.sqlite3') as db:
        grouped = Counter()
        for row in db.execute('SELECT state,reason,count(*) n FROM wa_history_scan_state GROUP BY 1,2'):
            state = row['state'] if row['state'] in ('indexed', 'review', 'pending', 'retry') else 'other'
            reason = 'none' if row['reason'] is None else row['reason'] if row['reason'] in SCAN_REASONS else 'other'
            grouped[(state, reason)] += row['n']
        result['raw_index'] = [dict(state=k[0], reason=k[1], count=n) for k, n in sorted(grouped.items())]
    with read_db(root, 'whatsapp.sqlite3') as db:
        row = db.execute("SELECT raw_json FROM events WHERE event_type='session.status' ORDER BY received DESC LIMIT 1").fetchone()
        value = None
        if row:
            try:
                payload = json.loads(row[0])
                value = payload.get('payload', {}).get('status')
            except (ValueError, TypeError, AttributeError):
                pass
        result['whatsapp_session'] = value if value in ('WORKING', 'FAILED', 'STARTING', 'STOPPED',
            'SCAN_QR_CODE', 'PASSKEY_REQUIRED', 'PASSKEY_CONFIRMATION_REQUIRED') else 'unknown'
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot', required=True, type=Path, help='0700 的私有备份目录')
    args = parser.parse_args(argv)
    try:
        result = diagnose(args.snapshot)
    except (OSError, ValueError, sqlite3.Error):
        print('备份读取失败，请核对权限、文件清单和表结构；未改动任何库')
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
