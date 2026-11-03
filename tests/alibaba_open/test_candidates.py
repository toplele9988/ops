"""Paid order hints never confirm a payment or guess a customer."""
from concurrent.futures import ThreadPoolExecutor

import pytest

from console.archive import validation as v
from console.archive.store import Store as ArchiveStore
from console.deals.service import Deals
from console.alibaba_open.candidates import Candidates, marker
from console.alibaba_open.store import Store

NOW = 2_000_000_000


@pytest.fixture
def candidate_context(tmp_path):
    archive = ArchiveStore(tmp_path / 'archive.sqlite3')
    archive.initialize()
    store = Store(tmp_path / 'orders.sqlite3', clock=lambda: NOW)
    candidates = Candidates(store, archive.path)
    return candidates, archive


def order(**changes):
    return dict(order_id='fictional-order-1', amount='123.45', currency='USD',
                buyer_id='fictional-buyer', payment_status='paid', paid_at_ms=None,
                modified_at_ms=NOW * 1000 - 1000) | changes


def test_paid_order_creates_unclaimed_open_deal_without_receipt(candidate_context):
    candidates, archive = candidate_context
    with archive.read() as db:
        versions = [r[0] for r in db.execute('SELECT version FROM schema_migrations')]
    result = candidates.upsert(order(), 'fictional-owner')
    with archive.read() as db:
        deal = dict(db.execute('SELECT * FROM deals WHERE id=?', (result['deal_id'],)).fetchone())
        assert deal['stage'] == 'awaiting_payment'
        assert deal['status'] == 'open'
        assert deal['customer_party_id'] is None
        assert deal['won_at'] is None
        assert 'deal_payments' not in {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert [r[0] for r in db.execute('SELECT version FROM schema_migrations')] == versions
    candidate = candidates.list()['items'][0]
    assert candidate['claim_state'] == 'unclaimed'
    assert candidate['amount_fen'] == 12345
    assert candidate['amount'] == '123.45'
    assert candidate['paid_at_ms'] is None


def test_duplicate_orders_and_concurrent_upserts_are_idempotent(candidate_context):
    candidates, archive = candidate_context
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: candidates.upsert(order(), 'fictional-owner'), range(8)))
    assert len({r['deal_id'] for r in results}) == 1
    assert sum(r['created'] for r in results) == 1
    assert len(candidates.list()['items']) == 1
    with archive.read() as db:
        assert db.execute('SELECT count(*) FROM deals').fetchone()[0] == 1
        events = db.execute('SELECT count(*) FROM deal_events').fetchone()[0]
    candidates.upsert(order(), 'fictional-owner')
    with archive.read() as db:
        assert db.execute('SELECT count(*) FROM deal_events').fetchone()[0] == events


def test_marker_recovers_lost_candidate_write_without_duplicate_deal(candidate_context):
    candidates, archive = candidate_context
    first = candidates.upsert(order(), 'fictional-owner')
    with candidates.store.write() as db:
        db.execute('DELETE FROM order_candidates')
    second = candidates.upsert(order(), 'fictional-owner')
    assert second['deal_id'] == first['deal_id']
    assert not second['created']
    with archive.read() as db:
        assert db.execute('SELECT count(*) FROM deals').fetchone()[0] == 1
        assert db.execute('SELECT count(*) FROM deal_events WHERE summary=?',
                          (marker('fictional-order-1'),)).fetchone()[0] == 1


def test_missing_archive_deal_requires_recovery_before_skipping_seen_order(candidate_context):
    candidates, archive = candidate_context
    first = candidates.upsert(order(), 'fictional-owner')
    assert not candidates.needs_recovery('fictional-order-1')
    with archive.write() as db:
        db.execute('DELETE FROM deals WHERE id=?', (first['deal_id'],))
    assert candidates.needs_recovery('fictional-order-1')
    candidates.upsert(order(), 'fictional-owner')
    assert not candidates.needs_recovery('fictional-order-1')


def replace_with_unrelated_deal(archive, deal_id):
    with archive.write() as db:
        db.execute('DELETE FROM deals WHERE id=?', (deal_id,))
    deals = Deals(store=archive, lookup=lambda key: None, clock=lambda: NOW)
    unrelated = deals.write('create', None, dict(title='虚构无关案子'), 'fictional-owner')['id']
    assert unrelated == deal_id
    return deals, deals.read('detail', row_id=unrelated)


