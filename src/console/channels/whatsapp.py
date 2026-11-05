"""WAHA GET-only adapter. Credentials never leave the injected transport boundary."""
import grp
import json
from pathlib import Path
import re
import sqlite3
import stat
import time
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from console.media import tokens
from console.names import NamesResolver
from console.names.sources import STATE_PATH
from console.wamedia import MediaStore, identity
from console.wamedia.ingest import IngestWorker
from . import rich, whatsapp_rich
from .common import CachedAdapter, ChannelUnavailable, conversation, placeholder, timestamp

CONFIG_PATH = Path('/etc/imgw/whatsapp-bridge.json')
CHAT_ID = re.compile(r'^[0-9A-Za-z._-]+@(c\.us|g\.us|lid)$')
SESSION_STATES = frozenset(('STOPPED', 'STARTING', 'SCAN_QR_CODE', 'PASSKEY_REQUIRED',
                            'PASSKEY_CONFIRMATION_REQUIRED', 'WORKING', 'FAILED'))


def local_url(url):
    parsed = urlsplit(url)
    if (parsed.scheme != 'http' or parsed.hostname not in ('127.0.0.1', 'localhost') or
            parsed.username is not None or parsed.password is not None or parsed.fragment):
        raise ValueError('渠道地址不可用')
    parsed.port  # Reject malformed ports before handing off to any transport.
    return url


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ChannelUnavailable()


def local_fetch(method, url, headers, timeout):
    """Only loopback HTTP GET, no redirects or environment proxy. Returns decoded JSON."""
    if method != 'GET':
        raise ChannelUnavailable()
    local_url(url)
    opener = build_opener(ProxyHandler({}), NoRedirect())
    with opener.open(Request(url, headers=headers, method=method), timeout=timeout) as response:
        if response.status != 200:
            raise ChannelUnavailable()
        raw = response.read(8 * 1024 * 1024 + 1)
        if len(raw) > 8 * 1024 * 1024:
            raise ChannelUnavailable()
        return json.loads(raw)


_BASE64 = re.compile(r'[A-Za-z0-9+/=\s]{120,}')


