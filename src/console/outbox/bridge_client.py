"""Explicit WhatsApp sends through the private gateway; no retry transport."""
import grp
import json
import os
from pathlib import Path
import re
import stat
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from console.channels.whatsapp import CONFIG_PATH
from .model import known_refusal

# Verified by read-only inspection of bridge.py main() and its service unit.
BRIDGE_API_URL = 'http://127.0.0.1:8788'
TIMEOUT = 20
CHAT_ID = re.compile(r'^[0-9A-Za-z_-]{3,100}@(c\.us|lid|g\.us)$')
MAX_RESPONSE = 65536
UNKNOWN = ('unknown', 'send_outcome_unknown')


def local_url(url, *, base=False):
    try:
        parsed = urlsplit(url)
        if (parsed.scheme != 'http' or parsed.hostname not in ('127.0.0.1', 'localhost')
                or parsed.username is not None or parsed.password is not None
                or parsed.fragment or parsed.query or (base and parsed.path not in ('', '/'))
                or any(c.isspace() or ord(c) < 32 for c in url)):
            raise ValueError
        parsed.port
    except (ValueError, TypeError, AttributeError):
        raise ValueError('发送服务地址不可用') from None
    return url.rstrip('/') if base else url


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError('发送服务响应不确定')


def _transport(method, url, headers, body, timeout):
    """Returns (HTTP status, parsed JSON). No proxy, redirect, or automatic retry."""
    local_url(url)
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode('utf8')
    request = Request(url, data=data, headers={**headers, 'Content-Type': 'application/json'}, method=method)
    opener = build_opener(ProxyHandler({}), NoRedirect())
    try:
        result = opener.open(request, timeout=timeout)
    except HTTPError as error:
        result = error
    with result:
        raw = result.read(MAX_RESPONSE + 1)
        if len(raw) > MAX_RESPONSE:
            raise ValueError('发送服务响应不确定')
        return result.code, json.loads(raw)


def local_get(url, headers, body, timeout):
    return _transport('GET', url, headers, body, timeout)


def local_post(url, headers, body, timeout):
    return _transport('POST', url, headers, body, timeout)


class BridgeClient:
    def __init__(self, config_path=CONFIG_PATH, *, api=None, post=None, get=None,
                 stat_fn=None, group_gid=None):
        self.path = Path(config_path)
        self.api = local_url(api, base=True) if api is not None else None
        self.post = local_post if post is None else post
        self.get = local_get if get is None else get
        self.stat_fn = stat_fn or (lambda p: p.lstat())
        self._real_stat = stat_fn is None
        self.group_gid = group_gid

    def _configuration(self):
        info = self.stat_fn(self.path)
        gid = self.group_gid if self.group_gid is not None else grp.getgrnam('imgw').gr_gid
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_gid != gid
                or stat.S_IMODE(info.st_mode) != 0o640):
            raise ValueError('发送服务配置不可用')
        fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd) as stream:
            actual = os.fstat(stream.fileno())
            if ((actual.st_ino, actual.st_dev) != (info.st_ino, info.st_dev) or actual.st_size > 65536
                    or (self._real_stat and (not stat.S_ISREG(actual.st_mode) or actual.st_uid != 0
                        or actual.st_gid != gid or stat.S_IMODE(actual.st_mode) != 0o640))):
                raise ValueError('发送服务配置不可用')
            data = json.load(stream)
        # Discard unrelated private configuration. No cache or public configuration accessor.
        key, session = data['bridge_api_key'], data['session']
        if not all(isinstance(v, str) and v and len(v) <= 4096 and not any(ord(c) < 32 for c in v)
                   for v in (key, session)):
            raise ValueError('发送服务配置不可用')
        api = local_url(self.api or data.get('api', BRIDGE_API_URL), base=True)
        return key, session, api

    def available(self):
        """Configuration availability only. Never calls the gateway."""
        try:
            self._configuration()
            return True
        except Exception:
            return False

    @staticmethod
    def _call(transport, api, path, headers, body=None):
        # Validate even when transports are injected; do not trust config URLs.
        result = transport(local_url(api + path), headers, body, TIMEOUT)
        status, payload = result
        if type(status) is not int or not isinstance(payload, dict):
            raise ValueError('发送服务响应不确定')
        return status, payload

    @staticmethod
    def _outcome(status, payload, idem):
        code = payload.get('error')
        if 400 <= status < 500 and known_refusal(code):
            return 'failed', code
        if 200 <= status < 300 and payload.get('idempotency_key') == idem:
            if (payload.get('state') == 'accepted' and payload.get('error') is None
                    and payload.get('error_code') is None):
                return 'sent', None
            code = payload.get('error_code')
            if payload.get('state') == 'rejected' and known_refusal(code):
                return 'failed', code
        return UNKNOWN

    def deliver(self, chat, text, idem, confirm_new_recipient, audit, attachment=None):
        """One send, plus exactly one safe bind/send exception verified in bridge.py.

        Any transport/parsing exception is uncertain. No exception text, headers,
        provider objects, phone numbers or credentials cross this boundary.
        """
        try:
            key, session, api = self._configuration()
            headers = {'Authorization': 'Bearer ' + key}
            status, binding = self._call(self.get, api, '/api/binding', headers)
            if not (200 <= status < 300 and binding.get('session') == session
                    and isinstance(binding.get('account_id'), str) and CHAT_ID.fullmatch(binding['account_id'])):
                code = binding.get('error')
                return ('failed', code) if 400 <= status < 500 and known_refusal(code) else UNKNOWN
            packet = dict(idempotency_key=idem, session=session, account_id=binding['account_id'],
                          chat_id=chat, actor='owner_explicit', text=text, auto_send=False)
            path = '/api/sendText'
            if attachment is not None:
                mime = attachment['mimetype']
                kind = 'sendImage' if mime.startswith('image/') else 'sendVideo' if mime.startswith('video/') else 'sendFile'
                path = '/api/' + kind
                packet.pop('text')
                packet['file'] = attachment
                packet['caption'] = ''
            status, payload = self._call(self.post, api, path, headers, packet)
            result = self._outcome(status, payload, idem)
            # Only this exact 409 is known to precede the gateway's in_flight insert.
            if not (status == 409 and payload.get('error') == 'recipient_not_bound' and confirm_new_recipient):
                return result
            audit('recipient_bind_attempt', actor='owner_explicit')
            try:
                status, bound = self._call(self.post, api, '/api/recipients/bind', headers,
                    dict(actor='owner_explicit', confirmed=True, session=session, account_id=binding['account_id'],
                         chat_id=chat, reason='后台首次发送，用户已确认'))
            except Exception:
                audit('recipient_bind_result', state='unknown', code='send_outcome_unknown')
                return UNKNOWN
            if not (200 <= status < 300 and bound.get('bound') is True):
                code = bound.get('error')
                result = ('failed', code) if 400 <= status < 500 and known_refusal(code) else UNKNOWN
                audit('recipient_bind_result', state=result[0], code=result[1])
                return result
            audit('recipient_bind_result', state='bound')
            audit('send_attempt', actor='owner_explicit', after_binding=True)
            status, payload = self._call(self.post, api, path, headers, packet)
            return self._outcome(status, payload, idem)
        except Exception:
            return UNKNOWN
