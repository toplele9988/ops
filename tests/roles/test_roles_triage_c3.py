from dataclasses import replace
from unittest.mock import Mock, patch

import pytest

from console.archive.validation import Conflict
from console.inbox.model import make_key
from console.roles import triage
from .conftest import answer
from .test_roles_migration import snapshot


def suggestions(result):
    return {s['conversation_key']: s for s in result['items']}


def buyer(env, account, *, name='虚构 Willow Buyer', age=0, **kwargs):
    key = make_key('conversation', 'alibaba', account, 'fictional-room')
    env.conv(name=name, key=key, age=age, **kwargs)
    env.inbox.conversations[key]['account'] = account
    return key


@pytest.mark.parametrize('name,kind', [
    ('虚构霁月激光厂家SUP～乐初工作', 'supplier'),
    ('虚构苍穹SUP-乐初工作对接', 'supplier'),
    ('虚构蓝湾CUS～乐初工作', 'customer'),
    ('SUP～LC工作-虚构快递', 'forwarder'),
    ('虚构打样SUP', 'vendor'),
])
def test_bare_codes_keep_classification_and_clean_suggested_names(env, name, kind):
    env.conv(name=name, channel='wechat', counterpart='unclassified')
    runner = Mock(side_effect=AssertionError('explicit group labels need no AI'))
    item = env.service(runner=runner).scan_triage('fictional-owner')['items'][0]
    assert item['suggested_party_kind'] == kind and item['confidence'] == .9
    assert item['action'] == ('create' if kind == 'customer' else 'identity')
    assert 'SUP' not in item['suggested_party_name'] and 'CUS' not in item['suggested_party_name']
    runner.assert_not_called()


def test_system_notice_is_ignored_and_accept_uses_existing_dismissal(env, monkeypatch):
    monkeypatch.setattr(triage, 'SYSTEM_COMPANY_NAME', '虚构辰光电子有限公司')
    key = env.conv(name='A-虚构辰光电子有限公司-0019', channel='wechat', text='虚构系统报价通知')
    runner = Mock(side_effect=AssertionError('ignore rules need no AI'))
    roles = env.service(runner=runner)
    item = roles.scan_triage('fictional-owner')['items'][0]
    assert (item['action'], item['reason'], item['confidence']) == ('ignore', '像是系统通知，不是客户或供应商', .8)
    roles.write('triage_accept', item['id'], {}, 'fictional-decider')
    row = roles.read('triage', {'status': 'dismissed'})['items'][0]
    assert row['decided_by'] == 'fictional-decider' and row['decided_deal_id'] is None
    env.message(key, '虚构更新的系统报价通知')
    assert roles.scan_triage('fictional-owner')['items'] == []
    with env.store.read() as db:
        assert db.execute('SELECT count(*) FROM deals').fetchone()[0] == 0
        assert db.execute('SELECT count(*) FROM parties').fetchone()[0] == 0
    runner.assert_not_called()


@pytest.mark.parametrize('name', [
    'B-虚构辰光电子有限公司-0019', 'A-虚构另一家公司-0019',
    'A-虚构辰光电子有限公司-业务', 'A-虚构辰光电子有限公司-0019咨询',
])
def test_system_notice_requires_all_three_name_conditions(env, monkeypatch, name):
    monkeypatch.setattr(triage, 'SYSTEM_COMPANY_NAME', '虚构辰光电子有限公司')
    env.conv(name=name, channel='wechat', text='Please quote the fictional lamp')
    assert env.roles.scan_triage('fictional-owner')['items'][0]['action'] == 'create'


@pytest.mark.parametrize('word', ['同行', '资源共享', '行业群', '交流群', '供需'])
def test_peer_groups_are_ignored_even_with_inquiry_keywords(env, word):
    env.conv(name='虚构星桥' + word, channel='wechat', text='Please quote price and quantity')
    runner = Mock(side_effect=AssertionError('peer groups need no AI'))
    item = env.service(runner=runner).scan_triage('fictional-owner')['items'][0]
    assert (item['action'], item['reason'], item['confidence']) == ('ignore', '像是同行交流群', .7)
    runner.assert_not_called()


@pytest.mark.parametrize('name,text', [
    ('微信群 ·room', '没有'), ('微信群 ·room', '好的'),
    ('微信群 ·room', '一二三四五六'), ('微信群 ·room', '请报价'),
    ('微信群 ·room', '这是一段没有明确业务线索的虚构闲聊'),
    ('微信群 ·a123', '没有'), ('', '好的'), ('   ', '还在确认内部安排'),
])
def test_unnamed_conversations_with_short_or_uninformative_messages_are_ignored(env, name, text):
    env.conv(name=name, channel='wechat', text=text)
    runner = Mock(side_effect=AssertionError('placeholder rules need no AI'))
    item = env.service(runner=runner).scan_triage('fictional-owner')['items'][0]
    assert (item['action'], item['reason'], item['confidence']) == ('ignore', '暂时看不出是哪笔生意', .5)
    runner.assert_not_called()


