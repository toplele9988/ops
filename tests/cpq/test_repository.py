"""Synthetic records below are test data, not catalog facts."""
from datetime import date, timedelta
import json
import sqlite3
import pytest
from console.cpq.workdb import open_work, ensure_schema
from console.security.media import make_asset_uid
from console.strobe.repository import StrobeRepository, SUPPLIER_LEGAL_NAME, open_catalog


@pytest.fixture
def isolated_repository(tmp_path, work_db):
    db = tmp_path / 'synthetic-catalog.sqlite3'
    conn = sqlite3.connect(db)
    try:
        conn.executescript('''
        CREATE TABLE equipment_specs (equipment_id TEXT PRIMARY KEY, supplier TEXT, model TEXT,
          fixture_type TEXT, wattage REAL, ip_rating TEXT, extraction_snapshot_json TEXT);
        CREATE TABLE cat_supplier (supplier_id TEXT PRIMARY KEY, legal_name TEXT);
        CREATE TABLE cat_supplier_model (supplier_model_id TEXT PRIMARY KEY, supplier_id TEXT, factory_model_code TEXT);
        CREATE TABLE cat_supplier_offer (offer_id TEXT PRIMARY KEY, supplier_id TEXT, supplier_model_id TEXT,
          quoted_on TEXT, valid_until TEXT, raw_cost_rmb_fen INTEGER, tax_basis TEXT,
          includes_flightcase INTEGER, fc_upgrade_rmb_fen INTEGER);
        CREATE TABLE cat_review (review_id INTEGER PRIMARY KEY, subject_kind TEXT, subject_id TEXT, decision TEXT);
        CREATE TABLE multimedia_assets (asset_id TEXT PRIMARY KEY, nas_rel_path TEXT,
          source_type TEXT, extraction_snapshot_json TEXT);
        ''')
        conn.executemany('INSERT INTO cat_supplier VALUES (?, ?)', [('S1', SUPPLIER_LEGAL_NAME), ('S2', 'Synthetic other supplier')])
        conn.executemany('INSERT INTO cat_supplier_model VALUES (?, ?, ?)', [('m1', 'S1', 'TEST-A'), ('m2', 'S2', 'TEST-A')])
        conn.execute('INSERT INTO equipment_specs VALUES (?, ?, ?, ?, ?, ?, ?)',
                     ('equipment:test-a', SUPPLIER_LEGAL_NAME, 'TEST-A', 'strobe', 100, 'IP65', '{}'))
        today = date.today().isoformat()
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        offers = [
            ('o1', 'S1', 'm1', today, None, 12000, None, None, None),
            ('o2', 'S1', 'm1', today, today, 8000, None, None, None),
            ('o3', 'S1', 'm1', today, None, 100, None, None, None),
            ('o4', 'S1', 'm1', today, yesterday, 50, None, None, None),
            ('o5', 'S2', 'm2', today, None, 1, None, None, None),
        ]
        conn.executemany('INSERT INTO cat_supplier_offer VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)', offers)
        conn.executemany('INSERT INTO cat_review VALUES (?, ?, ?, ?)', [
            (1, 'offer', 'o1', 'accept'), (2, 'offer', 'o2', 'accept'),
            (3, 'offer', 'o3', 'accept'), (4, 'offer', 'o3', 'reject'),
            (5, 'offer', 'o4', 'accept'), (6, 'offer', 'o5', 'accept'),
            (7, 'unrelated', 'o2', 'reject'),
        ])
        for index in range(2):
            conn.execute('INSERT INTO multimedia_assets VALUES (?, ?, ?, ?)', (
                f'asset:test-{index}', f'TEST-A_{index}.png', 'supplier_image',
                json.dumps({'equipment_id': 'equipment:test-a', 'model': 'ignored-name'}),
            ))
        conn.commit()
    finally:
        conn.close()
    config = tmp_path / 'cpq.yaml'
    config.write_text('public_sku:\n  pattern: "{brand}-TEST-{seq:03d}"\n', encoding='utf-8')
    return StrobeRepository(db, work_db, 'test-secret-not-for-prod', config)


