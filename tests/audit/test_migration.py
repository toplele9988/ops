import sqlite3
from unittest.mock import patch

import pytest

from console.archive.store import Store, configure, migrate
from console.archive.migration_v6 import upgrade
from console.roles.storage import save_run


@pytest.mark.parametrize('old_version', [2, 3, 4, 5])
def test_v6_preserves_existing_rows_references_indexes_and_old_writers(tmp_path, old_version):
    path = tmp_path / 'archive.db'
    store = Store(path, target_version=min(old_version, 4))
    store.initialize()
    if old_version >= 4:
        with store.write() as db:
            run = save_run(db, 'triage', 'conversation', 'fictional', 'fp', {}, '虚构说明', [], 'fictional', 0)
            db.execute("INSERT INTO triage_suggestions(conversation_key,action,reason,confidence,source,run_id,summary_json,created_at,updated_at) VALUES ('fictional','ignore','虚构',1,'rules',?,'{}','2030','2030')", (run['id'],))
            if old_version == 5:
                # A marker for a separately installed migration; no guessed v5
                # columns or extraction schema is introduced by this task.
                db.execute("INSERT INTO schema_migrations VALUES (5,'fictional')")
    with store.read() as db:
        before = [tuple(r) for r in db.execute('SELECT * FROM role_runs')] if old_version >= 4 else []
    Store(path, target_version=6).initialize()
    with store.read() as db:
        assert [tuple(r) for r in db.execute('SELECT * FROM role_runs')] == before
        assert not db.execute('PRAGMA foreign_key_check').fetchall()
        assert 'role_runs_latest' in {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='index'")}
        versions = [r[0] for r in db.execute('SELECT version FROM schema_migrations')]
        assert versions == ([2, 3, 4, 5, 6] if old_version == 5 else [2, 3, 4, 6])
    for target in (2, 3, 4, 6):
        Store(path, target_version=target).initialize()
    with Store(path, target_version=6).write() as db:
        save_run(db, 'audit', 'day', '2030-01-01', 'fp', {}, '虚构体检', [], 'fictional', 1)
    with store.read() as db:
        assert db.execute('SELECT count(*) FROM role_runs').fetchone()[0] == len(before) + 1
        assert db.execute('SELECT count(*) FROM schema_migrations WHERE version=6').fetchone()[0] == 1


def test_v6_rolls_back_and_preserves_foreign_key_mode():
    db = sqlite3.connect(':memory:')
    configure(db)
    migrate(db, target_version=4)
    schema = list(db.execute('SELECT name,sql FROM sqlite_master ORDER BY name'))
    def fail(conn):
        upgrade(conn)
        raise RuntimeError('fictional rollback')
    with patch('console.archive.migration_v6.upgrade', fail), pytest.raises(RuntimeError):
        migrate(db, target_version=6)
    assert list(db.execute('SELECT name,sql FROM sqlite_master ORDER BY name')) == schema
    assert db.execute('PRAGMA foreign_keys').fetchone()[0] == 1
    assert db.execute('SELECT max(version) FROM schema_migrations').fetchone()[0] == 4
    db.close()
