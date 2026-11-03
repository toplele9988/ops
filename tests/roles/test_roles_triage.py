import json
from unittest.mock import Mock, patch

import pytest

from console.archive.validation import Conflict
from console.deals.service import Deals
from .conftest import answer


def test_identity_rule_links_only_open_deal_without_ai(env):
    first = env.conv(name='虚构匹配客户')
    did = env.deal(key=first)
    party = env.deals.read('identity_lookup', {'key': first})['party']['id']
    second = env.conv(name='虚构别名')
    env.dw('identity', conversation_key=second, party_id=str(party))
    runner = Mock(side_effect=AssertionError('certain rules must not invoke AI'))
    result = env.service(runner=runner).scan_triage('fictional-owner')
    assert len(result['items']) == 1
    suggestion = result['items'][0]
    assert suggestion['suggested_deal_id'] == did and suggestion['source'] == 'rules'
    assert suggestion['suggested_party_id'] == party and suggestion['action'] == 'link'
    assert '虚构灯具案子' in result['runs'][0]['output_text']
    runner.assert_not_called()


def test_name_and_product_overlap_rules_and_group_role(env):
    key = env.conv(name='虚构 Acme 公司')
    did = env.deal(key=key)
    env.dw('item', did, name='ZX Beam 800 Lamp', qty='2')
    name_key = env.conv(name='虚构 Acme 公司', text='different topic')
    product_key = env.conv(name='未识别联系人', text='ZX Beam 800 Lamp please confirm')
    freight_key = env.conv(name='虚构运费货代群', text='shipping schedule', channel='wechat', counterpart='unclassified')
    suggestions = {s['conversation_key']: s for s in env.roles.scan_triage('fictional-owner')['items']}
    assert suggestions[name_key]['suggested_deal_id'] == did
    assert suggestions[product_key]['suggested_deal_id'] == did
    assert suggestions[freight_key]['suggested_party_kind'] == 'forwarder'
    assert suggestions[freight_key]['action'] == 'identity'
    for s in suggestions.values():
        assert len(s['conversation']['preview']) <= 80


@pytest.mark.parametrize('kwargs', [dict(age=30 * 86400 + 1), dict(role='seller'), dict(text='谢谢'), dict(counterpart='internal'), dict(counterpart='learning')])
def test_ineligible_conversations_are_excluded(env, kwargs):
    env.conv(**kwargs)
    assert env.roles.scan_triage('fictional-owner')['items'] == []


def test_thirty_day_boundary_and_unknown_last_message(env):
    key = env.conv(age=30 * 86400)
    env.message(key, role='unknown', at=env.clock())
    assert len(env.roles.scan_triage('fictional-owner')['items']) == 1


def test_uncertain_ai_sees_only_this_conversation_titles_and_products(env):
    private = env.conv(name='虚构其它客户', text='FICTIONAL-OTHER-CUSTOMER-PRIVATE')
    did = env.deal(key=private)
    env.dw('update', did, notes='FICTIONAL-DEAL-SECRET')
    target = env.conv(name='Unknown fictional person', text='Could we discuss a request?')
    runner = Mock(return_value=answer('建议：新建\n案子：无\n角色：客户\n把握：0.7\n理由：看起来是新询盘，需人工核对'))
    roles = env.service(runner=runner)
    result = roles.scan_triage('fictional-owner')
    assert result['items'][0]['source'] == 'ai'
    assert result['items'][0]['conversation_key'] == target
    prompt = runner.call_args.args[0]
    assert 'FICTIONAL-OTHER-CUSTOMER-PRIVATE' not in prompt and 'FICTIONAL-DEAL-SECRET' not in prompt
    assert 'Could we discuss a request?' in prompt
    roles.scan_triage('fictional-owner')
    assert runner.call_count == 1


