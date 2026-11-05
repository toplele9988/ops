"""F2b refresh acceptance: fictional source files and two-method clients only."""
from contextlib import closing
import hashlib
import json
import sqlite3
import threading
import time
from types import SimpleNamespace

import pytest

from console.directory import RefreshFailed, RefreshLimited, WechatDirectory


NOW = 2_000_000_000.0
SECRET = 'fictional-refresh-secret-must-not-leak'


def group(number):
    return f'fiction-refresh-{number:03d}@chatroom'


def contacts(*rooms, friends=(), ghs=()):
    return {'ret': 200, 'data': {'friends': list(friends), 'chatrooms': list(rooms), 'ghs': list(ghs)}}


def brief(names):
    return {'ret': 200, 'data': [dict(userName=identity, nickName=name) for identity, name in names.items()]}


def fingerprint(path):
    return hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns


class FakeClient:
    """Deliberately has no sending, enrollment, details, or friend lookup API."""
    def __init__(self, contact_result, brief_result=None):
        self.contact_result = contact_result
        self.brief_result = brief_result
        self.contact_calls = 0
        self.brief_calls = []

    def fetch_contacts_list(self):
        self.contact_calls += 1
        if isinstance(self.contact_result, Exception):
            raise self.contact_result
        return self.contact_result() if callable(self.contact_result) else self.contact_result

    def get_brief_info(self, ids):
        self.brief_calls.append(list(ids))
        assert 1 <= len(ids) <= 50 and len(ids) == len(set(ids))
        assert all(identity.endswith('@chatroom') for identity in ids)
        if isinstance(self.brief_result, Exception):
            raise self.brief_result
        return self.brief_result(ids) if callable(self.brief_result) else self.brief_result


@pytest.fixture
def env(tmp_path, monkeypatch):
    def offline(*args, **kwargs):
        raise AssertionError('refresh-name tests must not access the network')
    monkeypatch.setattr('socket.socket.connect', offline)
    monkeypatch.setattr('socket.create_connection', offline)
    e = SimpleNamespace(discovery=tmp_path / 'fiction-discovery.sqlite3',
        config=tmp_path / 'fiction-config.json', state=tmp_path / 'fiction-state.sqlite3',
        cache=tmp_path / 'fiction-directory.sqlite3', now=NOW, groups={}, conversations={})
    with closing(sqlite3.connect(e.discovery)) as db, db:
        db.execute('CREATE TABLE identities(identity TEXT PRIMARY KEY,kind TEXT,first_seen REAL,last_seen REAL)')
    with closing(sqlite3.connect(e.state)) as db, db:
        db.execute('CREATE TABLE messages(group_id TEXT,message_id TEXT,created_at REAL)')
        db.execute('CREATE TABLE onboarding_candidates(group_id TEXT PRIMARY KEY,group_name TEXT)')

    def configure():
        e.config.write_text(json.dumps({'groups': e.groups, 'secret': SECRET}), encoding='utf8')

    def discover(*identities):
        with closing(sqlite3.connect(e.discovery)) as db, db:
            db.executemany('INSERT INTO identities VALUES(?,?,?,?)',
                [(identity, 'group', NOW - 1000, NOW - 10) for identity in identities])

    def service(client=None, **kwargs):
        return WechatDirectory(discovery_path=e.discovery, config_path=e.config, state_path=e.state,
            cache_path=e.cache, clock=lambda: e.now, readonly_client=client,
            conversation_reader=lambda path, now: e.conversations, **kwargs)

    def rows(directory=None):
        directory = directory or service()
        return {row['id']: row for row in directory.list({'kind': 'all', 'page_size': 100})['items']}

    e.configure, e.discover, e.service, e.rows = configure, discover, service, rows
    configure()
    return e


def test_101_groups_use_exact_batches_and_never_lookup_friends(env):
    ids = [group(n) for n in range(101)]
    env.discover(*ids)
    client = FakeClient(contacts(*ids, friends=['fiction-person'], ghs=['gh_fiction']),
        lambda batch: brief({identity: f'SUP001 虚构群 {identity}' for identity in batch}))
    before = {path: fingerprint(path) for path in (env.discovery, env.config, env.state)}
    result = env.service(client).refresh()
    assert client.contact_calls == 1
    assert [len(batch) for batch in client.brief_calls] == [50, 50, 1]
    assert [identity for batch in client.brief_calls for identity in batch] == ids
    assert result['received'] == 103 and result['by_kind'] == {'group': 101, 'private': 1, 'official': 1}
    assert result['groups_named'] == 101 and result['missing_group_ids'] == [] and result['complete'] is True
    assert before == {path: fingerprint(path) for path in before}
    assert env.service().summary()['not_enrolled_suspected'] == {'supplier': 101, 'customer': 0}