def test_unnamed_inquiry_uses_buyer_messages_and_short_latest_message_wins(env):
    key = env.conv(name='微信群 ·room', channel='wechat', text='请确认虚构灯具的报价与数量', age=3)
    assert env.roles.scan_triage('fictional-owner')['items'][0]['action'] == 'create'
    env.message(key, '好的', at=env.clock() - 1)
    env.message(key, 'price and MOQ', role='unknown')
    assert env.roles.scan_triage('fictional-owner')['items'][0]['action'] == 'ignore'
    other = env.conv(name='微信群 ·a123', channel='wechat', text='What price and quantity?', role='seller', age=2)
    env.message(other, '请先等等内部消息')
    assert suggestions(env.roles.scan_triage('fictional-owner'))[other]['action'] == 'ignore'


def test_named_customer_without_inquiry_is_still_a_create_suggestion(env):
    env.conv(name='虚构明确客户', text='没有')
    item = env.roles.scan_triage('fictional-owner')['items'][0]
    assert item['action'] == 'create' and item['confidence'] == .3


@pytest.mark.parametrize('text', ['请提供虚构灯具的询价资料', 'How much are the fictional lamps?'])
def test_unnamed_long_inquiries_are_not_ignored(env, text):
    env.conv(name='微信群 ·room', text=text, channel='wechat')
    item = env.roles.scan_triage('fictional-owner')['items'][0]
    assert item['action'] == 'create' and item['confidence'] == .5


def test_private_contact_is_not_mistaken_for_a_peer_group(env):
    key = env.conv(name='虚构同行联系人', channel='wechat')
    env.inbox.conversations[key]['is_group'] = False
    assert env.roles.scan_triage('fictional-owner')['items'][0]['action'] == 'create'


def test_three_duplicate_accounts_use_only_primary_ai_and_all_become_link_suggestions(env):
    oldest = buyer(env, 'fictional-account-a', name='虚构 Willow Contact', age=3, text='FICTIONAL-SIBLING-PRIVATE')
    middle = buyer(env, 'fictional-account-b', name=' 虚构 willow contact ', age=2)
    newest = buyer(env, 'fictional-account-c', name='虚构 Willow Contact', age=1)
    runner = Mock(return_value=answer('建议：新建\n案子：无\n角色：客户\n把握：0.6\n理由：请核对虚构买家询盘'))
    roles = env.service(runner=runner)
    items = suggestions(roles.scan_triage('fictional-owner'))
    assert runner.call_count == 1 and 'FICTIONAL-SIBLING-PRIVATE' not in runner.call_args.args[0]
    assert items[newest]['action'] == 'create' and items[newest]['source'] == 'ai'
    assert all(items[key]['duplicate_of'] == newest and items[key]['action'] == 'identity' for key in (oldest, middle))
    accepted = roles.write('triage_accept', items[newest]['id'], {}, 'fictional-owner')
    pending = roles.read('triage')['items']
    assert len(pending) == 2 and all(s['action'] == 'link' and s['suggested_deal_id'] == accepted['deal_id'] for s in pending)
    assert runner.call_count == 1


@pytest.mark.parametrize('explicit_name', [False, True])
def test_duplicate_buyer_accept_promotes_sibling_to_link_atomically(env, explicit_name):
    older = buyer(env, 'fictional-account-a', age=7200)
    newer = buyer(env, 'fictional-account-b', age=3600)
    result = env.roles.scan_triage('fictional-owner')
    items = suggestions(result)
    main, child = items[newer], items[older]
    assert sum(s['action'] == 'create' for s in items.values()) == 1
    assert main['duplicate_of'] is None and result['items'][0]['conversation_key'] == newer
    assert child['action'] == 'identity' and child['duplicate_of'] == newer
    assert child['reason'] == '与另一条会话是同名买家，建议一起处理'
    assert all(r['reused'] for r in env.roles.scan_triage('fictional-owner')['runs'])
    with pytest.raises(Conflict, match='主建议'):
        env.roles.write('triage_accept', child['id'], {}, 'fictional-owner')
    fields = {'party_name': '虚构手动确认公司'} if explicit_name else {}
    accepted = env.roles.write('triage_accept', main['id'], fields, 'fictional-owner')
    service = env.service()
    refreshed = service.read('triage')['items'][0]
    assert refreshed['id'] == child['id'] and refreshed['action'] == 'link'
    assert refreshed['suggested_deal_id'] == accepted['deal_id']
    assert refreshed['suggested_party_id'] == accepted['party_id']
    assert '加入刚建的案子' in refreshed['reason'] and refreshed['run_id'] != child['run_id']
    assert env.deals.read('identity_lookup', {'key': older}) is None
    run = service.read('runs', {'role': 'triage'})['items'][0]
    assert run['output_json']['action'] == 'link'
    assert {'kind': 'triage_suggestion', 'id': main['id']} in run['evidence_json']
    rescanned = service.scan_triage('fictional-owner')['items'][0]
    assert rescanned['action'] == 'link' and rescanned['suggested_deal_id'] == accepted['deal_id']
    if explicit_name:
        assert rescanned['suggested_party_name'] == fields['party_name']
    assert service.scan_triage('fictional-owner')['runs'][0]['reused']
    with pytest.raises(Conflict, match='主建议'):
        service.write('triage_accept', child['id'], {'action': 'create'}, 'fictional-owner')
    linked = service.write('triage_accept', child['id'], {}, 'fictional-owner')
    assert linked['deal_id'] == accepted['deal_id'] and linked['party_id'] == accepted['party_id']
    assert service.write('triage_accept', main['id'], {}, 'fictional-owner')['reused']
    assert service.write('triage_accept', child['id'], {}, 'fictional-owner')['reused']
    with env.store.read() as db:
        assert db.execute('SELECT count(*) FROM deals').fetchone()[0] == 1
        assert db.execute('SELECT count(*) FROM deal_conversations').fetchone()[0] == 2
        assert db.execute('SELECT count(*) FROM parties').fetchone()[0] == 1


