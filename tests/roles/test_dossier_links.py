"""OP5: only invented records and temporary stores, never production dossiers."""
import json
import sqlite3
from unittest.mock import Mock

import pytest

from console.inbox.model import make_key
from console.operator.store import Store as OperatorStore
from console.roles import dossier_links, triage
from console.roles.automatic import AutomaticTriage
from .conftest import answer


def dossier(path, *, mappings=(), suppliers=None):
    suppliers = suppliers if suppliers is not None else [('S-FICTION-1', '虚构星光厂', '产品', [],
        [dict(channel='wechat_group', group_id='100001@chatroom', name='虚构工作群', source_id='SRC-FICTION-1')])]
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE sd_supplier(supplier_id TEXT,name TEXT,supplier_type TEXT,aliases_json TEXT,contacts_json TEXT)')
        db.execute('CREATE TABLE sd_legacy_map(legacy_store TEXT,legacy_table TEXT,legacy_key TEXT,entity_kind TEXT,entity_id TEXT,state TEXT,reason TEXT)')
        for sid, name, kind, aliases, contacts in suppliers:
            db.execute('INSERT INTO sd_supplier VALUES(?,?,?,?,?)', (sid, name, kind, json.dumps(aliases), json.dumps(contacts)))
        db.executemany('INSERT INTO sd_legacy_map VALUES(?,?,?,?,?,?,?)', mappings)
    return path


def group(env, **kwargs):
    options = dict(name='虚构无名称工作群', channel='wechat', counterpart='unclassified',
                   key=make_key('conversation', 'wechat', '微信', '100001@chatroom'))
    options.update(kwargs)
    return env.conv(**options)


def configured(env, path, **kwargs):
    return env.service(dossiers_path=path, **kwargs)


def test_existing_party_exact_alias_match_before_group_name_and_ignore(env, tmp_path):
    pid = env.aw('party_create', kind='supplier', name='虚构星光厂')
    wrong = env.aw('party_create', kind='supplier', name='虚构同行交流群')
    key = group(env, name='虚构同行交流群', role='seller', age=90 * 86400)
    path = dossier(tmp_path / 'dossiers.sqlite3')
    runner = Mock(side_effect=AssertionError('dossier certainty must not invoke runner'))
    roles = configured(env, path, runner=runner)
    before = path.read_bytes()
    result = roles.scan_triage('fictional-owner')
    s = result['items'][0]
    assert s['conversation_key'] == key and s['suggested_party_id'] == pid != wrong
    assert s['action'] == 'identity' and s['confidence'] == .95 and s['source'] == 'rules'
    assert s['reason'].startswith('档案：该群是供应商「虚构星光厂」的工作群（来源 SRC-FICTION-1）')
    assert any(e['kind'] == 'dossier_source' for e in result['runs'][0]['evidence_json'])
    runner.assert_not_called()
    assert path.read_bytes() == before and not path.with_name(path.name + '-shm').exists()
    assert env.deals.read('identity_lookup', {'key': key}) is None


@pytest.mark.parametrize('supplier_type,kind', [('产品', 'supplier'), ('物流', 'forwarder'), ('配套', 'vendor')])
def test_missing_party_created_only_on_human_acceptance(env, tmp_path, supplier_type, kind):
    key = group(env)
    path = dossier(tmp_path / 'dossiers.sqlite3', suppliers=[('S-FICTION-1', '虚构服务单位', supplier_type, [],
        [dict(channel='wechat_group', group_id='100001@chatroom', source_id='SRC-FICTION-2')])])
    roles = configured(env, path)
    s = roles.scan_triage('分拣员(自动)', use_ai=False)['items'][0]
    assert s['action'] == 'identity' and s['confidence'] == .9
    assert s['suggested_party_id'] is None and s['suggested_party_name'] == '虚构服务单位'
    assert s['suggested_party_kind'] == kind
    with env.store.read() as db:
        for table in ('identities', 'parties', 'deals'):
            assert db.execute('SELECT count(*) FROM ' + table).fetchone()[0] == 0
    accepted = roles.write('triage_accept', s['id'], {}, 'fictional-owner')
    owner = env.deals.read('identity_lookup', {'key': key})
    assert owner['party']['id'] == accepted['party_id']
    assert owner['party']['name'] == '虚构服务单位' and owner['party']['kind'] == kind
    assert env.deals.read('summary')['open'] == 0
    assert roles.scan_triage('分拣员(自动)', use_ai=False)['items'] == []


