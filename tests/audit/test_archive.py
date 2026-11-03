"""Fictional archive records only; no network, models or production data."""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import sqlite3
from types import SimpleNamespace

import pytest

from console.archive.store import Store
from console.audit import archive


TODAY = '2030-01-10'
STAMP = TODAY + 'T12:00:00+00:00'


class ArchiveEnv:
    def __init__(self, path):
        self.path = path
        Store(path, target_version=4).initialize()
        self.ctx = SimpleNamespace(archive_path=path, today=TODAY, memo={},
            now=datetime(2030, 1, 10, 12, tzinfo=timezone.utc).timestamp())

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path)
        try:
            with db:
                yield db
        finally:
            db.close()

    def insert(self, table, **values):
        with self.db() as db:
            return db.execute('INSERT INTO ' + table + '(' + ','.join(values) + ') VALUES (' +
                              ','.join('?' for _ in values) + ')', tuple(values.values())).lastrowid

    def product(self, **values):
        return self.insert('products', **(dict(name='虚构产品，不得出现在报告', status='confirmed',
            carton_l_cm=40, carton_w_cm=30, carton_h_cm=20, gross_kg=8,
            created_at=STAMP, updated_at=STAMP) | values))

    def offer(self, product=None, **values):
        return self.insert('offers', **(dict(product_id=product or self.product(), price_fen=1234,
            status='confirmed', quoted_on='2030-01-01', valid_until='2030-02-01',
            source_ref='token=fictional-secret-offer', created_at=STAMP, updated_at=STAMP) | values))

    def party(self, name='广州虚构晨星灯光设备有限公司', kind='supplier', **values):
        return self.insert('parties', **(dict(name=name, kind=kind, status='active',
            notes='Cookie=fictional-secret-notes', created_at=STAMP, updated_at=STAMP) | values))

    def freight(self, forwarder=None, **values):
        return self.insert('freight_quotes', **(dict(forwarder_id=forwarder or self.party('虚构货代', 'forwarder'),
            origin='虚构港口甲', destination_country='虚构国家乙', mode='sea', service='虚构服务', unit='cbm',
            currency='CNY', price_fen=5678, quoted_on='2030-01-01', valid_until='2030-02-01',
            status='confirmed', created_at=STAMP, updated_at=STAMP) | values))

    def deal(self, **values):
        return self.insert('deals', **(dict(title='虚构案子私密标题', status='open', stage='inquiry',
            created_at=STAMP, updated_at=STAMP, stage_changed_at=STAMP) | values))

    def suggestion(self, status='pending'):
        rid = self.insert('role_runs', role='triage', subject_kind='conversation', subject_key='fictional-key',
            input_fingerprint='fictional-fingerprint', status='ok', source='rules', output_json='{}',
            output_text='虚构建议', evidence_json='[]', created_at=STAMP, created_by='fictional-owner')
        return self.insert('triage_suggestions', conversation_key='fictional-key-' + str(rid),
            action='identity', reason='虚构内容', confidence=.5, source='rules', status=status,
            run_id=rid, summary_json='{}', created_at=STAMP, updated_at=STAMP)


@pytest.fixture
def env(tmp_path):
    return ArchiveEnv(tmp_path / 'fictional-archive.sqlite3')


@pytest.mark.parametrize('check', [archive.offers_missing_expiry, archive.expired_prices,
    archive.missing_packing, archive.duplicate_suppliers, archive.deals_without_conversations,
    archive.pending_triage])
def test_empty_archive_has_no_findings(env, check):
    item = check(env.ctx)
    assert item['count'] == 0 and item['level'] == 'ok'
    assert item['samples'] == []


def test_missing_expiry_trigger_and_clear(env):
    product = env.product()
    env.offer(product, valid_until=None)
    assert archive.offers_missing_expiry(env.ctx)['count'] == 1
    env.offer(product, quoted_on='2030-01-02')
    assert archive.offers_missing_expiry(env.ctx)['level'] == 'ok'


def test_current_prices_follow_confirmed_leaf_rules(env):
    product = env.product()
    old = env.offer(product, quoted_on='2030-01-03')
    correction = env.offer(product, quoted_on='2030-01-02', corrects_offer_id=old, valid_until=None)
    # A confirmed correction is current even when its quote date is earlier.
    assert archive.offers_missing_expiry(env.ctx)['count'] == 1
    env.offer(product, quoted_on='2030-01-04', corrects_offer_id=correction, status='suggested')
    env.offer(product, quoted_on='2030-01-05', status='rejected')
    assert archive.offers_missing_expiry(env.ctx)['count'] == 1
    # Same-date tie follows highest id and does not fall back to an older price.
    env.offer(product, quoted_on='2030-01-02', valid_until='2030-01-05')
    assert archive.offers_missing_expiry(env.ctx)['count'] == 0
    assert archive.expired_prices(env.ctx)['count'] == 1


def test_hidden_products_and_suggested_prices_are_excluded(env):
    env.offer(env.product(status='hidden'), valid_until=None)
    env.offer(env.product(status='hidden'), valid_until='2030-01-02')
    env.offer(status='suggested', valid_until=None)
    assert archive.offers_missing_expiry(env.ctx)['count'] == 0
    assert archive.expired_prices(env.ctx)['count'] == 0