def test_duplicate_recommendations_and_acceptance_roll_back_together(env):
    buyer(env, 'fictional-account-a', age=2)
    buyer(env, 'fictional-account-b', age=1)
    item = next(s for s in env.roles.scan_triage('fictional-owner')['items'] if s['action'] == 'create')
    with env.store.read() as db:
        before = snapshot(db)
    with patch('console.roles.service.save_suggestion', side_effect=RuntimeError('fictional refresh failure')):
        with pytest.raises(RuntimeError, match='fictional refresh failure'):
            env.roles.write('triage_accept', item['id'], {}, 'fictional-owner')
    with env.store.read() as db:
        assert snapshot(db) == before


def test_duplicate_followup_still_checks_changed_identity(env):
    older = buyer(env, 'fictional-account-a', age=2)
    newer = buyer(env, 'fictional-account-b', age=1)
    items = suggestions(env.roles.scan_triage('fictional-owner'))
    other = env.aw('party_create', kind='customer', name='虚构已确认另一客户')
    env.dw('identity', conversation_key=older, party_id=str(other))
    env.roles.write('triage_accept', items[newer]['id'], {}, 'fictional-owner')
    with pytest.raises(Conflict, match='归属已变化'):
        env.roles.write('triage_accept', items[older]['id'], {}, 'fictional-owner')
    assert env.deals.read('identity_lookup', {'key': older})['party']['id'] == other


def test_primary_explicit_link_keeps_siblings_on_the_selected_existing_deal(env):
    did = env.deal(title='虚构已有案子')
    older = buyer(env, 'fictional-account-a', age=2)
    newer = buyer(env, 'fictional-account-b', age=1)
    items = suggestions(env.roles.scan_triage('fictional-owner'))
    env.roles.write('triage_accept', items[newer]['id'], {'action': 'link', 'deal_id': str(did)}, 'fictional-owner')
    pending = env.roles.read('triage')['items'][0]
    assert pending['conversation_key'] == older and pending['suggested_deal_id'] == did
    assert '加入已确认的案子' in pending['reason']
    rescanned = env.roles.scan_triage('fictional-owner')['items'][0]
    assert rescanned['action'] == 'link' and rescanned['reason'] == pending['reason']
    assert env.deals.read('summary')['open'] == 1


def test_duplicate_primary_changes_with_new_messages_and_dismissal(env):
    older = buyer(env, 'fictional-account-a', age=2)
    newer = buyer(env, 'fictional-account-b', age=1)
    first = suggestions(env.roles.scan_triage('fictional-owner'))
    env.message(older, 'New fictional inquiry')
    second = suggestions(env.roles.scan_triage('fictional-owner'))
    assert second[older]['action'] == 'create' and second[newer]['duplicate_of'] == older
    assert second[older]['id'] == first[older]['id']
    env.roles.write('triage_dismiss', second[older]['id'], {}, 'fictional-owner')
    promoted = env.roles.scan_triage('fictional-owner')['items'][0]
    assert promoted['conversation_key'] == newer and promoted['action'] == 'create'
    assert promoted['duplicate_of'] is None


