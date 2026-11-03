"""Fictional channels only. All transports and private-file ownership are injected."""
from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

NOW = 1_800_000_000.0
SECRET = 'fictional-authorization-never-output'


def ali_message(cid='alice', role='buyer', age=60, text='Fictional question?', mid='m1'):
    return dict(account='fictional-account', conversation_id=cid, buyer_name='Fictional ' + cid,
                role=role, content=text, message_id=mid, country='Fictionland',
                sent_at=datetime.fromtimestamp(NOW - age, timezone.utc).isoformat())


def seed_ali(path, rows=None):
    with closing(sqlite3.connect(path)) as db, db:
        db.execute('CREATE TABLE IF NOT EXISTS inbound(id INTEGER PRIMARY KEY,payload TEXT)')
        db.executemany('INSERT INTO inbound(payload) VALUES(?)',
                       [(json.dumps(row),) for row in rows or [ali_message()]])


def wx_message(group='fictional-customer@chatroom', mid='wx-m1', sender='fictional-other',
               text='虚构资料请核对', age=30, **extra):
    return dict(dict(group_id=group, message_id=mid, sender_id=sender, own_wxid='fictional-own',
                text=text, created_at=int(NOW - age), attachments=[]), **extra)


def seed_wx(path, rows=None, roles=None):
    rows = rows if rows is not None else [wx_message()]
    with closing(sqlite3.connect(path)) as db, db:
        db.executescript('''CREATE TABLE messages(app_id TEXT,message_id TEXT,payload TEXT);
            CREATE TABLE message_scopes(app_id TEXT,message_id TEXT,scope_id TEXT,business_role TEXT);
            CREATE TABLE onboarding_candidates(group_id TEXT,group_name TEXT);
            CREATE TABLE fictional_rooms(group_id TEXT,room_name TEXT);
            CREATE TABLE fictional_contacts(wxid TEXT,nickname TEXT);''')
        db.executemany('INSERT INTO messages VALUES(?,?,?)', [('fictional-app', str(r['message_id']), json.dumps(r)) for r in rows])
        if roles is None:
            roles = {str(r['message_id']): 'customer' for r in rows}
        db.executemany('INSERT INTO message_scopes VALUES(?,?,?,?)',
                       [('fictional-app', mid, 'fictional-scope', role) for mid, role in roles.items()])


def fake_stat(path):
    info = path.lstat()
    fields = {k: getattr(info, k) for k in ('st_mode', 'st_size', 'st_ino', 'st_dev', 'st_mtime_ns')}
    return SimpleNamespace(**fields, st_uid=0, st_gid=731)


def seed_config(path, **extra):
    Path(path).write_text(json.dumps(dict(waha_url='http://127.0.0.1:12345',
        waha_api_key=SECRET, session='fictional-session', ignored_secret='fictional-ignored', **extra)))
    Path(path).chmod(0o640)


class FakeWAHA:
    def __init__(self):
        self.tick = 0
        self.calls = []
        self.error = None
        self.status = 'WORKING'
        self.chats = [dict(id='15550001111@c.us', name='Fictional WA customer',
            lastMessage=dict(body='Fictional quotation question?', fromMe=False, timestamp=NOW - 20, hasMedia=False)),
            dict(id='15550002222-77@g.us', name='Fictional WA group',
            lastMessage=dict(body='', fromMe=True, timestamp=NOW - 50, hasMedia=True))]
        self.history = [dict(id='fake-wa-2', body='Fictional answer', fromMe=True, timestamp=NOW - 5),
                        dict(id='fake-wa-1', body='Fictional question?', fromMe=False, timestamp=NOW - 20)]

    def fetch(self, method, url, headers, timeout):
        self.calls.append((method, url, headers, timeout))
        assert method == 'GET'
        assert timeout == 5
        assert headers == {'X-Api-Key': SECRET}
        if self.error:
            raise self.error
        if '/api/sessions/' in url:
            return dict(status=self.status, me={'id': '15559998888@c.us'})
        if '/chats/overview?limit=500' in url:
            return self.chats
        if '/messages?limit=100&downloadMedia=false' in url:
            return self.history
        raise AssertionError('unexpected fictional GET route')


def request(query=None, key='', authenticated=True, recheck=True):
    async def body():
        return b''
    return SimpleNamespace(state=SimpleNamespace(password_principal=SimpleNamespace(username='fictional-user'),
        password_recheck=lambda: recheck, session=SimpleNamespace(authenticated=authenticated), form=None),
        query_params=query or {}, path_params={'key': key, 'id': '1'}, body=body)


def seed_probe(path, age=60, state='collecting', account='fictional-account'):
    with closing(sqlite3.connect(path)) as db, db:
        db.execute('CREATE TABLE IF NOT EXISTS probe_state(account TEXT PRIMARY KEY,snapshot_json TEXT,first_seen_at REAL,received_at REAL,last_complete_at REAL)')
        db.execute('INSERT OR REPLACE INTO probe_state VALUES(?,?,?,?,?)', (account,
            json.dumps(dict(version='fictional-1.2', state=state, last_error='fictional-private-error')),
            NOW - 10000, NOW - age, NOW - age))