def test_expired_offer_and_freight_and_expiry_boundary(env):
    env.offer(valid_until='2030-01-09')
    env.freight(valid_until='2030-01-09')
    env.offer(valid_until=TODAY)
    env.freight(env.party('虚构另一货代', 'forwarder'), valid_until=TODAY)
    item = archive.expired_prices(env.ctx)
    assert item['count'] == 2 and item['level'] == 'high'
    assert item['counts'] == dict(offers=1, freight=1)


def test_freight_uses_exact_route_latest_confirmed(env):
    forwarder = env.party('虚构货代', 'forwarder')
    env.freight(forwarder, valid_until='2030-01-02')
    env.freight(forwarder, quoted_on='2030-01-03', destination_detail='')
    env.freight(forwarder, quoted_on='2030-01-04', status='suggested', valid_until='2030-01-05')
    assert archive.expired_prices(env.ctx)['count'] == 0
    env.freight(forwarder, currency='USD', valid_until='2030-01-02')
    assert archive.expired_prices(env.ctx)['count'] == 1


@pytest.mark.parametrize('field', ['carton_l_cm', 'carton_w_cm', 'carton_h_cm', 'gross_kg'])
def test_each_missing_packing_field_triggers(env, field):
    env.product(**{field: None})
    assert archive.missing_packing(env.ctx)['count'] == 1
    env.product(status='hidden', **{field: None})
    assert archive.missing_packing(env.ctx)['count'] == 1


def test_complete_packing_without_units_is_not_this_gap(env):
    env.product(units_per_carton=None)
    assert archive.missing_packing(env.ctx)['level'] == 'ok'


def test_duplicate_supplier_normalization_and_nonmatches(env):
    env.party()
    env.party('虚构晨星')
    env.party('  虚构 晨星  ')
    env.party('虚构晨昇')  # No arbitrary fuzzy edit-distance merge.
    env.party('虚构晨星', 'forwarder')
    env.party('深圳虚构晨星灯光设备有限公司', status='archived')
    item = archive.duplicate_suppliers(env.ctx)
    assert item['count'] == 2 and item['level'] == 'low'
    assert item['duplicate_groups'] == 1
    assert item['involved_records'] == 3


def test_duplicate_latin_names_case_width_punctuation(env):
    env.party('Fictional Nova')
    env.party('ＦＩＣＴＩＯＮＡＬ-ＮＯＶＡ')
    assert archive.duplicate_suppliers(env.ctx)['count'] == 1


def test_case_requires_a_participant_bound_conversation(env):
    deal = env.deal()
    assert archive.deals_without_conversations(env.ctx)['count'] == 1
    party = env.party('虚构客户', 'customer')
    env.insert('deal_participants', deal_id=deal, party_id=party, role='customer', created_at=STAMP)
    env.insert('deal_conversations', deal_id=deal, conversation_key='fictional-chat', created_at=STAMP)
    assert archive.deals_without_conversations(env.ctx)['count'] == 1
    with env.db() as db:
        db.execute('UPDATE deal_conversations SET party_id=?', (party,))
    assert archive.deals_without_conversations(env.ctx)['level'] == 'ok'


def test_closed_cases_are_included_and_global_identity_is_insufficient(env):
    env.deal(status='won')
    env.deal(status='lost')
    party = env.party()
    env.insert('identities', party_id=party, conversation_key='fictional-identity', created_at=STAMP,
               created_by='fictional-owner')
    assert archive.deals_without_conversations(env.ctx)['count'] == 2


def test_pending_triage_excludes_decided_rows(env):
    env.suggestion()
    env.suggestion('accepted')
    env.suggestion('dismissed')
    assert archive.pending_triage(env.ctx)['count'] == 1
    with env.db() as db:
        db.execute("UPDATE triage_suggestions SET status='dismissed'")
    assert archive.pending_triage(env.ctx)['level'] == 'ok'


def test_absent_optional_extract_is_skipped(env):
    assert archive.pending_prices(env.ctx) is None


@pytest.mark.parametrize('table', ['extract', 'extract_prices'])
def test_unknown_optional_extract_schema_is_not_guessed(env, table):
    with env.db() as db:
        db.execute('CREATE TABLE ' + table + ' (id INTEGER PRIMARY KEY)')
    with pytest.raises(ValueError, match='价格抽取表结构未核实'):
        archive.pending_prices(env.ctx)


def test_schema_failure_does_not_affect_unrelated_checks(env):
    with env.db() as db:
        db.execute('DROP TABLE freight_quotes')
    with pytest.raises(sqlite3.DatabaseError):
        archive.expired_prices(env.ctx)
    assert archive.missing_packing(env.ctx)['level'] == 'ok'


def test_bounded_samples_no_sensitive_metadata_and_readonly(env):
    for index in range(8):
        env.offer(valid_until=None)
        env.deal(title='虚构买家私密名字 ' + str(index))
    before = hashlib.sha256(env.path.read_bytes()).hexdigest()
    values = [check(env.ctx) for _, _, check in archive.CHECKS]
    assert hashlib.sha256(env.path.read_bytes()).hexdigest() == before
    assert all(len(value['samples']) <= 5 for value in values if value)
    output = json.dumps(values, ensure_ascii=False)
    for secret in ['fictional-secret', '虚构买家私密名字', '虚构产品，不得出现在报告', '1234', 'Cookie=']:
        assert secret not in output
