"""Fictional databases only; all real transports and source DBs are refused."""
import json
from pathlib import Path
import sqlite3

import pytest

NOW = 2_000_000_000.0
GROUP = 'fiction-room-a@chatroom'


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('network forbidden')
    monkeypatch.setattr('socket.socket.connect', forbidden)
    monkeypatch.setattr('socket.create_connection', forbidden)
    connect = sqlite3.connect
    def temporary(path, *args, **kwargs):
        assert str(tmp_path) in str(path), 'fictional databases only'
        return connect(path, *args, **kwargs)
    monkeypatch.setattr(sqlite3, 'connect', temporary)


@pytest.fixture
def state(tmp_path):
    path = tmp_path / 'state.sqlite3'
    with sqlite3.connect(path) as db:
        db.executescript('''CREATE TABLE messages(app_id TEXT,message_id TEXT,group_id TEXT,created_at REAL,payload TEXT);
            CREATE TABLE message_scopes(app_id TEXT,message_id TEXT,scope_id TEXT,business_role TEXT);''')
        p = dict(group_id=GROUP, message_id='fiction-mid', sender_id='fiction-member', own_wxid='fiction-own',
                 text='虚构产品资料请核对', created_at=NOW - 20)
        db.execute('INSERT INTO messages VALUES(?,?,?,?,?)', ('fiction-app', p['message_id'], GROUP, p['created_at'], json.dumps(p)))
        db.execute('INSERT INTO message_scopes VALUES(?,?,?,?)', ('fiction-app', p['message_id'], 'fiction-scope', 'customer'))
    return path


def directory(path, entries):
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE IF NOT EXISTS directory_contacts(id TEXT PRIMARY KEY,kind TEXT,first_seen REAL,last_seen REAL,remark TEXT,nickname TEXT)')
        db.executemany('INSERT OR REPLACE INTO directory_contacts VALUES(?,?,0,0,?,?)', entries)


def fingerprint(root):
    return {str(p.relative_to(root)): (p.read_bytes(), p.stat().st_mtime_ns, p.stat().st_mode)
            for p in Path(root).rglob('*') if p.is_file()}
