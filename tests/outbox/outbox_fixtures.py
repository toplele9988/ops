"""Synthetic identities only. Every test blocks real sockets and injects transports."""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from console.channels.common import COPY_HINTS, conversation
from console.inbox.model import waiting_state
from console.outbox import BridgeClient, Outbox

NOW = 1_800_000_000.0
SECRET = 'fictional-bridge-credential'
CHAT = 'fictional-contact@c.us'
ACCOUNT = 'fictional-owner@c.us'
SESSION = 'fictional-session'


def fake_stat(path):
    info = path.lstat()
    return SimpleNamespace(**{k: getattr(info, k) for k in (
        'st_mode', 'st_size', 'st_ino', 'st_dev', 'st_mtime_ns')}, st_uid=0, st_gid=731)


def seed_config(path, **overrides):
    path.write_text(json.dumps({**dict(bridge_api_key=SECRET, session=SESSION,
        ignored_secret='fictional-ignored-private', waha_api_key='fictional-waha-key'), **overrides}))
    path.chmod(0o640)


class FakeInbox:
    def __init__(self):
        self.conversations = {}
        for channel in ('whatsapp', 'alibaba', 'wechat', 'website'):
            self.add(channel, CHAT if channel == 'whatsapp' else 'fictional-conversation')

    def add(self, channel='whatsapp', chat=CHAT):
        conv = conversation(channel, channel, 'fictional-account', chat, '虚构客户',
            [dict(role='buyer', text='虚构询问', sent_at=NOW - 1, sender_label=None)])
        state, _ = waiting_state(conv['messages'], NOW)
        self.conversations[conv['key']] = dict(conv, **state, can_send=False, copy_hint=COPY_HINTS[channel])
        return conv['key']

    def key(self, channel='whatsapp'):
        return next(k for k, c in self.conversations.items() if c['channel'] == channel)

    def read(self):
        return dict(conversations=self.conversations, ready=True)

    def detail(self, key):
        return self.conversations.get(key)


class FakeGateway:
    def __init__(self):
        self.calls = []
        self.answers = []
        self.binding = (200, dict(session=SESSION, account_id=ACCOUNT))
        self.bind_answer = (200, {'bound': True, 'new': True})
        self.before_send = None

    def get(self, url, headers, body, timeout):
        assert url == 'http://127.0.0.1:8788/api/binding'
        assert body is None and headers == {'Authorization': 'Bearer ' + SECRET} and timeout == 20
        self.calls.append(('get', url, body))
        if isinstance(self.binding, BaseException):
            raise self.binding
        return self.binding

    def post(self, url, headers, body, timeout):
        assert headers == {'Authorization': 'Bearer ' + SECRET} and timeout == 20
        self.calls.append(('post', url, dict(body)))
        if url.endswith('/api/recipients/bind'):
            answer = self.bind_answer
        else:
            assert url.endswith('/api/sendText')
            if self.before_send:
                self.before_send(body)
            answer = self.answers.pop(0) if self.answers else (200, dict(
                idempotency_key=body['idempotency_key'], state='accepted', error_code=None))
            if callable(answer):
                answer = answer(body)
        if isinstance(answer, BaseException):
            raise answer
        return answer

    @property
    def sends(self):
        return [c for c in self.calls if c[1].endswith('/api/sendText')]

    @property
    def binds(self):
        return [c for c in self.calls if c[1].endswith('/api/recipients/bind')]


def form(**overrides):
    return {**dict(text='  虚构回复，请核对资料  ', request_id='fictional-request-01', confirm='1',
                   confirm_new_recipient='0'), **overrides}


def request(key='', *, form_data=None, body=b'', query=None, authenticated=True,
            principal=True, username='fictional-operator', recheck=True):
    async def read_body():
        return body
    return SimpleNamespace(state=SimpleNamespace(
        password_principal=SimpleNamespace(username=username) if principal else None,
        password_recheck=lambda: recheck, session=SimpleNamespace(authenticated=authenticated), form=form_data),
        query_params=query or {}, path_params={'key': key}, body=read_body)


class IsolatedTest(unittest.TestCase):
    def setUp(self):
        for target in ('socket.socket.connect', 'socket.socket.connect_ex', 'socket.create_connection',
                       'socket.getaddrinfo', 'urllib.request.urlopen'):
            guard = patch(target, side_effect=AssertionError('real network forbidden'))
            mocked = guard.start()
            self.addCleanup(guard.stop)
            self.addCleanup(mocked.assert_not_called)
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.config = self.root / 'fictional-config.json'
        self.path = self.root / 'private' / 'outbox.sqlite3'
        seed_config(self.config)
        self.net, self.hub = FakeGateway(), FakeInbox()
        self.bridge = BridgeClient(self.config, post=self.net.post, get=self.net.get,
                                   stat_fn=fake_stat, group_gid=731)
        self.now = NOW
        self.service = Outbox(self.path, bridge=self.bridge, inbox=self.hub, clock=lambda: self.now)
        self.key = self.hub.key()

    def enable(self):
        self.service.set_settings(dict(channel='whatsapp', enabled='1'))

    def send(self, **overrides):
        return self.service.send(self.key, form(**overrides))
