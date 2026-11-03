"""Fictional ledger fixtures; no channels, live files, network or model calls."""
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from console.archive import validation as v
from console.archive.service import get
from console.archive.store import Store, insert
from console.deals.service import Deals
from console.fulfil import payments


@pytest.fixture
def ledger(tmp_path):
    store = Store(tmp_path / 'fictional-ledger.sqlite3', target_version=8)
    deals = Deals(store=store, lookup=lambda key: None, clock=lambda: 1893456000)
    did = deals.write('create', None, {'title': '虚构灯具订单'}, 'fictional-owner')['id']
    stamp = '2030-01-01T00:00:00+00:00'
    with store.write() as db:
        rid = insert(db, 'quote_rounds', dict(deal_id=did, round_no=1, status='accepted', currency='USD',
                     created_at=stamp, updated_at=stamp), 'fictional-owner')
        insert(db, 'quote_lines', dict(round_id=rid, name='虚构产品', qty=4, sell_unit_minor=2350,
               cost_unit_fen=1391), 'fictional-owner')
    def call(operation, row_id, **fields):
        with store.write() as db:
            return getattr(payments, operation)(db, row_id, fields, 'fictional-owner', stamp, deals)
    def create(**overrides):
        fields = dict(kind='deposit', amount_fen='3000', currency='USD', received_on='2030-01-01', method='wire')
        fields.update(overrides)
        return call('create', did, **fields)['id']
    def read():
        with store.read() as db:
            return payments.list_payments(db, did)
    return SimpleNamespace(store=store, deals=deals, did=did, rid=rid, call=call, create=create, read=read)


def test_pending_never_confirms_or_advances_and_confirmation_uses_deal_semantics(ledger):
    pid = ledger.create(note='虚构私人备注', evidence='虚构电汇回执描述')
    assert ledger.read()['items'][0]['status'] == 'pending'
    assert ledger.read()['items'][0]['quote_round_id'] == ledger.rid
    assert ledger.deals.read('detail', row_id=ledger.did)['stage'] == 'inquiry'
    for confirm in ({}, {'confirm': '0'}):
        with pytest.raises(v.BadRequest, match='确认'):
            ledger.call('confirm', pid, **confirm)
    ledger.call('confirm', pid, confirm='1')
    detail = ledger.deals.read('detail', row_id=ledger.did)
    assert detail['stage'] == 'paid' and detail['status'] == 'won' and detail['won_at']
    assert any(e['summary'] == '收到客户付款' for e in detail['events'])
    payment = ledger.read()['items'][0]
    assert payment['confirmed_by'] == 'fictional-owner' and payment['confirmed_at']
    ledger.call('confirm', pid, confirm='1')
    assert ledger.deals.read('detail', row_id=ledger.did) == detail
    with ledger.store.read() as db:
        audits = [dict(r) for r in db.execute("SELECT * FROM audit WHERE table_name='deal_payments'")]
    assert [a['action'] for a in audits] == ['create', 'confirm']
    assert '虚构私人备注' not in str(audits) and '虚构电汇回执描述' not in str(audits)


@pytest.mark.parametrize('kind', ['deposit', 'full'])
def test_first_deposit_or_full_advances_once(ledger, kind):
    pid = ledger.create(kind=kind)
    ledger.call('confirm', pid, confirm='1', qualifies='0')
    won_at = ledger.deals.read('detail', row_id=ledger.did)['won_at']
    ledger.deals.write('update', ledger.did, {'stage': 'inspection'}, 'fictional-owner')
    ledger.call('confirm', ledger.create(kind=kind), confirm='1')
    current = ledger.deals.read('detail', row_id=ledger.did)
    assert current['stage'] == 'inspection' and current['won_at'] == won_at


def test_accepted_quote_pending_balance_and_refund_do_not_allow_stage_bypass(ledger):
    with ledger.store.write() as db:
        ledger.deals.quote_event(db, ledger.did, '虚构客户接受报价', 'fictional-owner', '2030-01-01', accepted=True)
    assert ledger.deals.read('detail', row_id=ledger.did)['stage'] == 'awaiting_payment'
    ledger.create()
    for kind in ('balance', 'refund'):
        ledger.call('confirm', ledger.create(kind=kind), confirm='1')
    for fields in ({'stage': 'paid'}, {'stage': 'shipped', 'status': 'won'}, {'status': 'won'}):
        with pytest.raises(v.BadRequest):
            ledger.deals.write('update', ledger.did, fields, 'fictional-owner')
    detail = ledger.deals.read('detail', row_id=ledger.did)
    assert detail['status'] == 'open' and detail['won_at'] is None