@pytest.mark.parametrize('output', ['', answer('建议：关联\n案子：999999\n角色：客户\n把握：0.9\n理由：虚构不存在的目标')])
def test_triage_bad_ai_falls_back_and_pending_updates_in_place(env, output):
    key = env.conv(name='Unknown synthetic contact', text='A general request')
    runner = Mock(return_value=output)
    roles = env.service(runner=runner)
    first = roles.scan_triage('fictional-owner')
    assert first['items'][0]['source'] == 'rules' and first['runs'][0]['status'] == 'fallback'
    env.message(key, 'A changed general request')
    second = roles.scan_triage('fictional-owner')
    assert second['items'][0]['id'] == first['items'][0]['id']
    assert second['items'][0]['run_id'] != first['items'][0]['run_id']


def test_batch_scan_has_only_one_runner_invocation(env):
    for n in range(5):
        env.conv(name=f'Unknown fictional {n}', text='A request')
    runner = Mock(return_value=answer('建议：新建\n案子：无\n角色：客户\n把握：0.6\n理由：需要人工核对'))
    result = env.service(runner=runner).scan_triage('fictional-owner')
    assert result['scanned'] == 5 and runner.call_count == 1
    assert sum(r['source'] == 'ai' for r in result['runs']) == 1


def test_accept_calls_existing_deals_create_link_identity_and_is_idempotent(env):
    key = env.conv(name='虚构客户组')
    s = env.roles.scan_triage('fictional-owner')['items'][0]
    calls = []
    original = Deals.write
    def spy(self, op, *args, **kwargs):
        calls.append(op)
        return original(self, op, *args, **kwargs)
    with patch.object(Deals, 'write', spy):
        accepted = env.roles.write('triage_accept', s['id'], {}, 'fictional-decider')
        again = env.roles.write('triage_accept', s['id'], {}, 'fictional-second-click')
    assert accepted['deal_id'] == again['deal_id'] and again['reused']
    assert 'create' in calls and calls.count('create') == 1 and 'identity' in calls
    assert env.deals.read('identity_lookup', {'key': key})['party']['id'] == accepted['party_id']
    assert env.roles.read('triage', {'status': 'accepted'})['items'][0]['decided_by'] == 'fictional-decider'
    assert env.roles.scan_triage('fictional-owner')['items'] == []
    # Link another conversation to the same deal via the existing operation.
    env.conv(name='虚构客户组')
    s = env.roles.scan_triage('fictional-owner')['items'][0]
    with patch.object(Deals, 'write', spy):
        env.roles.write('triage_accept', s['id'], {}, 'fictional-decider')
    assert 'conversation' in calls


def test_accept_classification_can_create_identity_without_deal(env):
    key = env.conv(name='虚构货代运费群', channel='wechat', counterpart='unclassified')
    s = env.roles.scan_triage('fictional-owner')['items'][0]
    result = env.roles.write('triage_accept', s['id'], {}, 'fictional-owner')
    assert result['deal_id'] is None and result['party_id']
    assert env.deals.read('identity_lookup', {'key': key})['party']['kind'] == 'forwarder'
    assert env.deals.read('summary')['open'] == 0


def test_dismiss_persists_when_new_messages_arrive(env):
    key = env.conv()
    s = env.roles.scan_triage('fictional-owner')['items'][0]
    env.roles.write('triage_dismiss', s['id'], {}, 'fictional-decider')
    env.clock.advance(5)
    env.message(key)
    assert env.roles.scan_triage('fictional-owner')['items'] == []
    row = env.roles.read('triage', {'status': 'dismissed'})['items'][0]
    assert row['decided_by'] == 'fictional-decider'
    with pytest.raises(Conflict):
        env.roles.write('triage_accept', s['id'], {}, 'fictional-owner')


