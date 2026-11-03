"""Explicit, audited WAHA login operations. No messaging or background write retries."""
import base64
from contextlib import closing
import errno
import json
import math
from pathlib import Path
import re
import socket
import sqlite3
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import ProxyHandler, Request, build_opener

from console.archive.store import ARCHIVE_PATH
from .common import timestamp
from .whatsapp import CONFIG_PATH, NoRedirect, SESSION_STATES, WhatsAppAdapter, local_url

LABELS = {
    'STOPPED': '未启动', 'STARTING': '正在启动', 'SCAN_QR_CODE': '等待扫码',
    'PASSKEY_REQUIRED': '需要通行密钥', 'PASSKEY_CONFIRMATION_REQUIRED': '等待确认通行密钥',
    'WORKING': '已登录', 'FAILED': '登录失败', 'UNKNOWN': '状态未知',
}
NEXT_STEPS = {
    'STOPPED': '会话没有启动，请先点登录',
    'STARTING': '正在启动，请稍候，页面将刷新状态',
    'SCAN_QR_CODE': '请用手机 WhatsApp 的“已关联的设备”扫描二维码',
    'PASSKEY_REQUIRED': '请在手机 WhatsApp 中完成通行密钥验证，再刷新状态',
    'PASSKEY_CONFIRMATION_REQUIRED': '请在手机 WhatsApp 中确认通行密钥，再刷新状态',
    'WORKING': '已经登录，可以返回收件箱查看消息',
    'FAILED': '需要重新扫码登录，请点登录后等待二维码',
    'UNKNOWN': '暂时无法识别会话状态，请稍后刷新或联系管理员',
}
PNG = b'\x89PNG\r\n\x1a\n'
MAX_RESPONSE = 2 * 1024 * 1024
REQUEST_BUDGET = 6.0  # Leaves room for local authentication, form parsing and audit I/O.


class LoginError(Exception):
    def __init__(self, message, *, code='unavailable', status_code=503, retry_after=None, outcome=None):
        super().__init__(message)
        self.code, self.status_code = code, status_code
        self.retry_after, self.outcome = retry_after, outcome


def _unknown():
    return LoginError('结果不明，请稍后刷新状态；不要立即重复点击', code='outcome_unknown', outcome='unknown')


def _failure(error):
    """Classify only exception types/status codes. Never use remote error text."""
    if isinstance(error, HTTPError):
        if error.code in (401, 403):
            return LoginError('WAHA 密钥不正确或无权限，请联系管理员', code='unauthorized')
        if error.code == 404:
            return LoginError('WAHA 会话不存在，请联系管理员检查配置', code='session_missing')
        return LoginError('WAHA 服务暂时不可用，请稍后刷新', code='upstream_error')
    reason = error.reason if isinstance(error, URLError) else error
    if isinstance(reason, (TimeoutError, socket.timeout)):
        return LoginError('WAHA 响应超时，请稍后刷新状态', code='timeout')
    if isinstance(reason, ConnectionRefusedError) or getattr(reason, 'errno', None) == errno.ECONNREFUSED:
        return LoginError('WAHA 服务没有启动或未监听，请联系管理员', code='service_not_started')
    return LoginError('无法连接 WAHA 服务，请联系管理员检查服务', code='unreachable')


def local_login_fetch(method, url, headers, timeout):
    """Loopback only, no redirects/proxies, a closed allowlist and one attempt.

    Kept separate from whatsapp.local_fetch, whose GET-only guarantee is unchanged.
    The service also bounds elapsed time even if a transport ignores socket timeouts.
    """
    local_url(url)
    parsed = urlsplit(url)
    session = r'[^/]+'
    allowed = (
        method == 'GET' and not parsed.query and re.fullmatch('/api/sessions/' + session, parsed.path)
        or method == 'GET' and parsed.query == 'format=image'
        and re.fullmatch('/api/' + session + '/auth/qr', parsed.path)
        or method == 'POST' and not parsed.query
        and re.fullmatch('/api/sessions/' + session + '/(?:start|restart|logout)', parsed.path)
    )
    if not allowed:
        raise ValueError('登录接口不可用')
    opener = build_opener(ProxyHandler({}), NoRedirect())
    request = Request(url, data=b'' if method == 'POST' else None, headers=headers, method=method)
    with opener.open(request, timeout=timeout) as response:
        raw = response.read(MAX_RESPONSE + 1)
        if len(raw) > MAX_RESPONSE:
            raise ValueError('登录响应不可用')
        if response.headers.get_content_type() == 'image/png':
            return raw
        return json.loads(raw) if raw else None


