"""Output persistence and conservative, cross-worker AI quota reservations."""
from contextlib import contextmanager
from functools import wraps
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import stat
import threading

from console.archive.store import insert, update
from .common import clean_party_name, dumps, safe, stamp
from .inputs import tables

_LOCKS, _LOCK = {}, threading.Lock()
_HELD = threading.local()


@contextmanager
def run_lock(store):
    key = ('connection', id(store.connection)) if store.connection is not None else str(store.path.resolve())
    with _LOCK:
        lock = _LOCKS.setdefault(key, threading.RLock())
    with lock:
        held = getattr(_HELD, 'keys', set())
        if key in held or store.connection is not None:
            yield
            return
        # Advisory locks live only in the owner's temporary directory, not in
        # a production source or an old business table. All role entry points
        # use this lock, including CLI runs and independent web workers.
        directory = Path('/tmp') / ('imgw-role-locks-' + str(os.getuid()))
        directory.mkdir(mode=0o700, exist_ok=True)
        info = directory.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError('岗位运行锁目录不可用')
        filename = hashlib.sha256(str(key).encode()).hexdigest()
        fd = os.open(directory / filename, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            _HELD.keys = held | {key}
            yield
        finally:
            _HELD.keys = held
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)


def serialized(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        with run_lock(self.store):
            return method(self, *args, **kwargs)
    return wrapped


class RateLimited(Exception):
    def __init__(self, retry_after, message='岗位 AI 调用已达上限，已提供规则建议，请稍后再试'):
        super().__init__(message)
        self.retry_after = max(1, int(retry_after))
        self.result = None


def reserve(store, now, config, *, bucket='shared', reservation_calls=3):
    # By default, one runner call can make up to three gateway attempts, so
    # reserve all three including failures. A named role bucket may instead
    # count one invocation when that role's configured limit is invocation-based.
    if bucket not in ('shared', 'extract', 'listing'):
        raise ValueError('岗位 AI 额度桶无效')
    if type(reservation_calls) is not int or not 1 <= reservation_calls <= 3:
        raise ValueError('岗位 AI 预留调用数无效')
    table = 'extract_ai_reservations' if bucket == 'extract' else 'role_ai_reservations'
    with store.write() as db:
        if bucket == 'extract':
            # Keep deployed migrations and the legacy shared table unchanged.
            # Old rows cannot be attributed to a role and remain in the shared
            # bucket; old writers/readers also cannot consume extraction quota.
            # BEGIN IMMEDIATE covers both initialization and the reservation.
            db.execute('CREATE TABLE IF NOT EXISTS extract_ai_reservations ('
                       'id INTEGER PRIMARY KEY, at REAL NOT NULL, '
                       'calls INTEGER NOT NULL CHECK(calls BETWEEN 1 AND 3))')
            db.execute('CREATE INDEX IF NOT EXISTS extract_ai_time ON extract_ai_reservations(at)')
        columns = {row[1] for row in db.execute('PRAGMA table_xinfo(' + table + ')')}
        scoped = bucket == 'extract' or 'bucket' not in columns
        if bucket == 'listing' and 'bucket' not in columns:
            raise ValueError('发品员额度表尚未迁移')
        where = ' WHERE at>?'
        args = [now - 3600]
        if not scoped:
            where += ' AND bucket=?'
            args.append(bucket)
        rows = list(db.execute('SELECT at,calls FROM ' + table + where + ' ORDER BY at', args))
        waits = []
        for window, limit in ((60, config.per_minute), (3600, config.per_hour)):
            recent = [r for r in rows if r['at'] > now - window]
            total = sum(r['calls'] for r in recent)
            if total + reservation_calls > limit:
                remaining = total
                wait = window
                for row in recent:
                    remaining -= row['calls']
                    wait = math.ceil(row['at'] + window - now)
                    if remaining + reservation_calls <= limit:
                        break
                waits.append(wait)
        if waits:
            if bucket == 'extract':
                raise RateLimited(max(waits), '抽取员 AI 调用已达独立额度上限，请下次再扫')
            raise RateLimited(max(waits))
        db.execute('DELETE FROM ' + table + ' WHERE at<=?', (now - 3600,))
        if bucket == 'listing' or 'bucket' in columns:
            db.execute('INSERT INTO ' + table + '(at,calls,bucket) VALUES (?,?,?)',
                       (now, reservation_calls, bucket))
        else:
            db.execute('INSERT INTO ' + table + '(at,calls) VALUES (?,?)', (now, reservation_calls))


def run_view(row):
    if row is None:
        return None
    result = dict(row)
    for field in ('output_json', 'evidence_json'):
        result[field] = json.loads(result[field])
    result['ai_success'] = bool(result['ai_success']) if result['ai_success'] is not None else None
    return result


def recent(store, role=None, limit=50, *, subject=None):
    with store.read() as db:
        if 'role_runs' not in tables(db):
            return []
        where, args = [], []
        if role:
            where.append('role=?')
            args.append(role)
        if subject is not None:
            where.append('subject_key=?')
            args.append(str(subject))
        clause = ' WHERE ' + ' AND '.join(where) if where else ''
        return [run_view(r) for r in db.execute('SELECT * FROM role_runs' + clause +
                ' ORDER BY created_at DESC,id DESC LIMIT ?', (*args, limit))]


def reused(store, role, key, fp):
    runs = recent(store, role, 1, subject=key)
    return runs[0] if runs and runs[0]['input_fingerprint'] == fp else None


def save_run(db, role, kind, key, fp, output, output_text, evidence, who, now, *, source='rules',
             status='ok', model=None, error=None, success=None):
    rid = insert(db, 'role_runs', dict(role=role, subject_kind=kind, subject_key=str(key), input_fingerprint=fp,
                 status=status, source=source, model=model, output_json=dumps(output), output_text=output_text,
                 evidence_json=dumps(evidence), error=error, created_at=stamp(now), created_by=who,
                 ai_success=success), who)
    return run_view(db.execute('SELECT * FROM role_runs WHERE id=?', (rid,)).fetchone())


def sync_alerts(db, alerts, protected, now, who):
    from .patrol import CHAT_KINDS
    at = stamp(now)
    active = {(r['kind'], r['ref_kind'], r['ref_id']): dict(r) for r in db.execute('SELECT * FROM role_alerts WHERE resolved_at IS NULL')}
    seen = set()
    for alert in alerts:
        key = (alert['kind'], alert['ref_kind'], alert['ref_id'])
        seen.add(key)
        old = active.get(key)
        if old:
            update(db, 'role_alerts', old['id'], dict(last_seen_at=at, message=alert['message'], severity=alert['severity']), who)
        else:
            insert(db, 'role_alerts', dict(alert, first_seen_at=at, last_seen_at=at), who)
    for key, old in active.items():
        if key not in seen and not (old['kind'] in CHAT_KINDS and old['deal_id'] in protected):
            update(db, 'role_alerts', old['id'], dict(resolved_at=at), who)


def suggestion_view(row):
    result = dict(row)
    result['conversation'] = json.loads(result.pop('summary_json'))
    result['suggested_party_name'] = result['conversation'].pop('suggested_party_name', None) or safe(
        clean_party_name(result['conversation'].get('counterpart_name')), 100)
    for field in ('duplicate_of', 'buyer_fingerprint'):
        result[field] = result['conversation'].pop(field, None)
    return result


def save_suggestion(db, run, snapshot, who, now):
    values = dict(run['output_json'], source=run['source'], run_id=run['id'], updated_at=stamp(now))
    # These optional fields live in the deployed v4 JSON snapshot, not new columns.
    metadata = {field: values.pop(field, None) for field in ('suggested_party_name', 'duplicate_of', 'buyer_fingerprint')}
    values['summary_json'] = dumps(dict(snapshot, **metadata))
    old = db.execute('SELECT * FROM triage_suggestions WHERE conversation_key=?', (run['subject_key'],)).fetchone()
    if old and old['status'] == 'pending':
        update(db, 'triage_suggestions', old['id'], values, who)
    elif not old:
        insert(db, 'triage_suggestions', dict(values, conversation_key=run['subject_key'], created_at=stamp(now)), who)
