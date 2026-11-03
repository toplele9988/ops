"""Independent regression checks for WAL readers and schema upgrades."""
import hashlib
from pathlib import Path
import sqlite3

import pytest

from console.archive.store import configure, migrate
from console.audit.common import read_db
from console.roles.storage import save_run


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def test_reading_each_new_wal_commit_preserves_main_and_wal(tmp_path):
    path = tmp_path / 'fictional-source.sqlite3'
    writer = sqlite3.connect(path)
    try:
        writer.execute('PRAGMA journal_mode=WAL')
        writer.execute('PRAGMA wal_autocheckpoint=0')
        writer.execute('CREATE TABLE fictional_numbers(n INTEGER)')
        writer.commit()
        for value in range(4):
            writer.execute('INSERT INTO fictional_numbers VALUES (?)', (value,))
            writer.commit()
            # WAL-mode readers may coordinate read marks in existing SHM. The
            # main database and WAL contain the business data and must not change.
            before = {suffix: digest(str(path) + suffix) for suffix in ('', '-wal')}
            with read_db(path) as reader:
                assert reader.execute('SELECT count(*) FROM fictional_numbers').fetchone()[0] == value + 1
                with pytest.raises(sqlite3.OperationalError):
                    reader.execute('INSERT INTO fictional_numbers VALUES (99)')
            assert {suffix: digest(str(path) + suffix) for suffix in ('', '-wal')} == before
    finally:
        writer.close()


def test_rollback_journal_writer_cannot_expose_uncommitted_pages(tmp_path):
    path = tmp_path / 'fictional-rollback.sqlite3'
    writer = sqlite3.connect(path)
    try:
        writer.execute('CREATE TABLE fictional_numbers(n INTEGER)')
        writer.executemany('INSERT INTO fictional_numbers VALUES (?)', [(0,)] * 10000)
        writer.commit()
        writer.execute('PRAGMA cache_size=10')
        writer.execute('BEGIN IMMEDIATE')
        # Spill dirty pages under a rollback journal without committing. An
        # immutable reader would otherwise ignore locks and observe mixed data.
        writer.execute('UPDATE fictional_numbers SET n=99')
        before = digest(path)
        with pytest.raises(OSError):
            with read_db(path) as reader:
                reader.execute('SELECT MIN(n),MAX(n) FROM fictional_numbers').fetchone()
        assert digest(path) == before
    finally:
        writer.rollback()
        writer.close()


@pytest.mark.parametrize('legacy_mode', [0, 1])
def test_v6_preserves_views_triggers_extra_columns_and_pragma(legacy_mode):
    db = sqlite3.connect(':memory:')
    try:
        configure(db)
        migrate(db, target_version=4)
        original = save_run(db, 'triage', 'conversation', 'fictional-key', 'fictional-fingerprint',
                            {}, '虚构分拣', [], 'fictional-owner', 0)
        db.execute("ALTER TABLE role_runs ADD COLUMN fictional_extension TEXT DEFAULT 'fictional-default'")
        db.execute("ALTER TABLE role_runs ADD COLUMN fictional_generated TEXT GENERATED ALWAYS AS (role || ':fictional') VIRTUAL")
        db.execute('CREATE VIEW fictional_role_view AS SELECT id,role,fictional_extension,fictional_generated FROM role_runs')
        db.execute('CREATE TABLE fictional_insert_log(run_id INTEGER)')
        db.execute('CREATE TRIGGER fictional_role_trigger AFTER INSERT ON role_runs BEGIN INSERT INTO fictional_insert_log VALUES (new.id); END')
        db.execute('CREATE INDEX fictional_role_index ON role_runs(fictional_extension)')
        db.commit()
        db.execute('PRAGMA legacy_alter_table=' + str(legacy_mode))
        before = [tuple(row) for row in db.execute('SELECT * FROM fictional_role_view')]
        migrate(db, target_version=6)
        assert db.execute('PRAGMA legacy_alter_table').fetchone()[0] == legacy_mode
        assert db.execute('PRAGMA foreign_keys').fetchone()[0] == 1
        assert [tuple(row) for row in db.execute('SELECT * FROM fictional_role_view')] == before
        assert original['id'] == before[0][0]
        assert not db.execute('PRAGMA foreign_key_check').fetchall()
        assert 'fictional_role_index' in {row[1] for row in db.execute('PRAGMA index_list(role_runs)')}
        run = save_run(db, 'audit', 'day', '2030-01-10', 'fictional-fingerprint', {},
                       '虚构体检', [], 'fictional-owner', 1)
        assert tuple(db.execute('SELECT fictional_extension,fictional_generated FROM role_runs WHERE id=?',
                                (run['id'],)).fetchone()) == ('fictional-default', 'audit:fictional')
        assert db.execute('SELECT run_id FROM fictional_insert_log').fetchone()[0] == run['id']
    finally:
        db.close()