def test_accept_rollback_includes_parties_deal_identity_and_decision(env):
    key = env.conv(name='虚构回滚客户')
    s = env.roles.scan_triage('fictional-owner')['items'][0]
    with patch.object(Deals, '_templates', side_effect=RuntimeError('fictional failure')), pytest.raises(RuntimeError):
        env.roles.write('triage_accept', s['id'], {}, 'fictional-owner')
    assert env.roles.read('triage')['items'][0]['status'] == 'pending'
    assert env.deals.read('identity_lookup', {'key': key}) is None
    with env.store.read() as db:
        assert db.execute('SELECT count(*) FROM parties').fetchone()[0] == 0
        assert db.execute('SELECT count(*) FROM deals').fetchone()[0] == 0


def test_stale_identity_change_requires_human_review(env):
    env.conv(name='虚构同名客户')
    original = env.aw('party_create', kind='customer', name='虚构同名客户')
    s = env.roles.scan_triage('fictional-owner')['items'][0]
    other = env.aw('party_create', kind='customer', name='虚构另一个客户')
    env.dw('identity', conversation_key=s['conversation_key'], party_id=str(other))
    assert s['suggested_party_id'] == original
    with pytest.raises(Conflict):
        env.roles.write('triage_accept', s['id'], {}, 'fictional-owner')


def test_known_customer_is_not_linked_to_another_customer_by_product_overlap(env):
    other_key = env.conv(name='虚构另一客户')
    did = env.deal(key=other_key)
    env.dw('item', did, name='ZX Beam Lamp', qty='2')
    target = env.conv(name='虚构已知客户', text='ZX Beam Lamp quotation please')
    party = env.aw('party_create', kind='customer', name='虚构已知客户')
    env.dw('identity', conversation_key=target, party_id=str(party))
    suggestion = env.roles.scan_triage('fictional-owner')['items'][0]
    assert suggestion['action'] == 'create' and suggestion['suggested_deal_id'] is None


def test_detail_recheck_excludes_a_conversation_already_replied_to(env):
    key = env.conv()
    original = env.inbox.detail
    def detail(key, now=None):
        env.message(key, role='seller', text='I have replied')
        return original(key, now)
    with patch.object(env.inbox, 'detail', detail):
        assert env.roles.scan_triage('fictional-owner')['items'] == []


@pytest.mark.parametrize('name,kind', [
    ('虚构星河灯光SUP-0011乐初供应商沟通', 'supplier'),
    ('虚构快递SUP0001-乐初工作群', 'forwarder'),
    ('虚构物流sup～0012乐初供应商沟通', 'forwarder'),
    ('虚构货代SUP＿0013', 'forwarder'),
    ('虚构运费SUP：0014', 'forwarder'),
    ('虚构空运SUP—0015', 'forwarder'),
    ('虚构海运sUp 0016', 'forwarder'),
    ('虚构打样SUP-0021乐初供应商沟通', 'vendor'),
    ('虚构包装SUP-0022乐初供应商沟通', 'vendor'),
    ('虚构验货SUP-0023乐初供应商沟通', 'vendor'),
    ('虚构报关SUP-0024乐初供应商沟通', 'vendor'),
    ('虚构认证SUP-0025乐初供应商沟通', 'vendor'),
    ('虚构 MC -乐初LC工作沟通 CUS0010', 'customer'),
    ('虚构工厂供应报价 cUs：００１１', 'customer'),
])
def test_company_codes_override_old_group_clues_without_ai(env, name, kind):
    env.conv(name=name, channel='wechat', counterpart='unclassified', text='Please confirm our request')
    runner = Mock(side_effect=AssertionError('coded groups must use rules'))
    result = env.service(runner=runner).scan_triage('fictional-owner')
    suggestion = result['items'][0]
    assert suggestion['suggested_party_kind'] == kind
    assert suggestion['confidence'] == .9 and suggestion['source'] == 'rules'
    assert suggestion['action'] == ('create' if kind == 'customer' else 'identity')
    runner.assert_not_called()


