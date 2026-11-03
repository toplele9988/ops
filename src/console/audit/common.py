"""Bounded read-only queries and metadata-only findings."""
from contextlib import closing, contextmanager
from contextvars import ContextVar
import hashlib
from pathlib import Path
import re
import sqlite3
import time

from console.roles.common import safe

LEVELS = dict(high=0, medium=1, low=2, ok=3)
_DEADLINE = ContextVar('audit_query_deadline', default=None)


@contextmanager
def query_budget(seconds):
    token = _DEADLINE.set(time.monotonic() + seconds)
    try:
        yield
    finally:
        _DEADLINE.reset(token)


@contextmanager
def read_db(path):
    path = Path(path).resolve()
    if not path.is_file():
        raise FileNotFoundError
    wal, shm = Path(str(path) + '-wal'), Path(str(path) + '-shm')
    journal = Path(str(path) + '-journal')
    has_wal = wal.exists() and wal.stat().st_size > 0
    if has_wal and not shm.exists():
        # Do not create a shared-memory file in a live source directory.
        raise OSError('source snapshot unavailable')
    if not has_wal and journal.exists() and journal.stat().st_size:
        # Immutable skips rollback recovery/locking. Never use it while a
        # journal may contain an uncommitted writer's original pages.
        raise OSError('source rollback snapshot unavailable')
    stat = path.stat()
    before = (stat.st_mtime_ns, stat.st_size, stat.st_ino)
    uri = path.as_uri() + '?mode=ro' + ('' if has_wal else '&immutable=1')
    deadline = _DEADLINE.get() or time.monotonic() + .5
    with closing(sqlite3.connect(uri, uri=True, timeout=.02)) as db:
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        db.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
        db.execute('BEGIN')
        yield db
    stat = path.stat()
    if not has_wal and ((wal.exists() and wal.stat().st_size > 0)
                        or (journal.exists() and journal.stat().st_size > 0)
                        or before != (stat.st_mtime_ns, stat.st_size, stat.st_ino)):
        raise OSError('source snapshot changed')


def sample_id(value):
    # A locator for comparing results, never a reversible inbox key or a name.
    return 'ref:' + hashlib.sha256(str(value).encode('utf8')).hexdigest()[:16]


def finding(key, title, level, count, meaning, remedy, samples=(), **details):
    if level not in LEVELS or type(count) is not int or count < 0:
        raise ValueError('invalid finding')
    # Only fixed application routes survive path redaction, never arbitrary
    # paths, query strings, attachment locations or URLs from a source.
    routes = {}
    def keep_route(match):
        token = f'AUDITROUTE{len(routes)}PLACEHOLDER'
        routes[token] = match[0]
        return token
    remedy = re.sub(r'(?<![\w/])/(?:inbox|products|freight|suppliers|deals|triage|customers)(?=$|\s|[，。；、）])', keep_route, remedy)
    remedy = safe(remedy, 1000)
    for token, route in routes.items():
        remedy = remedy.replace(token, route)
    return dict(key=key, title=safe(title, 100), level=level, count=count,
                meaning=safe(meaning, 1000), remedy=remedy,
                samples=list(samples)[:5], **details)


def failure_reason(error):
    # Never redact and echo an arbitrary exception: it may contain a bare secret.
    if isinstance(error, FileNotFoundError):
        return '来源文件不存在'
    if isinstance(error, PermissionError):
        return '来源没有读取权限'
    if isinstance(error, TimeoutError) or isinstance(error, sqlite3.OperationalError) and str(error) == 'interrupted':
        return '统计超出本项时间预算，请稍后重试'
    if isinstance(error, sqlite3.DatabaseError):
        return '数据库结构不兼容、损坏或正忙'
    if isinstance(error, (ValueError, TypeError, KeyError, AttributeError)):
        return '来源字段缺失或格式不兼容'
    if isinstance(error, OSError):
        return '来源快照暂不可读或读取期间发生变化'
    return '来源暂不可用'
