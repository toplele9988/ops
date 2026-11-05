from concurrent.futures import ThreadPoolExecutor
import sqlite3
import threading
from types import SimpleNamespace

import pytest

from console.names import NamesResolver, NamesRefresh
from console.names.store import MembersCache
from console.operator.store import Store
from conftest import GROUP, NOW


class Directory:
    def __init__(self):
        self.calls = 0
        self.error = None

    def refresh(self):
        self.calls += 1
        if self.error:
            raise self.error
        return {'group_failures': []}


@pytest.fixture
def env(state, tmp_path):
    e = SimpleNamespace(now=NOW, state=state, store=Store(tmp_path / 'operator.sqlite3'),
                        directory=Directory(), calls=[], groups=[f'fiction-room-{n}@chatroom' for n in range(7)], error=None)
    class Client:
        def get_chatroom_member_list(self, group):
            e.calls.append(group)
            if e.error:
                raise e.error
            return {'ret': 200, 'data': {'memberList': [dict(wxid='fiction-member', displayName='虚构成员')]}}
    e.client = Client()
    def refresh(**kwargs):
        return NamesRefresh(e.store, lambda: e.now, state_path=e.state, directory=e.directory,
                            members_client_factory=lambda: e.client, group_reader=lambda: e.groups, **kwargs)
    e.refresh = refresh
    return e


def test_three_per_poll_six_hours_and_rolling_day_survive_restart(env):
    assert env.refresh().poll()['groups_attempted'] == 3
    assert env.refresh().poll()['groups_attempted'] == 3
    assert env.refresh().poll()['groups_attempted'] == 1
    assert env.refresh().poll()['groups_attempted'] == 0
    assert env.directory.calls == 1 and len(set(env.calls)) == len(env.calls) == 7
    env.now += 21600 - .001
    assert env.refresh().poll()['directory_attempted'] == 0
    env.now += .001
    assert env.refresh().poll()['directory_attempted'] == 1
    assert env.directory.calls == 2 and len(env.calls) == 7
    env.now = NOW + 86400 - .001
    assert env.refresh().poll()['groups_attempted'] == 0
    env.now += .001
    assert env.refresh().poll()['groups_attempted'] == 3
    assert len(env.calls) == 10


def test_failure_claims_before_call_keeps_old_names_and_records_only_health(env):
    env.groups = [GROUP]
    cache = MembersCache(NamesResolver(env.state).members_path)
    cache.save(GROUP, {'fiction-member': ('虚构旧名', None)}, NOW - 1)
    before = cache.path.read_bytes()
    env.error = TimeoutError('fiction-secret-do-not-save')
    env.directory.error = RuntimeError('fiction-secret-do-not-save')
    result = env.refresh().poll()
    assert result['failures'] == 2 and env.calls == [GROUP]
    assert cache.path.read_bytes() == before
    assert NamesResolver(env.state).member_name(GROUP, 'fiction-member') == '虚构旧名'
    for delta in (1, 600, 21600):
        env.now = NOW + delta
        env.refresh().poll()
    assert env.calls == [GROUP]  # Failed groups have the same daily admission.
    assert env.directory.calls == 2
    with env.store.read() as db:
        rows = [dict(row) for row in db.execute('SELECT * FROM operator_health')]
    assert all(row['ok'] == 0 for row in rows)
    assert 'fiction-secret' not in str(rows) and GROUP not in str(rows)


def test_backoff_limits_failure_storm_across_different_groups(env):
    env.error = RuntimeError('fiction-secret')
    assert env.refresh().poll()['groups_attempted'] == 3
    env.now += 599
    assert env.refresh().poll()['groups_attempted'] == 0
    env.now += 1
    assert env.refresh().poll()['groups_attempted'] == 3
    assert len(set(env.calls)) == 6


def test_timeout_discards_late_response_without_saving(env):
    env.groups = [GROUP]
    release, finished = threading.Event(), threading.Event()
    def delayed(group):
        env.calls.append(group)
        release.wait(1)
        finished.set()
        return {'ret': 200, 'data': {'memberList': [dict(wxid='fiction-member', displayName='虚构迟到名')]}}
    env.client.get_chatroom_member_list = delayed
    try:
        result = env.refresh(timeout=.02).poll()
        assert result['failures'] == 1 and result['groups_saved'] == 0
    finally:
        release.set()
    assert finished.wait(1)
    assert not NamesResolver(env.state).members_path.exists()
    assert env.refresh(timeout=.02).poll()['groups_attempted'] == 0
    assert env.calls == [GROUP]


@pytest.mark.parametrize('payload', [None, {'ret': True, 'data': {'memberList': []}},
    {'ret': 200, 'data': {'memberList': []}},
    {'ret': 401, 'message': 'fiction-secret'}, {'ret': 200, 'data': {}},
    {'ret': 200, 'data': {'memberList': [{'wxid': 'fiction-member', 'displayName': {}}]}},
    {'ret': 200, 'data': {'memberList': [{'wxid': 'fiction-member'}, {'wxid': 'fiction-member'}]}}])
def test_malformed_reply_is_not_saved_or_retried(env, payload):
    env.groups = [GROUP]
    def fetch(group):
        env.calls.append(group)
        return payload
    env.client.get_chatroom_member_list = fetch
    result = env.refresh().poll()
    assert result['failures'] == 1 and result['groups_saved'] == 0
    assert env.refresh().poll()['groups_attempted'] == 0
    assert env.calls == [GROUP]


def test_concurrent_pollers_share_durable_reservations(env):
    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(lambda _: env.refresh().poll(), range(2)))
    assert sum(r['directory_attempted'] for r in results) == 1
    assert sum(r['groups_attempted'] for r in results) == 6
    assert len(env.calls) == len(set(env.calls)) == 6


def test_default_group_reader_only_returns_groups_with_messages(env):
    refresh = NamesRefresh(env.store, lambda: env.now, state_path=env.state, directory=env.directory,
                           members_client_factory=lambda: env.client)
    refresh.poll()
    assert env.calls == [GROUP]


def test_incomplete_directory_retries_hourly(env):
    env.directory.error = RuntimeError('fiction partial')
    env.refresh().poll()
    env.now = NOW + 1800
    env.refresh().poll()
    assert env.directory.calls == 1
    env.now = NOW + 3601
    env.refresh().poll()
    assert env.directory.calls == 2