@pytest.mark.parametrize('kind', ['supplier', 'forwarder', 'vendor'])
def test_supply_identity_takes_precedence_over_open_deals_and_product_matches(env, kind):
    did = env.deal()
    env.dw('item', did, name='Fictional Aurora Beam Lamp', qty='2')
    party = env.aw('party_create', name='虚构往来单位', kind=kind)
    env.dw('participant', did, party_id=str(party), role=kind)
    key = env.conv(name='虚构往来单位', text='Fictional Aurora Beam Lamp price please')
    env.dw('identity', conversation_key=key, party_id=str(party))
    suggestion = env.roles.scan_triage('fictional-owner')['items'][0]
    assert suggestion['action'] == 'identity' and suggestion['suggested_deal_id'] is None
    assert '新建案子' not in suggestion['reason']


def test_supply_channel_classification_does_not_create_deal(env):
    env.conv(name='虚构未命名单位', counterpart='supplier')
    suggestion = env.roles.scan_triage('fictional-owner')['items'][0]
    assert suggestion['action'] == 'identity' and suggestion['suggested_party_kind'] == 'supplier'
    assert '渠道分类' in suggestion['reason']


def test_sup_product_match_still_only_suggests_identity(env):
    did = env.deal()
    env.dw('item', did, name='Fictional Aurora Beam Lamp', qty='2')
    env.conv(name='虚构星河SUP-0011乐初供应商沟通', text='Fictional Aurora Beam Lamp price please')
    suggestion = env.roles.scan_triage('fictional-owner')['items'][0]
    assert suggestion['action'] == 'identity' and suggestion['confidence'] == .9
    assert suggestion['suggested_deal_id'] is None


@pytest.mark.parametrize('name,action', [
    ('虚构星河灯光SUP-0011乐初供应商沟通', 'identity'),
    ('虚构星河灯光CUS-0011乐初工作沟通', 'create'),
])
@pytest.mark.parametrize('explicit', [False, True])
def test_suggested_name_is_persisted_and_used_by_accept_unless_overridden(env, name, action, explicit):
    key = env.conv(name=name, channel='wechat', counterpart='unclassified')
    scanned = env.roles.scan_triage('fictional-owner')
    suggestion = scanned['items'][0]
    assert suggestion['suggested_party_name'] == '虚构星河灯光'
    assert scanned['runs'][0]['output_json']['suggested_party_name'] == '虚构星河灯光'
    env.inbox.conversations[key]['buyer_name'] = '虚构后来更名的群'
    service = env.service()
    listed = service.read('triage')['items'][0]
    assert listed['conversation']['counterpart_name'] == '虚构后来更名的群'
    assert listed['suggested_party_name'] == '虚构星河灯光'
    fields = {'party_name': '虚构手填单位'} if explicit else {}
    accepted = service.write('triage_accept', suggestion['id'], fields, 'fictional-owner')
    party = env.deals.read('identity_lookup', {'key': key})['party']
    assert party['name'] == ('虚构手填单位' if explicit else '虚构星河灯光')
    assert (accepted['deal_id'] is not None) == (action == 'create')
    assert service.write('triage_accept', suggestion['id'], fields, 'fictional-owner')['reused']


@pytest.mark.parametrize('message,confidence,clue', [
    ('Could we discuss a request?', .3, '价格关键词：未见'),
    ('Please quote the price', .5, '价格关键词：price、quote'),
    ('Please confirm MOQ and quantity', .5, '数量关键词：moq、quantity'),
    ('请确认报价、多少钱和数量', .5, '数量关键词：数量'),
    ('Fictional Aurora Beam Lamp price and MOQ please', .7, '与在谈产品匹配'),
])
def test_create_reason_explains_observed_clues_and_confidence(env, message, confidence, clue):
    for number in (1, 2):
        did = env.deal(title=f'虚构待区分案子 {number}')
        env.dw('item', did, name='Fictional Aurora Beam Lamp', qty='2')
    env.conv(name='虚构新联系人', text=message, channel='whatsapp', counterpart='unclassified')
    suggestion = env.roles.scan_triage('fictional-owner')['items'][0]
    assert suggestion['action'] == 'create' and suggestion['confidence'] == confidence
    reason = suggestion['reason']
    assert '渠道：WhatsApp' in reason and '对方：虚构新联系人' in reason
    assert f'最近对方消息：“{message[:40]}”' in reason and clue in reason
    assert '价格关键词：' in reason and '数量关键词：' in reason and '产品关键词：' in reason