def test_saved_discovery_groups_are_named_even_if_not_in_current_contacts(env):
    env.discover(group(1), group(2))
    client = FakeClient(contacts(group(2), group(3)),
        lambda ids: brief({identity: 'CUS001 虚构客户群' for identity in ids}))
    result = env.service(client).refresh()
    assert client.brief_calls == [[group(1), group(2), group(3)]]
    assert result['received'] == 2 and result['groups_named'] == 3
    assert set(env.rows()) == {group(1), group(2), group(3)}


def test_all_21_previously_unnamed_groups_reclassify_without_enrolling(env):
    ids = [group(n) for n in range(21)]
    env.discover(*ids)
    names = {identity: ('SUP001 虚构供应商' if n < 8 else 'CUS002 虚构客户' if n < 15
        else 'SUP003 虚构快递' if n < 18 else 'SUP004 虚构包装') for n, identity in enumerate(ids)}
    service = env.service(FakeClient(contacts(*ids), brief(names)))
    assert service.summary()['not_enrolled_suspected'] == {'supplier': 0, 'customer': 0}
    assert all(row['name'] == '未命名群' for row in env.rows(service).values())
    assert service.refresh()['groups_named'] == 21
    rows = env.rows(service)
    assert all(row['name'] == names[identity] and row['name_source'] == 'refresh_cache'
        and row['name_updated_at'] == NOW and row['name_update_label'].startswith('名称更新于 ')
        for identity, row in rows.items())
    assert rows[group(15)]['named_role'] == 'forwarder'
    assert rows[group(18)]['named_role'] == 'vendor'
    assert '像是供应商群' in rows[group(0)]['named_role_hint']
    assert '像是客户群' in rows[group(8)]['named_role_hint']
    assert all(row['registered'] is False and row['collect_state'] == 'not_enrolled'
        and row['inbox_visible'] is False for row in rows.values())
    summary = service.summary()
    assert summary['not_enrolled_suspected'] == {'supplier': 14, 'customer': 7}
    assert summary['groups']['directory_not_enrolled'] == 21
    assert summary['groups']['registered'] == 0
    assert summary['name_updated_at'] == NOW and summary['name_update_label'].startswith('名称更新于 ')


def test_cache_then_config_then_conversation_then_placeholder_precedence(env):
    env.discover(group(1), group(2), group(3), group(4), group(5))
    env.conversations = {group(5): {'buyer_name': 'CUS005 虚构会话保留名', 'counterpart_role': 'unclassified'},
        group(1): {'buyer_name': 'CUS001 虚构会话旧名', 'counterpart_role': 'unclassified'},
        group(2): {'buyer_name': 'CUS002 虚构会话旧名', 'counterpart_role': 'unclassified'},
        group(3): {'buyer_name': 'CUS003 虚构会话保留名', 'counterpart_role': 'unclassified'}}
    client = FakeClient(contacts(), brief({group(1): 'SUP001 虚构缓存名', group(2): 'SUP002 虚构缓存名'}))
    service = env.service(client)
    assert service.refresh()['groups_named'] == 2
    env.groups[group(1)] = {'enabled': True, 'business_role': 'customer', 'group_name': 'CUS001 虚构配置名'}
    env.configure()
    rows = env.rows(service)
    # WeChat's current name beats a registered name that may predate a rename.
    assert (rows[group(1)]['name'], rows[group(1)]['name_source']) == ('SUP001 虚构缓存名', 'refresh_cache')
    env.groups[group(3)] = {'enabled': True, 'business_role': 'customer', 'group_name': 'CUS003 虚构配置名'}
    env.configure()
    rows = env.rows(service)
    assert (rows[group(3)]['name'], rows[group(3)]['name_source']) == ('CUS003 虚构配置名', 'config')
    assert (rows[group(2)]['name'], rows[group(2)]['name_source']) == ('SUP002 虚构缓存名', 'refresh_cache')
    assert rows[group(2)]['named_role'] == 'supplier'
    assert (rows[group(5)]['name'], rows[group(5)]['name_source']) == ('CUS005 虚构会话保留名', 'conversation')
    assert (rows[group(4)]['name'], rows[group(4)]['name_source']) == ('未命名群', 'unknown')


