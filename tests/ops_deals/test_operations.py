"""W3/W6/W7 isolated acceptance: fictional SQLite, no network or live files."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from urllib.parse import urlencode
import asyncio
import json

from console.archive.service import Archive
from console.archive.store import Store
from console.archive.validation import BadRequest, Conflict
from console.deals.service import Deals
from console.fulfil.service import Fulfil
from console.quotes.service import Quotes


class OperationsTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='fictional-ops-deals-')
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.today = date(2030, 1, 1)
        self.store = Store(self.root / 'archive.sqlite3', target_version=9)
        self.clock = lambda: 1893456000
        self.deals = Deals(store=self.store, lookup=lambda _: None, clock=self.clock)
        self.archive = Archive(store=self.store, today=lambda: self.today)
        self.fulfil = Fulfil(store=self.store, deals=self.deals, today=lambda: self.today, clock=self.clock)
        self.fx = self.root / 'fx.json'
        self.fx.write_text('{"rate":"7","source":"fictional","source_updated":"2030-01-01","fetched_at":1893456000}')
        self.quotes = Quotes(store=self.store, today=lambda: self.today, fx_path=self.fx, clock=self.clock)
        guard = patch.object(socket.socket, 'connect', side_effect=AssertionError('network forbidden'))
        guard.start(); self.addCleanup(guard.stop)

    def create(self, **extra):
        return self.deals.write('create', None, dict(customer_name='虚构买家', product_name='虚构灯具', qty='4',
            destination_country='ZZ', operation_key='create-demo-0001', **extra), 'fictional-owner')['id']

    def test_atomic_create_receipt_context_and_default_tasks(self):
        did = self.create()
        self.assertEqual(self.create(), did)
        detail = self.deals.read('detail', row_id=did)
        self.assertEqual(detail['destination_country'], 'ZZ')
        self.assertEqual(detail['customer']['name'], '虚构买家')
        self.assertEqual(detail['items'][0]['qty'], 4)
        self.assertEqual(len(detail['tasks']['inquiry']), 1)
        with self.assertRaises(Conflict):
            self.create(title='changed payload')
        with self.assertRaises(BadRequest):
            self.deals.write('create', None, dict(customer_name='虚构未保存客户', product_name='虚构灯具', qty='0', destination_country='ZZ'), 'fictional-owner')
        self.assertEqual(self.deals.read('list')['total'], 1)

    def test_payment_is_human_confirmed_atomic_and_idempotent(self):
        did = self.create()
        with self.assertRaises(BadRequest):
            self.deals.write('update', did, {'stage': 'paid'}, 'fictional-owner')
        fields = dict(kind='full', amount_fen='30000', currency='USD', received_on='2030-01-01', method='wire', operation_key='payment-demo-0001')
        payment = self.fulfil.write('payment_create', did, fields, 'fictional-owner')
        self.assertEqual(payment, self.fulfil.write('payment_create', did, fields, 'fictional-owner'))
        self.assertEqual(self.deals.read('detail', row_id=did)['status'], 'open')
        with self.assertRaises(BadRequest):
            self.fulfil.write('payment_confirm', payment['id'], {}, 'fictional-owner')
        confirm = dict(confirm='1', qualifies='1', operation_key='confirm-demo-0001')
        result = self.fulfil.write('payment_confirm', payment['id'], confirm, 'fictional-owner')
        self.assertEqual(result, self.fulfil.write('payment_confirm', payment['id'], confirm, 'fictional-owner'))
        detail = self.deals.read('detail', row_id=did)
        self.assertEqual((detail['stage'], detail['status']), ('paid', 'won'))
        self.assertEqual(len(detail['tasks']['paid']), 3)
        self.assertEqual(sum(e['summary'] == '收到客户付款' for e in detail['events']), 1)
        self.assertEqual(len(self.fulfil.read('payments', row_id=did)['items']), 1)

    def setup_quote(self):
        did = self.create()
        supplier = self.archive.write('party_create', None, dict(kind='supplier', name='虚构成本供应方'), 'fictional-owner')['id']
        pid = self.archive.write('product_create', None, dict(name='虚构灯具', supplier_id=str(supplier)), 'fictional-owner')['id']
        offer = self.archive.write('offer_create', pid, dict(price='100', currency='CNY', unit='件', quoted_on='2029-12-01', valid_until='2030-02-01'), 'fictional-owner')['id']
        # Archive offer_create is confirmed by the human write contract.
        rid = self.quotes.write('create', did, {}, 'fictional-owner')['id']
        line = self.quotes.read('detail', row_id=rid)['lines'][0]['id']
        self.quotes.write('line_update', rid, dict(product_id=str(pid), sell_unit_price='50'), 'fictional-owner', line)
        self.quotes.write('update', rid, dict(incoterm='FOB', fx_rate='7', valid_until='2030-01-20'), 'fictional-owner')
        self.quotes.write('terms', rid, dict(confirm='1', payment='30_70', delivery_scope='虚构港交货', seller_name='虚构贸易公司', seller_address='虚构地址'), 'fictional-owner')
        return did, rid, line

    def test_drafts_refuse_expired_or_missing_validity_and_inverted_prices(self):
        did, rid, line = self.setup_quote()
        self.assertEqual(self.quotes.read('detail', row_id=rid)['destination_country'], 'ZZ')
        for kind in ('quote', 'pi'):
            fields = dict(kind=kind, lang='zh', operation_key='draft-demo-' + kind)
            draft = self.quotes.write('draft', rid, fields, 'fictional-owner')
            self.assertEqual(draft['mode'], 'dry-run')
            self.assertEqual(draft['state'], 'draft')
            self.assertNotIn('虚构成本供应方', draft['text'])
            self.assertEqual(draft, self.quotes.write('draft', rid, fields, 'fictional-owner'))
        self.assertEqual(len(self.deals.read('detail', row_id=did)['drafts']), 2)
        self.assertEqual(self.deals.read('detail', row_id=did)['value'], '200.00')
        self.quotes.write('line_update', rid, dict(sell_unit_price='1'), 'fictional-owner', line)
        with self.assertRaisesRegex(Conflict, '倒挂'):
            self.quotes.write('draft', rid, dict(kind='quote'), 'fictional-owner')
        self.quotes.write('line_update', rid, dict(sell_unit_price='50'), 'fictional-owner', line)
        for expiry in ('2029-12-31', None):
            with self.store.write() as db:
                db.execute('UPDATE quote_lines SET cost_valid_until=? WHERE id=?', (expiry, line))
            with self.assertRaisesRegex(Conflict, '过期|有效期'):
                self.quotes.write('draft', rid, dict(kind='pi'), 'fictional-owner')

    def test_fulfilment_dates_overdue_and_receipts(self):
        did = self.create()
        with self.assertRaises(BadRequest):
            self.fulfil.write('node_update', did, {'status': 'done'}, 'fictional-owner', 'po')
        pid = self.fulfil.write('payment_create', did, dict(kind='full', amount_fen='1', currency='USD', received_on='2030-01-01', method='other'), 'fictional-owner')['id']
        self.fulfil.write('payment_confirm', pid, {'confirm': '1'}, 'fictional-owner')
        self.fulfil.write('node_update', did, {'planned_on': '2029-12-30'}, 'fictional-owner', 'po')
        self.assertTrue(self.fulfil.read('board')['items'][0]['overdue'])
        fields = dict(status='done', completed_on='2030-01-01', operation_key='node-done-0001')
        result = self.fulfil.write('node_update', did, fields, 'fictional-owner', 'po')
        self.assertEqual(result, self.fulfil.write('node_update', did, fields, 'fictional-owner', 'po'))
        self.assertEqual(self.fulfil.read('board')['items'][0]['done'], 1)
        with self.assertRaises(BadRequest):
            self.fulfil.write('node_update', did, {'status': 'done'}, 'fictional-owner', 'inspection')

    def test_concurrent_create_and_rollback_receipts(self):
        self.store.initialize()
        with ThreadPoolExecutor(max_workers=3) as workers:
            ids = list(workers.map(lambda _: self.create(), range(6)))
        self.assertEqual(len(set(ids)), 1)
        with self.assertRaises(BadRequest):
            self.deals.write('update', ids[0], dict(status='lost', operation_key='loss-demo-0001'), 'fictional-owner')
        with self.store.read() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM deal_operation_receipts WHERE operation_key='loss-demo-0001'").fetchone()[0], 0)
        self.deals.write('update', ids[0], dict(status='lost', lost_reason='虚构客户取消采购', operation_key='loss-demo-0001'), 'fictional-owner')
        self.assertEqual(self.deals.read('detail', row_id=ids[0])['lost_reason'], '虚构客户取消采购')

    def test_link_and_supplier_draft_are_scoped_and_never_send(self):
        from console.inbox.model import make_key
        did = self.create()
        party = self.archive.write('party_create', None, dict(kind='supplier', name='虚构采购供应商'), 'fictional-owner')['id']
        key = make_key('conversation', 'wechat', 'fictional-account', 'fictional-supplier')
        fields = dict(conversation_key=key, party_id=str(party), operation_key='link-demo-0001')
        first = self.deals.write('conversation', did, fields, 'fictional-owner')
        self.assertEqual(first, self.deals.write('conversation', did, fields, 'fictional-owner'))
        with self.assertRaises(BadRequest):
            self.deals.write('draft', did, dict(kind='supplier_order', party_id=str(party)), 'fictional-owner')
        draft = self.deals.write('draft', did, dict(kind='price_request', party_id=str(party), conversation_key=key, operation_key='request-price-0001'), 'fictional-owner')
        self.assertEqual(draft['mode'], 'dry-run')
        self.assertEqual(len(self.deals.read('detail', row_id=did)['conversations']), 1)
        with self.assertRaises(BadRequest):
            self.deals.write('draft', did, dict(kind='price_request', party_id=str(party), conversation_key=make_key('conversation', 'wechat', 'fictional', 'other')), 'fictional-owner')

    def test_creation_from_known_conversation_keeps_actual_customer_identity(self):
        from console.inbox.model import make_key
        did = self.create()
        customer = self.deals.read('detail', row_id=did)['customer']['id']
        key = make_key('conversation', 'alibaba', 'fictional-account', 'fictional-customer')
        self.deals.write('conversation', did, dict(conversation_key=key, party_id=str(customer)), 'fictional-owner')
        second = self.deals.write('create', None, dict(conversation_key=key, customer_name='虚构渠道昵称', product_name='虚构灯具', qty='1', destination_country='ZZ'), 'fictional-owner')['id']
        self.assertEqual(self.deals.read('detail', row_id=second)['customer']['id'], customer)
        with self.store.read() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM parties WHERE kind='customer'").fetchone()[0], 1)

    def test_deals_api_new_route_auth_and_recheck_after_body(self):
        from console import v3_deals_api
        from starlette.responses import JSONResponse
        app = SimpleNamespace(error_response=lambda req, code: JSONResponse({'error': 'denied'}, status_code=code))
        routes = v3_deals_api.build(app, service=self.deals)
        draft_route = next(r for r in routes if r.path == '/api/v1/deals/{id}/drafts')
        def request(allowed=True):
            values = dict(kind='price_request', party_id='123', operation_key='route-draft-0001')
            state = SimpleNamespace(password_principal=SimpleNamespace(username='fictional-owner'), session=SimpleNamespace(authenticated=True), password_recheck=lambda: allowed, form={k: [v] for k, v in values.items()})
            return SimpleNamespace(state=state, path_params={'id': '1'}, query_params={}, headers={'content-length': str(len(urlencode(values).encode()))})
        denied = asyncio.run(draft_route.endpoint(request(False)))
        self.assertEqual(denied.status_code, 403)
        self.assertFalse(self.store.path.exists())
        req = request()
        checks = iter([True, False])
        req.state.password_recheck = lambda: next(checks)
        with patch.object(self.deals, 'write', side_effect=AssertionError('revoked request cannot write')):
            response = asyncio.run(draft_route.endpoint(req))
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.headers['cache-control'], 'no-store')
