"""Bounds shared by all overview sources, on top of pipeline's read-only snapshots."""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import sqlite3
import stat
import time

from console.pipeline.readers import read_db

MAX_DB_BYTES = 2 * 1024 ** 3
MAX_VALUE_BYTES = 2 * 1024 ** 2
MAX_JSON_BYTES = 32768
UNREADABLE = '未知：来源缺失、表结构不符、权限不足、读取超时或超过大小限制，请管理员核对。'


def timestamp(value, now):
    if type(value) in (int, float) and math.isfinite(value) and 0 < value <= now:
        return value
    return None


def iso(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat().replace('+00:00', 'Z') if value is not None else None


def check_db(path):
    path = Path(path)
    for candidate in (path, *(Path(str(path) + suffix) for suffix in ('-wal', '-shm', '-journal'))):
        try:
            info = candidate.lstat()
        except FileNotFoundError:
            if candidate == path:
                raise
            continue
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_DB_BYTES:
            raise ValueError('unsafe_or_large_source')


@contextmanager
def database(path, seconds):
    check_db(path)
    with read_db(path, seconds) as db:
        db.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, MAX_VALUE_BYTES)
        db.execute('PRAGMA temp_store=MEMORY')
        yield db


def pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError('duplicate_key')
        result[key] = value
    return result


def json_file(path, seconds):
    deadline = time.monotonic() + seconds
    # NONBLOCK prevents a substituted FIFO from hanging before fstat rejects it.
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_JSON_BYTES:
            raise ValueError('unsafe_or_large_source')
        raw = stream.read(MAX_JSON_BYTES + 1)
        after = os.fstat(stream.fileno())
    if (len(raw) > MAX_JSON_BYTES or (before.st_mtime_ns, before.st_size) !=
            (after.st_mtime_ns, after.st_size)):
        raise ValueError('changed_or_large_source')
    value = json.loads(raw, object_pairs_hook=pairs,
                       parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite')))
    if not isinstance(value, dict) or time.monotonic() >= deadline:
        raise ValueError('invalid_or_timeout')
    return value
