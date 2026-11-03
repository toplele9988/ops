from contextlib import contextmanager
import dataclasses
import json
from pathlib import Path
import sqlite3
import sys
import threading
from types import SimpleNamespace

import pytest

from conftest import selected

NOW = 2_000_000_000.0
SCHEMA = '''
CREATE TABLE messages(app_id TEXT,message_id TEXT,group_id TEXT,sender_id TEXT,created_at REAL,
    payload TEXT,status TEXT DEFAULT 'pending',rule TEXT DEFAULT '',updated_at REAL,PRIMARY KEY(app_id,message_id));
CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT);
CREATE TABLE message_scopes(app_id TEXT,message_id TEXT,profile_id TEXT,scope_id TEXT,own_wxid TEXT,business_role TEXT,
    PRIMARY KEY(app_id,message_id));
CREATE TABLE inbound_routes(app_id TEXT,message_id TEXT,route TEXT,PRIMARY KEY(app_id,message_id));
CREATE TABLE owner_messages(status TEXT);
CREATE TABLE onboarding_requests(status TEXT);
'''


@dataclasses.dataclass
class Message:
    app_id: str = 'fiction-app'
    message_id: str = 'fiction-1'
    group_id: str = 'fiction-group@chatroom'
    sender_id: str = 'fiction-sender'
    own_wxid: str = 'fiction-account'
    created_at: float = NOW - 5
    text: str = '虚构资料'
    at_ids: tuple = ()
    attachments: tuple = ()


class Store:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.db.execute("INSERT INTO metadata VALUES('paused','true')")
        self.db.commit()
        self.lock = threading.RLock()

    @contextmanager
    def transaction(self):
        with self.lock, self.db:
            yield self.db

    def binding_created_at(self, *args):
        return NOW - 100

    def message_scope_matches(self, *args, **kwargs):
        return True


@pytest.fixture
def harness(candidate, tmp_path, monkeypatch):
    store = Store(tmp_path / 'fiction-wechat.sqlite3')
    state = SimpleNamespace(message=Message(), retained=0, closed=False)

    def capture(*args, **kwargs):
        state.retained += 1

    def no_send(*args, **kwargs):
        raise AssertionError('虚构接收链不允许发送或调用模型')

    monkeypatch.setitem(sys.modules, 'receive_only', SimpleNamespace(capture=capture))
    monkeypatch.setitem(sys.modules, 'group_lifecycle', SimpleNamespace(ingest=lambda *a: False,
        is_closed=lambda *a: state.closed))
    monkeypatch.setitem(sys.modules, 'video_knowledge.collection_sources',
        SimpleNamespace(ingest_callback=lambda *a, **k: {'status': 'disabled'}))
    monkeypatch.setitem(sys.modules, 'group_onboarding', SimpleNamespace(ingest_candidate=lambda *a: None))
    monkeypatch.setitem(sys.modules, 'collection_policy',
        SimpleNamespace(enabled=lambda config: config.get('collection_only_groups') is True))
    env = {'MAX_BODY': 4*1024*1024, 'MAX_QUEUE': 200, 'dataclasses': dataclasses,
           'normalize_group_message': lambda payload: state.message,
           'record_media_progress': lambda *args: True,
           'complete_media_progress': lambda *args: None,
           '_json': lambda value: json.dumps(value, ensure_ascii=False)}
    fn = selected(candidate / 'wechat/api_groupbot.py', {'_ingest_message'}, env,
                  class_name='Runner')['_ingest_message']
    original = selected(Path('/opt/imgw/wechat/api_groupbot.py'), {'_ingest_message'}, env,
                        class_name='Runner')['_ingest_message']
    group = {'reply_mode': 'silent', 'business_role': 'learning'}
    config = dict(app_id='fiction-app', active_after=NOW-100, collection_only_groups=True,
                  receive_only_mode=True, send_enabled=False)
    runner = SimpleNamespace(store=store, config_path=tmp_path / 'unused-fiction-config.json',
        now=lambda: NOW, owner_control=SimpleNamespace(ingest=lambda *a: None),
        _group=lambda *a, **k: group, _memory_scope=lambda *a: ('fiction-profile', 'fiction-scope'),
        _upgrade_pending_group_file=lambda *a: False, client_factory=no_send)

    def ingest(number=1, *, baseline=False):
        state.message = dataclasses.replace(state.message, message_id=f'fiction-{number}')
        return (original if baseline else fn)(runner, {}, config)

    yield SimpleNamespace(store=store, state=state, group=group, config=config, runner=runner, ingest=ingest)
    store.db.close()