def test_create_excerpt_uses_latest_buyer_redacted_before_truncation(env):
    key = env.conv(name='虚构新联系人', text='Please quote price and MOQ', age=2)
    latest = '+86 138 0013 8000 fictional@example.invalid ' + '虚构内容' * 20
    env.message(key, latest, at=env.clock() - 1)
    env.message(key, 'UNKNOWN-MUST-NOT-BE-EXCERPT', role='unknown')
    suggestion = env.roles.scan_triage('fictional-owner')['items'][0]
    excerpt = suggestion['reason'].split('最近对方消息：“', 1)[1].split('”', 1)[0]
    assert excerpt == ('〔号码已隐藏〕 〔邮箱已隐藏〕 ' + '虚构内容' * 20)[:40]
    assert suggestion['confidence'] == .5
    assert '138' not in suggestion['reason'] and 'fictional@example' not in suggestion['reason']


def test_seller_keywords_do_not_strengthen_create_evidence(env):
    key = env.conv(name='虚构新联系人', text='Please ask for price MOQ', role='seller', age=1)
    env.message(key, 'Could we discuss something?')
    suggestion = env.roles.scan_triage('fictional-owner')['items'][0]
    assert suggestion['confidence'] == .3
    assert '价格关键词：未见' in suggestion['reason'] and '数量关键词：未见' in suggestion['reason']


def test_unknown_tail_does_not_hide_last_buyer_excerpt_or_its_evidence(env):
    env.conv(name='虚构新联系人', text='Please quote the price', age=3)
    key = next(iter(env.inbox.conversations))
    for number in range(13):
        env.message(key, '虚构未知观察', role='unknown', at=env.clock() - 2 + number / 10)
    result = env.roles.scan_triage('fictional-owner')
    suggestion = result['items'][0]
    assert '最近对方消息：“Please quote the price”' in suggestion['reason']
    assert suggestion['confidence'] == .5
    assert any(e['kind'] == 'message' and e['id'] == 'fictional-message-1' for e in result['runs'][0]['evidence_json'])


@pytest.mark.parametrize('role', ['供应商', '货代', '配套'])
def test_ai_supply_suggestions_cannot_create_cases(env, role):
    env.conv(name='虚构未知身份', text='Could we discuss a request?')
    runner = Mock(return_value=answer(f'建议：新建\n案子：无\n角色：{role}\n把握：0.7\n理由：需要确认身份'))
    suggestion = env.service(runner=runner).scan_triage('fictional-owner')['items'][0]
    assert suggestion['source'] == 'ai' and suggestion['action'] == 'identity'


def test_only_first_uncertain_conversation_uses_ai_after_coded_groups(env):
    env.conv(name='虚构供应商SUP-0001', age=0)
    first = env.conv(name='虚构未分类甲', text='price please', age=1)
    env.conv(name='虚构未分类乙', text='Could we discuss a request?', age=2)
    runner = Mock(return_value=answer('建议：新建\n案子：无\n角色：客户\n把握：0.5\n理由：请核对询价'))
    roles = env.service(runner=runner)
    result = roles.scan_triage('fictional-owner')
    assert runner.call_count == 1
    assert [r['subject_key'] for r in result['runs'] if r['source'] == 'ai'] == [first]
    roles.scan_triage('fictional-owner')
    assert runner.call_count == 1
