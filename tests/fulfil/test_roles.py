"""E1 roles checks use only synthetic SQLite, fake clocks and injected runners."""
from dataclasses import replace
from datetime import timedelta
import importlib.util
from pathlib import Path
import socket
from unittest.mock import patch

import pytest

from console.archive.store import Store, insert, update
from console.deals.rules import paid
from console.roles.common import stamp
from console.roles.inputs import BUSINESS_TABLES, FULFIL_TABLES


@pytest.fixture
def e1_env(tmp_path):
    # Reuse the existing synthetic quote/role builders without depending on a
    # particular pytest import mode or another directory's fixture discovery.
    spec = importlib.util.spec_from_file_location('e1_role_fixtures', Path(__file__).parents[1] / 'roles' / 'conftest.py')
    fixtures = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixtures)
    env = fixtures.Env(tmp_path)
    with Store(env.path, target_version=8).write():
        pass
    original_deal = env.deal
    def legacy_deal(*, stage='inquiry', **fields):
        did = original_deal(stage='inquiry' if paid(stage) else stage, **fields)
        if paid(stage):
            # These alert/quote-fallback tests deliberately model pre-ledger won data.
            change(env, 'deals', did, stage=stage, status='won', won_at=stamp(env.clock()))
        return did
    env.deal = legacy_deal
    return env