def test_original_silent_queue_stops_at_exactly_200_with_pause_untouched(harness):
    h = harness
    for n in range(200):
        assert h.ingest(n, baseline=True) == 200
    assert h.ingest(201, baseline=True) == 200  # Overflow is archived silently already.
    assert h.store.db.execute("SELECT count(*) FROM messages WHERE status='pending'").fetchone()[0] == 200
    assert h.store.db.execute("SELECT count(*) FROM messages WHERE status='collected_silent'").fetchone()[0] == 1


def test_receive_only_205_rows_keep_scopes_payloads_and_do_not_fill_reply_queue(harness):
    h = harness
    for n in range(205):
        assert h.ingest(n) == 200
    assert h.store.db.execute("SELECT status,count(*) FROM messages GROUP BY status").fetchone()[:] == ('collected_silent', 205)
    assert h.store.db.execute('SELECT count(*) FROM message_scopes').fetchone()[0] == 205
    assert h.store.db.execute('SELECT count(*) FROM inbound_routes').fetchone()[0] == 205
    assert h.store.db.execute("SELECT value FROM metadata WHERE key='paused'").fetchone()[0] == 'true'
    assert h.state.retained == 205
    assert json.loads(h.store.db.execute('SELECT payload FROM messages LIMIT 1').fetchone()[0])['text'] == '虚构资料'


@pytest.mark.parametrize('change', [
    {'receive_only_mode': False}, {'send_enabled': True}, {'collection_only_groups': False},
])
def test_other_modes_keep_original_queue_behavior(harness, change):
    h = harness
    h.config.update(change)
    assert h.ingest() == 200
    assert h.store.db.execute('SELECT status FROM messages').fetchone()[0] == 'pending'


def test_public_reply_mode_keeps_pending(harness):
    h = harness
    h.config['receive_only_mode'] = False
    h.config['community_public_reply'] = {'enabled': True}
    assert h.ingest() == 200
    assert h.store.db.execute('SELECT status FROM messages').fetchone()[0] == 'pending'


def test_non_silent_group_does_not_bypass_full_queue(harness):
    h = harness
    h.group['reply_mode'] = 'participate'
    for n in range(200):
        assert h.ingest(n) == 200
    assert h.ingest(201) == 503
    assert h.store.db.execute('SELECT count(*) FROM messages').fetchone()[0] == 200


def test_duplicate_does_not_requeue_or_reclassify_existing_pending(harness):
    h = harness
    assert h.ingest(baseline=True) == 200
    before = [tuple(r) for r in h.store.db.execute('SELECT * FROM messages')]
    assert h.ingest() == 200
    assert [tuple(r) for r in h.store.db.execute('SELECT * FROM messages')] == before


@pytest.mark.parametrize('boundary', ['closed', 'account', 'old', 'future'])
def test_original_admission_boundaries_still_reject(harness, boundary):
    h = harness
    if boundary == 'closed':
        h.state.closed = True
    elif boundary == 'account':
        h.state.message.app_id = 'fiction-other-account'
    elif boundary == 'old':
        h.state.message.created_at = NOW - 101
    else:
        h.state.message.created_at = NOW + 61
    assert h.ingest() == 200
    assert h.store.db.execute('SELECT count(*) FROM messages').fetchone()[0] == 0