class WhatsAppLogin:
    def __init__(self, archive_path=ARCHIVE_PATH, *, config_path=None, fetch=None, clock=None,
                 inbox=None, stat_fn=None, group_gid=None, allow_writes=True):
        archive_path = Path(archive_path)
        # An explicit test store must not silently read the production configuration.
        if config_path is None:
            config_path = CONFIG_PATH if archive_path == ARCHIVE_PATH else archive_path.parent / 'disabled-whatsapp.json'
        self.config = WhatsAppAdapter(config_path, stat_fn=stat_fn, group_gid=group_gid)
        self.fetch = local_login_fetch if fetch is None else fetch
        self.clock = time.time if clock is None else clock
        self.allow_writes, self.inbox = allow_writes, inbox
        if inbox is None and archive_path == ARCHIVE_PATH:
            from . import shared_inbox
            self.inbox = shared_inbox(whatsapp_config_path=config_path, stat_fn=stat_fn, group_gid=group_gid)
        self.audit_path = archive_path.with_name(archive_path.stem + '-whatsapp-login.sqlite3')
        self._lock = threading.Lock()
        self._slots = threading.BoundedSemaphore(4)
        self._observed = {}

    def _configuration(self):
        try:
            with self._lock:
                config = self.config._configuration()
                # Names are metadata, not arbitrary untrusted strings from WAHA.
                if not re.fullmatch(r'[A-Za-z0-9_.-]{1,128}', config['session']) or config['session'] in ('.', '..'):
                    raise ValueError
                return dict(config)
        except Exception:
            raise LoginError('WhatsApp 桥接配置不可用，请联系管理员检查配置权限和本机地址', code='configuration') from None

    def _call(self, config, method, path, deadline, *, png=False):
        remaining = min(4.0, deadline - time.monotonic())
        if remaining <= 0:
            raise LoginError('WAHA 响应超时，请稍后刷新状态', code='timeout')
        if not self._slots.acquire(blocking=False):
            raise LoginError('WAHA 请求繁忙，请稍后刷新状态', code='busy')
        done, result = threading.Event(), []
        headers = {'X-Api-Key': config['waha_api_key'], 'Accept': 'image/png' if png else 'application/json'}

        def perform():
            try:
                available = min(remaining, deadline - time.monotonic())
                if available <= 0:
                    raise TimeoutError()
                value = self.fetch(method, config['waha_url'].rstrip('/') + path, headers, available)
                result.append((True, value))
            except Exception as error:
                result.append((False, _failure(error)))
            finally:
                self._slots.release()
                done.set()

        # No late continuation can issue another request. A timed-out write stays
        # unknown even if the remote call eventually completes; never retry it.
        threading.Thread(target=perform, daemon=True).start()
        if not done.wait(remaining):
            raise LoginError('WAHA 响应超时，请稍后刷新状态', code='timeout')
        ok, value = result[0]
        if not ok:
            raise value from None
        return value

    def _info(self, config, deadline):
        info = self._call(config, 'GET', '/api/sessions/' + quote(config['session'], safe=''), deadline)
        if not isinstance(info, dict) or not isinstance(info.get('status'), str):
            raise LoginError('WAHA 会话状态暂时无法读取，请稍后刷新', code='invalid_response')
        return info

    def _last_message_at(self):
        adapter = getattr(self.inbox, 'adapters', {}).get('whatsapp') if self.inbox is not None else None
        # Reuse the inbox's read-only statistics without triggering two 5s GETs
        # or fetching chat content as a side effect of polling the login screen.
        if adapter is None or not adapter.lock.acquire(blocking=False):
            return None
        try:
            return max((t for c in adapter._raw.values() if (t := timestamp(c.get('last_seen'))) is not None), default=None)
        finally:
            adapter.lock.release()

    def _view(self, config, info):
        state = info.get('status')
        state = state if isinstance(state, str) and state in SESSION_STATES else 'UNKNOWN'
        me = info.get('me')
        phone = me.get('id') if isinstance(me, dict) else None
        phone = phone.split('@')[0] if isinstance(phone, str) and re.fullmatch(r'[0-9]{5,20}@c\.us', phone) else None
        engine = info.get('engine')
        engine = engine.get('engine') if isinstance(engine, dict) else engine
        engine = engine if engine in ('WEBJS', 'NOWEB', 'GOWS') else None
        key = (config['waha_url'], config['session'])
        with self._lock:
            previous, changed = self._observed.get(key, (None, None))
            if previous is not None and previous != state:
                changed = self.clock()
            self._observed[key] = state, changed
        return dict(session=config['session'], status=state, status_label=LABELS[state],
                    needs_qr=state == 'SCAN_QR_CODE', phone_masked='*' * (len(phone) - 4) + phone[-4:] if phone else None,
                    engine=engine, status_changed_at=changed,
                    status_changed_at_source='observed' if changed is not None else None,
                    last_message_at=self._last_message_at(), next_step=NEXT_STEPS[state])

    def status(self):
        deadline = time.monotonic() + REQUEST_BUDGET
        config = self._configuration()
        return self._view(config, self._info(config, deadline))

    def qr(self):
        deadline = time.monotonic() + REQUEST_BUDGET
        config = self._configuration()
        state = self._view(config, self._info(config, deadline))['status']
        if state != 'SCAN_QR_CODE':
            message = '当前已登录' if state == 'WORKING' else NEXT_STEPS[state]
            raise LoginError(message, code='qr_not_ready', status_code=409)
        raw = self._call(config, 'GET', '/api/' + quote(config['session'], safe='') + '/auth/qr?format=image', deadline, png=True)
        # Some WAHA versions wrap the PNG as mimetype/data when image content
        # negotiation is unavailable. Never expose the wrapper or raw QR string.
        if isinstance(raw, dict) and raw.get('mimetype') == 'image/png' and isinstance(raw.get('data'), str):
            try:
                raw = base64.b64decode(raw['data'], validate=True) if len(raw['data']) <= MAX_RESPONSE else None
            except (ValueError, TypeError):
                raw = None
        if not isinstance(raw, bytes) or not raw.startswith(PNG) or len(raw) > MAX_RESPONSE:
            raise LoginError('二维码暂时无法读取，请稍后刷新状态', code='invalid_qr')
        return raw, self.clock()

    def _connect_audit(self):
        self.audit_path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.audit_path, timeout=0.1)
        db.execute('CREATE TABLE IF NOT EXISTS login_audit (id INTEGER PRIMARY KEY, at REAL NOT NULL, '
                   'actor TEXT NOT NULL, action TEXT NOT NULL, result TEXT NOT NULL)')
        db.execute('CREATE TABLE IF NOT EXISTS login_throttle (action TEXT PRIMARY KEY, at REAL NOT NULL)')
        return db

    def _reserve(self, operation, actor):
        try:
            with closing(self._connect_audit()) as db, db:
                db.execute('BEGIN IMMEDIATE')
                now = self.clock()
                previous = db.execute('SELECT at FROM login_throttle WHERE action=?', (operation,)).fetchone()
                if previous is not None and now - previous[0] < 10:
                    # A backward wall-clock jump also fails closed.
                    retry = max(1, min(10, math.ceil(10 - (now - previous[0]))))
                    db.execute('INSERT INTO login_audit(at,actor,action,result) VALUES (?,?,?,?)',
                               (now, actor, operation, 'rate_limited'))
                    db.commit()
                    raise LoginError('操作太频繁，请稍后再试', code='rate_limited', status_code=429, retry_after=retry)
                db.execute('INSERT OR REPLACE INTO login_throttle VALUES (?,?)', (operation, now))
                db.execute('INSERT INTO login_audit(at,actor,action,result) VALUES (?,?,?,?)', (now, actor, operation, 'attempt'))
        except LoginError:
            raise
        except Exception:
            raise LoginError('登录操作审计暂时不可用，请稍后再试', code='audit_unavailable') from None

    def _audit(self, operation, actor, result):
        try:
            with closing(self._connect_audit()) as db, db:
                db.execute('INSERT INTO login_audit(at,actor,action,result) VALUES (?,?,?,?)',
                           (self.clock(), actor, operation, result))
        except Exception:
            raise LoginError('操作结果审计暂时不可用，请稍后刷新状态', code='audit_unavailable', outcome='unknown') from None

    def write(self, operation, fields, actor):
        deadline = time.monotonic() + REQUEST_BUDGET
        if not self.allow_writes:
            raise LoginError('演练环境禁止修改 WhatsApp 登录状态', code='writes_disabled', status_code=403)
        if operation not in ('start', 'logout'):
            raise LoginError('登录操作无效', code='invalid_action', status_code=400)
        expected = {'confirm'} if operation == 'start' else {'confirm', 'phrase'}
        if not isinstance(fields, dict) or set(fields) != expected or fields.get('confirm') != '1':
            raise LoginError('请确认此操作（confirm=1）', code='confirmation_required', status_code=400)
        if operation == 'logout' and fields.get('phrase') != '退出登录':
            raise LoginError('请输入“退出登录”确认', code='confirmation_required', status_code=400)
        if not isinstance(actor, str) or not actor or len(actor) > 200:
            raise LoginError('请先完成身份认证', code='unauthorized', status_code=403)
        self._reserve(operation, actor)
        sent = False
        try:
            config = self._configuration()
            before = self._view(config, self._info(config, deadline))
            state = before['status']
            if operation == 'start':
                if state == 'WORKING':
                    raise LoginError('已经登录，无需重新扫码', code='already_working', status_code=409)
                if state not in ('FAILED', 'STOPPED'):
                    raise LoginError(NEXT_STEPS[state], code='not_startable', status_code=409)
                action = 'restart' if state == 'FAILED' else 'start'
            else:
                if state != 'WORKING':
                    raise LoginError('当前未登录，不能退出登录', code='not_working', status_code=409)
                action = 'logout'
            # Exactly one mutation; no stop+start chain and no retry on failure.
            sent = True
            try:
                info = self._call(config, 'POST', '/api/sessions/' + quote(config['session'], safe='') + '/' + action, deadline)
            except LoginError:
                raise _unknown() from None
            if not isinstance(info, dict) or info.get('status') not in SESSION_STATES:
                raise _unknown()
            result = self._view(config, info)
            result.update(accepted=True, outcome='accepted')
            self._audit(operation, actor, 'accepted')
            return result
        except LoginError as error:
            self._audit(operation, actor, 'unknown' if sent else 'rejected')
            raise error from None
        except Exception:
            self._audit(operation, actor, 'unknown' if sent else 'failed')
            raise (_unknown() if sent else LoginError('登录操作暂时无法完成，请稍后刷新状态')) from None
