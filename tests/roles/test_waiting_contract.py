from unittest.mock import Mock

from console.deals.followups import Followups
from console.deals.waiting import observation
from console.roles.common import observation as role_observation


def test_deals_secretary_and_patrol_keep_both_conversations_and_original_clock(env):
    key = env.conv(role='seller', text='请核对包装', age=72*3600, counterpart='supplier')
    did = env.deal()
    pid = env.aw('party_create', kind='supplier', name='虚构双会话供应商')
    env.dw('conversation', did, party_id=str(pid), conversation_key=key)
    other = env.conv(role='buyer', text='另一个问题已经办好', age=1, counterpart='supplier')
    env.dw('conversation', did, party_id=str(pid), conversation_key=other)
    env.message(key, role='seller', text='请继续核对包装', at=env.clock()-3600)
    env.message(key, role='unknown', text='系统观察', at=env.clock())
    assert observation(env.inbox.conversations[key])['since'] == env.clock()-72*3600
    assert role_observation(env.inbox.conversations[key])['since'] == observation(env.inbox.conversations[key])['since']
    deal = env.deals.read('detail', row_id=did)
    out = env.roles.run_secretary(did, 'fictional-owner')['output_json']
    patrol = env.roles.run_patrol('fictional-owner')
    assert deal['waiting_on'] == ['supplier'] and deal['waiting_on_us'] and deal['overdue'] == ['supplier']
    assert {w['state'] for w in out['waiting_on']} == {'us', 'them'}
    assert any(w['waited_hours'] == 72 and w['availability'] == 'unknown' for w in out['waiting_on'])
    assert '已等 3 天' in patrol['output_text']


def test_roles_read_concrete_items_without_changing_wait_after_reminder(env, tmp_path):
    key = env.conv(role='seller', age=1)
    did = env.deal(key=key)
    pid = env.deals.read('detail', row_id=did)['customer']['id']
    path = tmp_path / 'followup-specific.sqlite3'
    followups = Followups(path, store=env.store, inbox=env.inbox, clock=env.clock, runner=None)
    iid = followups.write('create', did, dict(party_id=str(pid), conversation_key=key, title='虚构电压待确认',
                           since=str(env.clock()-72*3600)), 'fictional-owner')['id']
    followups.write('remind', did, dict(request_id='fictional-reminder'), 'fictional-owner', iid)
    env.message(key, text='另一件事已回复', role='buyer')
    roles = env.service(followups_path=path)
    waiting = roles.run_secretary(did, 'fictional-owner')['output_json']['waiting_on']
    item = next(w for w in waiting if w.get('item_id') == iid)
    assert item['since'] == env.clock()-72*3600 and item['waited_hours'] == 72
    assert item['last_reminded_at'] == env.clock() and item['state'] == 'them'
    roles.run_patrol('fictional-owner')
    assert any(a['ref_kind'] == 'waiting_item' and a['ref_id'] == str(iid) for a in roles.alerts())
    followups.write('resolve', did, {}, 'fictional-owner', iid)
    roles.run_patrol('fictional-owner')
    assert not any(a['ref_kind'] == 'waiting_item' for a in roles.alerts())


def test_roles_unknown_unreadable_and_no_link_explain_next_step(env):
    key = env.conv(role='unknown')
    did = env.deal(key=key)
    out = env.roles.run_secretary(did, 'fictional-owner')['output_json']
    assert '方向未知' in out['waiting_on'][0]['what']
    env.inbox.missing.add(key)
    out = env.roles.run_secretary(did, 'fictional-owner')['output_json']
    assert '无法读取' in out['waiting_on'][0]['what']
    pid = env.aw('party_create', kind='forwarder', name='虚构未关联货代')
    env.dw('participant', did, party_id=str(pid))
    out = env.roles.run_secretary(did, 'fictional-owner')['output_json']
    assert any('尚未关联会话' in w['what'] for w in out['waiting_on'])


def test_new_waiting_projection_does_not_send_raw_conversation_keys_to_model(env):
    key = env.conv(role='seller', text='请核对虚构包装', age=100)
    did = env.deal(key=key)
    runner = Mock(return_value='语言：中文\n现状：等待客户核对\n下一步：1\n风险：无\n=====待确认=====\n请核对')
    result = env.service(runner=runner).run_secretary(did, 'fictional-owner')
    assert result['source'] == 'ai'
    assert key not in runner.call_args.args[0]
