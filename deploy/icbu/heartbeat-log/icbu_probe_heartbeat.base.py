"""Authenticated LAN probe metadata only. No customer content or platform access."""
from __future__ import annotations

import json
from contextlib import contextmanager
import math
import os
import re
import sqlite3
import stat
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

HEARTBEAT_PATH = '/api/inbound/alibaba/probe-heartbeat'
DEFAULT_DB = '/var/lib/imgw/icbu/probe-status.sqlite3'
MAX_BODY = 4096
COUNTERS = {'contacts_loaded', 'contacts_scanned', 'unbound', 'metadata_skipped', 'queued', 'blocked'}
FIELDS = {'schema', 'account', 'instance_id', 'version', 'state', 'range', 'counters', 'last_error', 'cycle_complete'}


class HeartbeatRejected(ValueError):
    def __init__(self, code, status=400):
        self.code, self.status = code, status
        super().__init__(code)


def require(ok, code, status=400):
    if not ok:
        raise HeartbeatRejected(code, status)


def compact(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def server_clock(now=None):
    stamp = time.time() if now is None else now
    require(type(stamp) in (int, float) and math.isfinite(stamp) and 0 < stamp < 4102444800,
            'heartbeat_server_time_invalid', 503)
    utc = datetime.fromtimestamp(stamp, timezone.utc)
    return {'ok': True, 'server_time_utc': utc.isoformat(),
            'server_day_shanghai': utc.astimezone(timezone(timedelta(hours=8))).date().isoformat(),
            'poll_after_seconds': 120}


def validate(value):
    require(isinstance(value, dict) and set(value) == FIELDS, 'heartbeat_invalid_fields')
    require(value['schema'] == 'icbu_probe_heartbeat_v1', 'heartbeat_schema_invalid')
    require(value['account'] == 'Amy', 'heartbeat_account_not_bound', 403)
    require(isinstance(value['instance_id'], str) and re.fullmatch(
        r'[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}', value['instance_id']),
        'heartbeat_instance_invalid')
    require(isinstance(value['version'], str) and re.fullmatch(r'[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}', value['version']),
            'heartbeat_version_invalid')
    require(value['state'] in ('starting', 'running', 'idle', 'paused'), 'heartbeat_state_invalid')
    bounds = value['range']
    require(isinstance(bounds, dict) and set(bounds) == {'date_start', 'date_end'}, 'heartbeat_range_invalid')
    try:
        require(all(isinstance(bounds[k], str) and re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', bounds[k])
                    for k in bounds), 'heartbeat_range_invalid')
        start, end = date.fromisoformat(bounds['date_start']), date.fromisoformat(bounds['date_end'])
        require(date(2000, 1, 1) <= start <= end < date(2100, 1, 1) and (end - start).days <= 366,
                'heartbeat_range_invalid')
    except (ValueError, TypeError):
        raise HeartbeatRejected('heartbeat_range_invalid') from None
    counts = value['counters']
    require(isinstance(counts, dict) and set(counts) == COUNTERS
            and all(type(n) is int and 0 <= n <= 1000000 for n in counts.values()), 'heartbeat_counters_invalid')
    require(counts['contacts_scanned'] <= counts['contacts_loaded'], 'heartbeat_counters_invalid')
    error = value['last_error']
    require(error is None or isinstance(error, str) and re.fullmatch(r'[a-z][a-z0-9_]{0,95}', error),
            'heartbeat_error_code_invalid')
    require(type(value['cycle_complete']) is bool, 'heartbeat_cycle_flag_invalid')
    require(not value['cycle_complete'] or counts['contacts_scanned'] == counts['contacts_loaded'],
            'heartbeat_completed_cycle_inconsistent')
    # Deep JSON copy: callers cannot mutate nested values after validation.
    return json.loads(compact(value))


def read_payload(headers, stream):
    require(not headers.get('Transfer-Encoding') and not headers.get('Content-Encoding'),
            'heartbeat_encoding_not_supported')
    lengths = headers.get_all('Content-Length', [])
    require(len(lengths) == 1 and re.fullmatch(r'[0-9]{1,10}', lengths[0]), 'heartbeat_content_length_required', 411)
    size = int(lengths[0])
    require(1 <= size <= MAX_BODY, 'heartbeat_body_too_large', 413)
    kinds = headers.get_all('Content-Type', [])
    require(len(kinds) == 1 and kinds[0].lower().replace(' ', '') in
            ('application/json', 'application/json;charset=utf-8'), 'heartbeat_json_required', 415)
    raw = stream.read(size)
    require(len(raw) == size, 'heartbeat_incomplete_body')

    def pairs(items):
        result = {}
        for key, item in items:
            require(key not in result, 'heartbeat_duplicate_json_key')
            result[key] = item
        return result

    try:
        parsed = json.loads(raw.decode('utf-8'), object_pairs_hook=pairs,
                            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, UnicodeError) as exc:
        if isinstance(exc, HeartbeatRejected):
            raise
        raise HeartbeatRejected('heartbeat_invalid_json') from None
    return validate(parsed)


class HeartbeatStore:
    """One latest state per fixed account, plus last completed observation time."""
    def __init__(self, path=DEFAULT_DB):
        self.path = Path(path)
        require(self.path.is_absolute() and '..' not in self.path.parts
                and not any(p.is_symlink() for p in (self.path, *self.path.parents)), 'heartbeat_unsafe_path', 503)
        parent = self.path.parent.stat()
        require(parent.st_uid == os.geteuid() and stat.S_IMODE(parent.st_mode) == 0o700,
                'heartbeat_unsafe_parent', 503)
        self._parent_identity = (parent.st_dev, parent.st_ino)
        self._file_identity = None
        try:
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        except FileExistsError:
            pass
        else:
            os.close(fd)
        self._check_file()
        with self._connect() as db:
            version = db.execute('PRAGMA user_version').fetchone()[0]
            require(version in (0, 1), 'heartbeat_database_version_invalid', 503)
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript('''
              CREATE TABLE IF NOT EXISTS probe_state (
                account TEXT PRIMARY KEY CHECK(account='Amy'),
                snapshot_json TEXT NOT NULL,first_seen_at REAL NOT NULL,
                received_at REAL NOT NULL,last_complete_at REAL
              );
              PRAGMA user_version=1;
            ''')

    def _check_file(self):
        require(not any(p.is_symlink() for p in (self.path, *self.path.parents)), 'heartbeat_unsafe_path', 503)
        parent = self.path.parent.stat()
        require(parent.st_uid == os.geteuid() and stat.S_IMODE(parent.st_mode) == 0o700
                and (parent.st_dev, parent.st_ino) == self._parent_identity, 'heartbeat_unsafe_parent', 503)
        info = self.path.lstat()
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_uid == os.geteuid()
                and stat.S_IMODE(info.st_mode) == 0o600, 'heartbeat_unsafe_file', 503)
        identity = (info.st_dev, info.st_ino)
        require(self._file_identity is None or self._file_identity == identity, 'heartbeat_file_replaced', 503)
        self._file_identity = identity

    @contextmanager
    def _connect(self):
        self._check_file()
        db = sqlite3.connect(self.path, timeout=5)
        db.execute('PRAGMA synchronous=FULL')
        try:
            with db:
                yield db
        finally:
            db.close()

    def receive(self, value, now=None):
        value = validate(value)
        stamp = time.time() if now is None else now
        reply = server_clock(stamp)
        with self._connect() as db:
            db.execute('''INSERT INTO probe_state VALUES(?,?,?,?,?)
              ON CONFLICT(account) DO UPDATE SET snapshot_json=excluded.snapshot_json,
                received_at=excluded.received_at,
                last_complete_at=CASE WHEN excluded.last_complete_at IS NOT NULL
                  THEN excluded.last_complete_at ELSE probe_state.last_complete_at END''',
                       ('Amy', compact(value), stamp, stamp, stamp if value['cycle_complete'] else None))
        return {**reply, 'persisted': True}

    def status(self):
        with self._connect() as db:
            row = db.execute('SELECT snapshot_json,first_seen_at,received_at,last_complete_at FROM probe_state WHERE account=?',
                             ('Amy',)).fetchone()
        if row is None:
            return None
        return {'snapshot': json.loads(row[0]), 'first_seen_at': row[1],
                'received_at': row[2], 'last_complete_at': row[3]}
