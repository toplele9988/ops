"""Fictional, temporary databases only; the tests cannot open production SQLite."""
from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
import sqlite3
import socket

import pytest

NOW = datetime(2026, 10, 31, 12, 30, tzinfo=timezone(timedelta(hours=8))).timestamp()
SCHEMAS = {
    'inbound': '''CREATE TABLE inbound(id TEXT PRIMARY KEY,payload TEXT NOT NULL,
        state TEXT NOT NULL,archive_reason TEXT,created_at REAL NOT NULL);''',
    'probe': '''CREATE TABLE probe_state(account TEXT PRIMARY KEY,snapshot_json TEXT NOT NULL,
        first_seen_at REAL NOT NULL,received_at REAL NOT NULL,last_complete_at REAL);''',
    'wechat': '''CREATE TABLE messages(app_id TEXT,message_id TEXT,created_at REAL,payload TEXT,status TEXT,updated_at REAL);
        CREATE TABLE received_callback_archive(account_sha256 TEXT,callback_sha256 TEXT,
        received_at REAL,payload_json TEXT);
        CREATE TABLE video_card_jobs(job_id TEXT,state TEXT);
        CREATE TABLE receive_collection_jobs(job_id TEXT,state TEXT,result_status TEXT);
        CREATE TABLE company_note_assets(asset_id TEXT,status TEXT,archive_status TEXT);''',
    'whatsapp': '''CREATE TABLE events(event_key TEXT PRIMARY KEY,event_type TEXT,received REAL,raw_json TEXT);
        CREATE TABLE messages(message_key TEXT PRIMARY KEY,received REAL,content TEXT);
        CREATE TABLE recipients(session TEXT,account_id TEXT,chat_id TEXT,origin TEXT,created REAL);
        CREATE TABLE media_jobs(message_key TEXT,status TEXT,metadata_json TEXT,updated REAL);''',
}


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    connect = sqlite3.connect

    def local_connect(database, *args, **kwargs):
        assert str(database).startswith((str(tmp_path), tmp_path.as_uri())), '生产数据库不可用于测试'
        return connect(database, *args, **kwargs)

    def no_network(*args, **kwargs):
        raise AssertionError('测试不允许联网')

    monkeypatch.setattr(sqlite3, 'connect', local_connect)
    monkeypatch.setattr(socket.socket, 'connect', no_network)


@pytest.fixture
def paths(tmp_path):
    result = {}
    for key, sql in SCHEMAS.items():
        path = tmp_path / (key + '.sqlite3')
        with closing(sqlite3.connect(path)) as db, db:
            db.executescript(sql)
        result[key] = path
    return result


def execute(path, query, args=()):
    with closing(sqlite3.connect(path)) as db, db:
        db.execute(query, args)


def add(paths, key, at=NOW - 10, *, history=False, reason=None, raw=None, identity='fictional-1', state='running'):
    if key == 'alibaba_inbound':
        # history=false only counts as live when it carries a live_provenance object (221 contract).
        proof = {} if history else dict(live_provenance=dict(schema='amy-live-v1'))
        payload = raw if raw is not None else json.dumps(dict(history=history, role='buyer',
            account='fictional-account', conversation_id='fictional-conversation', content='虚构消息', **proof))
        execute(paths['inbound'], 'INSERT INTO inbound VALUES(?,?,?,?,?)', (identity, payload, 'archived', reason, at))
    elif key == 'alibaba_probe':
        payload = raw if raw is not None else json.dumps(dict(version='0.3.5.4', state=state, last_error='fictional-private-error'))
        execute(paths['probe'], 'INSERT INTO probe_state VALUES(?,?,?,?,?)', (identity, payload, at - 100, at, at))
    elif key == 'wechat_messages':
        execute(paths['wechat'], 'INSERT INTO messages VALUES(?,?,?,?,?,?)', ('fictional-app', identity, at, '{"text":"虚构消息"}', 'done', at))
    elif key == 'wechat_callbacks':
        execute(paths['wechat'], 'INSERT INTO received_callback_archive VALUES(?,?,?,?)', ('fictional-account-hash', identity, at, '{"text":"虚构回调"}'))
    elif key == 'whatsapp_events':
        execute(paths['whatsapp'], 'INSERT INTO events VALUES(?,?,?,?)', (identity, 'message.ack', at, '{"body":"虚构事件"}'))
    elif key == 'whatsapp_session':
        payload = raw if raw is not None else json.dumps(dict(payload=dict(status='WORKING'), me=dict(id='fictional-identity')))
        execute(paths['whatsapp'], 'INSERT INTO events VALUES(?,?,?,?)', (identity, 'session.status', at, payload))
    elif key == 'whatsapp_messages':
        execute(paths['whatsapp'], 'INSERT INTO messages VALUES(?,?,?)', (identity, at, '虚构消息'))


def source(paths, key, **options):
    from console.pipeline import PipelineHealth
    data = PipelineHealth(paths=paths, clock=lambda: NOW, **options).read()
    return next(item for item in data['sources'] if item['key'] == key)