def test_nickname_and_remark_preserve_only_verified_fields(env):
    env.discover(group(1), group(2))
    response = {'ret': 200, 'secret': SECRET, 'data': [
        {'userName': group(1), 'nickName': 'SUP001 虚构正式群名', 'remark': '虚构备注', 'token': SECRET},
        {'userName': group(2), 'remark': 'CUS002 虚构备注客户', 'unverifiedDisplayName': SECRET}]}
    service = env.service(FakeClient(contacts(), response))
    assert service.refresh()['groups_named'] == 2
    rows = env.rows(service)
    assert rows[group(1)]['name'] == '虚构备注'
    assert rows[group(2)]['name'] == 'CUS002 虚构备注客户'
    cached = {row['id']: row for row in service.cache.read()}
    assert cached[group(1)]['nickname'] == 'SUP001 虚构正式群名'
    assert cached[group(1)]['remark'] == '虚构备注'
    assert SECRET not in env.cache.read_bytes().decode('latin1')
    assert SECRET not in json.dumps(rows)


@pytest.mark.parametrize('response', [
    None, {}, [], {'ret': True, 'data': []}, {'ret': 403, 'data': [], 'message': SECRET},
    {'ret': 200}, {'ret': 200, 'data': {}},
    {'ret': 200, 'data': [{'nickName': '虚构无标识'}]},
    {'ret': 200, 'data': [{'userName': group(999), 'nickName': '虚构错误标识'}]},
    {'ret': 200, 'data': [{'userName': group(1), 'nickName': '虚构重复'},
                          {'userName': group(1), 'nickName': '虚构重复'}]},
    {'ret': 200, 'data': [{'userName': group(1), 'nickName': {'string': '虚构不猜字段'}}]},
    {'ret': 200, 'data': [{'userName': group(1), 'nickName': ' 虚构空白 '} ]},
    {'ret': 200, 'data': [{'userName': group(1), 'nickName': '虚构\n换行'}]},
    RuntimeError(SECRET), TimeoutError(SECRET),
], ids=['unknown', 'empty', 'list', 'bool-ret', 'rejected', 'no-data', 'dict-data',
        'no-id', 'wrong-id', 'duplicate', 'object-name', 'padded-name', 'control-name', 'raised', 'timeout'])
def test_bad_brief_retains_contacts_reports_missing_and_never_retries(env, response, caplog, capsys):
    env.discover(group(1))
    client = FakeClient(contacts(group(1), friends=['fiction-person']), response)
    service = env.service(client)
    result = service.refresh()
    assert result['received'] == 2 and result['groups_named'] == 0
    assert result['missing_group_ids'] == [group(1)] and result['complete'] is False
    assert result['group_failures'] and any('不重试' in failure['reason'] for failure in result['group_failures'])
    assert env.rows(service)[group(1)]['name'] == '未命名群'
    assert client.contact_calls == 1 and client.brief_calls == [[group(1)]]
    with pytest.raises(RefreshLimited):
        service.refresh()
    assert client.contact_calls == 1 and client.brief_calls == [[group(1)]]
    output = capsys.readouterr()
    assert SECRET not in json.dumps(result) + caplog.text + output.out + output.err
    assert SECRET not in env.cache.read_bytes().decode('latin1')


def test_partial_and_missing_name_rows_do_not_invent_fields(env):
    env.discover(group(1), group(2), group(3), group(4))
    client = FakeClient(contacts(), {'ret': 200, 'data': [
        {'userName': group(1), 'nickName': 'SUP001 虚构可确认群'},
        {'userName': group(2)},
        {'userName': group(3), 'nickname': '虚构错误大小写', 'displayName': '虚构未证实字段'}]})
    result = env.service(client).refresh()
    assert result['groups_named'] == 1 and result['missing_group_ids'] == [group(2), group(3), group(4)]
    # Unnamed groups are asked alone; this fixed reply names other groups too,
    # which is an unconfirmable format, so the single retries stop at once.
    assert result['complete'] is False and client.brief_calls == [[group(n) for n in (1, 2, 3, 4)], [group(2)]]
    rows = env.rows()
    assert rows[group(1)]['name'] == 'SUP001 虚构可确认群'
    assert all(rows[group(n)]['name'] == '未命名群' for n in (2, 3, 4))


