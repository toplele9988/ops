"""Fictional conversations and offline model doubles for language tests."""
from types import SimpleNamespace
import socket
import sqlite3
import subprocess

import pytest

from console.inbox.model import make_key


class Clock:
    def __init__(self):
        self.now = 1900000000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class Inbox:
    def __init__(self):
        self.conversations = {}
        self.calls = []

    def read(self, now=None):
        return dict(ready=True, conversations=self.conversations)

    def detail(self, key, now=None):
        self.calls.append(key)
        return self.conversations.get(key)

    def conversation(self, texts, *, channel='alibaba', country='ZZ', roles=None):
        key = make_key('conversation', channel, 'fictional-account', 'fictional-' + str(len(self.conversations)))
        messages = [dict(role=(roles or ['buyer'] * len(texts))[i], text=text, sent_at=1900000000.0 + i,
                         message_id='fictional-' + str(i)) for i, text in enumerate(texts)]
        self.conversations[key] = dict(key=key, channel=channel, account='fictional-account',
            buyer_name='虚构客户', country=country, messages=messages, n_messages=len(messages),
            first_seen=1900000000.0, last_seen=1900000000.0 + len(messages), counterpart_role='customer')
        return key


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError('语言测试只允许虚构数据与假模型，不得访问真实渠道或模型')
    monkeypatch.setattr(socket.socket, 'connect', blocked)
    monkeypatch.setattr(socket, 'create_connection', blocked)
    original = subprocess.run
    def guarded(command, *args, **kwargs):
        if 'ai-task' in str(command):
            return blocked()
        return original(command, *args, **kwargs)
    monkeypatch.setattr(subprocess, 'run', guarded)
    connect = sqlite3.connect
    def temporary_database(database, *args, **kwargs):
        value = str(database)
        if value.startswith(('/var/lib/imgw/', 'file:/var/lib/imgw/', '/opt/imgw/', '/srv/imgw-dev/')):
            raise AssertionError('语言测试数据库必须位于临时目录')
        return connect(database, *args, **kwargs)
    monkeypatch.setattr(sqlite3, 'connect', temporary_database)


@pytest.fixture
def inputs(tmp_path):
    return SimpleNamespace(path=tmp_path / 'lang.sqlite3', inbox=Inbox(), clock=Clock())