def test_confirm_is_atomic_and_duplicate_keeps_original_actor_time_and_events(ledger):
    pid = ledger.create()
    original = ledger.deals._write
    def checked(db, operation, row_id, values, who, stamp, child_id, **options):
        payment = get(db, 'deal_payments', pid)
        assert payment['status'] == 'confirmed'
        assert payment['confirmed_by'] == who and payment['confirmed_at'] == stamp
        assert options == {'payment_confirmed': True}
        return original(db, operation, row_id, values, who, stamp, child_id, **options)
    with patch.object(ledger.deals, '_write', checked):
        ledger.call('confirm', pid, confirm='1')
    before, detail = ledger.read(), ledger.deals.read('detail', row_id=ledger.did)
    with ledger.store.write() as db:
        payments.confirm(db, pid, {'confirm': '1', 'qualifies': '1'}, 'another-fictional-owner',
                         '2030-01-02T00:00:00+00:00', ledger.deals)
    assert ledger.read() == before
    assert ledger.deals.read('detail', row_id=ledger.did) == detail


def test_balance_needs_explicit_qualification_and_refund_cannot_qualify(ledger):
    pid = ledger.create(kind='balance')
    ledger.call('confirm', pid, confirm='1')
    assert ledger.deals.read('detail', row_id=ledger.did)['stage'] == 'inquiry'
    pid = ledger.create(kind='balance')
    ledger.call('confirm', pid, confirm='1', qualifies='1')
    assert ledger.deals.read('detail', row_id=ledger.did)['stage'] == 'paid'
    with pytest.raises(v.BadRequest, match='退款'):
        ledger.call('confirm', ledger.create(kind='refund'), confirm='1', qualifies='1')


def test_manual_rollback_stays_manual_without_explicit_new_qualification(ledger):
    ledger.call('confirm', ledger.create(), confirm='1')
    ledger.deals.write('update', ledger.did, {'stage': 'negotiating'}, 'fictional-owner')
    ledger.call('confirm', ledger.create(), confirm='1')
    assert ledger.deals.read('detail', row_id=ledger.did)['stage'] == 'negotiating'
    ledger.call('confirm', ledger.create(kind='balance'), confirm='1', qualifies='1')
    assert ledger.deals.read('detail', row_id=ledger.did)['stage'] == 'paid'


@pytest.mark.parametrize('kind', ['deposit', 'full'])
def test_void_only_qualifying_payment_makes_new_deposit_or_full_first_again(ledger, kind):
    old = ledger.create()
    ledger.call('confirm', old, confirm='1')
    ledger.deals.write('update', ledger.did, {'stage': 'negotiating'}, 'fictional-owner')
    ledger.call('void', old, note='虚构旧款作废')
    pid = ledger.create(kind=kind)
    ledger.call('confirm', pid, confirm='1', qualifies='0')
    detail = ledger.deals.read('detail', row_id=ledger.did)
    assert detail['stage'] == 'paid' and detail['status'] == 'won'
    assert [p['status'] for p in ledger.read()['items']] == ['void', 'confirmed']


def test_void_and_refund_are_signed_ledger_changes_without_stage_rollback(ledger):
    paid_id = ledger.create(amount_fen='4000', note='原备注')
    ledger.call('confirm', paid_id, confirm='1')
    refund_id = ledger.create(kind='refund', amount_fen='1000')
    ledger.call('confirm', refund_id, confirm='1')
    assert ledger.read()['summary'] == [dict(currency='USD', received_fen=3000, receivable_fen=9400,
                                      unreceived_fen=6400, quote_round_id=ledger.rid)]
    assert ledger.read()['items'][1]['signed_amount_fen'] == -1000
    ledger.call('void', paid_id, note='虚构重复收款')
    ledger.call('void', paid_id, note='重复点击不覆盖')
    item = ledger.read()['items'][0]
    assert item['note'] == '原备注' and item['void_note'] == '虚构重复收款'
    assert item['confirmed_at'] and item['voided_by'] == 'fictional-owner'
    assert ledger.read()['summary'][0]['received_fen'] == -1000
    assert ledger.deals.read('detail', row_id=ledger.did)['stage'] == 'paid'
    with pytest.raises(v.Conflict):
        ledger.call('confirm', paid_id, confirm='1')