@pytest.mark.parametrize('payment_status', ['paid', 'unpaid', 'unknown'])
def test_reused_archive_id_never_associates_or_updates_unrelated_deal(candidate_context, payment_status):
    candidates, archive = candidate_context
    first = candidates.upsert(order(), 'fictional-owner')
    deals, unrelated = replace_with_unrelated_deal(archive, first['deal_id'])
    assert candidates.needs_recovery('fictional-order-1')
    recovered = candidates.upsert(order(payment_status=payment_status, modified_at_ms=NOW * 1000), 'fictional-owner')
    assert recovered['deal_id'] != unrelated['id']
    assert deals.read('detail', row_id=unrelated['id']) == unrelated
    assert not candidates.needs_recovery('fictional-order-1')
    assert not candidates.list()['items'][0]['claimed']


def test_reused_claim_id_recovers_original_unclaimed_source(candidate_context):
    candidates, archive = candidate_context
    original = candidates.upsert(order(), 'fictional-owner')['deal_id']
    deals = Deals(store=archive, lookup=lambda key: None, clock=lambda: NOW)
    existing = deals.write('create', None, dict(title='虚构已认领案子'), 'fictional-owner')['id']
    candidates.claim('fictional-order-1', existing, 'fictional-owner')
    _, unrelated = replace_with_unrelated_deal(archive, existing)
    assert candidates.needs_recovery('fictional-order-1')
    recovered = candidates.upsert(order(), 'fictional-owner')
    assert recovered['deal_id'] == original
    assert not candidates.list()['items'][0]['claimed']
    assert deals.read('detail', row_id=existing) == unrelated


def test_older_detail_recovers_reused_id_without_overwriting_newer_order(candidate_context):
    candidates, archive = candidate_context
    first = candidates.upsert(order(amount='456.78', modified_at_ms=NOW * 1000), 'fictional-owner')
    deals, unrelated = replace_with_unrelated_deal(archive, first['deal_id'])
    recovered = candidates.upsert(order(), 'fictional-owner')
    assert recovered['deal_id'] != unrelated['id']
    candidate = candidates.list()['items'][0]
    assert candidate['amount_fen'] == 45678
    assert candidate['modified_at_ms'] == NOW * 1000
    assert deals.read('detail', row_id=unrelated['id']) == unrelated


def test_changed_order_does_not_overwrite_human_deal_or_receipt(candidate_context):
    candidates, archive = candidate_context
    result = candidates.upsert(order(), 'fictional-owner')
    deals = Deals(store=archive, lookup=lambda key: None, clock=lambda: NOW)
    deals.write('update', result['deal_id'], dict(value='77.00', currency='EUR', next_action='虚构人工待办'), 'fictional-owner')
    candidates.upsert(order(amount='234.56', modified_at_ms=NOW * 1000), 'fictional-owner')
    with archive.read() as db:
        row = db.execute('SELECT * FROM deals WHERE id=?', (result['deal_id'],)).fetchone()
        assert row['value_fen'] == 7700
        assert row['currency'] == 'EUR'
        assert row['next_action'] == '虚构人工待办'
        assert row['status'] == 'open'
    assert candidates.list()['items'][0]['amount_fen'] == 23456


def test_nonpaid_updates_candidate_but_never_rewrites_payments(candidate_context):
    candidates, archive = candidate_context
    candidates.upsert(order(), 'fictional-owner')
    candidates.upsert(order(amount=None, currency=None, payment_status='unpaid', modified_at_ms=NOW * 1000), 'fictional-owner')
    row = candidates.list()['items'][0]
    assert row['payment_status'] == 'unpaid'
    assert row['amount_fen'] == 12345
    assert candidates.upsert(order(order_id='fictional-unpaid', payment_status='unpaid'), 'fictional-owner')['skipped']
    with archive.read() as db:
        assert db.execute('SELECT count(*) FROM deals').fetchone()[0] == 1


def test_old_order_cannot_overwrite_newer_status(candidate_context):
    candidates, _ = candidate_context
    candidates.upsert(order(), 'fictional-owner')
    candidates.upsert(order(payment_status='unpaid', modified_at_ms=NOW * 1000), 'fictional-owner')
    candidates.upsert(order(), 'fictional-owner')
    assert candidates.list()['items'][0]['payment_status'] == 'unpaid'


def test_claim_is_manual_association_only_and_repeated_claim_idempotent(candidate_context):
    candidates, archive = candidate_context
    first = candidates.upsert(order(), 'fictional-owner')
    deals = Deals(store=archive, lookup=lambda key: None, clock=lambda: NOW)
    existing = deals.write('create', None, dict(title='虚构既有案子'), 'fictional-owner')['id']
    candidates.claim('fictional-order-1', existing, 'fictional-owner')
    with archive.read() as db:
        count = db.execute('SELECT count(*) FROM deal_events').fetchone()[0]
    candidates.claim('fictional-order-1', existing, 'fictional-owner')
    assert candidates.list()['items'][0]['claimed']
    assert candidates.list()['items'][0]['deal_id'] == existing
    with archive.read() as db:
        assert db.execute('SELECT count(*) FROM deal_events').fetchone()[0] == count
        assert all(r['status'] == 'open' and r['won_at'] is None for r in db.execute('SELECT * FROM deals'))
        assert db.execute('SELECT id FROM deals WHERE id=?', (first['deal_id'],)).fetchone()


