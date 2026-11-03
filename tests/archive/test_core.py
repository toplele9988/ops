from datetime import date, timedelta
import json
import os
from pathlib import Path
import sqlite3
import stat
import tempfile
import unittest

from console.archive import validity as val
from console.archive import validation as v
from console.archive.seed import short_name, is_model_label
from console.archive.service import Archive, parameters, source
from console.archive.store import Store, insert, now
from archive_fixtures import offer

TODAY = date(2030, 1, 10)


class PureTests(unittest.TestCase):
    def test_validity_boundary_table_and_no_expiry(self):
        for days, expected in ((-1, 'expired'), (0, 'expiring'), (1, 'expiring'), (6, 'expiring'), (7, 'valid'), (8, 'valid')):
            with self.subTest(days=days):
                self.assertEqual(val.validity((TODAY + timedelta(days=days)).isoformat(), TODAY), expected)
        self.assertEqual(val.validity(None, TODAY), 'no_expiry')
        for expiry in (None, '2030-01-09'):
            self.assertFalse(val.quote_state(offer(valid_until=expiry), TODAY)['usable_for_quote'])
        self.assertEqual(val.quote_state(offer(valid_until=None), TODAY)['validity_notice'], '没写有效期，请向对方确认')
        self.assertFalse(val.quote_state(offer(status='suggested'), TODAY)['usable_for_quote'])
        self.assertFalse(val.quote_state(offer(quoted_on='2030-01-11'), TODAY)['usable_for_quote'])
        self.assertFalse(val.quote_state(offer(), TODAY, current=False)['usable_for_quote'])

    def test_current_selection_confirmed_corrections_and_no_fallback(self):
        cases = [
            ([], None), ([offer(1), offer(2, status='suggested')], 1),
            ([offer(1), offer(2, status='rejected', corrects_offer_id=1)], 1),
            ([offer(1), offer(2, quoted_on='2029-01-01', corrects_offer_id=1)], 2),
            ([offer(1), offer(2, valid_until=None)], 2),
            ([offer(1), offer(2, valid_until='2030-01-02')], 2),
            ([offer(1), offer(2, quoted_on='2029-01-01')], 1),
            ([offer(1), offer(2, corrects_offer_id=1), offer(3, corrects_offer_id=2)], 3),
        ]
        for records, expected in cases:
            for fn in (val.current_offer, val.current_quote):
                with self.subTest(fn=fn.__name__, expected=expected):
                    selected = fn(records)
                    self.assertEqual(selected['id'] if selected else None, expected)

    def test_summary_groups_routes_and_ignores_superseded_prices(self):
        records = [offer(1), offer(2, corrects_offer_id=1, valid_until=None), offer(3, status='suggested')]
        self.assertEqual(val.counts(records, TODAY), dict(valid=0, expiring=0, expired=0, no_expiry=1, suggested=1))
        for row in records:
            del row['product_id']
            row.update(forwarder_id=1, origin='虚构港', destination_country='ZZ', mode='sea', service='DDP', unit='cbm')
        self.assertEqual(val.counts(records, TODAY)['no_expiry'], 1)
        separate = {**records[0], 'id': 4, 'currency': 'USD'}
        self.assertNotEqual(val.route_key(records[0]), val.route_key(separate))
        self.assertEqual(val.counts([*records, separate], TODAY)['valid'], 1)

    def test_money_exact_and_invalid_inputs(self):
        for text, fen in [('12.34', 1234), ('0.10', 10), ('0.2', 20), ('0.30', 30), ('0', 0), ('9999999999.99', 999999999999)]:
            self.assertEqual(v.money_to_fen(text), fen)
            self.assertEqual(v.money_to_fen(v.fen_to_money(fen)), fen)
        self.assertEqual(v.fen_to_money(v.money_to_fen('0.1') + v.money_to_fen('0.2')), '0.30')
        for bad in (0.1 + 0.2, '0.1+0.2', '0.30000000000000004', '1e2', 'NaN', 'Infinity', '-1', '1.234', '1,000', '9'*100):
            with self.subTest(value=bad), self.assertRaises(v.BadRequest):
                v.money_to_fen(bad)

    def test_short_name_rules(self):
        cases = [('广州虚构甲舞台灯光设备有限公司（舞台灯光）', '虚构甲'),
                 ('广州虚构乙灯光 包房灯', '虚构乙'), ('深圳市虚构丙光电科技有限责任公司', '虚构丙'),
                 ('虚构国际物流', '虚构国际物流'), ('广州', '广州')]
        for raw, expected in cases:
            with self.subTest(raw=raw):
                self.assertEqual(short_name(raw), expected)

    def test_model_label_table(self):
        for label in ('FA-100', 'F20 染色灯', '灯具 AB123', 'ＸＹ－２０', '400AB'):
            self.assertTrue(is_model_label(label), label)
        for label in ('6.光束角度：120°', '1 FA-10', '说明 FA-10 使用方法', '本产品 FA-10',
                      '外箱尺寸 50x40x30', '=DISPIMG(FA10)', '纯中文碎片', '功率100W', '光束灯 230W',
                      'FA10。说明书', 'A1'*40, '12345', '一、FA10', 'FA10\x01', 'FA10\n说明'):
            self.assertFalse(is_model_label(label), label)

    def test_numeric_ranges_and_dates(self):
        for field, valid, invalid in [
            ('carton_l_cm', ('2', '300'), ('1.99', '300.01')),
            ('gross_kg', ('0.05', '500'), ('0.049', '500.1')),
            ('units_per_carton', ('1', '10000'), ('0', '10001', '1.5'))]:
            for value in valid:
                self.assertIsNotNone(v.product_fields({field: value}))
            for value in invalid:
                with self.assertRaises(v.BadRequest):
                    v.product_fields({field: value})
        for day in ('2030-02-30', '20300101', '2030-1-1'):
            with self.assertRaises(v.BadRequest):
                v.iso_date(day)
        self.assertEqual(v.iso_date('2032-02-29'), '2032-02-29')


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'nested' / 'archive.sqlite3'
        self.store = Store(self.path)
        self.api = Archive(store=self.store, today=lambda: TODAY)

    def create(self, op, fields=None, row_id=None):
        return self.api.write(op, row_id, fields or {}, 'fictional-owner')['id']

    def test_migration_idempotence_wal_permissions_and_umask_restored(self):
        previous = os.umask(0o022)
        self.addCleanup(os.umask, previous)
        self.store.initialize()
        self.store.initialize()
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        actual = os.umask(0o022)
        self.assertEqual(actual, 0o022)
        with self.store.read() as db:
            self.assertEqual(db.execute('PRAGMA journal_mode').fetchone()[0], 'wal')
            self.assertEqual(db.execute('SELECT count(*) FROM schema_migrations').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT count(*) FROM audit').fetchone()[0], 1)
        self.path.chmod(0o644)
        with self.store.write() as db:
            self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
            for suffix in ('-wal', '-shm'):
                if Path(str(self.path) + suffix).exists():
                    self.assertEqual(stat.S_IMODE(Path(str(self.path) + suffix).stat().st_mode), 0o600)

    def test_missing_read_does_not_create_and_read_only(self):
        self.assertEqual(self.api.read('summary')['suppliers'], 0)
        self.assertFalse(self.path.parent.exists())
        self.store.initialize()
        with self.store.read() as db, self.assertRaises(sqlite3.OperationalError):
            db.execute("DELETE FROM schema_migrations")

    def test_write_rollback_and_audit_values_never_copied(self):
        pid = self.create('party_create', dict(kind='supplier', name='虚构供应商', notes='fake-secret-token'))
        self.create('party_update', dict(notes='fake-password', aliases='["fake-private-alias"]'), pid)
        with self.store.read() as db:
            audits = [dict(r) for r in db.execute("SELECT * FROM audit WHERE who='fictional-owner'")]
            self.assertEqual(len(audits), 2)
            self.assertNotIn('fake-secret-token', json.dumps(audits))
            self.assertNotIn('fake-password', json.dumps(audits))
            self.assertNotIn('fake-private-alias', json.dumps(audits))
        with self.assertRaises(RuntimeError), self.store.write() as db:
            insert(db, 'parties', dict(kind='supplier', name='回滚虚构', created_at=now(), updated_at=now()), 'fictional-owner')
            raise RuntimeError('fictional')
        with self.store.read() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM parties').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT count(*) FROM audit').fetchone()[0], 3)

    def test_product_workflow_merge_and_source_price_history(self):
        supplier = self.create('party_create', dict(kind='supplier', name='虚构供应商'))
        a = self.create('product_create', dict(supplier_id=str(supplier), name='FA-10', carton_l_cm='20'))
        b = self.create('product_create', dict(supplier_id=str(supplier), name='FB-20'))
        self.create('product_confirm', row_id=b)
        self.create('product_hide', row_id=b)
        self.create('product_restore', row_id=b)
        oid = self.create('offer_create', dict(price='12.34', unit='台', quoted_on='2030-01-01'), a)
        with self.store.write() as db:
            source(db, a, 'asset', 'fake-asset', '虚构文件', 'test', '来源甲')
            source(db, b, 'asset', 'fake-asset', '虚构文件', 'test', '来源乙')
        self.create('product_merge', {'into': str(b)}, a)
        target = self.api.read('products', row_id=b)
        self.assertEqual(target['offers'][0]['id'], oid)
        self.assertEqual(target['offers'][0]['price'], '12.34')
        self.assertIsNone(target['carton_l_cm'])  # Target fields are not guessed during merge.
        self.assertEqual(len(target['sources']), 1)
        self.assertIn('来源甲', target['sources'][0]['note'])
        self.assertIn('来源乙', target['sources'][0]['note'])
        self.assertEqual(self.api.read('products', row_id=a)['status'], 'hidden')
        with self.assertRaises(v.Conflict):
            self.create('product_restore', row_id=a)
        with self.assertRaises(v.Conflict):
            self.create('product_merge', {'into': str(b)}, b)

    def test_foreign_party_type_and_duplicate_model_validation(self):
        supplier = self.create('party_create', dict(kind='supplier', name='虚构供应商'))
        forwarder = self.create('party_create', dict(kind='forwarder', name='虚构货代'))
        self.create('product_create', dict(supplier_id=str(supplier), name='FA-10'))
        with self.assertRaises(v.Conflict):
            self.create('product_create', dict(supplier_id=str(supplier), name='fa_10'))
        with self.assertRaises(v.BadRequest):
            self.create('product_create', dict(supplier_id=str(forwarder), name='错误虚构'))
        with self.assertRaises(v.BadRequest):
            self.create('freight_create', dict(forwarder_id=str(supplier), price='1', origin='虚构港',
                destination_country='ZZ', mode='sea', service='DDP', unit='cbm', quoted_on='2030-01-01'))

    def test_freight_channel_defaults_can_be_set_and_updated(self):
        forwarder = self.create('party_create', dict(kind='forwarder', name='虚构渠道货代'))
        base = dict(forwarder_id=str(forwarder), origin='虚构港', destination_country='ZZ', mode='air',
                    service='虚构航空服务', unit='kg', price='2.30', currency='CNY',
                    quoted_on='2030-01-01', valid_until='2030-02-01')
        default_id = self.create('freight_create', base)
        platform_id = self.create('freight_create', dict(base, channel='platform_alibaba'))
        self.create('freight_update', {'channel':'other'}, row_id=default_id)
        rows = {row['id']:row for row in self.api.read('freight', parameters({}, 'freight'))['items']}
        self.assertEqual(rows[default_id]['channel'], 'other')
        self.assertEqual(rows[platform_id]['channel'], 'platform_alibaba')
        self.assertTrue(rows[default_id]['is_current'])
        self.assertTrue(rows[platform_id]['is_current'])
        with self.assertRaises(v.BadRequest):
            self.create('freight_update', {'channel':'wechat'}, row_id=default_id)

    def test_offer_correction_same_product_and_pending_state_transition(self):
        a = self.create('product_create', {'name': '虚构 FA-10'})
        b = self.create('product_create', {'name': '虚构 FB-20'})
        old = self.create('offer_create', dict(price='12.34', unit='台', quoted_on='2030-01-01', valid_until='2030-02-01'), a)
        with self.assertRaises(v.BadRequest):
            self.create('offer_create', dict(price='10', unit='台', quoted_on='2030-01-02', corrects_offer_id=str(old)), b)
        new = self.create('offer_create', dict(price='10', unit='台', quoted_on='2029-12-01', corrects_offer_id=str(old)), a)
        detail = self.api.read('products', row_id=a)
        self.assertEqual(detail['current_offer']['id'], new)
        self.assertFalse(detail['current_offer']['usable_for_quote'])
        self.assertFalse(detail['offers'][0]['usable_for_quote'])
        with self.assertRaises(v.Conflict):
            self.create('offer_status', {'action': 'reject'}, new)


if __name__ == '__main__':
    unittest.main()
