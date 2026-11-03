"""All records are invented; only local temporary SQLite and a fixed clock."""
import json
import sqlite3
from unittest.mock import patch

import pytest

from console.archive.service import Archive
from console.archive.store import Store, configure, migrate
from console.archive import migration_v7
from console.archive.validation import BadRequest, Conflict, Missing
from console.extract import storage, validation
from console.inbox.model import make_key
from console.roles.common import fingerprint
from console.roles.storage import save_run

NOW = 1893585600.0
KEY = make_key('conversation', 'wechat', 'fictional-account', 'fictional-supplier')


def snapshot(db):
    return {r[0]: [tuple(row) for row in db.execute('SELECT * FROM ' + r[0] + ' ORDER BY rowid')]
            for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}


@pytest.fixture
def setup_store(tmp_path):
    store = Store(tmp_path / 'fictional-archive.sqlite3', target_version=9)
    archive = Archive(store=store)
    supplier = archive.write('party_create', None, dict(kind='supplier', name='虚构星原供应商'), 'fictional-owner')['id']
    forwarder = archive.write('party_create', None, dict(kind='forwarder', name='虚构云海货代'), 'fictional-owner')['id']
    product = archive.write('product_create', None, dict(name='虚构追光灯', model='FICTION-300', supplier_id=str(supplier)), 'fictional-owner')['id']
    return store, archive, supplier, forwarder, product


def suggestion(setup_store, **changes):
    store, archive, supplier, forwarder, product = setup_store
    values = dict(kind='offer', conversation_key=KEY,
                  source_message_refs=[dict(kind='message', id='derived:fictional-message', derived=True, conversation_key=KEY, sent_at=NOW)],
                  party_id=supplier, party_hint='虚构星原', product_text='虚构追光灯', matched_product_id=product,
                  price_fen=128000, currency='CNY', unit='台', moq_units=2, lead_time_text='交期7天', tax_text='含税',
                  quoted_on='2030-01-02', excerpt='虚构追光灯含税1280元/台', confidence=0.4, source='rules')
    values.update(changes)
    values['input_fingerprint'] = fingerprint(values)
    with store.write() as db:
        return storage.save_suggestion(db, values, 'fictional-owner', NOW)[0]


def decide(store, row, raw, operation='accept'):
    return storage.decide(store, operation, row['id'], validation.fields(operation, raw), 'fictional-owner', NOW)


