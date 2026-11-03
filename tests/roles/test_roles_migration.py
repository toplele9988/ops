import json
import sqlite3
from unittest.mock import patch

import pytest

from console.archive import store
from console.archive.migration_v4 import migrate, upgrade
from console.roles.inputs import BUSINESS_TABLES


def snapshot(db):
    return {r[0]: [tuple(row) for row in db.execute('SELECT * FROM ' + r[0] + ' ORDER BY rowid')]
            for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}


@pytest.mark.parametrize('old_version', [2, 3])
def test_additive_migration_preserves_every_row_and_old_writers(env, old_version):
    if old_version == 3:
        env.good_round()
    else:
        env.deal(key=env.conv())
    with env.store.read() as db:
        before = snapshot(db)
        schema = {r[0]: r[1] for r in db.execute("SELECT name,sql FROM sqlite_master WHERE type='table'")}
        prior_versions = {row[0] for row in db.execute('SELECT version FROM schema_migrations')}
    migration_delta = sum(version not in prior_versions for version in (3, 4))
    env.roles.store.initialize()
    with env.store.read() as db:
        after = snapshot(db)
        for table, rows in before.items():
            assert rows == after[table][:len(rows)]
            assert len(after[table]) == len(rows) + (migration_delta if table in ('audit', 'schema_migrations') else 0)
            assert db.execute('SELECT sql FROM sqlite_master WHERE name=?', (table,)).fetchone()[0] == schema[table]
        assert not db.execute('PRAGMA foreign_key_check').fetchall()
    env.roles.store.initialize()
    with env.store.read() as db:
        assert snapshot(db) == after
    env.deal()
    env.good_round()


def test_v1_failure_rolls_back_all_versions_and_foreign_key_setting():
    db = sqlite3.connect(':memory:')
    store.configure(db)
    for sql in store.SCHEMA:
        db.execute(sql)
    db.execute('CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)')
    db.execute("INSERT INTO schema_migrations VALUES(1,'2030-01-01')")
    db.execute("INSERT INTO parties(id,kind,name,created_at,updated_at) VALUES(17,'supplier','虚构单位','2030-01-01','2030-01-01')")
    db.commit()
    before = snapshot(db)
    def fail(conn):
        upgrade(conn)
        raise RuntimeError('fictional migration failure')
    with patch('console.archive.migration_v4.upgrade', fail), pytest.raises(RuntimeError):
        migrate(db)
    assert snapshot(db) == before
    assert db.execute('PRAGMA foreign_keys').fetchone()[0] == 1
    migrate(db)
    assert [r[0] for r in db.execute('SELECT version FROM schema_migrations')] == [1, 2, 3, 4]
    assert db.execute('SELECT name FROM parties WHERE id=17').fetchone()[0] == '虚构单位'
    db.execute("INSERT INTO schema_migrations VALUES(99,'2030-01-01')")
    db.commit()
    before = snapshot(db)
    with pytest.raises(ValueError):
        migrate(db)
    assert snapshot(db) == before
    db.close()


def test_get_does_not_create_or_upgrade_database(env):
    assert {item['key'] for item in env.roles.read('roles')['items']} == {
        'secretary', 'triage', 'patrol', 'brief', 'audit', 'extract', 'listing'}
    assert env.roles.read('runs')['items'] == []
    assert not env.path.exists()
    did = env.deal()
    with env.store.read() as db:
        before = snapshot(db)
    for resource in ('roles', 'runs', 'triage', 'alerts'):
        env.roles.read(resource)
    with pytest.raises(ValueError, match='还没有分析'):
        env.roles.read('secretary', row_id=did)
    with env.store.read() as db:
        assert snapshot(db) == before


