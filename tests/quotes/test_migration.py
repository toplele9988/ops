import sqlite3
import unittest
from unittest.mock import patch
from console.archive import store
from console.archive.migration_v3 import migrate, upgrade
from fixtures import Fixture


def snapshot(db):
    return {r[0]: [tuple(row) for row in db.execute('SELECT * FROM '+r[0]+' ORDER BY rowid')]
            for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}


class MigrationTests(Fixture, unittest.TestCase):
    def test_v2_every_row_preserved_idempotent_and_old_store_still_writes(self):
        p,o=self.product()
        forwarder=self.aw('party_create',kind='forwarder',name='FICTIONAL-OLD-FORWARDER')
        with self.store.write() as db:
            store.insert(db,'freight_quotes',dict(forwarder_id=forwarder,origin='Fictional old port',
                destination_country='ZZ',destination_detail=None,mode='air',service='Fictional old service',
                unit='kg',price_fen=1234,currency='CNY',min_charge_fen=2000,transit_days_min=3,
                transit_days_max=5,quoted_on='2029-12-01',valid_until='2030-02-01',source_ref=None,
                notes=None,status='confirmed',created_at='2030-01-01',updated_at='2030-01-01'),'fictional-owner')
        did=self.deals.write('create',None,dict(title='Fictional deal'),'fictional-owner')['id']
        self.deals.write('item',did,dict(name='Fictional item',product_id=str(p),qty='3'),'fictional-owner')
        with self.store.read() as db:
            before=snapshot(db)
        self.quotes.store.initialize()
        with self.store.read() as db:
            after=snapshot(db)
            for table,rows in before.items():
                with self.subTest(table=table):
                    if table == 'freight_quotes':
                        self.assertEqual(rows,[tuple(row[:len(rows[0])]) for row in after[table][:len(rows)]])
                        self.assertEqual(dict(db.execute('SELECT channel FROM freight_quotes').fetchone()), {'channel':'forwarder'})
                    else:
                        self.assertEqual(rows,after[table][:len(rows)])
                    expected = 6 if table in ('audit','schema_migrations') else 0
                    self.assertEqual(len(after[table]),len(rows)+expected)
            self.assertFalse(db.execute('PRAGMA foreign_key_check').fetchall())
        self.quotes.store.initialize()
        with self.store.read() as db:
            self.assertEqual(after,snapshot(db))
        self.aw('product_update',p,name='Fictional changed')
        self.deals.write('update',did,dict(notes='Fictional note'),'fictional-owner')
        self.assertEqual(self.archive.read('products',row_id=p)['name'],'Fictional changed')

    def test_v1_upgrade_preserves_data_failure_rolls_back_and_unknown_version(self):
        db=sqlite3.connect(':memory:')
        self.addCleanup(db.close)
        store.configure(db)
        for sql in store.SCHEMA:
            db.execute(sql)
        db.execute('CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)')
        db.execute("INSERT INTO schema_migrations VALUES (1,'2030-01-01')")
        db.execute("INSERT INTO parties(id,kind,name,created_at,updated_at) VALUES (7,'supplier','Fictional','2030-01-01','2030-01-01')")
        db.execute("INSERT INTO products(id,supplier_id,name,created_at,updated_at) VALUES (11,7,'Fictional','2030-01-01','2030-01-01')")
        db.execute("INSERT INTO offers(id,product_id,price_fen,quoted_on,created_at,updated_at) VALUES (13,11,54321,'2030-01-01','2030-01-01','2030-01-01')")
        db.commit()
        before=snapshot(db)
        schema=[tuple(r) for r in db.execute('SELECT * FROM sqlite_master ORDER BY name')]
        def fail(db):
            upgrade(db)
            raise RuntimeError('Fictional failure')
        with patch('console.archive.migration_v3.upgrade',fail), self.assertRaises(RuntimeError):
            migrate(db)
        self.assertEqual(snapshot(db),before)
        self.assertEqual(schema,[tuple(r) for r in db.execute('SELECT * FROM sqlite_master ORDER BY name')])
        self.assertEqual(db.execute('PRAGMA foreign_keys').fetchone()[0],1)
        migrate(db)
        self.assertEqual([r[0] for r in db.execute('SELECT version FROM schema_migrations')],[1,2,3])
        for table,rows in before.items():
            if table not in ('schema_migrations','audit'):
                after=[tuple(r)[:len(rows[0])] for r in db.execute('SELECT * FROM '+table)] if rows else []
                self.assertEqual(rows,after)
        after=snapshot(db)
        migrate(db)
        self.assertEqual(snapshot(db),after)
        db.execute("INSERT INTO schema_migrations VALUES (99,'2030-01-01')")
        db.commit()
        before=snapshot(db)
        with self.assertRaises(ValueError):
            migrate(db)
        self.assertEqual(before,snapshot(db))

    def test_quote_fk_cascade_and_deal_item_removal_keeps_snapshot(self):
        did,rid,pid,oid,lid,cid=self.setup_round()
        self.qw('negotiation',rid,side='us',note='Fictional')
        with self.store.write() as db:
            db.execute('DELETE FROM deal_items WHERE deal_id=?',(did,))
        self.assertIsNone(self.detail(rid)['lines'][0]['deal_item_id'])
        with self.store.write() as db:
            db.execute('DELETE FROM deals WHERE id=?',(did,))
            for table in ('quote_rounds','quote_lines','quote_costs','quote_negotiations'):
                self.assertEqual(db.execute('SELECT count(*) FROM '+table).fetchone()[0],0)
            self.assertFalse(db.execute('PRAGMA foreign_key_check').fetchall())

    def test_get_v2_never_migrates_or_creates_missing_file(self):
        with self.assertRaises(ValueError):
            self.quotes.read('detail',row_id=1)
        self.assertFalse(self.path.exists())
        did=self.deals.write('create',None,{},'fictional-owner')['id']
        with self.store.read() as db:
            before=snapshot(db)
        self.assertEqual(self.quotes.read('list',row_id=did),{'items':[]})
        with self.assertRaises(ValueError):
            self.quotes.read('detail',row_id=1)
        with self.store.read() as db:
            self.assertEqual(before,snapshot(db))
