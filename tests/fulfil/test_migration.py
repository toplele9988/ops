"""v8 adds tables; deployed migration files and legacy data remain intact."""
import sqlite3
from unittest.mock import patch

import pytest

from console.archive import migration_v8, store


def snapshot(db):
    return {r[0]: [tuple(row) for row in db.execute('SELECT * FROM ' + r[0] + ' ORDER BY rowid')]
            for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}


@pytest.mark.parametrize('old_version', [2, 3, 4, 6, 7])
def test_v8_retains_every_old_row_and_all_old_entrypoints_accept_upgrade(tmp_path, old_version):
    path = tmp_path / 'fictional-migration.sqlite3'
    old = store.Store(path, target_version=old_version)
    with old.write() as db:
        party = store.insert(db, 'parties', dict(kind='supplier', name='虚构供应商',
                             created_at='2030-01-01', updated_at='2030-01-01'), 'fictional-owner')
        store.insert(db, 'products', dict(supplier_id=party, name='虚构产品',
                     created_at='2030-01-01', updated_at='2030-01-01'), 'fictional-owner')
        before = snapshot(db)
        schema = {r[0]: r[1] for r in db.execute("SELECT name,sql FROM sqlite_master WHERE sql IS NOT NULL")}
    upgraded = store.Store(path, target_version=8)
    upgraded.initialize()
    with upgraded.read() as db:
        after = snapshot(db)
        for table, rows in before.items():
            assert rows == after[table][:len(rows)]
            if table not in ('audit', 'schema_migrations'):
                assert rows == after[table]
        if old_version == 7:
            assert all(db.execute('SELECT sql FROM sqlite_master WHERE name=?', (name,)).fetchone()[0] == sql
                       for name, sql in schema.items())
        assert not db.execute('PRAGMA foreign_key_check').fetchall()
        assert set(after) - set(before) >= {'deal_payments', 'fulfilment_nodes', 'deal_issues', 'deal_issue_notes'}
    for version in (2, 3, 4, 6, 7, 8):
        store.Store(path, target_version=version).initialize()
    with upgraded.read() as db:
        assert snapshot(db) == after
    with old.write() as db:
        store.update(db, 'parties', party, {'name': '虚构旧入口写入'}, 'fictional-owner')


@pytest.mark.parametrize('old_version', [1, 7])
def test_v8_failure_rolls_back_entire_upgrade_and_fk_setting(old_version):
    db = sqlite3.connect(':memory:')
    store.configure(db)
    if old_version == 1:
        for sql in store.SCHEMA:
            db.execute(sql)
        db.execute('CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)')
        db.execute("INSERT INTO schema_migrations VALUES(1,'2030-01-01')")
        db.execute("INSERT INTO parties(id,kind,name,created_at,updated_at) VALUES(17,'supplier','虚构单位','2030-01-01','2030-01-01')")
        db.commit()
    else:
        store.migrate(db, target_version=7)
    before = snapshot(db)
    schema = [tuple(r) for r in db.execute('SELECT name,sql FROM sqlite_master ORDER BY name')]
    original = migration_v8.upgrade
    def fail(conn):
        original(conn)
        raise RuntimeError('fictional migration failure')
    with patch.object(migration_v8, 'upgrade', fail), pytest.raises(RuntimeError):
        migration_v8.migrate(db)
    assert snapshot(db) == before
    assert [tuple(r) for r in db.execute('SELECT name,sql FROM sqlite_master ORDER BY name')] == schema
    assert db.execute('PRAGMA foreign_keys').fetchone()[0] == 1
    migration_v8.migrate(db)
    assert max(r[0] for r in db.execute('SELECT version FROM schema_migrations')) == 8
    db.close()


def test_optional_v5_history_is_preserved_and_unknown_version_refused():
    db = sqlite3.connect(':memory:')
    store.configure(db)
    store.migrate(db, target_version=7)
    db.execute("INSERT INTO schema_migrations VALUES(5,'2030-01-01')")
    db.commit()
    migration_v8.migrate(db)
    assert [r[0] for r in db.execute('SELECT version FROM schema_migrations')] == [2, 3, 4, 5, 6, 7, 8]
    db.execute("INSERT INTO schema_migrations VALUES(99,'2030-01-01')")
    db.commit()
    before = snapshot(db)
    with pytest.raises(ValueError):
        migration_v8.migrate(db)
    assert snapshot(db) == before
    db.close()


def test_in_memory_store_v8_is_idempotent_and_get_does_not_create_file(tmp_path):
    path = tmp_path / 'fictional-no-file.sqlite3'
    missing = store.Store(path, target_version=8)
    with missing.read() as db:
        assert db.execute('SELECT count(*) FROM deal_payments').fetchone()[0] == 0
    assert not path.exists()
    db = sqlite3.connect(':memory:')
    store.configure(db)
    memory = store.Store(path, connection=db, target_version=8)
    memory.initialize()
    before = snapshot(db)
    memory.initialize()
    assert snapshot(db) == before
    db.close()