def test_currency_mismatch_requires_explanation_and_totals_never_mix(ledger):
    usd = ledger.create(amount_fen='1000')
    eur = ledger.create(currency='EUR', amount_fen='2000')
    ledger.call('confirm', usd, confirm='1')
    with pytest.raises(v.BadRequest, match='币种'):
        ledger.call('confirm', eur, confirm='1')
    assert ledger.read()['items'][1]['status'] == 'pending'
    ledger.call('confirm', eur, confirm='1', currency_note='虚构约定欧元付款，人工另行核销')
    assert ledger.read()['summary'] == [
        dict(currency='EUR', received_fen=2000, receivable_fen=None, unreceived_fen=None, quote_round_id=None),
        dict(currency='USD', received_fen=1000, receivable_fen=9400, unreceived_fen=8400, quote_round_id=ledger.rid)]


@pytest.mark.parametrize('amount', ['', '0', '-1', '+1', '1.00', '1e2', 'NaN', 'Infinity', ' 1',
                                    '１', '1000000000000', 3, True, None])
def test_amount_fen_requires_bounded_positive_integer_string(ledger, amount):
    with pytest.raises(v.BadRequest):
        ledger.create(amount_fen=amount)
    assert ledger.read()['items'] == []


@pytest.mark.parametrize('field,value', [('status', 'confirmed'), ('confirmed_by', 'robot'),
                                       ('qualifies', '1'), ('currency', 'usd'), ('received_on', '2030-02-30'),
                                       ('method', 'unknown'), ('kind', 'auto')])
def test_create_rejects_untrusted_confirmation_or_invalid_business_fields(ledger, field, value):
    with pytest.raises(v.BadRequest):
        ledger.create(**{field: value})


def test_default_quote_is_latest_accepted_and_empty_association_remains_empty(ledger):
    with ledger.store.write() as db:
        for number, status in [(2, 'accepted'), (3, 'sent')]:
            insert(db, 'quote_rounds', dict(deal_id=ledger.did, round_no=number, status=status, currency='USD',
                   created_at='2030-01-01', updated_at='2030-01-01'), 'fictional-owner')
    ledger.create()
    ledger.create(quote_round_id='')
    rows = ledger.read()['items']
    with ledger.store.read() as db:
        latest = db.execute('SELECT id FROM quote_rounds WHERE round_no=2').fetchone()[0]
    assert [p['quote_round_id'] for p in rows] == [latest, None]
    assert ledger.read()['summary'][0]['receivable_fen'] is None


def test_other_deal_quote_and_unknown_ids_are_missing(ledger):
    did = ledger.deals.write('create', None, {'title': '另一虚构案子'}, 'fictional-owner')['id']
    with ledger.store.write() as db:
        rid = insert(db, 'quote_rounds', dict(deal_id=did, round_no=1, status='accepted', currency='USD',
                     created_at='2030-01-01', updated_at='2030-01-01'), 'fictional-owner')
    for quote in (str(rid), '999999'):
        with pytest.raises(v.Missing):
            ledger.create(quote_round_id=quote)
    for operation, fields in [('confirm', {'confirm': '1'}), ('void', {})]:
        with pytest.raises(v.Missing):
            ledger.call(operation, 99999, **fields)
    with ledger.store.read() as db, pytest.raises(v.Missing):
        payments.list_payments(db, 999999)


def test_failed_deal_transition_rolls_back_payment_event_and_audit(ledger):
    pid = ledger.create()
    with ledger.store.read() as db:
        before = {t: [tuple(r) for r in db.execute('SELECT * FROM ' + t)]
                  for t in ('deal_payments', 'deal_events', 'deals', 'audit')}
    with patch.object(ledger.deals, '_write', side_effect=RuntimeError('fictional fault')):
        with pytest.raises(RuntimeError):
            ledger.call('confirm', pid, confirm='1')
    with ledger.store.read() as db:
        assert before == {t: [tuple(r) for r in db.execute('SELECT * FROM ' + t)] for t in before}


def test_payment_paths_never_enqueue_or_send_channels(ledger):
    with patch('console.outbox.store.Store._write') as outbox, \
         patch('console.outbox.bridge_client.BridgeClient.deliver') as delivery, \
         patch('console.outbox.bridge_client.local_post') as channel:
        pid = ledger.create()
        ledger.read()
        ledger.call('confirm', pid, confirm='1')
        ledger.call('void', pid)
        outbox.assert_not_called()
        delivery.assert_not_called()
        channel.assert_not_called()


def test_legacy_quote_summary_read_does_not_migrate(ledger, tmp_path):
    store = Store(tmp_path / 'fictional-v3.sqlite3', target_version=3)
    deals = Deals(store=store, lookup=lambda key: None, clock=lambda: 1893456000)
    did = deals.write('create', None, {}, 'fictional-owner')['id']
    with store.read() as db:
        assert payments.list_payments(db, did) == {'items': [], 'summary': []}
        assert [r[0] for r in db.execute('SELECT version FROM schema_migrations')] == [2, 3]
