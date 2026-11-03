import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from console.archive.seed import seed_archive, sync_packing_facts
from console.archive.service import Archive
from console.archive.store import Store
from archive_fixtures import catalog, report, report_product


class SeedTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.catalog, self.report = root / 'fake-catalog.sqlite3', root / 'fake-report.json'
        self.path = root / 'new' / 'fake-archive.sqlite3'
        catalog(self.catalog)
        report(self.report)
        self.store = Store(self.path)
        self.api = Archive(store=self.store)

    def seed(self, dry_run=False):
        return seed_archive(self.catalog, self.report, self.path, dry_run=dry_run)

    def products(self):
        with self.store.read() as db:
            return [dict(r) for r in db.execute('SELECT * FROM products ORDER BY id')]

    def test_idempotence_source_read_only_and_only_reference_sources(self):
        before = self.catalog.read_bytes(), self.report.read_bytes()
        first = self.seed()
        self.assertEqual(first['created']['suppliers'], 1)
        self.assertEqual(first['created']['products'], 4)
        self.assertEqual(first['created']['offers'], 1)
        self.assertEqual(first['conversation_matches'], 1)
        self.assertEqual(first['skipped']['unknown_supplier'], 1)
        self.assertEqual(first['skipped']['packing_fragment_label'], 1)
        self.assertEqual(first['skipped']['packing_conflict'], 1)
        with self.store.read() as db:
            initial_audits = db.execute('SELECT count(*) FROM audit').fetchone()[0]
            party = dict(db.execute('SELECT * FROM parties').fetchone())
            self.assertEqual(json.loads(party['conversation_ref']), {'channel': 'wechat', 'scope': 'group:fake-a'})
            self.assertEqual(json.loads(party['aliases_json']), [party['name']])
            self.assertIsNone(db.execute('SELECT valid_until FROM offers').fetchone()[0])
            self.assertEqual(db.execute('SELECT status FROM offers').fetchone()[0], 'suggested')
            dump = '\n'.join(db.iterdump())
            self.assertNotIn('虚构正文不能复制', dump)
            self.assertNotIn('/fictional/private', dump)
        second = self.seed()
        self.assertTrue(all(n == 0 for n in second['created'].values()), second)
        self.assertTrue(all(n == 0 for n in second['updated'].values()), second)
        with self.store.read() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM audit').fetchone()[0], initial_audits)
        self.assertEqual(before, (self.catalog.read_bytes(), self.report.read_bytes()))

    def test_dry_run_never_creates_or_changes_target(self):
        result = self.seed(True)
        self.assertEqual(result['created']['products'], 4)
        self.assertFalse(self.path.parent.exists())
        self.seed()
        before = self.path.read_bytes()
        result = self.seed(True)
        self.assertEqual(result['created']['products'], 0)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertFalse(Path(str(self.path) + '-wal').exists())

    def test_dry_run_includes_committed_active_wal_and_does_not_write_it(self):
        self.seed()
        with sqlite3.connect(self.path) as keeper:
            keeper.execute('PRAGMA journal_mode=WAL')
            keeper.execute('PRAGMA wal_autocheckpoint=0')
            keeper.execute("UPDATE parties SET notes='虚构 WAL 更新'")
            keeper.commit()
            main_before = self.path.read_bytes()
            wal = Path(str(self.path) + '-wal')
            wal_before = wal.read_bytes()
            result = self.seed(True)
            self.assertEqual(result['created']['suppliers'], 0)
            self.assertEqual(self.path.read_bytes(), main_before)
            self.assertEqual(wal.read_bytes(), wal_before)
        sync_before = self.path.read_bytes()
        sync_packing_facts(self.report, self.path, dry_run=True)
        self.assertEqual(self.path.read_bytes(), sync_before)

    def test_legacy_corrections_reference_new_ids_and_seed_never_reconfirms(self):
        with sqlite3.connect(self.catalog) as db:
            db.execute('INSERT INTO cat_supplier_offer(offer_id,supplier_id,supplier_model_id,quoted_on,valid_until,raw_cost_rmb_fen,corrects_offer_id) '
                       'VALUES (2,?,1,?,?,?,1)', ('fake-supplier-a', '2030-01-02', '2030-02-01', 2345))
        self.seed()
        with self.store.read() as db:
            offers = [dict(r) for r in db.execute('SELECT * FROM offers ORDER BY id')]
        self.assertEqual(offers[1]['corrects_offer_id'], offers[0]['id'])
        self.api.write('offer_status', offers[1]['id'], {'action': 'reject'}, 'fictional-owner')
        self.seed()
        with self.store.read() as db:
            self.assertEqual(db.execute('SELECT status FROM offers WHERE id=?', (offers[1]['id'],)).fetchone()[0], 'rejected')

    def test_protected_manual_and_confirmed_fields_survive_seed_and_sync(self):
        self.seed()
        a, b = self.products()[0], next(p for p in self.products() if p['model'] == 'FB-20')
        self.api.write('product_update', a['id'], {'carton_l_cm': '77', 'gross_kg': ''}, 'fictional-owner')
        self.api.write('product_confirm', b['id'], {}, 'fictional-owner')
        report(self.report, [report_product('spec-a', 'FA-10', '外箱尺寸 55x42x33 cm，毛重 13 kg，每箱数量 3 台'),
                             report_product('pack-b', 'FB-20', '外箱尺寸 65x45x35 cm，毛重 18 kg，每箱数量 2 台')])
        self.seed()
        sync_packing_facts(self.report, self.path)
        a = self.api.read('products', row_id=a['id'])
        b = self.api.read('products', row_id=b['id'])
        self.assertEqual(a['carton_l_cm'], 77)
        self.assertIsNone(a['gross_kg'])  # Explicit manual blank is also protected.
        self.assertEqual(a['carton_w_cm'], 42)
        self.assertEqual(a['field_state']['carton_l_cm']['state'], 'manual')
        self.assertEqual(b['carton_l_cm'], 60)
        self.assertEqual(b['gross_kg'], 15)
        self.assertEqual(b['field_state']['gross_kg']['state'], 'confirmed')

    def test_conflicts_never_select_candidate_and_later_resolution(self):
        self.seed()
        p = next(p for p in self.products() if p['model'] == 'FC-30')
        self.assertIsNone(p['carton_l_cm'])
        self.assertIsNone(p['gross_kg'])
        detail = self.api.read('products', row_id=p['id'])
        self.assertTrue(any(s['note'] == '包装资料有冲突' for s in detail['sources']))
        report(self.report, [report_product('pack-conflict', 'FC-30', '外箱尺寸 50x40x30 cm，毛重 12 kg')])
        sync_packing_facts(self.report, self.path)
        self.assertEqual(self.api.read('products', row_id=p['id'])['gross_kg'], 12)

    def test_new_conflict_withdraws_extracted_but_preserves_manual(self):
        self.seed()
        p = self.products()[0]
        self.api.write('product_update', p['id'], {'gross_kg': '20'}, 'fictional-owner')
        report(self.report, [report_product('spec-a', 'FA-10', '外箱尺寸 50x40x30 cm，外箱尺寸 80x40x30 cm，毛重 12 kg')])
        sync_packing_facts(self.report, self.path)
        detail = self.api.read('products', row_id=p['id'])
        self.assertIsNone(detail['carton_l_cm'])
        self.assertEqual(detail['gross_kg'], 20)
        self.assertFalse(detail['packing_ready'])

    def test_cross_entry_packaging_conflicts_do_not_pick_first(self):
        report(self.report, [report_product('pack-a', 'FX-10', '外箱尺寸 50x40x30 cm，毛重 12 kg'),
                             report_product('pack-b', 'fx_10', '外箱尺寸 80x40x30 cm，毛重 12 kg')])
        result = self.seed()
        p = next(p for p in self.products() if p['model'] == 'FX-10')
        self.assertIsNone(p['carton_l_cm'])
        self.assertEqual(result['skipped']['packing_conflict'], 2)

    def test_sync_only_extracted_fields_not_new_products_or_absent_fields(self):
        self.seed()
        p = self.products()[0]
        report(self.report, [report_product('spec-a', 'FA-10', '外箱尺寸 51x40x30 cm，毛重 13 kg，净重 8 kg'),
                             report_product('brand-new', 'FN-80', '外箱尺寸 50x40x30 cm，毛重 12 kg')])
        before = len(self.products())
        result = sync_packing_facts(self.report, self.path)
        detail = self.api.read('products', row_id=p['id'])
        self.assertEqual(detail['gross_kg'], 13)
        self.assertIsNone(detail['net_kg'])
        self.assertEqual(len(self.products()), before)
        self.assertEqual(result['skipped']['packing_unlinked'], 1)

    def test_merge_and_rename_do_not_recreate_seeded_product(self):
        self.seed()
        a, b = self.products()[:2]
        self.api.write('product_merge', a['id'], {'into': str(b['id'])}, 'fictional-owner')
        self.api.write('product_update', b['id'], {'name': '虚构新名称', 'model': 'FN-99'}, 'fictional-owner')
        self.seed()
        self.assertEqual(len(self.products()), 4)
        self.assertEqual(len(self.api.read('products', row_id=b['id'])['offers']), 1)
        self.assertEqual(self.api.read('products', row_id=a['id'])['status'], 'hidden')

    def test_ambiguous_or_customer_group_never_guessed_and_manual_link_preserved(self):
        with sqlite3.connect(self.catalog) as db:
            db.execute('INSERT INTO channel_role_registry VALUES (?,?,?,?,?,?)',
                       ('fake-reg-c', 'group', 'group:fake-c', '虚构星岚第二供应商群', 'CHANNEL_ROLE_SUPPLIER', 2))
        result = self.seed()
        self.assertEqual(result['conversation_matches'], 0)
        with self.store.read() as db:
            p = dict(db.execute('SELECT * FROM parties').fetchone())
        self.assertIsNone(p['conversation_ref'])
        manual = json.dumps({'channel': 'wechat', 'scope': 'group:fake-manual'})
        self.api.write('party_update', p['id'], {'conversation_ref': manual}, 'fictional-owner')
        self.seed()
        self.assertEqual(json.loads(self.api.read('parties', row_id=p['id'])['conversation_ref']), json.loads(manual))

    def test_source_target_aliases_and_missing_schema_fail_without_writes(self):
        before = self.catalog.read_bytes()
        for dry in (False, True):
            with self.assertRaises(ValueError):
                seed_archive(self.catalog, self.report, self.catalog, dry_run=dry)
        linked = self.catalog.parent / 'linked.sqlite3'
        os.link(self.catalog, linked)
        with self.assertRaises(ValueError):
            seed_archive(self.catalog, self.report, linked)
        self.assertEqual(self.catalog.read_bytes(), before)
        with sqlite3.connect(self.catalog) as db:
            db.execute('DROP TABLE cat_supplier_model')
        with self.assertRaises(sqlite3.OperationalError):
            self.seed()
        self.assertFalse(self.path.exists())

    def test_cli_aggregate_output_and_chinese_errors(self):
        command = [sys.executable, 'scripts/seed_archive.py', '--catalog', str(self.catalog),
                   '--packing-report', str(self.report), '--archive', str(self.path), '--dry-run']
        run = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(json.loads(run.stdout)['created']['offers'], 1)
        for sensitive in ('1234', '虚构星岚', 'group:fake-a', str(self.path)):
            self.assertNotIn(sensitive, run.stdout)
        self.catalog.unlink()
        failed = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(failed.returncode, 1)
        self.assertIn('档案处理失败', failed.stderr)
        self.assertNotIn(str(self.catalog), failed.stderr)
        self.assertFalse(self.path.exists())


if __name__ == '__main__':
    unittest.main()