class WhatsAppAdapter(CachedAdapter):
    name, label = 'whatsapp', 'WhatsApp'

    def __init__(self, config_path=CONFIG_PATH, fetch=None, clock=None, stat_fn=None, group_gid=None,
                 *, media_store=None, key_path=tokens.KEY_PATH, names_resolver=None):
        super().__init__()
        self.path = Path(config_path)
        self.names = names_resolver or NamesResolver(STATE_PATH if self.path == CONFIG_PATH else
                                                     self.path.parent / 'disabled-api-state.sqlite3')
        self.media_store = media_store or (MediaStore() if self.path == CONFIG_PATH else
                                           MediaStore(self.path.parent / 'disabled-wamedia.sqlite3',
                                                      self.path.parent / 'disabled-wamedia'))
        from os import environ
        queue = (environ.get('IMGW_WA_MEDIA_QUEUE', '/var/lib/imgw/ops-app/whatsapp-media-queue.sqlite3')
                 if self.path == CONFIG_PATH else self.path.parent / 'disabled-wamedia-queue.sqlite3')
        self.media_ingest = IngestWorker(self.media_store, queue, None)
        self.key_path = key_path
        self.fetch = local_fetch if fetch is None else fetch
        self.clock = time.monotonic if clock is None else clock
        self.stat_fn = stat_fn or (lambda p: p.lstat())
        self._real_stat = stat_fn is None
        self.group_gid = group_gid  # Tests supply a fictional gid and stat result.
        self._until = float('-inf')
        self._config_stamp = None
        self._config = None
        self._history = {}
        self.session_status = None
        self.account = 'WhatsApp · ****'
        self._revision = 0
        self.chats_total = 0

    def _configuration(self):
        info = self.stat_fn(self.path)
        gid = self.group_gid if self.group_gid is not None else grp.getgrnam('imgw').gr_gid
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_gid != gid
                or stat.S_IMODE(info.st_mode) != 0o640):
            raise ValueError('渠道配置不可用')
        stamp = (info.st_mtime_ns, info.st_size, info.st_ino, info.st_dev, info.st_mode, info.st_uid, info.st_gid)
        if stamp != self._config_stamp:
            # O_NOFOLLOW closes the lstat/open symlink race; fstat rechecks identity.
            import os
            fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(fd) as stream:
                actual = os.fstat(stream.fileno())
                if (actual.st_ino, actual.st_dev) != (info.st_ino, info.st_dev):
                    raise ValueError('渠道配置不可用')
                if self._real_stat and (actual.st_uid != 0 or actual.st_gid != gid
                        or stat.S_IMODE(actual.st_mode) != 0o640 or not stat.S_ISREG(actual.st_mode)):
                    raise ValueError('渠道配置不可用')
                if actual.st_size > 65536:
                    raise ValueError('渠道配置不可用')
                data = json.load(stream)
            selected = {k: data[k] for k in ('waha_url', 'waha_api_key', 'session')}
            if not all(isinstance(v, str) and v for v in selected.values()):
                raise ValueError('渠道配置不可用')
            local_url(selected['waha_url'])
            parsed = urlsplit(selected['waha_url'])
            if parsed.query or parsed.path not in ('', '/'):
                raise ValueError('渠道配置不可用')
            self._config, self._config_stamp = selected, stamp
            self._until, self._history = float('-inf'), {}
        return self._config

    def _get(self, config, path):
        # Keep all secret material inside this call, never attach it to a result/error.
        return self.fetch('GET', config['waha_url'].rstrip('/') + path,
                          {'X-Api-Key': config['waha_api_key']}, 5)

    def _message(self, raw, group=False, session=None):
        if not isinstance(raw, dict):
            return None
        sent_at = timestamp(raw.get('timestamp'))
        body = raw.get('body') or ''
        if sent_at is None or not isinstance(body, str):
            return None
        # WEBJS puts the raw base64 of an image/file in `body`; never show that as text.
        if raw.get('hasMedia') and (not body.strip() or _BASE64.fullmatch(body)):
            body = placeholder([raw.get('media') or {}])
        elif _BASE64.fullmatch(body):
            body = '[附件]'
        if not body:
            return None
        body.encode('utf8')
        participant = raw.get('participant') or raw.get('author')
        sender = ('成员 ' + participant.split('@')[0][-4:]) if group and isinstance(participant, str) else None
        message = dict(role='seller' if raw.get('fromMe') is True else 'buyer', text=body,
                       sent_at=sent_at, sender_label=sender)
        mid = raw.get('id')
        if raw.get('hasMedia') and isinstance(mid, str) and isinstance(session, str):
            try:
                key = identity(session, mid)
                entry = self.media_store.get(key)
            except (ValueError, UnicodeError):
                entry = None
            if entry:
                if entry['state'] == 'ready':
                    token = tokens.make(['wa', key], self.key_path)
                    kind = entry['kind']
                    if kind in ('image', 'video'):
                        block = dict(type=kind, media=token, size_bytes=entry['size'])
                    else:
                        name = entry['filename'] or ('语音附件' if kind == 'voice' else '附件')
                        if kind == 'voice':
                            name += '（请下载后用播放器打开）'
                        block = dict(type='file', name=name, size_bytes=entry['size'], media=token)
                else:
                    title = ('附件已保存在本机缓冲，请联系管理员检查存储挂载后重新排队' if entry.get('buffered') else
                             '附件暂不可取，请联系管理员检查后重新排队' if entry['state'] == 'failed' else
                             '附件待重试，稍后刷新查看' if entry.get('error') else '附件等待获取')
                    block = dict(type='card', kind='other', title=title, subtitle=entry.get('error', ''))
                message['blocks'] = [block]
                if body and not body.startswith('['):
                    message['blocks'].insert(0, dict(type='text', text=body))
                message['text'] = rich.summary(message['blocks'], body)
        link = whatsapp_rich.link(raw, body)
        if link:
            blocks = message.get('blocks') or [dict(type='text', text=body)]
            message['blocks'] = blocks + [link]
            message['text'] = rich.summary(message['blocks'], body)
        return message

    def _register_media(self, raw, session, chat_id):
        if isinstance(raw, dict) and raw.get('hasMedia') is True:
            try:
                self.media_ingest.enqueue(session, raw.get('id'), chat_id, raw.get('media'))
            except (ValueError, TypeError, OSError, sqlite3.Error):
                return False
        return True

    @staticmethod
    def _media_failure(message):
        if message is not None:
            blocks = message.get('blocks') or [dict(type='text', text=message['text'])]
            message['blocks'] = blocks + [dict(type='card', kind='other',
                                                title='附件登记失败，请联系管理员检查存储后刷新', subtitle='')]
        return message

    def _fail(self, tick):
        changed = self.ready or bool(self._raw) or self.session_status is not None
        self.ready, self._raw, self._history = False, {}, {}
        self.session_status = None
        self.chats_total = 0
        self._until = tick + 30
        if changed:
            self._revision += 1

    def _read(self, now):
        tick = self.clock()
        try:
            config = self._configuration()
        except Exception:
            self._config = self._config_stamp = None
            self._fail(tick)
            return (self._revision, self.ready), self.ready, self._raw
        try:
            if tick >= self._until:
                session = quote(config['session'], safe='')
                info = self._get(config, '/api/sessions/' + session)
                status = info.get('status')
                if status not in SESSION_STATES:
                    raise ValueError('渠道会话不可用')
                self.session_status = status
                if status != 'WORKING':
                    self._fail(tick)
                    self.session_status = status
                else:
                    labels = self.names.snapshot()
                    owner = (info.get('me') or {}).get('id', '')
                    suffix = owner.split('@')[0][-4:] if isinstance(owner, str) and CHAT_ID.fullmatch(owner) else ''
                    self.account = 'WhatsApp · ****' + suffix
                    chats = self._get(config, '/api/' + session + '/chats/overview?limit=500')
                    if not isinstance(chats, list):
                        raise ValueError('渠道聊天不可用')
                    raw, valid_ids = {}, set()
                    for chat in chats:
                        if not isinstance(chat, dict):
                            continue
                        cid = chat.get('id')
                        if not isinstance(cid, str) or len(cid) > 1000 or not CHAT_ID.fullmatch(cid):
                            continue
                        valid_ids.add(cid)
                        registered = self._register_media(chat.get('lastMessage'), config['session'], cid)
                        message = self._message(chat.get('lastMessage'), cid.endswith('@g.us'), config['session'])
                        if not registered:
                            message = self._media_failure(message)
                        if message is None:
                            continue
                        resolved = labels.contact(cid, channel='whatsapp', provided_name=chat.get('name'))
                        conv = conversation(self.name, self.label, self.account, cid, resolved.value,
                                            [message], is_group=cid.endswith('@g.us'))
                        country = labels.country(cid, channel='whatsapp')
                        conv.update(country=country[0] if country else '', name_missing=not resolved.named,
                                    name_missing_reason=resolved.reason)
                        raw[conv['key']] = conv
                    self.chats_total = len(valid_ids)
                    self._raw, self.ready, self._until = raw, True, tick + 30
                    self._revision += 1
        except Exception:
            self._fail(tick)
        return (self._revision, self.ready), self.ready, self._raw

    def messages(self, key):
        from console.inbox.model import decode_key
        with self.lock:
            try:
                channel, account, cid = decode_key(key, 'conversation')
                if channel != self.name or not CHAT_ID.fullmatch(cid) or key not in self._view or not self.ready:
                    raise ValueError('会话不可用')
                tick = self.clock()
                cached = self._history.get(key)
                if cached is not None and tick < cached[0]:
                    return list(cached[1])
                config = self._configuration()
                rows = self._get(config, '/api/' + quote(config['session'], safe='') + '/chats/' +
                                 quote(cid, safe='') + '/messages?limit=100&downloadMedia=false')
                if not isinstance(rows, list):
                    raise ValueError('消息不可用')
                messages, seen = [], set()
                for row in rows:
                    registered = self._register_media(row, config['session'], cid)
                    message = self._message(row, cid.endswith('@g.us'), config['session'])
                    if not registered:
                        message = self._media_failure(message)
                    if message is None:
                        continue
                    identity = row.get('id') or (message['sent_at'], message['role'], message['text'])
                    if identity not in seen:
                        seen.add(identity)
                        messages.append(message)
                messages.sort(key=lambda m: m['sent_at'])
                self._history[key] = tick + 15, tuple(messages)
                return list(messages)
            except Exception:
                self._fail(self.clock())
                raise ChannelUnavailable() from None

    def health(self, now):
        result = super().health(now)
        result.update(session_status=self.session_status,
                      chats_total=self.chats_total,
                      chats_7d=sum(now - 7 * 86400 <= c['last_seen'] <= now for c in self._view.values()))
        return result