def test_latest_review_expiration_supplier_and_fen_conversion(isolated_repository):
    repository = isolated_repository
    assert repository.cost_basis('equipment:test-a') == 80.0
    assert {offer['offer_id'] for offer in repository.offers_for_admin('equipment:test-a')} == {'o1', 'o2'}
    items = repository.list_strobes()
    assert len(items) == 1
    assert items[0]['model_id'] == 'equipment:test-a'
    assert items[0]['has_cost_basis'] is True
    assert items[0]['internal_sku'] == 'XL-TEST-001'
    assert repository.get_strobe('equipment:missing') is None
    assert repository.cost_basis('equipment:missing') is None


def test_catalog_connection_rejects_writes(isolated_repository):
    with open_catalog(isolated_repository.db_path) as conn:
        assert conn.execute('PRAGMA query_only').fetchone()[0] == 1
        with pytest.raises(sqlite3.OperationalError):
            conn.execute('DELETE FROM equipment_specs')


def test_text_asset_links_flags_and_public_sku(isolated_repository):
    repository = isolated_repository
    assert len(repository.images_for('equipment:test-a')) == 2
    conn = open_work(repository.work_db)
    try:
        conn.executemany('INSERT INTO cpq_asset_flag(asset_id, customer_safe) VALUES (?, 0)', [('asset:test-0',), ('asset:test-1',)])
        conn.execute('INSERT INTO cpq_public_sku VALUES (?, ?, ?)', ('equipment:test-a', 'TEST-PUBLIC', date.today().isoformat()))
        conn.commit()
    finally:
        conn.close()
    assert len(repository.images_for('equipment:test-a')) == 2
    item = repository.get_strobe('equipment:test-a')
    assert item['images'] == []
    assert item['internal_sku'] == 'TEST-PUBLIC'
    uid = make_asset_uid(repository.media_uid_secret, 'asset:test-0')
    assert repository.asset_by_uid(uid).asset_id == 'asset:test-0'


def test_missing_or_unreadable_work_flags_default_safe(isolated_repository, tmp_path):
    repository = isolated_repository
    repository.work_db = tmp_path / 'nonexistent.sqlite3'
    assert all(image['customer_safe'] for image in repository.images_for('equipment:test-a'))
    assert not repository.work_db.exists()
    repository.work_db.write_bytes(b'not sqlite')
    assert all(image['customer_safe'] for image in repository.images_for('equipment:test-a'))


def test_asset_index_refreshes_after_ttl(isolated_repository):
    repository = isolated_repository
    repository._refresh_assets()
    conn = sqlite3.connect(repository.db_path)
    try:
        conn.execute('INSERT INTO multimedia_assets VALUES (?, ?, ?, ?)', (
            'asset:test-new', 'new.png', 'supplier_image', json.dumps({'equipment_id': 'equipment:test-a'})))
        conn.commit()
    finally:
        conn.close()
    repository._asset_expires_at = 0
    uid = make_asset_uid(repository.media_uid_secret, 'asset:test-new')
    assert repository.asset_by_uid(uid).equipment_id == 'equipment:test-a'


def test_work_schema_is_idempotent_and_text_ids(work_db):
    conn = open_work(work_db)
    try:
        ensure_schema(conn)
        ensure_schema(conn)
        assert conn.execute('PRAGMA journal_mode').fetchone()[0] == 'wal'
        assert conn.execute('PRAGMA busy_timeout').fetchone()[0] == 5000
        for table, field in [('cpq_asset_flag', 'asset_id'), ('cpq_public_sku', 'model_id'),
                             ('cpq_packing_profile', 'model_id'), ('cpq_spec_override', 'model_id')]:
            columns = {row['name']: row['type'] for row in conn.execute(f'PRAGMA table_info({table})')}
            assert columns[field] == 'TEXT'
        assert conn.execute("SELECT name FROM sqlite_master WHERE name = 'cpq_freight_lane'").fetchone()
    finally:
        conn.close()
