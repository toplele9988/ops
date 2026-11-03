from concurrent.futures import ThreadPoolExecutor
from datetime import date
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from console.archive import validation as v
from console.archive.service import Archive
from console.archive.store import Store
from console.deals.offers import Offers
from console.deals.service import Deals
from console.inbox.model import make_key
from console.quotes.service import Quotes


class OfferComparisonTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name)
        self.store = Store(self.path / 'archive.sqlite3', target_version=9)
        self.store.initialize()
        self.archive = Archive(store=self.store, today=lambda: date(2033, 5, 18))
        self.deals = Deals(store=self.store, lookup=lambda key: None, clock=lambda: 2_000_000_000)
        self.offers = Offers(store=self.store, path=self.path / 'offers.sqlite3',
                             today=lambda: date(2033, 5, 18), clock=lambda: 2_000_000_000)
        self.quotes = Quotes(store=self.store, today=lambda: date(2033, 5, 18), clock=lambda: 2_000_000_000,
                             fx_path=self.path / 'absent-fx.json')
        self.customer = self.party('customer', '虚构客户 CUS-101')
        self.supplier1 = self.party('supplier', '虚构工厂甲 SUP-201')
        self.supplier2 = self.party('supplier', '虚构工厂乙 SUP-202')
        self.deal = self.dw('create', customer_party_id=str(self.customer))
        for supplier in (self.supplier1, self.supplier2):
            self.dw('participant', self.deal, party_id=str(supplier), role='supplier')
        self.product1 = self.product(self.supplier1)
        self.product2 = self.product(self.supplier2)
        self.offer1 = self.offer(self.product1, '100.00')
        self.offer2 = self.offer(self.product2, '90.00')
        self.item = self.dw('item', self.deal, name='虚构灯需求', product_id=str(self.product1), qty='24',
                            note='虚构包装要求，待确认')
        self.guard = patch('socket.socket.connect', side_effect=AssertionError('测试必须离线'))
        self.guard.start()
        self.addCleanup(self.guard.stop)

    def party(self, kind, name):
        return self.archive.write('party_create', None, dict(kind=kind, name=name), 'fictional-owner')['id']

    def product(self, supplier, **extra):
        values = dict(supplier_id=str(supplier), name='虚构摇头灯', model='FX-100', wattage='100',
                      units_per_carton='2', carton_l_cm='50', carton_w_cm='40', carton_h_cm='30', gross_kg='10')
        values.update(extra)
        return self.archive.write('product_create', None, values, 'fictional-owner')['id']

    def offer(self, product, price, **extra):
        values = dict(price=price, currency='CNY', unit='台', quoted_on='2033-05-01', valid_until='2033-06-01',
                      moq_units='12', lead_days_min='7', lead_days_max='10', includes_flightcase='0',
                      source_ref='fictional-quote-source')
        values.update(extra)
        return self.archive.write('offer_create', product, values, 'fictional-owner')['id']

    def dw(self, op, row=None, **fields):
        return self.deals.write(op, row, fields, 'fictional-owner')['id']

    def choose(self, offer=None, **fields):
        return self.offers.choose(self.deal, self.item,
            dict(offer_id=str(offer or self.offer2), reason='虚构理由：交期可满足且包装已核对', confirm_specs='1', **fields),
            'fictional-owner')

    def test_two_supplier_prices_and_customer_codes_do_not_write_anything(self):
        before = self.store.path.read_bytes()
        result = self.offers.read(self.deal)
        self.assertEqual(result['customer']['codes'], ['CUS101'])
        options = result['items'][0]['options']
        self.assertEqual([row['price'] for row in options], ['100.00', '90.00'])
        self.assertEqual([row['supplier']['codes'] for row in options], [['SUP201'], ['SUP202']])
        self.assertEqual(options[1]['match'], 'model_candidate')
        self.assertTrue(options[1]['requires_spec_confirmation'])
        self.assertEqual(options[0]['packing']['units_per_carton'], 2)
        self.assertEqual(options[0]['source_ref'], 'fictional-quote-source')
        self.assertEqual(options[0]['moq_units'], 12)
        self.assertEqual((options[0]['lead_days_min'], options[0]['lead_days_max']), (7, 10))
        self.assertEqual(before, self.store.path.read_bytes())
        self.assertFalse(self.offers.path.exists())

    def test_different_spec_and_unmatched_models_are_visible_but_require_confirmation(self):
        self.archive.write('product_update', self.product2, dict(wattage='120'), 'fictional-owner')
        option = self.offers.read(self.deal)['items'][0]['options'][1]
        self.assertEqual(option['match'], 'different_specs')
        self.assertIn('wattage', option['spec_differences'])
        with self.assertRaisesRegex(v.BadRequest, '人工核对规格'):
            self.offers.choose(self.deal, self.item, dict(offer_id=str(self.offer2), reason='虚构替代方案'), 'fictional-owner')
        self.archive.write('product_update', self.product2, dict(model='FX-200'), 'fictional-owner')
        self.assertEqual(self.offers.read(self.deal)['items'][0]['options'][1]['match'], 'unconfirmed')
        selected = self.choose()
        self.assertEqual(selected['offer_id'], self.offer2)

    def test_manual_demand_does_not_guess_same_product_by_name(self):
        iid = self.dw('item', self.deal, name='FX-100', qty='12')
        item = self.offers.read(self.deal, iid)['items'][0]
        self.assertTrue(all(option['match'] == 'unconfirmed' for option in item['options']))
        with self.assertRaises(v.BadRequest):
            self.offers.choose(self.deal, iid, dict(offer_id=str(self.offer1), reason='虚构理由'), 'fictional-owner')

    def test_selection_is_idempotent_and_does_not_change_archive_or_migrations(self):
        before = self.store.path.read_bytes()
        first, second = self.choose(), self.choose()
        self.assertEqual(first['id'], second['id'])
        self.assertTrue(second['reused'])
        item = self.offers.read(self.deal)['items'][0]
        self.assertEqual(item['selection']['offer_id'], self.offer2)
        self.assertEqual(len(item['history']), 1)
        self.assertEqual(before, self.store.path.read_bytes())
        with sqlite3.connect(self.offers.path) as db:
            audit = db.execute('SELECT fields FROM offer_selection_audit').fetchone()[0]
            self.assertEqual(json.loads(audit), ['confirm_specs', 'offer_id', 'reason'])
            self.assertNotIn('虚构理由', audit)

    def test_quote_choice_reuses_existing_line_and_preserves_other_round_snapshots(self):
        round1 = self.quotes.write('create', self.deal, {}, 'fictional-owner')['id']
        round2 = self.quotes.write('create', self.deal, {}, 'fictional-owner')['id']
        before = self.quotes.read('detail', row_id=round1)
        selected = self.choose(round_id=str(round2))
        result = self.quotes.read('detail', row_id=round2)
        self.assertEqual(len(result['lines']), 1)
        self.assertEqual(result['lines'][0]['offer_id'], self.offer2)
        self.assertEqual(result['lines'][0]['product_id'], self.product2)
        self.assertEqual(result['lines'][0]['qty'], 24)
        self.assertEqual(result['lines'][0]['cost_unit'], '90.00')
        self.assertEqual(result['lines'][0]['id'], selected['quote_line_id'])
        self.assertEqual(before, self.quotes.read('detail', row_id=round1))
        self.assertTrue(self.choose(round_id=str(round2))['reused'])
        self.assertEqual(len(self.quotes.read('detail', row_id=round2)['negotiations']), 1)

    def test_sent_round_and_wrong_deal_round_are_rejected_without_selection(self):
        other = self.dw('create')
        round_id = self.quotes.write('create', other, {}, 'fictional-owner')['id']
        with self.assertRaises(v.Missing):
            self.choose(round_id=str(round_id))
        round_id = self.quotes.write('create', self.deal, {}, 'fictional-owner')['id']
        with self.store.write() as db:
            db.execute("UPDATE quote_rounds SET status='sent' WHERE id=?", (round_id,))
        with self.assertRaisesRegex(v.Conflict, '已发出'):
            self.choose(round_id=str(round_id))
        self.assertIsNone(self.offers.read(self.deal)['items'][0]['selection'])

    def test_quote_service_failure_rolls_back_choice_and_line(self):
        rid = self.quotes.write('create', self.deal, {}, 'fictional-owner')['id']
        before = self.quotes.read('detail', row_id=rid)
        with patch('console.deals.offers.insert', side_effect=RuntimeError('虚构事务失败')):
            with self.assertRaises(RuntimeError):
                self.choose(round_id=str(rid))
        self.assertEqual(before, self.quotes.read('detail', row_id=rid))
        self.assertFalse(self.offers.path.exists() and self.offers.path.stat().st_size)

    def test_repeat_choice_rechecks_sent_state_and_does_not_replay_business_writes(self):
        rid = self.quotes.write('create', self.deal, {}, 'fictional-owner')['id']
        self.choose(round_id=str(rid))
        with self.store.write() as db:
            db.execute("UPDATE quote_rounds SET status='sent' WHERE id=?", (rid,))
        before = self.quotes.read('detail', row_id=rid)
        self.assertTrue(self.choose(round_id=str(rid))['reused'])
        with self.assertRaisesRegex(v.Conflict, '已发出'):
            self.offers.choose(self.deal, self.item, dict(offer_id=str(self.offer2), reason='虚构新的选择理由',
                confirm_specs='1', round_id=str(rid)), 'fictional-owner')
        self.assertEqual(before, self.quotes.read('detail', row_id=rid))

    def test_repeat_choice_repairs_changed_draft_reference_without_duplicate_line(self):
        rid = self.quotes.write('create', self.deal, {}, 'fictional-owner')['id']
        selected = self.choose(round_id=str(rid))
        self.quotes.write('line_update', rid, dict(product_id=str(self.product1), offer_id=str(self.offer1)),
                          'fictional-owner', selected['quote_line_id'])
        self.assertFalse(self.choose(round_id=str(rid))['reused'])
        self.assertEqual(len(self.quotes.read('detail', row_id=rid)['lines']), 1)
        self.assertEqual(self.quotes.read('detail', row_id=rid)['lines'][0]['offer_id'], self.offer2)

    def test_migration_version_and_schema_stay_fixed_when_choice_updates_draft(self):
        rid = self.quotes.write('create', self.deal, {}, 'fictional-owner')['id']
        def schema():
            with self.store.read() as db:
                return (list(db.execute('SELECT version,applied_at FROM schema_migrations ORDER BY version')),
                        list(db.execute('SELECT type,name,sql FROM sqlite_master ORDER BY name')))
        before = schema()
        self.choose(round_id=str(rid))
        self.assertEqual(before, schema())

    def test_archive_commit_survives_but_sidecar_commit_is_lost_retry_only_restores_selection(self):
        rid = self.quotes.write('create', self.deal, {}, 'fictional-owner')['id']
        first = self.choose(round_id=str(rid))
        before = self.quotes.read('detail', row_id=rid)
        with self.store.read() as db:
            archive_audits = db.execute('SELECT count(*) FROM audit').fetchone()[0]
            archive_events = db.execute('SELECT count(*) FROM deal_events').fetchone()[0]
        # Model the two possible durable outcomes of an interrupted WAL/ATTACH
        # commit without killing a process or touching any non-temporary DB.
        with sqlite3.connect(self.offers.path) as db:
            db.execute('DELETE FROM offer_selections')
            db.execute('DELETE FROM offer_selection_audit')
        recovered = self.choose(round_id=str(rid))
        self.assertTrue(recovered['reused'])
        self.assertEqual(recovered['quote_line_id'], first['quote_line_id'])
        self.assertEqual(before, self.quotes.read('detail', row_id=rid))
        self.assertEqual(len(self.offers.read(self.deal)['items'][0]['history']), 1)
        with self.store.read() as db:
            self.assertEqual(archive_audits, db.execute('SELECT count(*) FROM audit').fetchone()[0])
            self.assertEqual(archive_events, db.execute('SELECT count(*) FROM deal_events').fetchone()[0])
            self.assertEqual(db.execute('SELECT count(*) FROM quote_negotiations').fetchone()[0], 1)

    def test_sidecar_commit_survives_but_archive_commit_is_lost_retry_repairs_existing_choice(self):
        rid = self.quotes.write('create', self.deal, {}, 'fictional-owner')['id']
        backup = self.path / 'fictional-before-choice.sqlite3'
        with sqlite3.connect(self.store.path) as source, sqlite3.connect(backup) as target:
            source.backup(target)
        first = self.choose(round_id=str(rid))
        with sqlite3.connect(backup) as source, sqlite3.connect(self.store.path) as target:
            source.backup(target)
        recovered = self.choose(round_id=str(rid))
        self.assertTrue(recovered['restored'])
        self.assertEqual(recovered['id'], first['id'])
        self.assertEqual(len(self.offers.read(self.deal)['items'][0]['history']), 1)
        round_row = self.quotes.read('detail', row_id=rid)
        self.assertEqual(len(round_row['lines']), 1)
        self.assertEqual(round_row['lines'][0]['offer_id'], self.offer2)
        self.assertEqual(len(round_row['negotiations']), 1)

    def test_proven_choice_can_be_restored_after_round_sent_and_offer_superseded(self):
        rid = self.quotes.write('create', self.deal, {}, 'fictional-owner')['id']
        self.choose(round_id=str(rid))
        with self.store.write() as db:
            db.execute("UPDATE quote_rounds SET status='sent' WHERE id=?", (rid,))
        self.offer(self.product2, '95.00', quoted_on='2033-05-17')
        before = self.quotes.read('detail', row_id=rid)
        with sqlite3.connect(self.offers.path) as db:
            db.execute('DELETE FROM offer_selections')
        self.assertTrue(self.choose(round_id=str(rid))['restored'])
        self.assertEqual(before, self.quotes.read('detail', row_id=rid))

    def test_expired_current_price_never_falls_back_to_old_cheaper_price(self):
        self.offer(self.product2, '80.00', quoted_on='2033-05-17', valid_until='2033-05-17')
        option = self.offers.read(self.deal)['items'][0]['options'][1]
        self.assertEqual(option['price'], '80.00')
        self.assertFalse(option['usable_for_quote'])
        with self.assertRaises(v.Conflict):
            self.choose(option['offer_id'])
        with self.assertRaises(v.Conflict):
            self.choose(self.offer2)

    def test_foreign_currency_can_be_compared_but_not_silently_added_as_cny(self):
        foreign = self.offer(self.product2, '20.00', currency='USD', quoted_on='2033-05-02')
        self.assertEqual(self.offers.read(self.deal)['items'][0]['options'][1]['currency'], 'USD')
        selected = self.choose(foreign)
        self.assertEqual(selected['offer_id'], foreign)
        rid = self.quotes.write('create', self.deal, {}, 'fictional-owner')['id']
        with self.assertRaisesRegex(v.BadRequest, '人民币报价'):
            self.choose(foreign, round_id=str(rid))

    def test_missing_price_unit_cannot_write_quote_cost_or_selection(self):
        unitless = self.offer(self.product2, '90.00', quoted_on='2033-05-02')
        with self.store.write() as db:
            db.execute('UPDATE offers SET unit=NULL WHERE id=?', (unitless,))
        rid = self.quotes.write('create', self.deal, {}, 'fictional-owner')['id']
        before = self.quotes.read('detail', row_id=rid)
        archive_before = self.store.path.read_bytes()
        with self.assertRaisesRegex(v.BadRequest, '还没写单价单位'):
            self.choose(unitless, round_id=str(rid))
        self.assertEqual(before, self.quotes.read('detail', row_id=rid))
        self.assertEqual(archive_before, self.store.path.read_bytes())
        self.assertIsNone(self.offers.read(self.deal)['items'][0]['selection'])
        self.assertFalse(self.offers.path.exists() and self.offers.path.stat().st_size)
        self.assertEqual(self.choose(unitless)['offer_id'], unitless)
        self.assertIsNone(self.offers.read(self.deal)['items'][0]['options'][1]['unit'])

    def test_codes_from_aliases_identity_and_original_names_keep_links_and_conflicts(self):
        key1 = make_key('conversation', 'wechat', 'fictional-account', 'fictional-one')
        key2 = make_key('conversation', 'whatsapp', 'fictional-account', 'fictional-two')
        for key in (key1, key2):
            self.dw('conversation', self.deal, conversation_key=key, party_id=str(self.supplier1))
        self.archive.write('party_update', self.supplier2, dict(aliases='["虚构别名 SUP-201"]'), 'fictional-owner')
        class Inbox:
            def read(self, now):
                return dict(conversations={key1: dict(buyer_name='虚构群 SUP-２０１'), key2: dict(buyer_name='虚构群 SUP-299')})
        self.offers.inbox = Inbox()
        parties = {row['id']: row for row in self.offers.read(self.deal)['participants']}
        first = parties[self.supplier1]
        self.assertEqual(first['codes'], ['SUP201', 'SUP299'])
        self.assertEqual(len(first['conversations']), 2)
        self.assertEqual(len(first['warnings']), 2)
        self.assertTrue(any('其他单位' in warning for warning in parties[self.supplier2]['warnings']))
        self.assertEqual(first['conversations'][0]['href'], '/inbox?c=' + key1)
        self.assertEqual(first['party_href'], '/suppliers?party=' + str(self.supplier1))

    def test_concurrent_identical_choice_writes_once(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: self.choose(), range(8)))
        self.assertEqual(len({row['id'] for row in results}), 1)
        self.assertEqual(len(self.offers.read(self.deal)['items'][0]['history']), 1)

    def test_unrelated_supplier_invalid_fields_and_path_symlinks_are_rejected(self):
        outsider = self.party('supplier', '虚构案外供应商')
        offer = self.offer(self.product(outsider), '70.00')
        with self.assertRaises(v.Conflict):
            self.choose(offer)
        for fields in (dict(offer_id='1 OR 1=1', reason='虚构'), dict(offer_id=str(self.offer2), reason=''),
                       dict(offer_id=str(self.offer2), reason='虚构', archive_path='/tmp/other.sqlite3')):
            with self.assertRaises(v.BadRequest):
                self.offers.choose(self.deal, self.item, fields, 'fictional-owner')
        link = self.path / 'linked.sqlite3'
        link.symlink_to(self.store.path)
        with self.assertRaises(v.BadRequest):
            other = Offers(store=self.store, path=link)
            other.choose(self.deal, self.item, dict(offer_id=str(self.offer1), reason='虚构'), 'fictional-owner')
        other_deal = self.dw('create')
        with self.assertRaises(v.Missing):
            self.offers.choose(other_deal, self.item, dict(offer_id=str(self.offer1), reason='虚构'), 'fictional-owner')
