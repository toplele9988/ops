"""Fictional customer inquiries only; every write stays inside tmp_path."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import socket
import sqlite3
from unittest.mock import patch

import pytest

from console.archive import validation as v
from console.archive.service import Archive
from console.archive.store import Store
from console.deals.demands import Demands, candidates
from console.deals.service import Deals
from console.inbox.model import make_key

NOW = 2_000_000_000
KEY = make_key('conversation', 'alibaba', 'fictional-account', 'fictional-inquiry')
KEY2 = make_key('conversation', 'wechat', 'fictional-account', 'fictional-inquiry-two')
CN = '需要24台LX-100摇头灯，电压：220V；目的地：西班牙；配套要求：航空箱。'
ES = 'Necesito 30 unidades de LX-100 moving head; destino: Madrid, España; voltaje: 220V; accesorios: flight cases.'


class Inbox:
    def __init__(self):
        self.conversations = {KEY: dict(key=KEY, messages=[dict(role='buyer', text=CN, sent_at=NOW - 100, message_id='fictional-1')])}

    def detail(self, key, now):
        return self.conversations.get(key)


@pytest.fixture
def context(tmp_path, monkeypatch):
    monkeypatch.setattr(socket.socket, 'connect', lambda *a: pytest.fail('测试禁止连接真实渠道'))
    store = Store(tmp_path / 'archive.sqlite3')
    archive = Archive(store=store)
    customer = archive.write('party_create', None, dict(kind='customer', name='虚构客户 CUS018'), 'fictional-owner')['id']
    deals = Deals(store=store, lookup=lambda key: None, clock=lambda: NOW)
    deal_id = deals.write('create', None, dict(customer_party_id=str(customer), conversation_key=KEY), 'fictional-owner')['id']
    inbox = Inbox()
    demands = Demands(store=store, path=tmp_path / 'demands.sqlite3', inbox=inbox, clock=lambda: NOW)
    return demands, deals, archive, inbox, deal_id, customer


def scanned(context):
    demands, _, _, _, deal_id, _ = context
    result = demands.scan(deal_id, {}, 'fictional-owner')
    assert result['added'] == 1
    return result['items'][0]['id']


def test_chinese_and_spanish_keep_literal_fields():
    cn, es = candidates(CN)[0], candidates(ES)[0]
    assert cn == dict(name='LX-100摇头灯', qty=24, destination='西班牙', specifications='220V', accessories='航空箱')
    assert es == dict(name='LX-100 moving head', qty=30, destination='Madrid, España', specifications='220V', accessories='flight cases')


@pytest.mark.parametrize('text', ['需要一些摇头灯，目的地待定', '需要大约24台LX-100摇头灯',
                                   '需要20或30台LX-100摇头灯', '需要20-30台LX-100摇头灯',
                                   'Necesito 20 o 30 unidades de LX-100 moving head',
                                   'Necesito aproximadamente 30 unidades de LX-100 moving head'])
def test_ambiguity_does_not_invent_quantity(text):
    assert candidates(text)[0]['qty'] is None
    assert candidates(text)[0]['destination'] is None


def test_multiple_product_requirements_do_not_share_specifications():
    items = candidates('需要20台LX-100摇头灯，220V；需要40台LX-200摇头灯，110V；目的地：西班牙')
    assert [(i['name'], i['qty'], i['specifications']) for i in items] == [('LX-100摇头灯', 20, '220V'), ('LX-200摇头灯', 40, '110V')]
    assert all(i['destination'] == '西班牙' for i in items)


def test_read_does_not_create_state_and_returns_only_customer_conversations(context):
    demands, deals, archive, _, deal_id, _ = context
    supplier = archive.write('party_create', None, dict(kind='supplier', name='虚构供应方 SUP071'), 'fictional-owner')['id']
    deals.write('conversation', deal_id, dict(conversation_key=KEY2, party_id=str(supplier)), 'fictional-owner')
    result = demands.read(deal_id)
    assert result['items'] == []
    assert [c['key'] for c in result['conversations']] == [KEY]
    assert not demands.store.path.exists()


def test_scan_is_candidate_only_and_has_source(context):
    demands, deals, _, _, deal_id, _ = context
    before = demands.archive.path.read_bytes()
    row_id = scanned(context)
    assert demands.archive.path.read_bytes() == before
    item = demands.read(deal_id)['items'][0]
    assert item['id'] == row_id and item['status'] == 'pending'
    assert item['accepted_item_id'] is None
    assert item['source']['text'] == CN
    assert item['source']['message_id'] == 'fictional-1'
    assert item['source']['sent_at'] == NOW - 100
    assert item['source']['conversation_key'] == KEY
    assert deals.read('detail', row_id=deal_id)['items'] == []
    assert demands.scan(deal_id, {}, 'fictional-owner')['added'] == 0


def test_accept_creates_item_with_confirmed_overrides_and_never_a_price(context):
    demands, deals, _, _, deal_id, _ = context
    row_id = scanned(context)
    result = demands.accept(deal_id, row_id, dict(qty='28', destination='虚构目的地', accessories='虚构灯钩'), 'fictional-owner')
    item = deals.read('detail', row_id=deal_id)['items'][0]
    assert item['id'] == result['accepted_item_id']
    assert item['qty'] == 28 and item['product_id'] is None and item['target_price'] is None
    assert '目的地：虚构目的地' in item['note'] and '配套要求：虚构灯钩' in item['note']
    state = demands.read(deal_id)['items'][0]
    assert state['status'] == 'accepted' and state['decided_by'] == 'fictional-owner'
    assert state['qty'] == 28
    with demands.archive.read() as db:
        assert db.execute('SELECT max(version) FROM schema_migrations').fetchone()[0] == 2
        assert db.execute('SELECT count(*) FROM products').fetchone()[0] == 0
        assert db.execute('SELECT count(*) FROM offers').fetchone()[0] == 0


def test_repeat_and_parallel_accept_create_only_one_item(context):
    demands, deals, _, _, deal_id, _ = context
    row_id = scanned(context)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: demands.accept(deal_id, row_id, {}, 'fictional-owner'), range(4)))
    assert len({r['accepted_item_id'] for r in results}) == 1
    assert sum(not r['reused'] for r in results) == 1
    assert len(deals.read('detail', row_id=deal_id)['items']) == 1


def test_dismiss_is_idempotent_and_cannot_accept(context):
    demands, _, _, _, deal_id, _ = context
    row_id = scanned(context)
    for _ in range(2):
        assert demands.dismiss(deal_id, row_id, 'fictional-owner')['ok']
    with pytest.raises(v.Conflict, match='已忽略'):
        demands.accept(deal_id, row_id, {}, 'fictional-owner')


def test_accepted_candidate_cannot_be_dismissed(context):
    demands, _, _, _, deal_id, _ = context
    row_id = scanned(context)
    demands.accept(deal_id, row_id, {}, 'fictional-owner')
    with pytest.raises(v.Conflict, match='已确认'):
        demands.dismiss(deal_id, row_id, 'fictional-owner')


@pytest.mark.parametrize('direction,text', [('seller', CN), ('unknown', CN), ('buyer', ''), ('buyer', '请问你们好吗？')])
def test_no_product_without_inbound_customer_text(context, direction, text):
    demands, _, _, inbox, deal_id, _ = context
    inbox.conversations[KEY]['messages'] = [dict(role=direction, text=text, sent_at=NOW)]
    result = demands.scan(deal_id, {}, 'fictional-owner')
    assert result['items'] == []


def test_missing_identity_or_noncustomer_cannot_scan(context):
    demands, deals, archive, _, deal_id, _ = context
    supplier = archive.write('party_create', None, dict(kind='supplier', name='虚构供应方'), 'fictional-owner')['id']
    deals.write('conversation', deal_id, dict(conversation_key=KEY2, party_id=str(supplier)), 'fictional-owner')
    with pytest.raises(v.Conflict, match='本案客户'):
        demands.scan(deal_id, dict(conversation_key=KEY2), 'fictional-owner')
    with demands.archive.write() as db:
        db.execute('DELETE FROM identities WHERE conversation_key=?', (KEY,))
    with pytest.raises(v.Conflict, match='本案客户'):
        demands.scan(deal_id, {}, 'fictional-owner')
    assert not demands.store.path.exists()


def test_identity_transfer_after_scan_requires_new_review(context):
    demands, deals, archive, _, deal_id, _ = context
    row_id = scanned(context)
    customer = archive.write('party_create', None, dict(kind='customer', name='虚构另一客户'), 'fictional-owner')['id']
    deals.write('identity', None, dict(conversation_key=KEY, party_id=str(customer), confirm='1'), 'fictional-owner')
    with pytest.raises(v.Conflict):
        demands.accept(deal_id, row_id, {}, 'fictional-owner')
    assert demands.read(deal_id)['items'][0]['status'] == 'pending'
    assert deals.read('detail', row_id=deal_id)['items'] == []


def test_missing_or_changed_source_refuses_accept(context):
    demands, _, _, inbox, deal_id, _ = context
    row_id = scanned(context)
    inbox.conversations[KEY]['messages'][0]['text'] = ES
    with pytest.raises(v.Conflict, match='原文已变化'):
        demands.accept(deal_id, row_id, {}, 'fictional-owner')
    inbox.conversations[KEY] = None
    with pytest.raises(v.BadRequest, match='暂时读不到'):
        demands.accept(deal_id, row_id, {}, 'fictional-owner')


def test_two_deals_have_independent_candidates_and_cannot_access_other_candidate(context):
    demands, deals, _, _, deal_id, _ = context
    row_id = scanned(context)
    other = deals.write('create', None, dict(title='虚构另一案子'), 'fictional-owner')['id']
    with pytest.raises(v.Missing):
        demands.accept(other, row_id, {}, 'fictional-owner')
    with pytest.raises(v.Missing):
        demands.dismiss(other, row_id, 'fictional-owner')
    assert demands.read(other)['items'] == []


def test_demand_and_item_rollback_together(context):
    demands, deals, _, _, deal_id, _ = context
    row_id = scanned(context)
    with patch.object(Deals, '_event', side_effect=RuntimeError('fictional failure')):
        with pytest.raises(RuntimeError):
            demands.accept(deal_id, row_id, {}, 'fictional-owner')
    assert demands.read(deal_id)['items'][0]['status'] == 'pending'
    assert deals.read('detail', row_id=deal_id)['items'] == []
    assert demands.accept(deal_id, row_id, {}, 'fictional-owner')['ok']


def test_recover_from_archive_commit_without_sidecar_decision(context):
    demands, deals, _, _, deal_id, _ = context
    row_id = scanned(context)
    first = demands.accept(deal_id, row_id, {}, 'fictional-owner')
    with demands.store.write() as db:
        db.execute("UPDATE candidates SET status='pending',accepted_item_id=NULL WHERE id=?", (row_id,))
    result = demands.accept(deal_id, row_id, {}, 'fictional-owner')
    assert result['reused'] and result['accepted_item_id'] == first['accepted_item_id']
    assert len(deals.read('detail', row_id=deal_id)['items']) == 1


@pytest.mark.parametrize('fields', [dict(qty='0'), dict(qty='2.5'), dict(qty='1000001'), dict(name=''),
                                  dict(price='50'), dict(product_id='1'), dict(archive_path='/tmp/evil')])
def test_invalid_confirmation_fields_do_not_write(context, fields):
    demands, deals, _, _, deal_id, _ = context
    row_id = scanned(context)
    with pytest.raises(v.BadRequest):
        demands.accept(deal_id, row_id, fields, 'fictional-owner')
    assert deals.read('detail', row_id=deal_id)['items'] == []


def test_private_store_rejects_symlink_and_keeps_value_free_audit(context, tmp_path):
    demands, _, _, _, deal_id, _ = context
    target = tmp_path / 'fictional-other.sqlite3'
    target.write_bytes(b'fictional untouched file')
    demands.store.path.symlink_to(target)
    with pytest.raises(v.BadRequest, match='路径不可用'):
        demands.scan(deal_id, {}, 'fictional-owner')
    assert target.read_bytes() == b'fictional untouched file'
    demands.store.path.unlink()
    row_id = scanned(context)
    demands.accept(deal_id, row_id, dict(name="虚构灯'); DROP TABLE deals; --"), 'fictional-owner')
    assert demands.store.path.stat().st_mode & 0o777 == 0o600
    with demands.store.read() as db:
        raw = json.dumps([dict(r) for r in db.execute('SELECT * FROM audit')])
    assert 'LX-100' not in raw and 'DROP TABLE' not in raw and '24' not in raw
    with demands.archive.read() as db:
        assert db.execute('SELECT count(*) FROM deals').fetchone()[0] == 1


def test_long_text_is_skipped_without_partial_extraction(context):
    demands, _, _, inbox, deal_id, _ = context
    inbox.conversations[KEY]['messages'][0]['text'] = CN + '虚构补充' * 2000
    result = demands.scan(deal_id, {}, 'fictional-owner')
    assert result['added'] == 0 and result['skipped'] == 1


def test_synthetic_message_id_is_marked_derived(context):
    demands, _, _, inbox, deal_id, _ = context
    del inbox.conversations[KEY]['messages'][0]['message_id']
    scanned(context)
    source = demands.read(deal_id)['items'][0]['source']
    assert source['derived'] and source['id'].startswith('derived:')


def test_parent_directory_symlink_is_rejected_for_sidecar_and_archive(context, tmp_path):
    demands, _, _, _, deal_id, _ = context
    linked = tmp_path / 'fictional-symlink'
    linked.symlink_to(tmp_path, target_is_directory=True)
    demands.store.path = linked / 'another-demands.sqlite3'
    with pytest.raises(v.BadRequest, match='路径不可用'):
        demands.scan(deal_id, {}, 'fictional-owner')
    demands.store.path = tmp_path / 'demands.sqlite3'
    row_id = scanned(context)
    demands.archive.path = linked / 'archive.sqlite3'
    with pytest.raises(v.BadRequest, match='路径不可用'):
        demands.accept(deal_id, row_id, {}, 'fictional-owner')


def test_missing_previously_accepted_item_requires_manual_reconciliation(context):
    demands, deals, _, _, deal_id, _ = context
    row_id = scanned(context)
    result = demands.accept(deal_id, row_id, {}, 'fictional-owner')
    deals.write('item_remove', deal_id, {}, 'fictional-owner', result['accepted_item_id'])
    with pytest.raises(v.Conflict, match='确认结果需要核对'):
        demands.accept(deal_id, row_id, {}, 'fictional-owner')
    assert deals.read('detail', row_id=deal_id)['items'] == []


def test_wrong_conversation_cannot_supply_source_text(context):
    demands, _, _, inbox, deal_id, _ = context
    inbox.conversations[KEY]['key'] = KEY2
    with pytest.raises(v.BadRequest, match='暂时读不到'):
        demands.scan(deal_id, {}, 'fictional-owner')


def test_archive_hardlink_cannot_be_used_as_candidate_store(context, tmp_path):
    demands, _, _, inbox, _, _ = context
    before = demands.archive.path.read_bytes()
    linked = tmp_path / 'fictional-archive-hardlink.sqlite3'
    os.link(demands.archive.path, linked)
    with pytest.raises(v.BadRequest, match='须与档案库分开'):
        Demands(store=demands.archive, path=linked, inbox=inbox, clock=lambda: NOW)
    assert demands.archive.path.read_bytes() == before
    with demands.archive.read() as db:
        assert not db.execute("SELECT name FROM sqlite_master WHERE name='candidates'").fetchone()