def test_timeout_discards_late_brief_and_stops_later_batches(env):
    ids = [group(n) for n in range(51)]
    env.discover(*ids)
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    def delayed(batch):
        entered.set()
        release.wait(2)
        finished.set()
        return brief({identity: 'SUP001 虚构迟到名称' for identity in batch})
    client = FakeClient(contacts(*ids), delayed)
    service = env.service(client, refresh_timeout=.1)
    try:
        result = service.refresh()
        assert entered.is_set()
        assert result['received'] == 51 and result['groups_named'] == 0
        assert result['missing_group_ids'] == ids and result['complete'] is False
        assert any('超时' in failure['reason'] for failure in result['group_failures'])
        before = fingerprint(env.cache)
    finally:
        release.set()
    assert finished.wait(1)
    assert client.brief_calls == [ids[:50]]
    assert fingerprint(env.cache) == before
    assert all(row['name'] == '未命名群' for row in env.rows(service).values())


def test_explicit_contact_failure_never_starts_brief(env):
    env.discover(group(1))
    client = FakeClient(RuntimeError(SECRET), brief({group(1): '虚构不可采用'}))
    service = env.service(client)
    with pytest.raises(RefreshFailed) as caught:
        service.refresh()
    assert caught.value.status_code == 502 and SECRET not in str(caught.value)
    assert client.contact_calls == 1 and client.brief_calls == []
    assert env.rows(service)[group(1)]['name'] == '未命名群'


def test_named_cache_skips_lookup_but_stale_config_name_does_not(env):
    env.discover(group(1), group(2))
    # The collection config keeps the name from before the group was renamed.
    env.groups[group(2)] = {'enabled': True, 'business_role': 'customer', 'group_name': 'CUS002 虚构旧名'}
    env.configure()
    def answer(batch):
        return brief({group(1): 'SUP001 虚构缓存名'} if len(batch) > 1 else {batch[0]: 'CUS002 虚构新名'})
    client = FakeClient(contacts(group(1), group(2)), answer)
    service = env.service(client)
    assert service.refresh()['groups_named'] == 2
    assert client.brief_calls == [[group(1), group(2)], [group(2)]]
    assert env.rows(service)[group(2)]['name'] == 'CUS002 虚构新名'
    env.now += 600
    result = service.refresh()
    assert result['groups_named'] == 0 and result['complete'] is True
    assert client.contact_calls == 2 and len(client.brief_calls) == 2
    assert env.rows(service)[group(1)]['name_updated_at'] == NOW


def test_legacy_cache_is_read_without_migration_then_upgraded_only_on_refresh(env):
    with closing(sqlite3.connect(env.cache)) as db, db:
        db.execute('CREATE TABLE directory_contacts(id TEXT PRIMARY KEY,kind TEXT NOT NULL,first_seen REAL NOT NULL,last_seen REAL NOT NULL)')
        db.execute('CREATE TABLE directory_refresh_attempt(singleton INTEGER PRIMARY KEY,attempted_at REAL NOT NULL)')
        db.execute('INSERT INTO directory_contacts VALUES(?,?,?,?)', (group(1), 'group', NOW - 1000, NOW - 1000))
    env.discover(group(1))
    service = env.service(FakeClient(contacts(group(1)), brief({group(1): 'SUP001 虚构升级名称'})))
    before = fingerprint(env.cache)
    assert env.rows(service)[group(1)]['name'] == '未命名群'
    service.summary()
    assert fingerprint(env.cache) == before
    assert service.refresh()['groups_named'] == 1
    assert env.rows(service)[group(1)]['name'] == 'SUP001 虚构升级名称'
    assert service.cache.read()[0]['first_seen'] == NOW - 1000
    after = fingerprint(env.cache)
    service.list()
    service.summary()
    assert fingerprint(env.cache) == after


def test_late_contact_result_cannot_trigger_brief_or_write_contacts(env):
    env.discover(group(1))
    release, finished = threading.Event(), threading.Event()
    def delayed():
        release.wait(2)
        finished.set()
        return contacts(group(2))
    client = FakeClient(delayed, brief({group(1): '虚构不得查询'}))
    service = env.service(client, refresh_timeout=.05)
    try:
        with pytest.raises(RefreshFailed) as caught:
            service.refresh()
        assert caught.value.status_code == 504
        before = fingerprint(env.cache)
    finally:
        release.set()
    assert finished.wait(1)
    assert client.brief_calls == [] and fingerprint(env.cache) == before
    assert set(env.rows(service)) == {group(1)}


