"""Owner-triggered, bounded order reads; payment confirmation stays in fulfilment."""
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import threading
import time

from console.archive import validation as v
from console.archive.store import ARCHIVE_PATH
from .app_credentials import Credentials
from .errors import OpenError
from .protocol import Client
from .store import Store, check_path

_LOCKS = {}
_LOCKS_GUARD = threading.Lock()
DEFAULT_LIMIT = 1000


class AlibabaOpen:
    def __init__(self, archive_path=ARCHIVE_PATH, *, db_path=None, key_path=None,
                 credentials_path=None, credentials=None, store=None, client=None,
                 candidates=None, clock=None, network_enabled=False, page_size=50,
                 initial_days=30, max_pages=200, **client_options):
        self.clock = clock or time.time
        self.store = store or Store(db_path, key_path=key_path, clock=self.clock)
        self.credentials = credentials or Credentials(credentials_path)
        archive_file = Path(os.path.abspath(archive_path))
        credential_file = getattr(self.credentials, 'path', None)
        files = [Path(str(base) + suffix) for base in (self.store.path, archive_file)
                 for suffix in ('', '-wal', '-shm', '-journal', '.lock', '.operation.lock')]
        files.append(self.store.key_path)
        if credential_file is not None:
            files.append(Path(credential_file).absolute())
        for index, file in enumerate(files):
            for other in files[index + 1:]:
                if (os.path.abspath(file) == os.path.abspath(other) or
                        file.exists() and other.exists() and file.samefile(other)):
                    raise v.BadRequest('阿里授权、密钥、凭据和案子档案必须使用不同的文件')
        self.client = client or Client(self.credentials, clock=self.clock,
                                      enable_network=network_enabled, **client_options)
        self.network_enabled = bool(network_enabled or client is not None)
        self.client.before_request = self._before_request
        self._candidates = candidates
        self.archive_path = archive_path
        self.page_size = max(1, min(100, int(page_size)))
        self.initial_days = max(1, min(3650, int(initial_days)))
        self.max_pages = max(1, min(1000, int(max_pages)))

    @property
    def candidates(self):
        if self._candidates is None:
            from .candidates import Candidates
            self._candidates = Candidates(self.store, archive_path=self.archive_path)
        return self._candidates

    def _values(self):
        with self.store.read() as db:
            return {r['key']: json.loads(r['value']) for r in db.execute('SELECT key,value FROM kv')}

    def _set(self, **values):
        with self.store.write() as db:
            for key, value in values.items():
                db.execute('INSERT OR REPLACE INTO kv VALUES(?,?)', (key, json.dumps(value)))

    @contextmanager
    def _operation(self):
        path = Path(str(self.store.path) + '.operation.lock')
        with _LOCKS_GUARD:
            lock = _LOCKS.setdefault(str(path), threading.Lock())
        if not lock.acquire(blocking=False):
            raise v.Conflict('阿里接入正在处理上一项操作，请先刷新状态再操作')
        fd = None
        try:
            check_path(path)
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            check_path(path)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise v.Conflict('阿里接入正在处理上一项操作，请先刷新状态再操作') from None
            yield
        finally:
            if fd is not None:
                os.close(fd)
            lock.release()

    def _before_request(self):
        now = self.clock()
        day = datetime.fromtimestamp(now, timezone.utc).date().isoformat()
        with self.store.write() as db:
            values = {r['key']: json.loads(r['value']) for r in db.execute('SELECT key,value FROM kv')}
            count = values.get('calls_today', 0) if values.get('calls_day') == day else 0
            if count >= values.get('daily_limit', DEFAULT_LIMIT):
                raise v.Conflict('今天的阿里接口调用次数已用完，请明天再试或由管理员调整上限')
            for key, value in dict(calls_day=day, calls_today=count + 1).items():
                db.execute('INSERT OR REPLACE INTO kv VALUES(?,?)', (key, json.dumps(value)))

    def _status(self):
        now, values = self.clock(), self._values()
        configured = self.credentials.status()
        metadata = self.store.metadata()
        uncertain = bool(values.get('refresh_pending') or values.get('authorization_pending'))
        key_ready = True
        if metadata['has_token']:
            try:
                check_path(self.store.key_path)
                key_ready = self.store.key_path.is_file()
            except v.BadRequest:
                key_ready = False
        expired_without_refresh = (metadata['expires_at'] is not None and metadata['expires_at'] <= now and
                                   metadata['refresh_expires_at'] is not None and metadata['refresh_expires_at'] <= now)
        authorized = bool(metadata['has_token'] and not metadata['reauthorize'] and not uncertain and
                          not expired_without_refresh and key_ready)
        paused = values.get('paused', True)
        day = datetime.fromtimestamp(now, timezone.utc).date().isoformat()
        if not configured['configured']:
            next_step = configured['message']
        elif not key_ready:
            next_step = '授权加密密钥缺失或权限不正确，请管理员恢复原密钥；无法恢复时重新授权'
        elif not self.network_enabled:
            next_step = '接入仍处于离线保护状态，请总指挥核对接口契约后再启用联网'
        elif not authorized:
            next_step = '请打开授权链接，在浏览器完成授权，再把地址栏里的授权码粘贴到这里'
        elif paused:
            next_step = '订单读取已暂停；准备好后取消暂停，再手动读取一次'
        elif values.get('next_poll_at', 0) > now:
            next_step = '上次订单读取未完成，请等到可重试时间后手动读取；水位没有推进'
        else:
            next_step = '可以手动读取订单；已付款订单只生成候选，仍需到案子里核对并确认收款'
        return dict(configured=configured['configured'], authorized=authorized,
                    token_expires_in=max(0, int((metadata['expires_at'] or now) - now)),
                    last_poll_at=values.get('last_poll_at'),
                    last_poll_result=values.get('last_poll_result', '还没有读取订单'),
                    next_poll_at=values.get('next_poll_at'), next_step=next_step,
                    paused=paused, daily_limit=values.get('daily_limit', DEFAULT_LIMIT),
                    calls_today=values.get('calls_today', 0) if values.get('calls_day') == day else 0,
                    network_enabled=self.network_enabled)

    def read(self, resource):
        if resource == 'status':
            return self._status()
        if resource == 'candidates':
            return self.candidates.list()
        if resource == 'authorization':
            return dict(url=self.client.authorization_url(state=secrets.token_urlsafe(24)))
        raise v.BadRequest('不支持的阿里接入读取操作')

    def _access_token(self, who):
        metadata, values = self.store.metadata(), self._values()
        if (not metadata['has_token'] or metadata['reauthorize'] or values.get('refresh_pending') or
                values.get('authorization_pending')):
            raise OpenError('阿里授权需要重新确认，请在浏览器重新授权后粘贴新的授权码', reauthorize=True)
        token = self.store.token()
        if token['expires_at'] > self.clock() + 60:
            return token['access_token']
        if not token.get('refresh_token') or (token.get('refresh_expires_at') is not None and
                                             token['refresh_expires_at'] <= self.clock()):
            self.store.mark_reauthorize(who)
            raise OpenError('阿里授权已到期，请在浏览器重新授权后粘贴新的授权码', reauthorize=True)
        # Persist before a rotating-token request; a crash must never replay it.
        self._set(refresh_pending=True)
        try:
            refreshed = self.client.refresh(token['refresh_token'])
            self.store.save_token(refreshed, who)
        except Exception:
            self.store.mark_reauthorize(who)
            raise OpenError('阿里授权刷新未完成，请重新授权；系统不会重复使用旧刷新令牌', reauthorize=True) from None
        self._set(refresh_pending=False)
        return self.store.token()['access_token']

    def _authorize(self, fields, who):
        code = fields.get('code', '').strip()
        if not code or len(code) > 4096 or re.search(r'[\s\x00-\x1f\x7f]', code):
            raise v.BadRequest('请粘贴地址栏里的授权码，不要粘贴整个网址')
        digest = hashlib.sha256(code.encode('utf8')).hexdigest()
        with self.store.write() as db:
            key = 'code_attempt:' + digest
            if db.execute('SELECT 1 FROM kv WHERE key=?', (key,)).fetchone():
                raise v.Conflict('这个授权码已经提交过，请先刷新状态；需要时重新授权取得新的授权码')
            db.execute('INSERT INTO kv VALUES(?,?)', (key, 'true'))
        self._set(authorization_pending=True)
        try:
            token = self.client.exchange(code)
            self.store.save_token(token, who)
            self._set(refresh_pending=False, authorization_pending=False)
        except Exception:
            self.store.mark_reauthorize(who)
            self.store.audit('exchange', who, 'unknown')
            raise OpenError('授权码提交未完成，请先刷新状态；如仍未授权，请在浏览器重新授权取得新的授权码，系统不会自动重试', unknown=True) from None
        self.store.audit('exchange', who, 'ok')
        return dict(ok=True)

    def _poll(self, who):
        now, values = self.clock(), self._values()
        if values.get('paused', True):
            raise v.Conflict('订单读取已暂停，请先取消暂停再手动读取')
        if values.get('next_poll_at', 0) > now:
            raise v.Conflict('订单读取正在等待退避时间，请稍后刷新状态再手动读取')
        self._set(last_poll_at=now, last_poll_result='订单正在读取，尚未完成')
        try:
            token = self._access_token(who)
            until = int(now * 1000)
            watermark = values.get('watermark_ms', values.get('initial_since_ms',
                                   max(0, until - self.initial_days * 86400000)))
            if 'watermark_ms' not in values and 'initial_since_ms' not in values:
                self._set(initial_since_ms=watermark)
            if watermark > until:
                raise OpenError('本机时钟早于已读取水位，请管理员先校准时钟；水位没有回退')
            since = max(0, watermark - 120000)
            seen = set()
            with self.store.write() as db:
                db.execute('CREATE TABLE IF NOT EXISTS observed_orders (order_id TEXT PRIMARY KEY,modified_at_ms INTEGER NOT NULL)')
            updated = 0
            for page in range(self.max_pages):
                listing = self.client.orders(token, since, until, page, self.page_size)
                if not isinstance(listing, dict) or type(listing.get('has_more')) is not bool or not isinstance(listing.get('orders'), list):
                    raise OpenError('订单列表格式无法核对，请管理员检查接口契约')
                if len(listing['orders']) > self.page_size or (listing['has_more'] and not listing['orders']):
                    raise OpenError('订单分页不完整，请管理员检查接口契约')
                for stub in listing['orders']:
                    oid, modified = stub.get('order_id'), stub.get('modified_at_ms')
                    if (not isinstance(oid, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', oid) or
                            type(modified) is not int or not 0 < modified <= until):
                        raise OpenError('订单编号或修改时间无法核对，请管理员检查接口契约')
                    if oid in seen:
                        raise OpenError('订单分页出现重复记录，水位没有推进，请管理员检查分页契约')
                    seen.add(oid)
                    with self.store.read() as db:
                        old = db.execute('SELECT modified_at_ms FROM observed_orders WHERE order_id=?', (oid,)).fetchone()
                    if old and old['modified_at_ms'] >= modified and not self.candidates.needs_recovery(oid):
                        continue
                    detail = self.client.order(token, oid)
                    if detail.get('order_id') != oid:
                        raise OpenError('订单详情与列表编号不一致，水位没有推进，请管理员核对')
                    detail['modified_at_ms'] = modified
                    outcome = self.candidates.upsert(detail, who)
                    updated += int(bool(outcome.get('changed') or outcome.get('created')))
                    with self.store.write() as db:
                        db.execute('INSERT OR REPLACE INTO observed_orders VALUES(?,?)', (oid, modified))
                if not listing['has_more']:
                    break
            else:
                raise OpenError('订单页数超过本次读取上限，水位没有推进，请管理员核对时间范围')
            self._set(watermark_ms=until, failures=0, next_poll_at=0,
                      last_poll_result='订单读取完成；候选仍需人工核对并确认收款')
            self.store.audit('poll', who, 'ok')
            return dict(ok=True, updated=updated)
        except Exception as error:
            if isinstance(error, OpenError) and error.reauthorize:
                self.store.mark_reauthorize(who)
            failures = min(10, int(values.get('failures', 0)) + 1)
            self._set(failures=failures, next_poll_at=self.clock() + min(3600, 30 * 2 ** (failures - 1)),
                      last_poll_result='订单读取未完成，水位没有推进；请按提示核对后再手动读取')
            self.store.audit('poll', who, 'failed')
            if isinstance(error, (v.BadRequest, v.Conflict, OpenError)):
                raise
            raise OpenError('订单读取未完成，水位没有推进，请稍后手动读取或请管理员检查服务', retryable=True) from None

    def write(self, operation, fields, who):
        allowed = {'authorize': ('code', 'confirm'), 'poll': ('confirm',),
                   'settings': ('paused', 'daily_limit', 'confirm'),
                   'claim': ('order_id', 'deal_id', 'confirm')}
        if operation not in allowed:
            raise v.BadRequest('不支持的阿里接入操作')
        v.whitelist(fields, allowed[operation])
        if fields.get('confirm') != '1':
            raise v.BadRequest('请核对后点击确认，系统才会执行这项操作')
        with self._operation():
            if operation == 'settings':
                values = {}
                if 'paused' in fields:
                    values['paused'] = v.choice(fields['paused'], ('0', '1')) == '1'
                if 'daily_limit' in fields:
                    values['daily_limit'] = v.integer(fields['daily_limit'], maximum=100000)
                if not values:
                    raise v.BadRequest('请填写暂停状态或每日调用上限')
                self._set(**values)
                self.store.audit('settings', who, 'ok')
                return dict(ok=True)
            if operation == 'authorize':
                return self._authorize(fields, who)
            if operation == 'claim':
                return self.candidates.claim(fields.get('order_id'), fields.get('deal_id'), who)
            return self._poll(who)
