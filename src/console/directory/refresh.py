"""Bounded read-only contact/name refresh; writes stay in the directory cache."""
from contextlib import closing
import math
import os
from pathlib import Path
import queue
import re
import sqlite3
import stat
import threading
import time

from console.inbox.store import read_database
from .errors import RefreshFailed, RefreshLimited
from .sources import text
from console.roles.triage import placeholder_name

COOLDOWN = 600
SINGLE_MARGIN = 3  # seconds kept free before the refresh deadline
_CONTACTS = '''CREATE TABLE IF NOT EXISTS directory_contacts (
    id TEXT PRIMARY KEY, kind TEXT NOT NULL CHECK(kind IN ('group','private','official')),
    first_seen REAL NOT NULL, last_seen REAL NOT NULL)'''
_ATTEMPT = '''CREATE TABLE IF NOT EXISTS directory_refresh_attempt (
    singleton INTEGER PRIMARY KEY CHECK(singleton=1), attempted_at REAL NOT NULL)'''
_TABLES = {'directory_contacts', 'directory_refresh_attempt'}
_UNAVAILABLE = '通讯录缓存暂时无法使用；本次没有重试，请检查独立缓存位置和权限'
_UNKNOWN = '联系人接口未给出可确认结果（可能超时或通信失败）；本次不重试，仍显示已有通讯录'
_TIMEOUT = '拉取通讯录超时，结果不明；本次不重试，仍显示已有通讯录'
_NAME_COLUMNS = {'nickname': 'TEXT', 'remark': 'TEXT', 'name_updated_at': 'REAL'}


def cached_name(row):
    from console.names.clean import first_name
    return first_name(row.get('remark'), row.get('nickname'))


def _brief_rows(payload, requested):
    """Mirror contact-discovery.discovery.apply_brief's exact ID/field checks.

    Partial replies are allowed; an absent field never deletes an old value.
    No provider diagnostics, credentials, member lists or raw replies survive.
    """
    if (not isinstance(payload, dict) or type(payload.get('ret')) is not int
            or payload['ret'] != 200 or not isinstance(payload.get('data'), list)
            or len(payload['data']) > len(requested)):
        raise ValueError('invalid brief response')
    rows = {}
    for entry in payload['data']:
        if not isinstance(entry, dict) or entry.get('userName') not in requested:
            raise ValueError('unexpected group identity')
        identity = entry['userName']
        if identity in rows:
            raise ValueError('duplicate group identity')
        values = {}
        for source, field in (('nickName', 'nickname'), ('remark', 'remark')):
            value = entry.get(source)
            if value is None or value == '':
                continue
            if (not isinstance(value, str) or not 0 < len(value) <= 128 or value != value.strip()
                    or any(ord(c) < 32 or ord(c) == 127 for c in value)):
                raise ValueError('invalid display name')
            value.encode('utf8')
            if not placeholder_name(value):
                values[field] = value
        rows[identity] = values
    return rows


def _check_path(path):
    # The cache is application-owned. Never follow an existing symlink, including
    # one in its parent directories, or write through a hard link to another DB.
    for part in (path, *path.parents):
        if part.is_symlink():
            raise ValueError('cache symlink')
    if path.exists():
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError('cache is not a private regular file')


def _safe_rows(payload):
    if (not isinstance(payload, dict) or type(payload.get('ret')) is not int
            or payload['ret'] != 200 or not isinstance(payload.get('data'), dict)):
        raise ValueError('invalid contact response')
    rows = {}
    for field, kind in (('friends', 'private'), ('chatrooms', 'group'), ('ghs', 'official')):
        values = payload['data'].get(field)
        if not isinstance(values, list) or len(values) > 100000:
            raise ValueError('invalid contact list')
        for identity in values:
            if (not isinstance(identity, str) or not identity or len(identity) > 1000
                    or identity.strip() != identity or any(ord(c) < 32 for c in identity)):
                raise ValueError('invalid contact id')
            identity.encode('utf8')
            if (kind == 'group') != identity.endswith('@chatroom'):
                raise ValueError('invalid contact kind')
            if identity in rows and rows[identity] != kind:
                raise ValueError('conflicting contact kind')
            rows[identity] = kind
    return rows


