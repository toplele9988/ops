from contextlib import closing
import sqlite3
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from console.archive import store
from console.archive.migration_v2 import upgrade


def fixture(db):
    store.configure(db)
    for statement in store.SCHEMA:
        db.execute(statement)
    db.execute('CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)')
    db.execute("INSERT INTO schema_migrations VALUES (1,'2030-01-01')")
    for pid, kind in ((7, 'supplier'), (29, 'forwarder')):
        db.execute("INSERT INTO parties(id,kind,name,short_name,aliases_json,notes,created_at,updated_at) VALUES (?,?,?,'虚构','[]','虚构备注','2030-01-01','2030-01-02')",
                   (pid, kind, '虚构单位' + str(pid)))
    db.execute("INSERT INTO products(id,supplier_id,name,model,created_at,updated_at) VALUES (31,7,'虚构灯','FX-1','2030-01-01','2030-01-02')")
    db.execute("INSERT INTO products(id,supplier_id,name,created_at,updated_at) VALUES (32,7,'虚构旧灯','2030-01-01','2030-01-02')")
    db.execute("INSERT INTO product_sources VALUES (51,31,'manual','fictional-ref','虚构来源','虚构说明')")
    db.execute("INSERT INTO offers(id,product_id,price_fen,currency,quoted_on,created_at,updated_at) VALUES (61,31,1234,'USD','2030-01-01','2030-01-01','2030-01-02')")
    db.execute("INSERT INTO offers(id,product_id,price_fen,corrects_offer_id,quoted_on,created_at,updated_at) VALUES (62,31,4321,61,'2030-01-02','2030-01-02','2030-01-02')")
    db.execute("INSERT INTO terms VALUES (71,7,'payment','虚构条款','confirmed',NULL,'2030-01-01')")
    db.execute("INSERT INTO terms VALUES (72,29,'lead_time','虚构时效','suggested',NULL,'2030-01-01')")
    db.execute("INSERT INTO freight_quotes(id,forwarder_id,origin,destination_country,mode,service,unit,price_fen,currency,quoted_on,created_at,updated_at) VALUES (81,29,'虚构港','ZZ','sea','DDP','cbm',2345,'USD','2030-01-01','2030-01-01','2030-01-02')")
    db.execute('INSERT INTO product_merges VALUES (91,32,31)')
    store.audit(db, 'fictional', 'seed', 'parties', 7, ('name',))
    db.execute('CREATE INDEX fictional_party_index ON parties(status,name)')
    db.commit()


def snapshot(db):
    return {r[0]: [dict(row) for row in db.execute('SELECT * FROM ' + r[0] + ' ORDER BY id')]
            for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name!='schema_migrations'")}


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.addCleanup(self.db.close)
        fixture(self.db)

    def test_v1_preserves_every_old_value_id_and_index_and_fk(self):
        before = snapshot(self.db)
        store.migrate(self.db)
        after = snapshot(self.db)
        for table, rows in before.items():
            with self.subTest(table=table):
                if table == 'audit':
                    self.assertEqual(rows, after[table][:-1])
                else:
                    self.assertEqual(len(rows), len(after[table]))
                    self.assertEqual(rows, [{k: r[k] for k in rows[i]} for i, r in enumerate(after[table])])
        self.assertEqual([r[0] for r in self.db.execute('SELECT version FROM schema_migrations')], [1, 2])
        self.assertEqual(self.db.execute('PRAGMA foreign_keys').fetchone()[0], 1)
        self.assertFalse(self.db.execute('PRAGMA foreign_key_check').fetchall())
        self.assertIsNotNone(self.db.execute("SELECT sql FROM sqlite_master WHERE name='fictional_party_index'").fetchone())
        for kind in ('customer', 'vendor'):
            self.db.execute("INSERT INTO parties(kind,name,country,created_at,updated_at) VALUES (?,'虚构同名','ZZ','2030-01-01','2030-01-01')", (kind,))
        self.db.commit()
        preserved = snapshot(self.db)
        store.migrate(self.db)
        self.assertEqual(preserved, snapshot(self.db))
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("DELETE FROM parties WHERE id=7")
        self.db.rollback()

    def test_failure_restores_schema_rows_indexes_and_enforcement(self):
        before = snapshot(self.db)
        schema = [tuple(r) for r in self.db.execute('SELECT * FROM sqlite_master ORDER BY name')]
        def fail(db):
            upgrade(db)
            raise RuntimeError('fictional migration failure')
        with patch('console.archive.migration_v2.upgrade', fail), self.assertRaises(RuntimeError):
            store.migrate(self.db)
        self.assertEqual(before, snapshot(self.db))
        self.assertEqual(schema, [tuple(r) for r in self.db.execute('SELECT * FROM sqlite_master ORDER BY name')])
        self.assertEqual(self.db.execute('PRAGMA foreign_keys').fetchone()[0], 1)
        store.migrate(self.db)

    def test_foreign_key_check_failure_rolls_back(self):
        self.db.execute('PRAGMA foreign_keys=OFF')
        self.db.execute('UPDATE products SET supplier_id=999 WHERE id=31')
        self.db.commit()
        self.db.execute('PRAGMA foreign_keys=ON')
        before = snapshot(self.db)
        with self.assertRaises(ValueError):
            store.migrate(self.db)
        self.assertEqual(before, snapshot(self.db))
        self.assertEqual(self.db.execute('PRAGMA foreign_keys').fetchone()[0], 1)

    def test_store_migrates_v1_file_and_fresh_baseline_is_v2(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'archive.sqlite3'
            with closing(sqlite3.connect(path)) as target:
                self.db.backup(target)
            target = store.Store(path)
            target.initialize()
            with target.read() as db:
                self.assertEqual(db.execute('SELECT count(*) FROM parties').fetchone()[0], 2)
                self.assertEqual(db.execute('SELECT max(version) FROM schema_migrations').fetchone()[0], 2)
            fresh = store.Store(Path(directory) / 'new.sqlite3')
            fresh.initialize()
            with fresh.read() as db:
                self.assertEqual([r[0] for r in db.execute('SELECT version FROM schema_migrations')], [2])

    def test_unknown_version_rejected_without_changes(self):
        self.db.execute("INSERT INTO schema_migrations VALUES (99,'2030-01-01')")
        self.db.commit()
        before = snapshot(self.db)
        with self.assertRaises(ValueError):
            store.migrate(self.db)
        self.assertEqual(before, snapshot(self.db))