def test_contact_extra_name_fields_are_not_interpreted_as_real_names(env):
    response = contacts(group(1), friends=['fiction-person'])
    response['data'].update(nickName=SECRET, contacts=[{'userName': 'fiction-person', 'nickName': SECRET}])
    client = FakeClient(response, {'ret': 200, 'data': []})
    service = env.service(client)
    result = service.refresh()
    rows = env.rows(service)
    assert rows[group(1)]['name'] == '未命名群'
    assert rows['fiction-person']['name'] == '未命名联系人'
    assert client.brief_calls == [[group(1)]]
    assert SECRET not in json.dumps(result) + json.dumps(rows) + env.cache.read_bytes().decode('latin1')


def test_contact_and_all_brief_batches_share_one_total_budget(env, monkeypatch):
    from console.directory import refresh
    ids = [group(n) for n in range(101)]
    env.discover(*ids)
    elapsed = [0.0]
    # Advance a simulated monotonic clock at each completed fake request. This
    # verifies the shared deadline without sleeping for a production-size budget.
    monkeypatch.setattr(refresh, 'time', SimpleNamespace(time=time.time, monotonic=lambda: elapsed[0]))
    def fetch():
        elapsed[0] = 18.0
        return contacts(*ids)
    def fetch_brief(batch):
        elapsed[0] = 24.0 if batch[0] == ids[0] else 26.0
        return brief({identity: 'SUP001 虚构及时名称' for identity in batch})
    client = FakeClient(fetch, fetch_brief)
    service = env.service(client, refresh_timeout=25)
    result = service.refresh()
    assert client.contact_calls == 1 and client.brief_calls == [ids[:50], ids[50:100]]
    assert result['groups_named'] == 50 and result['missing_group_ids'] == ids[50:]
    assert result['complete'] is False
    assert any('超时' in failure['reason'] for failure in result['group_failures'])
    assert len([row for row in service.cache.read() if row.get('nickname')]) == 50


@pytest.mark.parametrize('bad_time', [1e300, -1e300, 'fictional-invalid-time'])
def test_bad_cache_name_timestamp_keeps_names_and_overview_available(env, bad_time):
    service = env.service(FakeClient(contacts(group(1), group(2)),
        brief({group(1): 'SUP001 虚构缓存群', group(2): 'CUS002 虚构正常群'})))
    service.refresh()
    with closing(sqlite3.connect(env.cache)) as db, db:
        db.execute('UPDATE directory_contacts SET name_updated_at=? WHERE id=?', (bad_time, group(1)))
    before = fingerprint(env.cache)
    row = env.rows(service)[group(1)]
    assert row['name'] == 'SUP001 虚构缓存群'
    assert row['name_updated_at'] is None and row['name_update_label'] is None
    summary = service.summary()
    assert summary['name_updated_at'] == NOW
    assert summary['not_enrolled_suspected'] == {'supplier': 1, 'customer': 1}
    assert fingerprint(env.cache) == before


def test_groups_unnamed_in_a_batch_are_asked_once_alone(env):
    env.discover(group(1), group(2), group(3))
    def answer(batch):
        if len(batch) > 1:  # the provider drops some names in multi-ID replies
            return brief({group(1): 'SUP001 虚构批量名'})
        return brief({batch[0]: '虚构单查名 ' + batch[0][-6:]} if batch[0] == group(2) else {})
    client = FakeClient(contacts(), answer)
    result = env.service(client).refresh()
    assert client.brief_calls == [[group(1), group(2), group(3)], [group(2)], [group(3)]]
    assert result['groups_named'] == 2 and result['missing_group_ids'] == [group(3)]
    rows = env.rows()
    assert rows[group(1)]['name'] == 'SUP001 虚构批量名' and rows[group(2)]['name'].startswith('虚构单查名')


def test_single_name_retry_stops_at_first_failure(env):
    env.discover(group(1), group(2), group(3))
    def answer(batch):
        if len(batch) > 1:
            return brief({})
        raise RuntimeError('fictional provider failure')
    client = FakeClient(contacts(), answer)
    result = env.service(client).refresh()
    assert client.brief_calls == [[group(1), group(2), group(3)], [group(1)]]
    assert result['groups_named'] == 0 and result['complete'] is False
    assert sorted(i for f in result['group_failures'] for i in f['ids']) == [group(1), group(2), group(3)]