@pytest.mark.parametrize('version', [2, 3, 4, 6])
def test_migration_keeps_rows_audits_and_old_writers(tmp_path, version):
    path = tmp_path / 'fictional-upgrade.sqlite3'
    old = Store(path, target_version=version)
    archive = Archive(store=old)
    archive.write('party_create', None, dict(kind='supplier', name='虚构旧供应商'), 'fictional-owner')
    if version >= 4:
        with old.write() as db:
            run = save_run(db, 'triage', 'conversation', KEY, 'fictional-fp', dict(action='identity'), '虚构建议', [], 'fictional-owner', NOW)
            db.execute("INSERT INTO triage_suggestions(conversation_key,action,reason,confidence,source,run_id,summary_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                       (KEY, 'identity', '虚构理由', .5, 'rules', run['id'], '{}', '2030-01-02', '2030-01-02'))
            db.execute('CREATE VIEW fictional_runs AS SELECT id FROM role_runs')
            db.execute('CREATE TABLE fictional_role_events(run_id INTEGER REFERENCES role_runs(id))')
            db.execute('CREATE TRIGGER fictional_role_trigger AFTER INSERT ON role_runs BEGIN INSERT INTO fictional_role_events VALUES(new.id); END')
    with old.read() as db:
        before = snapshot(db)
    upgraded = Store(path, target_version=7)
    upgraded.initialize()
    with upgraded.read() as db:
        after = snapshot(db)
        for table, rows in before.items():
            assert after[table][:len(rows)] == rows
            if table not in ('audit', 'schema_migrations'):
                assert after[table] == rows
        assert not db.execute('PRAGMA foreign_key_check').fetchall()
        assert [r[0] for r in db.execute('SELECT version FROM schema_migrations ORDER BY version')] == [2, 3, 4, 6, 7]
        if version >= 4:
            assert db.execute('SELECT count(*) FROM fictional_runs').fetchone()[0] == 1
    for old_target in (2, 3, 4, 6, 7):
        Store(path, target_version=old_target).initialize()
    with upgraded.read() as db:
        assert snapshot(db) == after
    with upgraded.write() as db:
        for role in ('extract', 'audit'):
            save_run(db, role, 'day', '2030-01-02', role, dict(count=0), '虚构运行', [], 'fictional-owner', NOW)
        if version >= 4:
            assert db.execute('SELECT count(*) FROM fictional_role_events').fetchone()[0] == 2


def test_failed_v7_upgrade_rolls_back_schema_rows_and_foreign_keys(tmp_path):
    db = sqlite3.connect(':memory:')
    configure(db)
    migrate(db, target_version=6)
    before = snapshot(db)
    schema = list(db.execute('SELECT name,sql FROM sqlite_master ORDER BY name'))
    original = migration_v7.upgrade
    def failing(conn):
        original(conn)
        raise RuntimeError('fictional failure')
    with patch.object(migration_v7, 'upgrade', failing), pytest.raises(RuntimeError):
        migrate(db, target_version=7)
    assert snapshot(db) == before
    assert [tuple(r) for r in db.execute('SELECT name,sql FROM sqlite_master ORDER BY name')] == [tuple(r) for r in schema]
    assert db.execute('PRAGMA foreign_keys').fetchone()[0] == 1
    db.close()


def test_read_does_not_create_or_migrate(tmp_path):
    path = tmp_path / 'fictional-missing.sqlite3'
    store = Store(path, target_version=7)
    assert storage.list_suggestions(store)['items'] == []
    assert storage.summary(store) == dict(pending=dict(offer=0, freight=0), pending_quality=dict(trusted=0, low=0), total=0, last_scan_at=None)
    assert not path.exists()
    Store(path).initialize()
    with Store(path).read() as db:
        before = snapshot(db)
    storage.list_suggestions(store)
    storage.summary(store)
    with Store(path).read() as db:
        assert snapshot(db) == before


def test_accept_offer_archive_fields_audit_and_idempotence(setup_store):
    store, _, supplier, _, product = setup_store
    row = suggestion(setup_store)
    result = decide(store, row, dict(valid_until='2030-01-31', price='1290.25', lead_days_min='7', lead_days_max='9'))
    assert result['reused'] is False
    with store.read() as db:
        record = dict(db.execute('SELECT * FROM offers').fetchone())
        assert (record['product_id'], record['price_fen'], record['currency'], record['unit'], record['status']) == (product, 129025, 'CNY', '台', 'confirmed')
        assert (record['moq_units'], record['lead_days_min'], record['lead_days_max'], record['tax_basis']) == (2, 7, 9, '含税')
        assert (record['quoted_on'], record['valid_until']) == ('2030-01-02', '2030-01-31')
        assert KEY in record['source_ref'] and 'derived:fictional-message' in record['source_ref'] and '由抽取员建议，老板确认' in record['source_ref']
        accepted = dict(db.execute('SELECT * FROM extraction_suggestions').fetchone())
        assert accepted['status'] == 'accepted' and accepted['decided_by'] == 'fictional-owner'
        assert accepted['decided_at'].startswith('2030-01-02')
        before = snapshot(db)
        assert all(set(json.loads(a[0])) == {'fields'} for a in db.execute('SELECT detail FROM audit'))
    again = decide(store, row, {})
    assert again == dict(result, reused=True)
    with store.read() as db:
        assert snapshot(db) == before


@pytest.mark.parametrize('change,form,error', [
    ({}, {}, '有效期'),
    ({'suggested_valid_until': '2030-01-31'}, {}, '有效期'),
    ({'matched_product_id': None}, {'valid_until':'2030-01-31'}, '产品'),
    ({'price_fen': None}, {'valid_until':'2030-01-31'}, '金额'),
    ({'currency': None}, {'valid_until':'2030-01-31'}, '币种'),
    ({'unit': None}, {'valid_until':'2030-01-31'}, '单位'),
    ({}, {'valid_until':'2030-01-01'}, '有效期不能早于'),
])
def test_invalid_accept_never_changes_suggestion_or_archive(setup_store, change, form, error):
    store = setup_store[0]
    row = suggestion(setup_store, **change)
    with store.read() as db:
        before = snapshot(db)
    with pytest.raises(BadRequest, match=error):
        decide(store, row, form)
    with store.read() as db:
        assert snapshot(db) == before


def test_explicit_suggested_validity_confirmation(setup_store):
    store = setup_store[0]
    row = suggestion(setup_store, suggested_valid_until='2030-01-31')
    decide(store, row, dict(confirm_valid_until='1'))
    with store.read() as db:
        assert db.execute('SELECT valid_until FROM offers').fetchone()[0] == '2030-01-31'


def test_new_candidate_creation_is_atomic_and_stays_candidate(setup_store):
    store = setup_store[0]
    row = suggestion(setup_store, matched_product_id=None, product_text='虚构新产品')
    with store.read() as db:
        before = snapshot(db)
    with pytest.raises(BadRequest):
        decide(store, row, dict(create_product='1', valid_until='2030-01-31', lead_days_min='9', lead_days_max='7'))
    with store.read() as db:
        assert snapshot(db) == before
    result = decide(store, row, dict(create_product='1', product_name='虚构新追光灯', valid_until='2030-01-31'))
    with store.read() as db:
        product = dict(db.execute('SELECT * FROM products WHERE id=?', (result['product_id'],)).fetchone())
        assert product['status'] == 'candidate' and product['name'] == '虚构新追光灯'
        assert product['supplier_id'] == setup_store[2]


def test_accept_freight_exact_archive_contract(setup_store):
    store = setup_store[0]
    row = suggestion(setup_store, kind='freight', party_id=setup_store[3], matched_product_id=None,
                     product_text=None, price_fen=4500, unit='kg', mode='air', origin='虚构始发港',
                     destination_country='美国', destination_detail='虚构目的仓', min_charge_fen=50000,
                     moq_units=None, lead_time_text=None, tax_text=None)
    result = decide(store, row, dict(valid_until='2030-01-31', service='虚构空运专线', transit_days_min='3', transit_days_max='7'))
    with store.read() as db:
        freight = dict(db.execute('SELECT * FROM freight_quotes').fetchone())
        assert freight['status'] == 'confirmed'
        assert freight['channel'] == 'forwarder'
        assert (freight['forwarder_id'], freight['price_fen'], freight['min_charge_fen'], freight['unit'], freight['mode']) == (setup_store[3], 4500, 50000, 'kg', 'air')
        assert freight['destination_country'] == '美国' and freight['service'] == '虚构空运专线'
        assert freight['transit_days_min'] == 3 and freight['transit_days_max'] == 7
        assert db.execute('SELECT count(*) FROM offers').fetchone()[0] == 0
    assert decide(store, row, {}) == dict(result, reused=True)


@pytest.mark.parametrize('missing', ['party_id', 'mode', 'unit', 'price_fen', 'destination_country', 'origin'])
def test_freight_required_fields(setup_store, missing):
    values = dict(kind='freight', party_id=setup_store[3], unit='kg', mode='air', origin='虚构港', destination_country='美国')
    values[missing] = None
    row = suggestion(setup_store, **values)
    with pytest.raises(BadRequest):
        decide(setup_store[0], row, dict(valid_until='2030-01-31', service='虚构航线'))
    assert storage.list_suggestions(setup_store[0])['items'][0]['status'] == 'pending'


def test_dismiss_is_durable_and_accept_conflicts(setup_store):
    store = setup_store[0]
    row = suggestion(setup_store)
    decide(store, row, {}, 'dismiss')
    assert storage.summary(store)['total'] == 0
    with pytest.raises(Conflict):
        decide(store, row, dict(valid_until='2030-01-31'))
    with store.read() as db:
        before = snapshot(db)
    decide(store, row, {}, 'dismiss')
    suggestion(setup_store)
    with store.read() as db:
        assert snapshot(db) == before


def test_unknown_and_accepted_dismiss(setup_store):
    with pytest.raises(Missing):
        storage.decide(setup_store[0], 'dismiss', 999999, {}, 'fictional-owner', NOW)
    row = suggestion(setup_store)
    decide(setup_store[0], row, dict(valid_until='2030-01-31'))
    with pytest.raises(Conflict):
        decide(setup_store[0], row, {}, 'dismiss')


def test_pagination_filters_redaction_and_summary(setup_store):
    store = setup_store[0]
    for i in range(4):
        suggestion(setup_store, price_fen=i*100, excerpt='x'*190 + 'fictional@example.invalid')
    suggestion(setup_store, kind='freight')
    params = validation.query(dict(kind='offer', page='2', page_size='2'), 'suggestions')
    result = storage.list_suggestions(store, params)
    assert result['total'] == 4 and len(result['items']) == 2
    assert all(len(r['excerpt']) <= 200 and '@' not in r['excerpt'] for r in result['items'])
    assert storage.list_suggestions(store, dict(page=999999))['items'] == []
    assert storage.summary(store)['pending'] == dict(offer=4, freight=1)
    with store.write() as db:
        run = save_run(db, 'extract', 'day', '2030-01-02', 'fictional-scan', {}, '虚构扫描', [], 'fictional-owner', NOW)
    assert storage.summary(store)['last_scan_at'] == run['created_at']


@pytest.mark.parametrize('form', [{'status':'confirmed'}, {'created_by':'evil'}, {'price':'1e4'}, {'price':'NaN'}, {'price':'1.234'}, {'valid_until':'2030-02-30'}, {'create_product':'yes'}, {'product_id':'0'}, {'mode':'boat'}, {'product_id':'1','create_product':'1'}])
def test_accept_validation_rejects_unsafe_inputs(form):
    with pytest.raises(BadRequest):
        validation.fields('accept', form)


@pytest.mark.parametrize('form', [{'days':'0'}, {'limit':'101'}, {'conversation_key':'invalid'}, {'token':'x'}])
def test_scan_validation(form):
    with pytest.raises(BadRequest):
        validation.fields('scan', form)


def test_upgrade_from_v1_and_optional_v5_preserves_history(tmp_path):
    for old_version in (1, 5):
        path = tmp_path / ('fictional-old-' + str(old_version) + '.sqlite3')
        if old_version == 1:
            from console.archive.store import SCHEMA
            with sqlite3.connect(path) as db:
                configure(db)
                for sql in SCHEMA:
                    db.execute(sql)
                db.execute('CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)')
                db.execute("INSERT INTO schema_migrations VALUES(1,'2030-01-02')")
                db.execute("INSERT INTO parties(id,kind,name,created_at,updated_at) VALUES(100,'supplier','虚构历史单位','2030-01-02','2030-01-02')")
        else:
            previous = Store(path, target_version=4)
            previous.initialize()
            with previous.write() as db:
                db.execute("INSERT INTO schema_migrations VALUES(5,'2030-01-02')")
                db.execute('ALTER TABLE role_runs ADD COLUMN fictional_extra TEXT')
                save_run(db, 'triage', 'day', '2030-01-02', 'fictional-fp', {}, '虚构旧建议', [], 'fictional-owner', NOW)
                db.execute("UPDATE role_runs SET fictional_extra='虚构扩展'")
        upgraded = Store(path, target_version=7)
        upgraded.initialize()
        with upgraded.read() as db:
            versions = [r[0] for r in db.execute('SELECT version FROM schema_migrations ORDER BY version')]
            assert versions == ([1, 2, 3, 4, 6, 7] if old_version == 1 else [2, 3, 4, 5, 6, 7])
            if old_version == 1:
                assert db.execute('SELECT name FROM parties WHERE id=100').fetchone()[0] == '虚构历史单位'
            else:
                assert db.execute('SELECT fictional_extra FROM role_runs').fetchone()[0] == '虚构扩展'
            assert not db.execute('PRAGMA foreign_key_check').fetchall()


def test_vendor_candidate_keeps_correct_kind_and_unknown_supplier(setup_store):
    store, archive, *_ = setup_store
    vendor = archive.write('party_create', None, dict(kind='vendor', name='虚构配套厂'), 'fictional-owner')['id']
    row = suggestion(setup_store, party_id=vendor, matched_product_id=None)
    result = decide(store, row, dict(create_product='1', product_name='虚构纸箱', valid_until='2030-01-31'))
    with store.read() as db:
        assert db.execute('SELECT supplier_id FROM products WHERE id=?', (result['product_id'],)).fetchone()[0] is None
        assert db.execute('SELECT kind FROM parties WHERE id=?', (vendor,)).fetchone()[0] == 'vendor'


def test_wrong_forwarder_product_supplier_and_hidden_product_rejected(setup_store):
    store, archive, supplier, forwarder, product = setup_store
    row = suggestion(setup_store, kind='freight', mode='air', unit='kg', origin='虚构港', destination_country='美国')
    with pytest.raises(BadRequest, match='类型不符'):
        decide(store, row, dict(valid_until='2030-01-31', service='虚构线'))
    other = archive.write('party_create', None, dict(kind='supplier', name='虚构另一供应商'), 'fictional-owner')['id']
    row = suggestion(setup_store, party_id=other, price_fen=128100)
    with pytest.raises(Conflict, match='供应商'):
        decide(store, row, dict(valid_until='2030-01-31'))
    row = suggestion(setup_store)
    archive.write('product_hide', product, {}, 'fictional-owner')
    with pytest.raises(BadRequest, match='隐藏产品'):
        decide(store, row, dict(valid_until='2030-01-31'))


def test_late_decision_write_failure_rolls_back_archive_create(setup_store):
    store = setup_store[0]
    row = suggestion(setup_store, matched_product_id=None)
    with store.read() as db:
        before = snapshot(db)
    with patch.object(storage, 'update', side_effect=RuntimeError('fictional write failure')), pytest.raises(RuntimeError):
        decide(store, row, dict(create_product='1', product_name='虚构事务回滚产品', valid_until='2030-01-31'))
    with store.read() as db:
        assert snapshot(db) == before


def test_identity_changed_after_scan_requires_explicit_current_party_and_product(setup_store):
    from console.deals.service import Deals
    store, archive, supplier, _, product = setup_store
    deals = Deals(store=store, lookup=lambda key: None, clock=lambda: NOW)
    deals.write('identity', None, dict(conversation_key=KEY, party_id=str(supplier)), 'fictional-owner')
    row = suggestion(setup_store)
    other = archive.write('party_create', None, dict(kind='supplier', name='虚构新归属供应商'), 'fictional-owner')['id']
    other_product = archive.write('product_create', None, dict(name='虚构新归属产品', supplier_id=str(other)), 'fictional-owner')['id']
    deals.write('identity', None, dict(conversation_key=KEY, party_id=str(other), confirm='1'), 'fictional-owner')
    with store.read() as db:
        before = snapshot(db)
    with pytest.raises(Conflict, match='会话归属已变化'):
        decide(store, row, dict(valid_until='2030-01-31'))
    with pytest.raises(Conflict, match='会话归属已变化'):
        decide(store, row, dict(valid_until='2030-01-31', party_id=str(supplier)))
    with pytest.raises(Conflict, match='产品供应商'):
        decide(store, row, dict(valid_until='2030-01-31', party_id=str(other)))
    with store.read() as db:
        assert snapshot(db) == before
    result = decide(store, row, dict(valid_until='2030-01-31', party_id=str(other), product_id=str(other_product)))
    assert result['product_id'] == other_product
    # Reusing an accepted decision must return its original record even if the
    # current identity subsequently changes again; no additional business write.
    deals.write('identity', None, dict(conversation_key=KEY, party_id=str(supplier), confirm='1'), 'fictional-owner')
    assert decide(store, row, {}) == dict(result, reused=True)


def test_newly_classified_customer_cannot_confirm_a_supplier_hint(setup_store):
    from console.deals.service import Deals
    store, archive, supplier, *_ = setup_store
    row = suggestion(setup_store, party_id=None)
    customer = archive.write('party_create', None, dict(kind='customer', name='虚构客户'), 'fictional-owner')['id']
    Deals(store=store, lookup=lambda key: None, clock=lambda: NOW).write('identity', None,
        dict(conversation_key=KEY, party_id=str(customer)), 'fictional-owner')
    for selected in (supplier, customer):
        with pytest.raises(Conflict, match='会话归属已变化'):
            decide(store, row, dict(valid_until='2030-01-31', party_id=str(selected)))
    assert storage.list_suggestions(store)['items'][0]['status'] == 'pending'
