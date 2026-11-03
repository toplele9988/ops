"""Private, encrypted authorization state and serialized order observations."""
from contextlib import closing, contextmanager
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import stat
import sys
import threading
import time

from console.archive import validation as v

DEFAULT_PATH = '/var/lib/imgw/ops-app/alibaba-open.sqlite3'
_LOCK = threading.RLock()
SCHEMA = (
    'CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY,value TEXT NOT NULL)',
    '''CREATE TABLE IF NOT EXISTS token_state (
       id INTEGER PRIMARY KEY CHECK(id=1), encrypted BLOB NOT NULL,
       expires_at REAL NOT NULL, refresh_expires_at REAL, reauthorize INTEGER NOT NULL DEFAULT 0)''',
    '''CREATE TABLE IF NOT EXISTS order_candidates (
       order_id TEXT PRIMARY KEY, deal_id INTEGER NOT NULL,
       claim_state TEXT NOT NULL CHECK(claim_state IN ('unclaimed','claimed')),
       amount_fen INTEGER NOT NULL, currency TEXT NOT NULL, buyer_id TEXT NOT NULL,
       payment_status TEXT NOT NULL, paid_at_ms INTEGER, modified_at_ms INTEGER NOT NULL,
       updated_at REAL NOT NULL)''',
    '''CREATE TABLE IF NOT EXISTS audit (
       id INTEGER PRIMARY KEY, at REAL NOT NULL, who TEXT NOT NULL,
       operation TEXT NOT NULL, result TEXT NOT NULL)''',
)


def check_path(path, *, private=True):
    path = Path(path).absolute()
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise v.BadRequest('阿里授权存储路径不可用，请管理员检查文件权限')
    try:
        info = path.stat()
    except FileNotFoundError:
        return
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or
            (private and (stat.S_IMODE(info.st_mode) != 0o600 or info.st_uid != os.geteuid()))):
        raise v.BadRequest('阿里授权存储权限不正确，请管理员设置为当前服务用户可读写的 0600 文件')


def _crypto():
    # Match the console's optional system-package fallback without changing venv precedence.
    if importlib.util.find_spec('cryptography') is None:
        fallback = '/usr/lib/python3/dist-packages'
        if Path(fallback).is_dir() and fallback not in sys.path:
            sys.path.append(fallback)
    try:
        from cryptography.fernet import Fernet
        return Fernet
    except ImportError:
        raise v.BadRequest('授权加密组件未安装，请管理员安装 cryptography 后重新授权') from None


@contextmanager
def file_lock(path):
    check_path(path)
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600:
            raise v.BadRequest('阿里授权锁文件权限不正确，请管理员检查')
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


