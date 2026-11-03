from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock
import json
import socket

import pytest

from console.archive.service import Archive
from console.archive.store import Store
from console.archive.validation import BadRequest, Conflict, Missing
from console.deals.followups import Followups
from console.deals.service import Deals
from console.inbox.model import make_key
from console.lang import Lang


@pytest.fixture
def waiting(tmp_path, monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError('虚构等待测试禁止真实网络和模型')
    monkeypatch.setattr(socket.socket, 'connect', blocked)
    monkeypatch.setattr('console.inbox.ai.official_runner', blocked)
    now = [2_000_000_000.0]
    store = Store(tmp_path / 'archive.sqlite3')
    archive = Archive(store=store)
    conversations = {}
    inbox = SimpleNamespace(read=lambda now=None: dict(conversations=conversations),
                            detail=lambda key, now=None: conversations.get(key))
    deals = Deals(store=store, inbox=inbox, clock=lambda: now[0])
    did = deals.write('create', None, dict(title='虚构采购需求'), 'fictional-owner')['id']
    def party(kind='supplier', suffix='甲'):
        pid = archive.write('party_create', None, dict(kind=kind, name='虚构' + suffix + kind), 'fictional-owner')['id']
        key = make_key('conversation', 'wechat', 'fictional-account', suffix + kind)
        conversations[key] = dict(key=key, channel='wechat', buyer_name='虚构' + suffix,
                                  messages=[dict(role='seller', text='请确认包装', sent_at=now[0] - 3600)])
        deals.write('conversation', did, dict(party_id=str(pid), conversation_key=key), 'fictional-owner')
        return pid, key
    pid, key = party()
    lang = Mock()
    lang.read.return_value = dict(contact=dict(native_language='es', source='manual', locked=True))
    lang.write.return_value = dict(text='Confirme el embalaje.', back_translation='请确认包装。', native_language='es',
                                   validation=dict(ok=True), contact=dict(source='manual', locked=True))
    runner = Mock(return_value='语言：中文\n请确认包装。\n=====待确认=====\n请核对包装要求')
    service = Followups(tmp_path / 'followups.sqlite3', store=store, inbox=inbox, lang=lang, runner=runner,
                        clock=lambda: now[0])
    return SimpleNamespace(service=service, deals=deals, archive=archive, store=store, did=did, pid=pid, key=key,
                           party=party, now=now, conversations=conversations, lang=lang, runner=runner, inbox=inbox)


def create(env, **fields):
    return env.service.write('create', env.did, dict(party_id=str(env.pid), conversation_key=env.key,
                             title='核对虚构灯具包装', **fields), 'fictional-owner')['id']


def test_item_wait_survives_unrelated_cross_conversation_reply_and_reminder(waiting):
    e = waiting
    iid = create(e, since=str(e.now[0] - 72*3600))
    second = make_key('conversation', 'whatsapp', 'fictional-account', 'second')
    e.deals.write('conversation', e.did, dict(party_id=str(e.pid), conversation_key=second), 'fictional-owner')
    e.conversations[second] = dict(messages=[dict(role='buyer', text='另一件事已完成', sent_at=e.now[0])])
    before = e.service.read(e.did)['items'][0]
    e.service.write('remind', e.did, dict(request_id='fictional-reminder-1'), 'fictional-owner', iid)
    e.now[0] += 3600
    after = e.service.read(e.did)['items'][0]
    assert after['since'] == before['since'] and after['waited_hours'] == 73
    assert after['last_reminded_at'] == e.now[0] - 3600 and after['status'] == 'open'
    assert e.deals.read('detail', row_id=e.did)['waiting_on'] == ['supplier']
    assert e.deals.read('detail', row_id=e.did)['waiting_on_us']


def test_create_and_reminder_repeat_and_concurrency_are_idempotent(waiting):
    e = waiting
    with ThreadPoolExecutor(max_workers=4) as pool:
        ids = list(pool.map(lambda _: create(e), range(4)))
    assert len(set(ids)) == 1 and len(e.service.read(e.did)['items']) == 1
    first = e.service.write('remind', e.did, dict(request_id='fictional-once'), 'fictional-owner', ids[0])
    e.now[0] += 5
    repeated = e.service.write('remind', e.did, dict(request_id='fictional-once'), 'fictional-owner', ids[0])
    assert not first['reused'] and repeated['reused']
    assert e.service.read(e.did)['items'][0]['last_reminded_at'] == e.now[0] - 5


@pytest.mark.parametrize('kind', ['customer', 'supplier', 'forwarder', 'vendor'])
def test_each_counterpart_uses_controlled_draft_and_lang_public_service_only(waiting, kind):
    e = waiting
    if kind != 'supplier':
        e.pid, e.key = e.party(kind)
    iid = create(e)
    e.deals.write('update', e.did, dict(notes='FICTIONAL_INTERNAL_COST_NEVER_SEND'), 'fictional-owner')
    result = e.service.write('draft', e.did, dict(supplement='请确认还缺什么资料'), 'fictional-owner', iid)
    assert result['draft_only'] and result['native_language'] == 'es' and result['contact']['locked']
    assert result['back_translation'] and e.runner.call_count == 1
    prompt = e.runner.call_args.args[0]
    assert 'FICTIONAL_INTERNAL_COST_NEVER_SEND' not in prompt
    assert '事项：核对虚构灯具包装' in prompt
    assert dict(customer='向客户', supplier='向产品供应商', forwarder='向货代', vendor='向配套供应商')[kind] in prompt
    e.lang.write.assert_called_once_with('compose', dict(conversation_key=e.key, text='请确认包装。'), 'fictional-owner')
    item = e.service.read(e.did)['items'][0]
    assert item['last_reminded_at'] is None and item['status'] == 'open'


def test_unknown_native_language_refuses_model_call(waiting):
    e = waiting
    iid = create(e)
    e.lang.read.return_value = dict(contact=dict(native_language=None))
    with pytest.raises(BadRequest, match='母语尚未确认'):
        e.service.write('draft', e.did, {}, 'fictional-owner', iid)
    e.runner.assert_not_called()


def test_new_claim_in_model_draft_is_rejected_without_translation_or_retry(waiting):
    e = waiting
    iid = create(e)
    e.runner.return_value = '语言：中文\n明天价格 USD 999，保证发货。\n=====待确认=====\n请核对'
    with pytest.raises(BadRequest, match='没有发送任何消息'):
        e.service.write('draft', e.did, {}, 'fictional-owner', iid)
    assert e.runner.call_count == 1
    e.lang.write.assert_not_called()


def test_real_lang_public_compose_with_fake_translation_keeps_manual_language(waiting, tmp_path):
    e = waiting
    def translate(prompt):
        return prompt.split('\n文本：\n', 1)[1]
    lang = Lang(tmp_path / 'lang.sqlite3', inbox=e.inbox, runner=translate, clock=lambda: e.now[0])
    lang.write('contact', dict(conversation_key=e.key, native_language='es', locked='1'), 'fictional-owner')
    e.service.lang = lang
    result = e.service.write('draft', e.did, {}, 'fictional-owner', create(e))
    assert result['native_language'] == 'es' and result['contact']['source'] == 'manual'
    assert result['validation']['ok'] and result['back_translation'] == '请确认包装。'


def test_readable_unknown_unreadable_and_unlinked_are_distinct(waiting):
    e = waiting
    iid = create(e)
    e.conversations[e.key]['messages'].append(dict(role='unknown', text='虚构系统观察', sent_at=e.now[0]))
    result = e.service.read(e.did)
    assert result['participants'][0]['availability'] == 'unknown'
    assert result['participants'][0]['conversations'][0]['state'] == 'them'
    e.conversations.clear()
    assert e.service.read(e.did)['participants'][0]['availability'] == 'unreadable'
    e.deals.write('participant_remove', e.did, {}, 'fictional-owner',
                  e.deals.read('detail', row_id=e.did)['participants'][0]['id'])
    assert e.service.read(e.did)['items'][0]['availability'] == 'unlinked'
    with pytest.raises(Conflict):
        e.service.write('draft', e.did, {}, 'fictional-owner', iid)


def test_no_cross_deal_or_changed_identity_and_no_archive_schema_upgrade(waiting):
    e = waiting
    iid = create(e)
    with e.store.read() as db:
        versions = list(db.execute('SELECT version FROM schema_migrations'))
    other = e.deals.write('create', None, dict(title='虚构另一案'), 'fictional-owner')['id']
    with pytest.raises(Missing):
        e.service.write('resolve', other, {}, 'fictional-owner', iid)
    with pytest.raises(Conflict):
        e.service.write('create', other, dict(party_id=str(e.pid), conversation_key=e.key, title='越界事项'), 'fictional-owner')
    e.service.write('resolve', e.did, {}, 'fictional-owner', iid)
    assert e.service.write('resolve', e.did, {}, 'fictional-owner', iid)['reused']
    with e.store.read() as db:
        assert list(db.execute('SELECT version FROM schema_migrations')) == versions
    with e.service.store.read() as db:
        audits = '\n'.join(row[0] for row in db.execute('SELECT fields FROM audit'))
    assert '核对虚构' not in audits and 'fictional-owner' not in audits


def test_read_does_not_create_store_and_symlink_write_is_rejected(waiting, tmp_path):
    e = waiting
    assert not e.service.store.path.exists()
    e.service.read(e.did)
    assert not e.service.store.path.exists()
    target = tmp_path / 'fictional-target'
    target.write_text('保持不变')
    e.service.store.path.symlink_to(target)
    with pytest.raises(ValueError, match='路径不可用'):
        create(e)
    assert target.read_text() == '保持不变'


@pytest.mark.parametrize('suffix', ['-journal', '-wal', '-shm'])
def test_sqlite_sidecar_symlinks_are_rejected(waiting, tmp_path, suffix):
    target = tmp_path / 'fictional-target'
    target.write_text('保持不变')
    (tmp_path / ('followups.sqlite3' + suffix)).symlink_to(target)
    with pytest.raises(ValueError, match='路径不可用'):
        create(waiting)
    assert target.read_text() == '保持不变'


def test_parent_symlink_and_archive_path_are_rejected_without_archive_mutation(waiting, tmp_path):
    e = waiting
    before = e.store.path.read_bytes()
    with pytest.raises(ValueError, match='独立文件'):
        Followups(e.store.path, store=e.store, inbox=e.inbox)
    link = tmp_path / 'fictional-link'
    link.symlink_to(tmp_path, target_is_directory=True)
    followups = Followups(link / 'other.sqlite3', store=e.store, inbox=e.inbox)
    with pytest.raises(ValueError, match='路径不可用'):
        followups.read(e.did)
    assert e.store.path.read_bytes() == before


def test_draft_only_uses_selected_product_and_accepted_demand_facts(waiting):
    e = waiting
    selected = e.deals.write('item', e.did, dict(name='虚构 ZX-220 灯', qty='24',
                             note='FICTIONAL_COST_NOT_ALLOWED'), 'fictional-owner')['id']
    other = e.deals.write('item', e.did, dict(name='虚构 OTHER-999 灯', qty='99'), 'fictional-owner')['id']
    e.service.demands = Mock()
    e.service.demands.read.return_value = dict(items=[
        dict(status='accepted', accepted_item_id=selected, destination='虚构港', specifications='220V DMX',
             accessories='灯钩和安全绳', source=dict(text='FICTIONAL_SOURCE_NOT_ALLOWED')),
        dict(status='accepted', accepted_item_id=other, destination='OTHER_DESTINATION', specifications='999V'),
        dict(status='pending', accepted_item_id=selected, destination='UNCONFIRMED_DESTINATION')])
    iid = create(e, item_id=str(selected))
    e.service.write('draft', e.did, {}, 'fictional-owner', iid)
    prompt = e.runner.call_args.args[0]
    for confirmed in ('虚构 ZX-220 灯', '数量：24', '目的地：虚构港', '规格：220V DMX', '配套要求：灯钩和安全绳'):
        assert confirmed in prompt
    for forbidden in ('OTHER-999', 'OTHER_DESTINATION', '999V', 'UNCONFIRMED_DESTINATION',
                      'FICTIONAL_SOURCE_NOT_ALLOWED', 'FICTIONAL_COST_NOT_ALLOWED'):
        assert forbidden not in prompt


def test_draft_without_selected_product_does_not_mix_whole_deal_items(waiting):
    e = waiting
    e.deals.write('item', e.did, dict(name='OTHER-999', qty='99'), 'fictional-owner')
    e.service.write('draft', e.did, {}, 'fictional-owner', create(e))
    assert 'OTHER-999' not in e.runner.call_args.args[0]


def test_removed_product_cannot_be_used_for_followup_draft(waiting):
    e = waiting
    product = e.deals.write('item', e.did, dict(name='虚构 ZX-220', qty='24'), 'fictional-owner')['id']
    iid = create(e, item_id=str(product))
    e.deals.write('item_remove', e.did, {}, 'fictional-owner', product)
    with pytest.raises(Conflict, match='产品项已移除'):
        e.service.write('draft', e.did, {}, 'fictional-owner', iid)
    e.runner.assert_not_called()


@pytest.mark.parametrize('operation', ['draft', 'remind'])
def test_waiting_on_us_cannot_chase_counterpart(waiting, operation):
    e = waiting
    iid = create(e, waiting_on='us')
    with pytest.raises(BadRequest, match='这项等待由我方处理'):
        e.service.write(operation, e.did, {}, 'fictional-owner', iid)
    e.runner.assert_not_called()
    assert e.service.read(e.did)['items'][0]['last_reminded_at'] is None