def test_duplicate_matching_crosses_scan_batches_after_primary_acceptance(env):
    older = buyer(env, 'fictional-account-a', age=2)
    newer = buyer(env, 'fictional-account-b', age=1)
    roles = env.service(config=replace(env.roles.config, scan_limit=1))
    first = roles.scan_triage('fictional-owner')
    assert first['scanned'] == 1 and first['remaining'] == 1
    assert first['items'][0]['conversation_key'] == newer
    accepted = roles.write('triage_accept', first['items'][0]['id'], {'party_name': '虚构确认新名称'}, 'fictional-owner')
    second = roles.scan_triage('fictional-owner')['items'][0]
    assert second['conversation_key'] == older and second['action'] == 'link'
    assert second['suggested_deal_id'] == accepted['deal_id']


def test_historical_closed_primary_does_not_hide_the_current_confirmed_deal(env):
    buyer(env, 'fictional-account-a', age=3)
    first = env.roles.scan_triage('fictional-owner')['items'][0]
    previous = env.roles.write('triage_accept', first['id'], {'party_name': '虚构确认公司'}, 'fictional-owner')
    env.dw('update', previous['deal_id'], status='lost', lost_reason='虚构取消原因')
    buyer(env, 'fictional-account-b', age=2)
    second = env.roles.scan_triage('fictional-owner')['items'][0]
    assert second['action'] == 'create'
    current = env.roles.write('triage_accept', second['id'], {'party_name': '虚构确认公司'}, 'fictional-owner')
    buyer(env, 'fictional-account-c', age=1)
    third = env.roles.scan_triage('fictional-owner')['items'][0]
    assert third['action'] == 'link' and third['suggested_deal_id'] == current['deal_id']


@pytest.mark.parametrize('names,channels', [
    (('虚构 Willow Buyer', '虚构 Willow Buyer'), ('alibaba', 'whatsapp')),
    (('虚构星桥CUS0011', '虚构星桥CUS0012'), ('wechat', 'wechat')),
    (('微信群 ·room', '微信群 ·room'), ('wechat', 'wechat')),
    (('虚构' + '长名称' * 40 + '甲', '虚构' + '长名称' * 40 + '乙'), ('wechat', 'wechat')),
])
def test_duplicates_require_real_full_names_in_the_same_channel(env, names, channels):
    for name, channel in zip(names, channels):
        env.conv(name=name, channel=channel, text='Please quote price and quantity')
    items = env.roles.scan_triage('fictional-owner')['items']
    assert len(items) == 2 and all(s['action'] == 'create' and s['duplicate_of'] is None for s in items)


def test_same_names_with_conflicting_confirmed_identities_are_not_combined(env):
    for suffix in ('a', 'b'):
        key = buyer(env, 'fictional-account-' + suffix)
        party = env.aw('party_create', name='虚构确认单位-' + suffix, kind='customer')
        env.dw('identity', conversation_key=key, party_id=str(party))
    items = env.roles.scan_triage('fictional-owner')['items']
    assert len(items) == 2 and all(s['action'] == 'create' and s['duplicate_of'] is None for s in items)


def test_supply_conversations_with_the_same_name_stay_independent_identities(env):
    for suffix in ('a', 'b'):
        buyer(env, 'fictional-account-' + suffix, name='虚构工厂SUP')
    items = env.roles.scan_triage('fictional-owner')['items']
    assert len(items) == 2 and all(s['action'] == 'identity' and s['duplicate_of'] is None for s in items)


def test_scan_preview_and_list_share_priority_confidence_and_waiting_order(env):
    old = env.conv(name='虚构已有买家')
    env.deal(key=old)
    link = env.conv(name='虚构已有买家')
    identity_low = env.conv(name='虚构云海工厂', age=300)
    identity_high = env.conv(name='虚构霜叶SUP', age=1)
    low = env.conv(name='虚构未定买家', text='Please discuss a request', age=20000)
    recent = env.conv(name='虚构近期买家', text='Please quote price', age=1)
    waiting = env.conv(name='虚构久等买家', text='Please quote price', age=500)
    high = env.conv(name='虚构明确买家CUS0012', age=0)
    ignored = env.conv(name='虚构同行群', channel='wechat', age=50000)
    expected = [identity_high, identity_low, high, waiting, recent, low, link, ignored]
    preview = env.roles.scan_triage('fictional-owner', apply=False)
    assert [s['conversation_key'] for s in preview['items']] == expected
    scanned = env.roles.scan_triage('fictional-owner')
    assert [s['conversation_key'] for s in scanned['items']] == expected
    assert [s['conversation_key'] for s in env.roles.read('triage')['items']] == expected
    with env.store.read() as db:
        before = snapshot(db)
    env.message(waiting, 'Please quote price', at=env.clock())
    listed = env.roles.read('triage')['items']
    assert [s['conversation_key'] for s in listed] == [identity_high, identity_low, high, recent, waiting, low, link, ignored]
    with env.store.read() as db:
        assert snapshot(db) == before
    env.inbox.missing.update(env.inbox.conversations)
    assert [s['conversation_key'] for s in env.roles.read('triage')['items']] == expected