class Store:
    def __init__(self, path=None, *, key_path=None, clock=None):
        self.path = Path(path or os.environ.get('OPS_ALIBABA_OPEN_DB', DEFAULT_PATH)).absolute()
        self.key_path = Path(key_path or os.environ.get('OPS_ALIBABA_OPEN_KEY_PATH', str(self.path) + '.key')).absolute()
        self.clock = clock or time.time
        if self.path == self.key_path:
            raise v.BadRequest('授权数据库和加密密钥必须分别保存')

    def _check(self):
        for path in (self.path, *(Path(str(self.path) + suffix) for suffix in ('-wal', '-shm', '-journal'))):
            check_path(path)

    @staticmethod
    def _configure(db):
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA busy_timeout=10000')

    @contextmanager
    def read(self):
        self._check()
        if not self.path.exists():
            with closing(sqlite3.connect(':memory:')) as db:
                self._configure(db)
                for sql in SCHEMA:
                    db.execute(sql)
                yield db
            return
        with closing(sqlite3.connect(self.path.as_uri() + '?mode=ro', uri=True)) as db:
            self._configure(db)
            db.execute('PRAGMA query_only=ON')
            db.execute('BEGIN')
            yield db

    @contextmanager
    def write(self):
        with _LOCK:
            self._check()
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with file_lock(Path(str(self.path) + '.lock')):
                fd = os.open(self.path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
                os.close(fd)
                self._check()
                with closing(sqlite3.connect(self.path, timeout=10)) as db:
                    self._configure(db)
                    db.execute('PRAGMA journal_mode=DELETE')
                    db.execute('BEGIN IMMEDIATE')
                    for sql in SCHEMA:
                        db.execute(sql)
                    db.commit()
                    db.execute('BEGIN IMMEDIATE')
                    try:
                        yield db
                        db.commit()
                    except BaseException:
                        db.rollback()
                        raise
                    finally:
                        self._check()

    def _cipher(self, *, create=False):
        check_path(self.key_path)
        if create:
            self.key_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            fd = os.open(self.key_path, os.O_RDONLY | os.O_NOFOLLOW)
        except FileNotFoundError:
            if not create:
                raise v.BadRequest('授权加密密钥缺失，请管理员恢复密钥或重新授权') from None
            raw = _crypto().generate_key()
            try:
                fd = os.open(self.key_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
            except FileExistsError:
                return self._cipher()
            try:
                os.write(fd, raw)
                os.fsync(fd)
            finally:
                os.close(fd)
            return _crypto()(raw)
        try:
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or
                    info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size > 100):
                raise v.BadRequest('授权加密密钥权限不正确，请管理员检查')
            raw = os.read(fd, 101)
        finally:
            os.close(fd)
        try:
            return _crypto()(raw)
        except (ValueError, TypeError):
            raise v.BadRequest('授权加密密钥无效，请管理员恢复密钥或重新授权') from None

    def metadata(self):
        with self.read() as db:
            row = db.execute('SELECT expires_at,refresh_expires_at,reauthorize FROM token_state WHERE id=1').fetchone()
            return dict(has_token=bool(row), expires_at=row['expires_at'] if row else None,
                        refresh_expires_at=row['refresh_expires_at'] if row else None,
                        reauthorize=bool(row['reauthorize']) if row else False)

    def token(self):
        with self.read() as db:
            row = db.execute('SELECT * FROM token_state WHERE id=1').fetchone()
        if not row:
            return None
        try:
            payload = json.loads(self._cipher().decrypt(row['encrypted']))
            if (not isinstance(payload, dict) or not isinstance(payload.get('access_token'), str)
                    or not payload['access_token'] or not isinstance(payload.get('refresh_token'), str)):
                raise ValueError
            return payload
        except v.BadRequest:
            raise
        except Exception:
            raise v.BadRequest('保存的授权无法解密，请管理员恢复密钥或重新授权') from None

    def save_token(self, token, who='system'):
        now = self.clock()
        try:
            fields = {}
            for key in ('access_token', 'refresh_token'):
                value = token[key]
                if not isinstance(value, str) or not (1 if key == 'access_token' else 0) <= len(value) <= 16384:
                    raise ValueError
                fields[key] = value
            expires_at = token.get('expires_at', now + token.get('expires_in', 0))
            refresh_expires_at = token.get('refresh_expires_at')
            if refresh_expires_at is None and token.get('refresh_expires_in') is not None:
                refresh_expires_at = now + token['refresh_expires_in']
            if (type(expires_at) not in (int, float) or not now < expires_at <= now + 10 * 366 * 86400 or
                    refresh_expires_at is not None and (type(refresh_expires_at) not in (int, float) or
                    not now <= refresh_expires_at <= now + 10 * 366 * 86400)):
                raise ValueError
            fields.update(expires_at=expires_at, refresh_expires_at=refresh_expires_at)
        except (KeyError, TypeError, ValueError, OverflowError):
            raise v.BadRequest('授权返回缺少有效令牌或有效期，请重新授权') from None
        with self.write() as db:
            encrypted = self._cipher(create=True).encrypt(json.dumps(fields).encode('utf8'))
            db.execute('INSERT OR REPLACE INTO token_state VALUES(1,?,?,?,0)',
                       (encrypted, expires_at, refresh_expires_at))
            self.audit_row(db, 'token_saved', who, 'ok')

    def mark_reauthorize(self, who='system'):
        with self.write() as db:
            db.execute('UPDATE token_state SET reauthorize=1 WHERE id=1')
            self.audit_row(db, 'refresh', who, 'reauthorize')

    def audit_row(self, db, operation, who, result):
        if operation not in ('token_saved', 'refresh', 'poll', 'exchange', 'settings', 'candidate', 'claim') or result not in (
                'ok', 'failed', 'unknown', 'reauthorize', 'skipped'):
            raise v.BadRequest('审计操作无效')
        db.execute('INSERT INTO audit(at,who,operation,result) VALUES(?,?,?,?)',
                   (self.clock(), who, operation, result))

    def audit(self, operation, who, result):
        with self.write() as db:
            self.audit_row(db, operation, who, result)