@pytest.mark.parametrize('state', ['mapped', 'imported', 'confirmed'])
def test_legacy_mapping_precedes_names_and_ignores_other_store_and_unmapped_state(env, tmp_path, state):
    preferred = env.aw('party_create', kind='supplier', name='虚构旧库名称')
    env.aw('party_create', kind='supplier', name='虚构星光厂')
    env.aw('party_create', kind='supplier', name='虚构别名单位', aliases='["虚构星光厂"]')
    group(env)
    path = dossier(tmp_path / 'dossiers.sqlite3', mappings=[
        ('archive.sqlite3', 'parties', str(preferred), 'supplier', 'S-FICTION-1', state, 'fictional'),
        ('other.sqlite3', 'parties', '2', 'supplier', 'S-FICTION-1', 'mapped', 'fictional'),
        ('archive', 'parties', '3', 'supplier', 'S-FICTION-1', 'rejected', 'fictional')])
    assert configured(env, path).scan_triage('fictional-owner')['items'][0]['suggested_party_id'] == preferred


@pytest.mark.parametrize('party_field', ['name', 'short_name', 'aliases'])
def test_whitespace_casefold_and_dossier_aliases_match_active_parties(env, tmp_path, party_field):
    fields = dict(kind='supplier', name='虚构单位')
    fields[party_field] = '["  fictional alias  "]' if party_field == 'aliases' else '  fictional alias  '
    pid = env.aw('party_create', **fields)
    group(env)
    path = dossier(tmp_path / 'dossiers.sqlite3', suppliers=[('S-FICTION-1', '虚构新档案名称', '产品', [' Fictional ALIAS '],
        [dict(channel='wechat_group', group_id='100001@chatroom', source_id='SRC-FICTION')])])
    assert configured(env, path).scan_triage('fictional-owner')['items'][0]['suggested_party_id'] == pid


@pytest.mark.parametrize('conflict', ['group', 'party', 'mapping'])
def test_conflicts_fall_back_and_explain_ambiguity(env, tmp_path, conflict):
    group(env)
    suppliers = [('S-FICTION-1', '虚构星光厂', '产品', [],
        [dict(channel='wechat_group', group_id='100001@chatroom', source_id='SRC-FICTION')])]
    mappings = []
    if conflict == 'group':
        suppliers.append(('S-FICTION-2', '虚构月光厂', '产品', [], suppliers[0][-1]))
    else:
        for name in ('虚构星光厂', '虚构同名别称厂'):
            pid = env.aw('party_create', kind='supplier', name=name, aliases='["虚构星光厂"]')
            if conflict == 'mapping':
                mappings.append(('archive', 'parties', str(pid), 'supplier', 'S-FICTION-1', 'mapped', 'fictional'))
    path = dossier(tmp_path / 'dossiers.sqlite3', suppliers=suppliers, mappings=mappings)
    roles = configured(env, path)
    inputs = roles.inputs(env.clock())
    inputs.dossier_links = dossier_links.index(dossier_links.load(path, archive_path=env.path))
    rule = triage.rules(next(iter(env.inbox.conversations.values())), inputs)
    assert rule['certain'] is False and '档案冲突：' in rule['reason']
    s = roles.scan_triage('fictional-owner', use_ai=False)['items'][0]
    assert not s['reason'].startswith('档案：') and '档案冲突：' in s['reason']


def test_model_cannot_erase_conflict_reason(env, tmp_path):
    group(env)
    contacts = [dict(channel='wechat_group', group_id='100001@chatroom')]
    path = dossier(tmp_path / 'dossiers.sqlite3', suppliers=[
        ('S-FICTION-1', '虚构甲厂', '产品', [], contacts), ('S-FICTION-2', '虚构乙厂', '产品', [], contacts)])
    runner = Mock(return_value=answer('建议：归属\n案子：无\n角色：供应商\n把握：0.9\n理由：档案：请核对'))
    s = configured(env, path, runner=runner).scan_triage('fictional-owner')['items'][0]
    assert s['source'] == 'ai' and '档案冲突：' in s['reason'] and not s['reason'].startswith('档案：')


def test_existing_identity_not_suggested_even_when_it_disagrees_with_dossier(env, tmp_path):
    key = group(env)
    pid = env.aw('party_create', kind='supplier', name='虚构人工已确认单位')
    env.dw('identity', conversation_key=key, party_id=str(pid))
    assert configured(env, dossier(tmp_path / 'dossiers.sqlite3')).scan_triage('fictional-owner')['items'] == []


