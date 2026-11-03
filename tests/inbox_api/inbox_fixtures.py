"""Entirely fictional inputs. Never opens production data or an actual AI runner."""
from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

NOW = 1_800_000_000.0


def message(conversation='conv-a', account='fictional-alpha', buyer='Fictional Alice',
            role='buyer', text='Fictional product question', age=7200, message_id='message-a', **extra):
    return dict(account=account, buyer_name=buyer, conversation_id=conversation, content=text,
                role=role, sent_at=datetime.fromtimestamp(NOW - age, timezone.utc).isoformat(),
                observed_at='2027-01-15T08:00:00+00:00', country='Fictionland', history=False,
                message_id=message_id, event_id='fictional-event', url='https://example.invalid/chat', **extra)


def fixture():
    first = message()
    return [first, {**first, 'content': 'Duplicate must be ignored'},
            message(role='unknown', text='Fictional observation', age=3600, message_id='message-a2'),
            message('conv-b', buyer='Fictional Bob', age=3000, message_id='message-b1'),
            message('conv-b', buyer='Fictional Bob', role='seller', text='Fictional seller answer', age=1000, message_id='message-b2'),
            message('conv-c', age=90000, message_id='message-a'),  # same ID, different conversation
            message(account='fictional-beta', age=600, message_id='message-a'),  # same conversation ID, different account
            message('conv-u', buyer='Fictional Unknown', role='unknown', age=50, message_id=None),
            message('conv-u', buyer='Fictional Unknown', role='unknown', age=50, message_id=None)]


def seed(path, messages=None):
    with closing(sqlite3.connect(path)) as db, db:
        db.execute('CREATE TABLE IF NOT EXISTS inbound (id INTEGER PRIMARY KEY, payload TEXT, fingerprint TEXT, '
                   'state TEXT, created_at REAL, updated_at REAL, draft TEXT)')
        for item in fixture() if messages is None else messages:
            db.execute('INSERT INTO inbound (payload) VALUES (?)',
                       (json.dumps(item, ensure_ascii=False) if not isinstance(item, str) else item,))


def request(*, query=None, form=None, authenticated=True, principal=True, recheck=True,
            username='fictional-tester', body=b'', key='', draft_id='1'):
    async def read_body():
        return body
    return SimpleNamespace(state=SimpleNamespace(
        password_principal=SimpleNamespace(username=username) if principal else None,
        password_recheck=lambda: recheck, session=SimpleNamespace(authenticated=authenticated), form=form),
        query_params=query or {}, path_params={'key': key, 'id': draft_id}, body=read_body)


def paths(root):
    root = Path(root)
    return root / 'fictional-inbound.sqlite3', root / 'private' / 'drafts.sqlite3', root / 'fictional-wechat.sqlite3', root / 'fictional-whatsapp'