class RefreshCache:
    def __init__(self, path, clock=time.time, contact_fetcher=None, timeout=25,
                 readonly_client=None, readonly_client_factory=None, group_candidates=None):
        self.path = Path(path).absolute()
        self.clock = clock or time.time
        self.contact_fetcher = contact_fetcher
        self.readonly_client = readonly_client
        self.readonly_client_factory = readonly_client_factory
        self.group_candidates = group_candidates
        if not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or not 0 < timeout <= 60:
            raise ValueError('刷新超时须在 0 至 60 秒内')
        self.timeout = timeout

    def read(self, *, database=read_database):
        """No creation, migrations, chmod, or writes when displaying the cache."""
        _check_path(self.path)
        if not self.path.exists():
            return []
        with database(self.path) as db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='directory_contacts'").fetchone():
                return []
            columns = {r[1] for r in db.execute('PRAGMA table_info(directory_contacts)')}
            extra = [key for key in _NAME_COLUMNS if key in columns]
            keys = ['id', 'kind', 'first_seen', 'last_seen', *extra]
            return [dict(zip(keys, row)) for row in db.execute(
                'SELECT ' + ','.join(keys) + ' FROM directory_contacts ORDER BY id')]

    def _prepare(self):
        _check_path(self.path)
        if self.path.exists() and self.path.stat().st_size:
            # Refuse a misconfigured path to a source database before any chmod,
            # schema statement, or write. This cache has its own two tables only.
            with read_database(self.path) as db:
                tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if not tables <= _TABLES:
                    raise ValueError('not a directory cache')
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, 'O_NOFOLLOW', 0)
        try:
            fd = os.open(self.path, flags, 0o600)
        except FileExistsError:
            _check_path(self.path)
        else:
            os.close(fd)
        self.path.chmod(0o600)

    def _reserve(self, now):
        self._prepare()
        with closing(sqlite3.connect(self.path.as_uri() + '?mode=rw', uri=True, timeout=5)) as db:
            with db:
                # Cross-process serialization: reserve before any network call.
                db.execute('BEGIN IMMEDIATE')
                db.execute(_CONTACTS)
                db.execute(_ATTEMPT)
                previous = db.execute('SELECT attempted_at FROM directory_refresh_attempt WHERE singleton=1').fetchone()
                if previous and now - previous[0] < COOLDOWN:
                    raise RefreshLimited(math.ceil(COOLDOWN - (now - previous[0])))
                columns = {r[1] for r in db.execute('PRAGMA table_info(directory_contacts)')}
                for key, kind in _NAME_COLUMNS.items():
                    if key not in columns:
                        db.execute('ALTER TABLE directory_contacts ADD COLUMN ' + key + ' ' + kind)
                db.execute('INSERT INTO directory_refresh_attempt(singleton,attempted_at) VALUES (1,?) '
                           'ON CONFLICT(singleton) DO UPDATE SET attempted_at=excluded.attempted_at', (now,))

    def _call_once(self, function, deadline, *, brief=False):
        remaining = deadline - time.monotonic()
        timeout_message = '补充群名超时，结果不明；本次不重试，已取得的通讯录和群名仍保留' if brief else _TIMEOUT
        unknown_message = '群简要接口未给出可确认结果；本次不重试，已有名称仍保留' if brief else _UNKNOWN
        if remaining <= 0:
            raise RefreshFailed(timeout_message, 504)
        result = queue.Queue(maxsize=1)

        def run():
            try:
                payload = function()
            except TimeoutError:
                result.put(('timeout', None))
            except Exception:
                # Never retain or expose exception text, URLs, tokens or payloads.
                result.put(('failed', None))
            else:
                result.put(('ok', payload))

        worker = threading.Thread(target=run, daemon=True, name='directory-contact-refresh')
        worker.start()
        try:
            status, payload = result.get(timeout=remaining)
        except queue.Empty:
            raise RefreshFailed(timeout_message, 504) from None
        if status == 'timeout' or time.monotonic() >= deadline:
            raise RefreshFailed(timeout_message, 504)
        if status != 'ok' or payload is None:
            raise RefreshFailed(unknown_message)
        return payload

    def _save(self, rows, at, *, names=None):
        try:
            _check_path(self.path)
            with closing(sqlite3.connect(self.path.as_uri() + '?mode=rw', uri=True, timeout=5)) as db:
                with db:
                    if names is None:
                        db.executemany('INSERT INTO directory_contacts(id,kind,first_seen,last_seen) VALUES (?,?,?,?) '
                                       'ON CONFLICT(id) DO UPDATE SET kind=excluded.kind,last_seen=excluded.last_seen',
                                       ((i, k, at, at) for i, k in rows.items()))
                    else:
                        db.executemany('INSERT INTO directory_contacts '
                            '(id,kind,first_seen,last_seen,nickname,remark,name_updated_at) VALUES (?,\'group\',?,?,?,?,?) '
                            'ON CONFLICT(id) DO UPDATE SET last_seen=excluded.last_seen,'
                            'nickname=COALESCE(excluded.nickname,directory_contacts.nickname),'
                            'remark=COALESCE(excluded.remark,directory_contacts.remark),name_updated_at=excluded.name_updated_at',
                            ((i, at, at, value.get('nickname'), value.get('remark'), at)
                             for i, value in names.items() if cached_name(value)))
        except Exception:
            raise RefreshFailed('已请求通讯录，但独立缓存保存失败；本次不重试，仍显示已有通讯录', 503) from None

    def refresh(self):
        if self.contact_fetcher is None and self.readonly_client is None and self.readonly_client_factory is None:
            raise RefreshFailed('通讯录刷新暂不可用：尚未配置只读联系人接口，当前仅显示本地发现记录', 503)
        now = self.clock()
        try:
            self._reserve(now)
        except RefreshLimited:
            raise
        except Exception:
            raise RefreshFailed(_UNAVAILABLE, 503) from None
        deadline = time.monotonic() + self.timeout
        client = self.readonly_client
        if client is None and self.readonly_client_factory is not None:
            try:
                client = self.readonly_client_factory()
            except RefreshFailed:
                raise
            except Exception:
                raise RefreshFailed('通讯录刷新暂不可用：只读客户端配置无法读取', 503) from None
        fetcher = self.contact_fetcher if self.contact_fetcher is not None else client.fetch_contacts_list
        payload = self._call_once(fetcher, deadline)
        try:
            rows = _safe_rows(payload)
        except (ValueError, TypeError, UnicodeError, RecursionError):
            raise RefreshFailed('联系人接口返回格式无法确认；本次不重试，仍显示已有通讯录') from None
        refreshed_at = self.clock()
        self._save(rows, refreshed_at)
        notes, failures, named = [], [], set()
        # Read candidate IDs only after the contact snapshot is safely cached.
        # This includes groups known solely from callbacks in the discovery DB.
        try:
            if self.group_candidates is not None:
                candidates, notes = self.group_candidates()
            else:
                candidates = [r['id'] for r in self.read() if r['kind'] == 'group' and not cached_name(r)]
        except Exception:
            candidates = [i for i, k in rows.items() if k == 'group']
            notes = ['本地待补名群列表无法完整读取；本次只核对新拉取的群']
        candidates = sorted(set(candidates))
        valid = [i for i in candidates if re.fullmatch(r'[A-Za-z0-9_-]{1,128}@chatroom', i)]
        invalid = sorted(set(candidates) - set(valid))
        if invalid:
            failures.append(dict(ids=invalid, reason='群标识格式无法确认，未请求群简要接口'))
        brief_fetcher = getattr(client, 'get_brief_info', None)
        if valid and not callable(brief_fetcher):
            failures.append(dict(ids=valid, reason='尚未配置只读群简要接口，暂未补充群名'))
        elif valid:
            def ask(batch):
                payload = self._call_once(lambda: brief_fetcher(batch), deadline, brief=True)
                try:
                    names = _brief_rows(payload, batch)
                except (ValueError, TypeError, UnicodeError, RecursionError):
                    raise RefreshFailed('群简要接口返回格式无法确认；本次不重试，已有名称仍保留') from None
                self._save({}, self.clock(), names=names)
                accepted = {i for i, value in names.items() if cached_name(value)}
                named.update(accepted)
                return accepted

            unnamed, stopped = [], False
            for offset in range(0, len(valid), 50):
                batch = valid[offset:offset + 50]
                try:
                    missing = sorted(set(batch) - ask(batch))
                    if len(batch) > 1:
                        unnamed += missing
                    elif missing:
                        failures.append(dict(ids=missing, reason='群简要接口未返回有效名称或备注'))
                except RefreshFailed as error:
                    failures.append(dict(ids=batch, reason=str(error)))
                    # A failed/unknown request is never repeated, and no later
                    # batches are launched after it. Late workers cannot save.
                    if valid[offset + 50:]:
                        failures.append(dict(ids=valid[offset + 50:], reason='前一批未完成，本次未继续请求'))
                    stopped = True
                    break
            # The provider often omits names in multi-ID replies but answers the
            # same group asked alone. Ask each still-unnamed group once, in order,
            # while time remains; stop at the first failed or unknown reply.
            for index, identity in enumerate(unnamed if not stopped else []):
                if deadline - time.monotonic() < SINGLE_MARGIN:
                    failures.append(dict(ids=unnamed[index:], reason='补充群名时间不够，下次再查'))
                    break
                try:
                    if identity not in ask([identity]):
                        failures.append(dict(ids=[identity], reason='群简要接口未返回有效名称或备注'))
                except RefreshFailed as error:
                    failures.append(dict(ids=unnamed[index:], reason=str(error)))
                    break
        missing = sorted(set(candidates) - named)
        return dict(refreshed_at=refreshed_at, received=len(rows),
                    by_kind={k: sum(v == k for v in rows.values()) for k in ('group', 'private', 'official')},
                    groups_named=len(named), missing_group_ids=missing, group_failures=failures,
                    complete=not missing and not notes, notes=notes,
                    message=('通讯录已刷新，部分群名尚未取得' if missing or notes else '通讯录及群名已刷新') +
                            '；仅更新独立目录缓存，没有接入采集或发送消息')
