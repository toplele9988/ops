"""Fictional data only. Every SQLite/JSON source is confined to pytest's temp dir."""
from contextlib import closing
from datetime import datetime, timezone
import builtins
import io
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess

import pytest

from console.overview import Overview
from console.overview.budget import FREE_MODELS, LEDGER_KEYS
from console.overview.service import PATHS
from console.overview.safety import iso

NOW = datetime(2026, 11, 2, 12, tzinfo=timezone.utc).timestamp()
PRIVATE = 'FICTIONAL_PRIVATE_BODY_DO_NOT_OUTPUT'
SCHEMAS = {
    'inbound': '''CREATE TABLE inbound(id TEXT PRIMARY KEY,payload TEXT,state TEXT,
        archive_reason TEXT,created_at REAL);''',
    'probe': '''CREATE TABLE probe_state(account TEXT PRIMARY KEY,snapshot_json TEXT,
        first_seen_at REAL,received_at REAL,last_complete_at REAL);
        CREATE TABLE heartbeat_log(id INTEGER PRIMARY KEY,received_at REAL,instance_id TEXT,
        version TEXT,state TEXT,last_error TEXT,cycle_complete INTEGER,counters_json TEXT,
        range_start TEXT,range_end TEXT);''',
    'wechat': '''CREATE TABLE messages(app_id TEXT,message_id TEXT,group_id TEXT,sender_id TEXT,
        created_at REAL,payload TEXT,status TEXT,rule TEXT,updated_at REAL);
        CREATE TABLE received_callback_archive(received_at REAL,payload_json TEXT);
        CREATE TABLE company_media(app_id TEXT,message_id TEXT,text TEXT,original BLOB);''',
    'calls': '''CREATE TABLE calls(cache_key TEXT,attempt INTEGER,prompt_sha256 TEXT,model TEXT,
        state TEXT,error_code TEXT,day INTEGER,reserved_tokens INTEGER,response_json TEXT,
        response_sha256 TEXT,created_at REAL,updated_at REAL);''',
    'analysis': '''CREATE TABLE windows(version TEXT,window_index INTEGER,window_sha256 TEXT,
        plan_json TEXT,state TEXT,result_json TEXT,error_code TEXT,attempts INTEGER,updated_at REAL);
        CREATE TABLE current_conversations(conversation_key TEXT,analysis_version TEXT);''',
    'whatsapp': '''CREATE TABLE messages(message_key TEXT,content TEXT,received REAL,sent_at REAL);
        CREATE TABLE events(event_key TEXT,event_type TEXT,received REAL,raw_json TEXT);
        CREATE TABLE recipients(chat_id TEXT);
        CREATE TABLE knowledge_jobs(message_key TEXT,status TEXT,asset_id TEXT,error_code TEXT,updated REAL);''',
    'physical': '''CREATE TABLE gate_balances(bucket TEXT PRIMARY KEY,balance_json TEXT);
        CREATE TABLE gate_calls(request_key TEXT,response_json TEXT);''',
}


@pytest.fixture(autouse=True)
def isolation(tmp_path, monkeypatch):
    connect, os_open, builtin_open, io_open = sqlite3.connect, os.open, builtins.open, io.open

    def local_connect(path, *args, **kwargs):
        assert str(path).startswith((str(tmp_path), tmp_path.as_uri())), '测试禁止生产 SQLite'
        return connect(path, *args, **kwargs)

    def local_open(path, *args, **kwargs):
        assert isinstance(path, int) or str(path).startswith(str(tmp_path)), '测试禁止生产 JSON'
        return os_open(path, *args, **kwargs)

    def safe_open(original):
        def call(path, *args, **kwargs):
            if not isinstance(path, int):
                text = str(path)
                production = text.startswith(('/etc/imgw/', '/var/lib/imgw/'))
                assert not production or text.startswith(str(Path.cwd())), '测试禁止真实业务文件'
            return original(path, *args, **kwargs)
        return call

    def forbidden(*args, **kwargs):
        raise AssertionError('总览禁止网络和子进程')

    monkeypatch.setattr(sqlite3, 'connect', local_connect)
    monkeypatch.setattr(os, 'open', local_open)
    monkeypatch.setattr(builtins, 'open', safe_open(builtin_open))
    monkeypatch.setattr(io, 'open', safe_open(io_open))
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    monkeypatch.setattr(os, 'system', forbidden)


def execute(path, sql, args=()):
    with closing(sqlite3.connect(path)) as db, db:
        db.execute(sql, args)


def write_json(path, value):
    path.write_text(json.dumps(value), encoding='utf8')