def test_lost_candidate_state_recovers_latest_human_claim(candidate_context):
    candidates, archive = candidate_context
    candidates.upsert(order(), 'fictional-owner')
    deals = Deals(store=archive, lookup=lambda key: None, clock=lambda: NOW)
    existing = deals.write('create', None, dict(title='虚构既有案子'), 'fictional-owner')['id']
    candidates.claim('fictional-order-1', existing, 'fictional-owner')
    with candidates.store.write() as db:
        db.execute('DELETE FROM order_candidates')
    restored = candidates.upsert(order(), 'fictional-owner')
    assert restored['deal_id'] == existing
    assert candidates.list()['items'][0]['claimed']


def test_explicit_claim_repairs_missing_marker_before_idempotent_shortcut(candidate_context):
    candidates, archive = candidate_context
    candidates.upsert(order(), 'fictional-owner')
    deals = Deals(store=archive, lookup=lambda key: None, clock=lambda: NOW)
    existing = deals.write('create', None, dict(title='虚构既有案子'), 'fictional-owner')['id']
    candidates.claim('fictional-order-1', existing, 'fictional-owner')
    with archive.write() as db:
        db.execute('DELETE FROM deal_events WHERE summary=?', (marker('fictional-order-1', claimed=True),))
    assert candidates.needs_recovery('fictional-order-1')
    candidates.claim('fictional-order-1', existing, 'fictional-owner')
    assert not candidates.needs_recovery('fictional-order-1')
    before = deals.read('detail', row_id=existing)
    candidates.claim('fictional-order-1', existing, 'fictional-owner')
    assert deals.read('detail', row_id=existing) == before


@pytest.mark.parametrize('changes', [dict(amount=None), dict(amount='0'), dict(amount='-1'), dict(amount='1.234'), dict(amount='NaN'),
                                    dict(amount='1e3'), dict(amount=1.23), dict(currency='usd'), dict(currency='ZZZ'),
                                    dict(currency='JPY'), dict(buyer_id='../../bad'), dict(order_id="x';DROP TABLE deals;--"),
                                    dict(modified_at_ms=True), dict(paid_at_ms='unknown')])
def test_invalid_paid_order_rejected_without_creating_deal(candidate_context, changes):
    candidates, archive = candidate_context
    with pytest.raises(v.BadRequest):
        candidates.upsert(order(**changes), 'fictional-owner')
    assert candidates.list()['items'] == []
    with archive.read() as db:
        assert db.execute('SELECT count(*) FROM deals').fetchone()[0] == 0


def test_missing_archive_never_created_or_migrated(tmp_path):
    path = tmp_path / 'missing-archive.sqlite3'
    candidates = Candidates(Store(tmp_path / 'orders.sqlite3', clock=lambda: NOW), path)
    with pytest.raises(v.BadRequest, match='尚未准备好'):
        candidates.upsert(order(), 'fictional-owner')
    assert not path.exists()


def test_archive_and_candidate_rollback_together(candidate_context, monkeypatch):
    candidates, archive = candidate_context
    def fail(*args, **kwargs):
        raise RuntimeError('synthetic rollback')
    monkeypatch.setattr(candidates, '_audit', fail)
    with pytest.raises(RuntimeError, match='synthetic rollback'):
        candidates.upsert(order(), 'fictional-owner')
    assert candidates.list()['items'] == []
    with archive.read() as db:
        assert db.execute('SELECT count(*) FROM deals').fetchone()[0] == 0


def test_newest_claim_marker_recovers_rolled_back_but_valid_source_reference(candidate_context):
    candidates, archive = candidate_context
    first = candidates.upsert(order(), 'fictional-owner')
    deals = Deals(store=archive, lookup=lambda key: None, clock=lambda: NOW)
    target = deals.write('create', None, dict(title='虚构人工认领案子'), 'fictional-owner')['id']
    candidates.claim('fictional-order-1', target, 'fictional-owner')
    with candidates.store.write() as db:
        db.execute("UPDATE order_candidates SET deal_id=?,claim_state='unclaimed'", (first['deal_id'],))
    assert candidates.needs_recovery('fictional-order-1')
    restored = candidates.upsert(order(), 'fictional-owner')
    assert restored['deal_id'] == target
    assert candidates.list()['items'][0]['claimed']
    assert not candidates.needs_recovery('fictional-order-1')