@pytest.fixture(autouse=True)
def offline_roles_e1(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError('E1 roles tests cannot access network or real AI')
    monkeypatch.setattr(socket.socket, 'connect', blocked)
    monkeypatch.setattr(socket, 'create_connection', blocked)
    monkeypatch.setattr('console.inbox.ai.official_runner', blocked)


def add_node(env, did, node, **values):
    at = stamp(env.clock())
    with env.store.write() as db:
        return insert(db, 'fulfilment_nodes', dict(deal_id=did, node=node, created_at=at, updated_at=at, **values), 'fictional-owner')


def add_payment(env, did, *, amount=1200, kind='deposit', status='confirmed', currency='USD', age=0):
    at = stamp(env.clock() - age)
    values = dict(deal_id=did, kind=kind, amount_fen=amount, currency=currency,
                  received_on='2030-01-10', method='wire', status=status,
                  created_at=at, created_by='fictional-owner', updated_at=at)
    if status == 'confirmed':
        values.update(confirmed_at=at, confirmed_by='fictional-owner')
    elif status == 'void':
        values.update(voided_at=at, voided_by='fictional-owner')
    with env.store.write() as db:
        return insert(db, 'deal_payments', values, 'fictional-owner')


def add_issue(env, did, *, age=0, status='open'):
    at = stamp(env.clock() - age)
    with env.store.write() as db:
        return insert(db, 'deal_issues', dict(deal_id=did, kind='quality', status=status,
                      description='虚构灯具划痕', created_at=at, updated_at=at, created_by='fictional-owner'), 'fictional-owner')


def change(env, table, rid, **fields):
    with env.store.write() as db:
        update(db, table, rid, fields, 'fictional-owner')


def alerts(env):
    env.roles.run_patrol('fictional-owner')
    return env.roles.alerts()


@pytest.mark.parametrize('days,kind', [(4, None), (3, 'delivery_due'), (0, 'delivery_due'), (-1, 'delivery_overdue')])
def test_delivery_dates_include_won_deals_and_boundaries(e1_env, days, kind):
    env = e1_env
    did = env.deal(stage='paid')
    day = env.roles.day()
    add_node(env, did, 'supplier_confirmation', status='done', promised_on=(day + timedelta(days=days)).isoformat())
    found = alerts(env)
    assert {a['kind'] for a in found} == ({kind} if kind else set())
    assert env.deals.read('detail', row_id=did)['stage'] == 'paid'


@pytest.mark.parametrize('shipping', ['stage', 'departed', 'transit', 'signed'])
def test_shipped_progress_resolves_delivery_without_advancing_stage(e1_env, shipping):
    env = e1_env
    did = env.deal(stage='paid')
    add_node(env, did, 'supplier_confirmation', promised_on='2030-01-09')
    assert alerts(env)[0]['kind'] == 'delivery_overdue'
    if shipping == 'stage':
        env.dw('update', did, stage='shipped')
    elif shipping == 'departed':
        add_node(env, did, 'booking', departed_on='2030-01-10')
    elif shipping == 'transit':
        add_node(env, did, 'transit', status='doing')
    else:
        add_node(env, did, 'signoff', status='done', signed_on='2030-01-10')
    assert alerts(env) == []
    assert env.deals.read('detail', row_id=did)['stage'] == ('shipped' if shipping == 'stage' else 'paid')


@pytest.mark.parametrize('result', ['fail', 'needs_fix'])
def test_inspection_failure_remains_until_pass_and_deduplicates(e1_env, result):
    env = e1_env
    did = env.deal(stage='inspection')
    node = add_node(env, did, 'inspection', status='done', inspection_result=result)
    first = alerts(env)[0]
    env.clock.advance(1)
    repeated = alerts(env)[0]
    assert first['kind'] == 'inspection_failed' and repeated['id'] == first['id']
    assert repeated['first_seen_at'] == first['first_seen_at']
    assert repeated['last_seen_at'] > first['last_seen_at']
    change(env, 'fulfilment_nodes', node, inspection_result='pass')
    assert alerts(env) == []
    with env.store.read() as db:
        assert db.execute('SELECT resolved_at FROM role_alerts WHERE id=?', (first['id'],)).fetchone()[0]


@pytest.mark.parametrize('days,expected', [(0, False), (-1, True), (1, False)])
def test_eta_overdue_and_signed_resolution(e1_env, days, expected):
    env = e1_env
    did = env.deal(stage='shipped')
    add_node(env, did, 'booking', eta=(env.roles.day() + timedelta(days=days)).isoformat())
    assert bool(alerts(env)) == expected
    add_node(env, did, 'signoff', status='done', signed_on='2030-01-10')
    assert alerts(env) == []


@pytest.mark.parametrize('age,expected', [(7 * 86400, False), (7 * 86400 + 1, True)])
def test_signed_open_issue_age_and_resolution(e1_env, age, expected):
    env = e1_env
    did = env.deal(stage='delivered')
    iid = add_issue(env, did, age=age)
    found = alerts(env)
    assert {a['kind'] for a in found} == ({'issue_open_overdue'} if expected else set())
    change(env, 'deal_issues', iid, status='resolved', resolution='虚构已处理')
    assert alerts(env) == []


def test_unsigned_issue_does_not_trigger_signed_rule(e1_env):
    env = e1_env
    did = env.deal(stage='paid')
    add_issue(env, did, age=20 * 86400)
    assert alerts(env) == []
    add_node(env, did, 'signoff', status='done', signed_on='2030-01-10')
    assert alerts(env)[0]['kind'] == 'issue_open_overdue'


@pytest.mark.parametrize('age,expected', [(2 * 86400, False), (2 * 86400 + 1, True)])
@pytest.mark.parametrize('decision', ['confirmed', 'void'])
def test_pending_payment_age_boundary_and_manual_resolution(e1_env, age, expected, decision):
    env = e1_env
    did = env.deal(stage='paid')
    pid = add_payment(env, did, status='pending', age=age)
    found = alerts(env)
    assert {a['kind'] for a in found} == ({'payment_pending'} if expected else set())
    with env.store.read() as db:
        assert db.execute('SELECT status FROM deal_payments WHERE id=?', (pid,)).fetchone()[0] == 'pending'
    extra = {'confirmed_at': stamp(env.clock()), 'confirmed_by': 'fictional-owner'} if decision == 'confirmed' \
        else {'voided_at': stamp(env.clock()), 'voided_by': 'fictional-owner'}
    change(env, 'deal_payments', pid, status=decision, **extra)
    assert alerts(env) == []


def test_new_thresholds_are_configurable_and_invalidate_patrol(e1_env):
    env = e1_env
    did = env.deal(stage='paid')
    add_node(env, did, 'supplier_confirmation', promised_on='2030-01-14')
    add_payment(env, did, status='pending', age=86400 + 1)
    assert alerts(env) == []
    env.roles = env.service(config=replace(env.roles.config, delivery_due_days=4, pending_payment_days=1))
    assert {a['kind'] for a in alerts(env)} == {'delivery_due', 'payment_pending'}
    env.dw('update', did, stage='delivered')
    add_issue(env, did, age=86400 + 1)
    env.roles = env.service(config=replace(env.roles.config, issue_open_days=1))
    assert 'issue_open_overdue' in {a['kind'] for a in alerts(env)}


def accepted_deal(env, price='20.00'):
    did, rid, *_ = env.good_round(amount=price)
    env.qw('status', rid, action='send', confirm='1')
    env.qw('status', rid, action='accept')
    # Historical won rows without payments retain the quote-fallback coverage.
    change(env, 'deals', did, stage='paid', status='won', won_at=stamp(env.clock()))
    return did, rid


def test_brief_prefers_confirmed_payments_refunds_and_separate_currencies(e1_env):
    env = e1_env
    paid, _ = accepted_deal(env, '999.00')
    fallback, _ = accepted_deal(env, '20.00')
    add_payment(env, paid, amount=1200)
    add_payment(env, paid, amount=200, kind='refund')
    add_payment(env, paid, amount=500, kind='balance', currency='EUR')
    add_payment(env, paid, amount=9000, status='pending')
    add_payment(env, paid, amount=9000, status='void')
    add_payment(env, fallback, amount=20000, status='pending')
    add_payment(env, fallback, amount=20000, status='void')
    result = env.roles.run_brief('fictional-owner')['output_json']['results']['today']
    assert result['count'] == 2 and result['amount_pending'] == 0
    assert result['amounts'] == {'EUR': '5.00', 'USD': '50.00'}
    assert result['amount_sources'] == {'payment': 1, 'quote': 1}
    entries = {e['deal_id']: e for e in result['deals']}
    assert entries[paid]['source'] == 'payment'
    assert entries[paid]['amounts'] == {'EUR': '5.00', 'USD': '10.00'}
    assert entries[paid]['currency'] is None and entries[paid]['amount'] is None
    assert entries[fallback]['source'] == 'quote' and entries[fallback]['amount'] == '40.00'


def test_brief_zero_net_payment_stays_payment_and_change_refreshes(e1_env):
    env = e1_env
    did, _ = accepted_deal(env)
    add_payment(env, did, amount=1200)
    pid = add_payment(env, did, amount=1200, kind='refund')
    initial = env.roles.run_brief('fictional-owner')
    assert initial['output_json']['results']['today']['amounts'] == {'USD': '0.00'}
    change(env, 'deal_payments', pid, status='void', voided_at=stamp(env.clock()), voided_by='fictional-owner')
    refreshed = env.roles.run_brief('fictional-owner')
    assert refreshed['id'] != initial['id'] and not refreshed['reused']
    assert refreshed['output_json']['results']['today']['amounts'] == {'USD': '12.00'}
    assert refreshed['output_json']['results']['today']['deals'][0]['source'] == 'payment'


def test_brief_fulfilment_counts_deduplicate_deals_and_week_bounds(e1_env):
    env = e1_env
    moving = env.deal(stage='paid')
    add_node(env, moving, 'transit', status='doing')
    add_node(env, moving, 'booking', departed_on='2030-01-09', eta='2030-01-13')
    later = env.deal(stage='shipped')
    add_node(env, later, 'booking', eta='2030-01-14')
    signed = env.deal(stage='shipped')
    add_node(env, signed, 'booking', eta='2030-01-10')
    add_node(env, signed, 'signoff', status='done', signed_on='2030-01-10')
    add_issue(env, moving)
    add_issue(env, moving, status='waiting_party')
    add_issue(env, signed, status='resolved')
    add_payment(env, moving, status='pending')
    add_payment(env, signed, status='pending')
    result = env.roles.run_brief('fictional-owner')
    assert result['output_json']['fulfilment'] == dict(in_transit=2, arriving_this_week=1, open_issues=1, pending_payments=2)
    assert '在途货物 2 单' in result['output_text'] and '待核对收款 2 笔' in result['output_text']


def test_roles_do_not_write_fulfilment_or_send_any_channel(e1_env):
    env = e1_env
    did, _ = accepted_deal(env)
    add_node(env, did, 'supplier_confirmation', promised_on='2030-01-09')
    add_node(env, did, 'inspection', inspection_result='fail', status='blocked')
    add_payment(env, did, status='pending', age=3 * 86400)
    add_issue(env, did, age=10 * 86400)

    def snapshot():
        with env.store.read() as db:
            return {t: [tuple(r) for r in db.execute('SELECT * FROM ' + t + ' ORDER BY id')]
                    for t in BUSINESS_TABLES + FULFIL_TABLES}

    before = snapshot()
    with patch('console.outbox.store.Store._write') as outbox, \
         patch('console.outbox.bridge_client.BridgeClient.deliver') as delivery, \
         patch('console.outbox.bridge_client.local_post') as channel:
        env.roles.run_patrol('fictional-owner')
        env.roles.run_brief('fictional-owner')
        outbox.assert_not_called()
        delivery.assert_not_called()
        channel.assert_not_called()
    assert snapshot() == before


def test_actual_fulfil_service_drives_patrol_resolution_and_brief(e1_env):
    from console.fulfil.service import Fulfil
    env = e1_env
    did, rid, *_ = env.good_round()
    env.qw('status', rid, action='send', confirm='1')
    env.qw('status', rid, action='accept')
    fulfil = Fulfil(store=env.store, clock=env.clock, today=env.roles.day)

    def write(op, row_id, **fields):
        return fulfil.write(op, row_id, fields, 'fictional-operator')['id']

    def node(key, **fields):
        return fulfil.write('node_update', did, fields, 'fictional-operator', key)['id']

    pid = write('payment_create', did, kind='deposit', amount_fen='1200', currency='USD',
                received_on=env.roles.day().isoformat(), method='wire')
    env.clock.advance(2 * 86400 + 1)
    assert {a['kind'] for a in alerts(env)} == {'payment_pending'}
    assert fulfil.read('payments', row_id=did)['items'][0]['status'] == 'pending'
    write('payment_confirm', pid, confirm='1')
    assert env.deals.read('detail', row_id=did)['stage'] == 'paid'
    assert alerts(env) == []
    yesterday = (env.roles.day() - timedelta(days=1)).isoformat()
    node('supplier_confirmation', status='done', promised_on=yesterday)
    node('inspection', status='done', inspection_result='fail')
    node('booking', status='done', eta=yesterday)
    assert {a['kind'] for a in alerts(env)} == {'delivery_overdue', 'inspection_failed', 'eta_overdue'}
    iid = write('issue_create', did, kind='quality', description='虚构验货问题待处理')
    node('signoff', status='done')
    env.clock.advance(7 * 86400 + 1)
    assert {a['kind'] for a in alerts(env)} == {'inspection_failed', 'issue_open_overdue'}
    node('inspection', inspection_result='pass')
    write('issue_update', iid, status='resolved', resolution='虚构问题已处理，复验通过')
    assert alerts(env) == []
    result = env.roles.run_brief('fictional-owner')['output_json']
    assert result['results']['month']['amounts'] == {'USD': '12.00'}
    assert result['results']['month']['deals'][0]['source'] == 'payment'
    assert result['fulfilment'] == dict(in_transit=0, arriving_this_week=0, open_issues=0, pending_payments=0)
    assert env.deals.read('detail', row_id=did)['stage'] == 'paid'


def test_legacy_roles_inputs_keep_new_counts_empty_without_migration(tmp_path):
    spec = importlib.util.spec_from_file_location('e1_role_legacy_fixtures', Path(__file__).parents[1] / 'roles' / 'conftest.py')
    fixtures = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixtures)
    env = fixtures.Env(tmp_path)
    accepted_deal(env)
    env.roles.run_patrol('fictional-owner')
    result = env.roles.run_brief('fictional-owner')['output_json']
    assert result['results']['today']['deals'][0]['source'] == 'quote'
    assert result['fulfilment'] == dict(in_transit=0, arriving_this_week=0, open_issues=0, pending_payments=0)
    with env.store.read() as db:
        assert db.execute('SELECT max(version) FROM schema_migrations').fetchone()[0] == 9