@pytest.mark.parametrize('kwargs,expected', [
    (dict(role='seller', age=180 * 86400), True),
    (dict(role='buyer', text='谢谢', age=40 * 86400), True),
    (dict(role='unknown', age=50 * 86400), True),
    (dict(age=180 * 86400 + 1), False), (dict(age=-1), False),
    (dict(counterpart='learning'), False), (dict(counterpart='internal'), False), (dict(counterpart='ignored'), False)])
def test_eligibility_relaxed_only_for_dossier_groups(env, tmp_path, kwargs, expected):
    key = group(env, **kwargs)
    env.conv(name='虚构非档案联系人', **kwargs)
    result = configured(env, dossier(tmp_path / 'dossiers.sqlite3')).scan_triage('fictional-owner', use_ai=False)
    assert [s['conversation_key'] for s in result['items']] == ([key] if expected else [])


def test_dossier_uses_latest_message_clock_while_other_conversations_keep_known_direction(env, tmp_path):
    key = group(env, age=181 * 86400)
    env.message(key, role='unknown')
    other = env.conv(age=31 * 86400)
    env.message(other, role='unknown')
    result = configured(env, dossier(tmp_path / 'dossiers.sqlite3')).scan_triage('fictional-owner', use_ai=False)
    assert [s['conversation_key'] for s in result['items']] == [key]


def test_rule_only_scan_never_invokes_runner_or_reuses_ai_output(env):
    env.conv(name='Fictional ambiguous contact', text='A request')
    runner = Mock(return_value=answer('建议：新建\n案子：无\n角色：客户\n把握：0.7\n理由：虚构模型建议'))
    roles = env.service(runner=runner)
    assert roles.scan_triage('fictional-owner')['items'][0]['source'] == 'ai'
    runner.reset_mock()
    result = roles.scan_triage('分拣员(自动)', use_ai=False)
    assert result['items'][0]['source'] == 'rules'
    assert all(r['source'] == 'rules' and r['status'] == 'ok' and r['created_by'] == '分拣员(自动)' for r in result['runs'])
    runner.assert_not_called()


def test_ten_minute_throttle_survives_restart_including_empty_scans(env, tmp_path):
    roles = Mock()
    store = OperatorStore(tmp_path / 'operator.sqlite3')
    env.clock.advance(.1234)
    poller = AutomaticTriage(roles, store, env.clock)
    poller.poll()
    roles.scan_triage.assert_called_once_with('分拣员(自动)', apply=True, use_ai=False)
    env.clock.advance(599)
    restarted = AutomaticTriage(roles, OperatorStore(store.path), env.clock)
    assert restarted.poll() == dict(skipped=True)
    env.clock.advance(1.001)
    restarted.poll()
    assert roles.scan_triage.call_count == 2
    env.clock.advance(-1200)
    assert restarted.poll() == dict(skipped=True)


def test_failed_scan_still_throttled(env, tmp_path):
    roles = Mock()
    roles.scan_triage.side_effect = RuntimeError('fictional unavailable inbox')
    poller = AutomaticTriage(roles, OperatorStore(tmp_path / 'operator.sqlite3'), env.clock)
    with pytest.raises(RuntimeError):
        poller.poll()
    assert poller.poll() == dict(skipped=True)


@pytest.mark.parametrize('mode', ['missing', 'empty', 'malformed'])
def test_unavailable_dossier_optional_and_never_created(env, tmp_path, mode):
    path = tmp_path / 'optional' / 'dossiers.sqlite3'
    if mode != 'missing':
        path.parent.mkdir()
        with sqlite3.connect(path) as db:
            db.execute('CREATE TABLE unrelated(id INTEGER)' if mode == 'empty' else 'CREATE TABLE sd_supplier(id INTEGER)')
    assert dossier_links.load(path, archive_path=env.path) == []
    assert configured(env, path).scan_triage('分拣员(自动)', use_ai=False)['items'] == []
    assert path.exists() == (mode != 'missing')
    if mode == 'missing':
        assert not path.parent.exists()


def test_inactive_parties_not_matched_and_non_group_contacts_ignored(env, tmp_path):
    pid = env.aw('party_create', kind='supplier', name='虚构星光厂')
    env.aw('party_update', pid, status='archived')
    path = dossier(tmp_path / 'dossiers.sqlite3')
    with sqlite3.connect(path) as db:
        db.execute('INSERT INTO sd_supplier VALUES(?,?,?,?,?)', ('S-FICTION-2', '虚构手机联系人', '产品', '[]',
            json.dumps([dict(phone='fictional-phone'), dict(channel='wechat_group', group_id='invalid')])) )
    links = dossier_links.load(path, archive_path=env.path)
    assert len(links) == 1
    assert dossier_links.match(links, env.roles.inputs(env.clock()).rows['parties'])[1] is None