def test_memory_store_v4_and_active_alert_unique_constraint():
    with sqlite3.connect(':memory:') as db:
        store.configure(db)
        memory = store.Store('/tmp/fictional-unused', connection=db, target_version=4)
        memory.initialize()
        assert {r[0] for r in db.execute('SELECT version FROM schema_migrations')} == {2, 3, 4}
        assert 'role_alert_active' in {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='index'")}


def test_suggested_name_uses_v4_snapshot_and_store_stays_idempotent(env):
    env.conv(name='虚构星河SUP-0011乐初供应商沟通')
    env.roles.scan_triage('fictional-owner')
    with env.store.read() as db:
        before = snapshot(db)
        summary = json.loads(db.execute('SELECT summary_json FROM triage_suggestions').fetchone()[0])
        assert summary['suggested_party_name'] == '虚构星河'
        schema = list(db.execute('SELECT name,sql FROM sqlite_master ORDER BY name'))
    for _ in range(2):
        store.Store(env.path, target_version=4).initialize()
    with env.store.read() as db:
        assert snapshot(db) == before
        assert list(db.execute('SELECT name,sql FROM sqlite_master ORDER BY name')) == schema
        assert [r[0] for r in db.execute('SELECT version FROM schema_migrations')] == [2, 3, 4]


def test_legacy_v4_suggestions_get_clean_name_without_read_migration(env):
    key = env.conv(name='虚构星河SUP~乐初供应商沟通')
    suggestion = env.roles.scan_triage('fictional-owner')['items'][0]
    # Reproduce an old v4 snapshot, which has no suggested name yet.
    with env.roles.store.write() as db:
        summary = json.loads(db.execute('SELECT summary_json FROM triage_suggestions').fetchone()[0])
        summary.pop('suggested_party_name')
        db.execute('UPDATE triage_suggestions SET summary_json=?', (json.dumps(summary),))
        before = snapshot(db)
    with patch('console.archive.store.migrate', side_effect=AssertionError('GET must not migrate')):
        listed = env.roles.read('triage')['items'][0]
    assert listed['suggested_party_name'] == '虚构星河'
    with env.store.read() as db:
        assert snapshot(db) == before
    env.roles.write('triage_accept', suggestion['id'], {}, 'fictional-owner')
    assert env.deals.read('identity_lookup', {'key': key})['party']['name'] == '虚构星河'


def test_c3_duplicate_metadata_and_acceptance_do_not_change_deployed_v4_schema(env):
    env.roles.store.initialize()
    with env.store.read() as db:
        schema = [tuple(r) for r in db.execute('SELECT name,sql FROM sqlite_master ORDER BY name')]
    env.conv(name='虚构重复买家', age=2)
    env.conv(name='虚构重复买家', age=1)
    result = env.roles.scan_triage('fictional-owner')
    main = next(s for s in result['items'] if s['action'] == 'create')
    env.roles.write('triage_accept', main['id'], {}, 'fictional-owner')
    with env.store.read() as db:
        assert [tuple(r) for r in db.execute('SELECT name,sql FROM sqlite_master ORDER BY name')] == schema
        assert [r[0] for r in db.execute('SELECT version FROM schema_migrations')] == [2, 3, 4]
        snapshots = [json.loads(r[0]) for r in db.execute('SELECT summary_json FROM triage_suggestions')]
        assert all(s['buyer_fingerprint'] for s in snapshots)
        assert sum(bool(s['duplicate_of']) for s in snapshots) == 1
        before = snapshot(db)
    for version in (2, 3, 4):
        store.Store(env.path, target_version=version).initialize()
    with env.store.read() as db:
        assert snapshot(db) == before
    env.good_round()


def test_legacy_v4_without_duplicate_metadata_is_read_only_until_rescan(env):
    env.conv(name='虚构重复买家', age=2)
    env.conv(name='虚构重复买家', age=1)
    env.roles.scan_triage('fictional-owner')
    with env.roles.store.write() as db:
        for row in db.execute('SELECT id,summary_json FROM triage_suggestions').fetchall():
            summary = json.loads(row['summary_json'])
            summary.pop('duplicate_of')
            summary.pop('buyer_fingerprint')
            db.execute("UPDATE triage_suggestions SET summary_json=?,action='create' WHERE id=?", (json.dumps(summary), row['id']))
        db.execute("UPDATE role_runs SET input_fingerprint='fictional-pre-c3-rule-version'")
        before = snapshot(db)
    with patch('console.archive.store.migrate', side_effect=AssertionError('GET must not migrate')):
        listed = env.roles.read('triage')['items']
    assert all(s['duplicate_of'] is None and s['buyer_fingerprint'] is None for s in listed)
    with env.store.read() as db:
        assert snapshot(db) == before
    refreshed = env.roles.scan_triage('fictional-owner')
    assert all(not r['reused'] for r in refreshed['runs'])
    assert sum(s['action'] == 'create' for s in refreshed['items']) == 1