@pytest.fixture
def paths(tmp_path):
    result = {key: tmp_path / (key + ('.sqlite3' if key in SCHEMAS else '.json')) for key in PATHS}
    for key, schema in SCHEMAS.items():
        with closing(sqlite3.connect(result[key])) as db, db:
            db.executescript(schema)
    return result


def inbound(paths, identity='fake-message', *, at=NOW - 5, sent_at=None, history=False, conv='fake-conversation'):
    payload = dict(account='fake-account', conversation_id=conv, sent_at=sent_at or iso(at),
                   history=history, content=PRIVATE, buyer_name=PRIVATE,
                   live_provenance={} if not history else None)
    execute(paths['inbound'], 'INSERT INTO inbound VALUES(?,?,?,?,?)',
            (identity, json.dumps(payload), 'archived', None, at))


def probe(paths, *, state='running', at=NOW - 5, error=None, paused_seconds=None, gap=False):
    raw = dict(state=state, version='0.3.5.4', last_error=error, private=PRIVATE)
    execute(paths['probe'], 'INSERT OR REPLACE INTO probe_state VALUES(?,?,?,?,?)',
            ('fake-account', json.dumps(raw), NOW - 999999, at, at))
    if paused_seconds is not None:
        since = NOW - paused_seconds
        beats = [since] if gap else list(range(int(since), int(at), 120))
        for stamp in sorted(set(beats + [at])):
            execute(paths['probe'], 'INSERT INTO heartbeat_log(received_at,instance_id,state) VALUES(?,?,?)',
                    (stamp, 'fake-instance', state))


def session(paths, state='WORKING', at=NOW - 5, identity='session'):
    execute(paths['whatsapp'], 'INSERT INTO events VALUES(?,?,?,?)',
            (identity, 'session.status', at, json.dumps(dict(payload=dict(status=state), me=dict(id=PRIVATE), private=PRIVATE))))


def budgets(paths):
    write_json(paths['free_snapshot'], dict(provider='dashscope', source='owner_browser_observation',
        confidence='masked_key_match', verified_at=iso(NOW - 60), valid_until=iso(NOW + 3600),
        models={model: dict(free_only=True, remaining_tokens=500000, expires_on='2027-01-01') for model in FREE_MODELS}))
    for key in LEDGER_KEYS:
        write_json(paths[key], dict(version=1, policy_sha256='a' * 64,
            models={model: dict(accounted_tokens=100, actual_total_tokens=100, actual_prompt_tokens=60,
                actual_completion_tokens=40, unknown_reserved_tokens=0, in_flight=False, blocked_reason=None,
                calls=1, completed_calls=1)
                for model in FREE_MODELS}))
    execute(paths['physical'], 'INSERT INTO gate_balances VALUES(?,?)', ('deepseek_official', json.dumps(dict(
        provider='deepseek_official', currency='CNY', total_micros=1234567, observed_at=NOW - 60,
        receipt=dict(body=PRIVATE)))))


@pytest.fixture
def healthy(paths):
    inbound(paths)
    probe(paths)
    execute(paths['wechat'], 'INSERT INTO messages VALUES(?,?,?,?,?,?,?,?,?)',
            ('fake-app', 'fake-wx', 'fake-group', 'fake-sender', NOW - 10, PRIVATE, 'done', None, NOW - 5))
    execute(paths['wechat'], 'INSERT INTO received_callback_archive VALUES(?,?)', (NOW - 5, PRIVATE))
    execute(paths['wechat'], 'INSERT INTO company_media VALUES(?,?,?,?)', ('fake-app', 'fake-wx', PRIVATE, b'fake'))
    execute(paths['calls'], 'INSERT INTO calls(state,updated_at,response_json) VALUES(?,?,?)', ('complete', NOW - 5, PRIVATE))
    execute(paths['analysis'], 'INSERT INTO current_conversations VALUES(?,?)', ('fake-conversation', 'current'))
    execute(paths['analysis'], 'INSERT INTO windows(version,state,updated_at,result_json) VALUES(?,?,?,?)',
            ('current', 'validated', NOW - 5, PRIVATE))
    session(paths)
    execute(paths['whatsapp'], 'INSERT INTO messages VALUES(?,?,?,?)', ('fake-wa', PRIVATE, NOW - 5, NOW - 10))
    execute(paths['whatsapp'], 'INSERT INTO knowledge_jobs VALUES(?,?,?,?,?)', ('fake-wa', 'indexed_claims', 'fake-asset', None, NOW - 5))
    budgets(paths)
    return paths


def read(paths, **options):
    return Overview(paths=paths, clock=lambda: NOW, **options).read()


def channel(data, key):
    return next(item for item in data['channels'] if item['key'] == key)


def stage(data, channel_key, key):
    return next(item for item in channel(data, channel_key)['stages'] if item['key'] == key)
