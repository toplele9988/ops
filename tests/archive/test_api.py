import asyncio
from datetime import date
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.parse import urlencode

from console.archive.service import Archive
from console.archive.store import Store, insert, now

try:
    from console import v3_archive_api as api
    from starlette.datastructures import QueryParams
    from starlette.responses import JSONResponse
except ModuleNotFoundError as error:
    if error.name != 'starlette' and not (error.name or '').startswith('starlette.'):
        raise
    api = None


def request(*, fields=None, form=None, body=b'', query=None, row_id=None,
            authenticated=True, principal=True, recheck=True, headers=None):
    async def read_body():
        return body
    if headers is None:
        size = len(urlencode(fields if fields is not None else form, doseq=True).encode()) if fields is not None or form is not None else len(body)
        headers = {'content-length': str(size)}
    return SimpleNamespace(state=SimpleNamespace(
        password_principal=SimpleNamespace(username='fictional-owner') if principal else None,
        password_recheck=lambda: recheck, session=SimpleNamespace(authenticated=authenticated),
        form={k: [v] for k, v in fields.items()} if fields is not None else form),
        path_params={} if row_id is None else {'id': str(row_id)}, query_params=query or {},
        headers=headers or {}, body=read_body)


@unittest.skipIf(api is None, 'starlette not installed')
class ApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'state' / 'archive.sqlite3'
        self.store = Store(self.path)
        self.service = Archive(store=self.store, today=lambda: date(2030, 1, 10))
        app = SimpleNamespace(error_response=lambda request, code: JSONResponse({'error': '请先完成身份认证'}, status_code=code))
        self.routes = api.build(app, service=self.service)
        self.handlers = {(r.path.removeprefix('/api/v1/archive/'), 'POST' if 'POST' in r.methods else 'GET'): r.endpoint for r in self.routes}

    def call(self, path, method='GET', **kwargs):
        return asyncio.run(self.handlers[path, method](request(**kwargs)))

    def data(self, path, method='GET', **kwargs):
        result = self.call(path, method, **kwargs)
        self.assertEqual(result.status_code, 200, result.body.decode())
        self.assertEqual(result.headers['cache-control'], 'no-store')
        self.assertEqual(result.headers['x-content-type-options'], 'nosniff')
        return json.loads(result.body)

    def create_party(self, kind='supplier', name='虚构供应商'):
        return self.data('parties', 'POST', fields=dict(kind=kind, name=name))['id']

    def create_product(self, supplier=None, name='FX-10', **fields):
        if supplier is not None:
            fields['supplier_id'] = str(supplier)
        return self.data('products', 'POST', fields=dict(name=name, **fields))['id']

    def create_offer(self, product, **fields):
        values = dict(price='12.34', unit='台', quoted_on='2030-01-01', valid_until='2030-02-01')
        values.update(fields)
        return self.data('products/{id}/offers', 'POST', row_id=product, fields=values)['id']

    def freight_fields(self, forwarder, **fields):
        return dict(forwarder_id=str(forwarder), origin='虚构始发港', destination_country='ZZ',
                    destination_detail='虚构目的地', mode='sea', service='DDP', unit='cbm',
                    price='0.30', min_charge='12.34', currency='CNY', quoted_on='2030-01-01',
                    **({'valid_until': '2030-02-01'} | fields))

    def test_route_contract_and_all_auth_gates(self):
        self.assertEqual(len(self.routes), 21)
        for route in self.routes:
            for options in ({'authenticated': False}, {'principal': False}, {'recheck': False}):
                req = request(row_id=1, fields={}, **options)
                result = asyncio.run(route.endpoint(req))
                self.assertEqual(result.status_code, 403, route.path)
                self.assertEqual(result.headers['cache-control'], 'no-store')
                self.assertEqual(result.headers['x-content-type-options'], 'nosniff')
        self.assertFalse(self.path.exists())

    def test_post_recheck_precedes_body_and_write_in_worker_thread(self):
        req = request(recheck=False)
        async def forbidden_body():
            self.fail('must not read body before recheck')
        req.body = forbidden_body
        with patch.object(self.service, 'write', side_effect=AssertionError('must not write')):
            self.assertEqual(asyncio.run(self.handlers['parties', 'POST'](req)).status_code, 403)
        events, workers = [], []
        write = self.service.write
        def checked():
            events.append('recheck')
            workers.append(threading.get_ident())
            return True
        def written(*args):
            events.append('write')
            workers.append(threading.get_ident())
            return write(*args)
        req = request(fields={'kind': 'supplier', 'name': '虚构供应商'})
        req.state.password_recheck = checked
        with patch.object(self.service, 'write', written):
            self.assertEqual(asyncio.run(self.handlers['parties', 'POST'](req)).status_code, 200)
        self.assertEqual(events, ['recheck', 'write'])
        self.assertTrue(all(w != threading.get_ident() for w in workers))

    def test_get_rechecks_after_reads_missing_parameters_and_failures(self):
        events = []
        read = self.service.read
        def read_tracked(*args):
            events.append('read')
            return read(*args)
        req = request()
        req.state.password_recheck = lambda: events.append('recheck') or False
        with patch.object(self.service, 'read', read_tracked):
            self.assertEqual(asyncio.run(self.handlers['summary', 'GET'](req)).status_code, 403)
        self.assertEqual(events, ['read', 'recheck'])
        self.assertEqual(self.call('products', query={'page': 'bad'}, recheck=False).status_code, 403)
        with patch.object(self.service, 'read', side_effect=RuntimeError('/fictional/private-secret')):
            self.assertEqual(self.call('summary', recheck=False).status_code, 403)
            result = self.call('summary')
            self.assertEqual(result.status_code, 503)
            self.assertEqual(json.loads(result.body), {'error': '档案暂时无法读取'})
        self.assertFalse(self.path.exists())

    def test_forms_middleware_fallback_duplicates_unknown_and_size_limits(self):
        req = request(fields={'kind': 'supplier', 'name': '虚构表单', 'csrf': 'fake-csrf-secret'})
        async def forbidden_body():
            self.fail('middleware form must not reread body')
        req.body = forbidden_body
        self.assertEqual(asyncio.run(self.handlers['parties', 'POST'](req)).status_code, 200)
        result = self.data('parties', 'POST', body=urlencode({'kind': 'forwarder', 'name': '虚构 + & % 货代'}).encode())
        self.assertGreater(result['id'], 0)
        for kwargs in (
            {'form': {'kind': ['supplier', 'forwarder'], 'name': ['虚构']}},
            {'form': {'kind': 'supplier', 'name': ['虚构']}},
            {'fields': {'kind': 'supplier', 'name': '虚构', 'secret': 'fake-secret'}},
            {'body': b'name=' + b'x' * 8192},
            {'fields': {'kind': 'supplier', 'name': '虚构'}, 'headers': {'content-length': '8193'}},
            {'fields': {'kind': 'supplier', 'name': '虚构'}, 'headers': {}},
            {'fields': {'kind': 'supplier', 'name': '虚构', 'notes': '字' * 1200}},
            {'body': b'name=bad&name=duplicate&kind=supplier'},
            {'body': b'name=\xff&kind=supplier'},
        ):
            with self.subTest(kwargs=list(kwargs)):
                response = self.call('parties', 'POST', **kwargs)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(set(json.loads(response.body)), {'error'})
        with self.store.read() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM parties').fetchone()[0], 2)
            self.assertNotIn('fake-csrf-secret', '\n'.join(db.iterdump()))
        req = request(fields={'kind': 'supplier', 'name': '虚构缓存正文'}, headers={})
        req._body = urlencode({'kind': 'supplier', 'name': '虚构缓存正文'}).encode()
        self.assertEqual(asyncio.run(self.handlers['parties', 'POST'](req)).status_code, 200)

    def test_chunked_body_stops_at_limit(self):
        consumed = []
        req = request()
        async def stream():
            for i in range(5):
                consumed.append(i)
                yield b'x' * 4096
        req.stream = stream
        response = asyncio.run(self.handlers['parties', 'POST'](req))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(consumed, [0, 1, 2])
        self.assertFalse(self.path.exists())

    def test_party_detail_terms_notes_lookup_and_manual_update(self):
        party = self.create_party()
        ref = json.dumps(dict(channel='wechat', scope='group:fictional'))
        self.data('parties/{id}/update', 'POST', row_id=party, fields=dict(notes='字' * 100,
                  short_name='虚构', aliases='["虚构别名"]', status='paused', conversation_ref=ref))
        self.data('parties/{id}/terms', 'POST', row_id=party, fields=dict(field='lead_time', value='虚构十天'))
        with self.store.write() as db:
            tid = insert(db, 'terms', dict(party_id=party, field='payment', value='虚构付款条款', status='suggested', updated_at=now()), 'seed')
        self.data('terms/{id}/status', 'POST', row_id=tid, fields={'action': 'confirm'})
        self.assertEqual(self.call('terms/{id}/status', 'POST', row_id=tid, fields={'action': 'reject'}).status_code, 409)
        self.assertNotIn('last_activity', self.data('parties')['items'][0])
        self.service.conversation_lookup = lambda ref: 1894000000.0
        listing = self.data('parties', query={'q': '虚构别名', 'kind': 'supplier', 'status': 'paused'})
        self.assertEqual(len(listing['items'][0]['notes']), 80)
        self.assertEqual(listing['items'][0]['last_activity'], 1894000000.0)
        detail = self.data('parties/{id}', row_id=party)
        self.assertEqual(detail['terms']['payment'][0]['status'], 'confirmed')
        self.assertEqual(detail['terms']['lead_time'][0]['value'], '虚构十天')
        self.service.conversation_lookup = lambda _: (_ for _ in ()).throw(RuntimeError('fake'))
        self.assertNotIn('last_activity', self.data('parties/{id}', row_id=party))

    def test_product_updates_states_filters_and_pagination(self):
        supplier = self.create_party()
        a = self.create_product(supplier, carton_l_cm='50', carton_w_cm='40', carton_h_cm='30', gross_kg='12', units_per_carton='2')
        b = self.create_product(supplier, 'FX-20')
        self.create_offer(a)
        self.create_offer(b, valid_until='')
        self.data('products/{id}/confirm', 'POST', row_id=a, fields={})
        self.data('products/{id}/update', 'POST', row_id=a, fields={'net_kg': '10'})
        detail = self.data('products/{id}', row_id=a)
        self.assertEqual(detail['field_state']['net_kg']['state'], 'manual')
        self.assertTrue(detail['packing_ready'])
        self.assertTrue(detail['current_offer']['is_cost'])
        self.assertNotIn('price_fen', detail['current_offer'])
        self.assertEqual(self.data('products', query={'packing': 'ready', 'price': 'valid'})['total'], 1)
        self.assertEqual(self.data('products', query={'packing': 'missing', 'price': 'none'})['items'][0]['id'], b)
        self.assertEqual(self.data('products', query={'supplier': str(supplier), 'q': 'fx', 'status': 'confirmed'})['total'], 1)
        self.assertEqual(self.data('products', query={'q': '%'})['total'], 0)
        page = self.data('products', query={'page': '2', 'page_size': '1'})
        self.assertEqual((page['total'], page['items'][0]['id']), (2, b))
        self.assertEqual(self.data('products', query={'page': '99'})['items'], [])
        party = self.data('parties/{id}', row_id=supplier)
        self.assertEqual(party['packing_coverage'], 0.5)
        self.assertEqual(party['products'], {'candidate': 1, 'confirmed': 1, 'hidden': 0})
        self.data('products/{id}/hide', 'POST', row_id=b, fields={})
        self.assertEqual(self.data('products')['total'], 1)
        self.assertEqual(self.data('products', query={'status': 'hidden'})['total'], 1)
        self.data('products/{id}/restore', 'POST', row_id=b, fields={})
        self.assertEqual(self.data('products')['total'], 2)

    def test_offer_and_freight_expiry_current_flags_summary_and_status(self):
        product = self.create_product()
        old = self.create_offer(product)
        self.create_offer(product, quoted_on='2030-01-02', valid_until='')
        detail = self.data('products/{id}', row_id=product)
        self.assertFalse(any(o['usable_for_quote'] for o in detail['offers']))
        self.assertEqual(detail['validity'], 'no_expiry')
        self.assertEqual(self.data('summary')['offers']['no_expiry'], 1)
        self.assertEqual(self.data('summary')['offers']['valid'], 0)
        with self.store.write() as db:
            oid = insert(db, 'offers', dict(product_id=product, price_fen=55, currency='CNY', unit='台',
                         quoted_on='2030-01-03', valid_until='2030-01-11', corrects_offer_id=old,
                         status='suggested', created_at=now(), updated_at=now()), 'seed')
        self.assertEqual(self.data('summary')['offers']['suggested'], 1)
        self.data('offers/{id}/status', 'POST', row_id=oid, fields={'action': 'confirm'})
        self.assertTrue(self.data('products/{id}', row_id=product)['current_offer']['usable_for_quote'])
        self.assertEqual(self.data('summary')['offers']['expiring'], 1)
        forwarder = self.create_party('forwarder', '虚构货代')
        self.data('freight', 'POST', fields=self.freight_fields(forwarder))
        self.data('freight', 'POST', fields=self.freight_fields(forwarder, valid_until=''))
        self.assertFalse(any(r['usable_for_quote'] for r in self.data('freight')['items']))
        self.assertEqual(self.data('freight', query={'state': 'none'})['total'], 1)
        self.assertEqual(self.data('freight', query={'state': 'valid'})['total'], 0)
        self.assertEqual(self.data('parties/{id}', row_id=forwarder)['freight_quotes'], [])
        third = self.data('freight', 'POST', fields=self.freight_fields(forwarder, valid_until='2030-01-09'))['id']
        freight = self.data('freight', query={'state': 'expired', 'forwarder': str(forwarder), 'country': 'ZZ', 'mode': 'sea'})
        self.assertEqual(freight['items'][0]['id'], third)
        self.assertEqual(freight['items'][0]['min_charge'], '12.34')
        self.assertEqual(freight['items'][0]['price'], '0.30')
        self.assertTrue(freight['items'][0]['is_cost'])
        self.assertEqual(self.data('summary')['freight']['expired'], 1)
        with self.store.write() as db:
            fid = insert(db, 'freight_quotes', dict(forwarder_id=forwarder, origin='虚构港乙', destination_country='YY',
                mode='air', service='DAP', unit='kg', price_fen=77, currency='CNY', quoted_on='2030-01-01',
                valid_until='2030-01-10', status='suggested', created_at=now(), updated_at=now()), 'seed')
        self.data('freight/{id}/status', 'POST', row_id=fid, fields={'action': 'confirm'})
        self.assertEqual(self.data('summary')['freight']['expiring'], 1)
        self.assertTrue(self.data('parties/{id}', row_id=forwarder)['freight_quotes'][0]['usable_for_quote'])

    def test_bad_fields_ranges_chinese_errors_and_no_partial_write(self):
        product = self.create_product()
        for fields in ({'carton_w_cm': '1'}, {'gross_kg': '501'}, {'units_per_carton': '0'},
                       {'field_state_json': '{}'}, {'status': 'confirmed'}, {'name': '字' * 201}):
            result = self.call('products/{id}/update', 'POST', row_id=product, fields=fields)
            self.assertEqual(result.status_code, 400)
            self.assertRegex(json.loads(result.body)['error'], '[\u4e00-\u9fff]')
        for fields in ({'price': '0.1+0.2'}, {'price': '0.30000000000000004'}, {'quoted_on': '2030-02-31'},
                       {'valid_until': '2029-01-01'}, {'lead_days_min': '10', 'lead_days_max': '2'},
                       {'includes_flightcase': '2'}, {'currency': 'usd'}):
            result = self.call('products/{id}/offers', 'POST', row_id=product,
                fields=dict(price='12.34', unit='台', quoted_on='2030-01-01') | fields)
            self.assertEqual(result.status_code, 400, result.body)
        self.assertEqual(self.call('products/{id}', row_id=999).status_code, 404)
        for query in ({'page': '0'}, {'page_size': '101'}, {'price': 'unknown'}, {'packing': 'x'},
                      {'supplier': '-1'}, {'q': '字' * 501}, QueryParams('q=a&q=b')):
            self.assertEqual(self.call('products', query=query).status_code, 400)
        with self.store.read() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM offers').fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT count(*) FROM audit WHERE who='fictional-owner'").fetchone()[0], 1)

    def test_audit_one_row_per_business_write_no_freeform_secrets_or_price(self):
        supplier = self.create_party()
        product = self.create_product(supplier)
        self.create_offer(product, source_ref='fake-secret-reference')
        self.data('products/{id}/update', 'POST', row_id=product, fields={'description': 'fake-password'})
        self.data('parties/{id}/update', 'POST', row_id=supplier, fields={'notes': 'fake-token'})
        self.data('products/{id}/confirm', 'POST', row_id=product, fields={})
        with self.store.read() as db:
            rows = [dict(r) for r in db.execute("SELECT * FROM audit WHERE who='fictional-owner'")]
        self.assertEqual(len(rows), 6)
        text = json.dumps(rows)
        for secret in ('fake-secret-reference', 'fake-password', 'fake-token', '12.34'):
            self.assertNotIn(secret, text)
        self.assertTrue(all(r['who'] == 'fictional-owner' for r in rows))

    def test_reject_suggestions_for_all_three_record_types(self):
        supplier = self.create_party()
        product = self.create_product(supplier)
        forwarder = self.create_party('forwarder', '虚构货代拒绝测试')
        with self.store.write() as db:
            term = insert(db, 'terms', dict(party_id=supplier, field='moq', value='虚构起订数',
                                           status='suggested', updated_at=now()), 'seed')
            offer = insert(db, 'offers', dict(product_id=product, price_fen=22, currency='CNY', quoted_on='2030-01-01',
                valid_until='2030-02-01', status='suggested', created_at=now(), updated_at=now()), 'seed')
            freight = insert(db, 'freight_quotes', dict(forwarder_id=forwarder, origin='虚构港', destination_country='ZZ',
                mode='air', service='DDP', unit='kg', price_fen=33, currency='CNY', quoted_on='2030-01-01',
                valid_until='2030-02-01', status='suggested', created_at=now(), updated_at=now()), 'seed')
        for path, identity in [('terms/{id}/status', term), ('offers/{id}/status', offer), ('freight/{id}/status', freight)]:
            self.data(path, 'POST', row_id=identity, fields={'action': 'reject'})
            self.assertEqual(self.call(path, 'POST', row_id=identity, fields={'action': 'confirm'}).status_code, 409)
        summary = self.data('summary')
        self.assertEqual(sum(summary['offers'].values()), 0)
        self.assertEqual(sum(summary['freight'].values()), 0)

    def test_merge_endpoint_and_detail_product_cap(self):
        supplier = self.create_party()
        ids = [self.create_product(supplier, f'FZ-{i:03}') for i in range(52)]
        self.create_offer(ids[0])
        self.data('products/{id}/merge', 'POST', row_id=ids[0], fields={'into': str(ids[1])})
        detail = self.data('parties/{id}', row_id=supplier)
        self.assertEqual(len(detail['product_items']), 50)
        self.assertEqual(detail['products']['hidden'], 1)
        target = self.data('products/{id}', row_id=ids[1])
        self.assertEqual(len(target['offers']), 1)
        self.assertEqual(self.call('products/{id}/restore', 'POST', row_id=ids[0], fields={}).status_code, 409)


if __name__ == '__main__':
    unittest.main()
