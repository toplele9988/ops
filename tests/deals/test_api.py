import asyncio
import json
from types import SimpleNamespace
import threading
import unittest
from unittest.mock import patch
from urllib.parse import urlencode

from console.inbox.model import make_key
import test_service as fixtures
from test_service import KEY, KEY2

try:
    from console import v3_deals_api as api, v3_archive_api as archive_api
    from starlette.datastructures import QueryParams
    from starlette.responses import JSONResponse
except ModuleNotFoundError as error:
    if error.name != 'starlette' and not (error.name or '').startswith('starlette.'):
        raise
    api = None


def request(*, fields=None, form=None, body=b'', query=None, ids=None, principal=True,
            authenticated=True, recheck=True, headers=None):
    async def read_body():
        return body
    if headers is None:
        headers = {'content-length': str(len(urlencode(fields if fields is not None else form, doseq=True).encode())
                    if fields is not None or form is not None else len(body))}
    return SimpleNamespace(state=SimpleNamespace(password_principal=SimpleNamespace(username='fictional-owner') if principal else None,
        session=SimpleNamespace(authenticated=authenticated), password_recheck=lambda: recheck,
        form={k: [v] for k, v in fields.items()} if fields is not None else form),
        path_params=ids or {}, query_params=query or {}, headers=headers, body=read_body)


@unittest.skipIf(api is None, 'starlette not installed')
class ApiTests(unittest.TestCase):
    def setUp(self):
        fixtures.DealServiceTests.setUp(self)
        self.app = SimpleNamespace(error_response=lambda request, code: JSONResponse({'error': '请先完成身份认证'}, status_code=code))
        self.routes = api.build(self.app, service=self.deals)
        self.handlers = {(r.path.removeprefix('/api/v1/'), 'POST' if 'POST' in r.methods else 'GET'): r.endpoint for r in self.routes}

    def call(self, path, method='GET', **kwargs):
        return asyncio.run(self.handlers[path, method](request(**kwargs)))

    def data(self, path, method='GET', **kwargs):
        response = self.call(path, method, **kwargs)
        self.assertEqual(response.status_code, 200, response.body.decode())
        self.assertEqual(response.headers['cache-control'], 'no-store')
        self.assertEqual(response.headers['x-content-type-options'], 'nosniff')
        return json.loads(response.body)

    def create(self, **fields):
        return self.data('deals', 'POST', fields=fields)['id']

    def test_all_routes_auth_headers_and_post_recheck_before_body_and_write(self):
        self.assertEqual(len(self.routes), 18)
        for route in self.routes:
            for denied in ({'principal': False}, {'authenticated': False}, {'recheck': False}):
                response = asyncio.run(route.endpoint(request(ids={'id': '1'}, fields={}, **denied)))
                self.assertEqual(response.status_code, 403, route.path)
                self.assertEqual(response.headers['cache-control'], 'no-store')
                self.assertEqual(response.headers['x-content-type-options'], 'nosniff')
        self.assertFalse(self.path.exists())
        req = request(recheck=False)
        async def forbidden_body():
            self.fail('recheck must precede body')
        req.body = forbidden_body
        with patch.object(self.deals, 'write', side_effect=AssertionError('recheck must precede write')):
            self.assertEqual(asyncio.run(self.handlers['deals', 'POST'](req)).status_code, 403)
        order, threads = [], []
        original = self.deals.write
        def write(*args):
            order.append('write')
            threads.append(threading.get_ident())
            return original(*args)
        req = request(fields={'title': '虚构案子'})
        req.state.password_recheck = lambda: order.append('recheck') or True
        with patch.object(self.deals, 'write', write):
            self.assertEqual(asyncio.run(self.handlers['deals', 'POST'](req)).status_code, 200)
        self.assertEqual(order, ['recheck', 'recheck', 'write'])
        self.assertTrue(all(t != threading.get_ident() for t in threads))

    def test_post_rechecks_after_form_parsing_before_business_write(self):
        req = request(fields={'title': '虚构撤权案子'})
        checks = iter((True, False))
        req.state.password_recheck = lambda: next(checks)
        with patch.object(self.deals, 'write', side_effect=AssertionError('revoked principal must not write')):
            self.assertEqual(asyncio.run(self.handlers['deals', 'POST'](req)).status_code, 403)
        self.assertFalse(self.path.exists())

    def test_get_rechecks_reads_errors_and_fixed_error_messages(self):
        events = []
        req = request()
        req.state.password_recheck = lambda: events.append('recheck') or False
        def read(*args):
            events.append('read')
            return {}
        with patch.object(self.deals, 'read', read):
            self.assertEqual(asyncio.run(self.handlers['deals/summary', 'GET'](req)).status_code, 403)
        self.assertEqual(events, ['read', 'recheck'])
        self.assertEqual(self.call('deals', query={'page': 'bad'}, recheck=False).status_code, 403)
        with patch.object(self.deals, 'read', side_effect=RuntimeError('/fictional/private/path')):
            response = self.call('deals/summary')
            self.assertEqual(response.status_code, 503)
            self.assertEqual(json.loads(response.body), {'error': '案子暂时无法读取'})
        with patch.object(self.deals, 'write', side_effect=RuntimeError('/fictional/private/path')):
            response = self.call('deals', 'POST', fields={})
            self.assertEqual(json.loads(response.body), {'error': '案子暂时无法保存'})

    def test_forms_cached_fallback_limits_duplicates_and_stream(self):
        req = request(fields={'title': '虚构缓存', 'csrf': 'fake-csrf'})
        async def forbidden_body():
            self.fail('cached form must not reread stream')
        req.body = forbidden_body
        self.assertEqual(asyncio.run(self.handlers['deals', 'POST'](req)).status_code, 200)
        self.data('deals', 'POST', body=urlencode({'title': '虚构 + & %'}).encode())
        for kwargs in ({'form': {'title': ['a', 'b']}}, {'form': {'title': 'a'}},
                       {'body': b'title=\xff'}, {'body': b'title=a&title=b'},
                       {'body': b'title=' + b'x'*8192}, {'fields': {'title': '字'*501}},
                       {'fields': {'notes': '不允许字段'}}, {'fields': {}, 'headers': {}},
                       {'fields': {}, 'headers': {'content-length': '8193'}}):
            self.assertEqual(self.call('deals', 'POST', **kwargs).status_code, 400)
        req = request()
        async def stream():
            yield b'title='
            yield b'x'*8192
        req.stream = stream
        self.assertEqual(asyncio.run(self.handlers['deals', 'POST'](req)).status_code, 400)

    def test_create_link_identity_multi_deal_and_remove_endpoints(self):
        a = self.create(conversation_key=KEY, party_kind='customer', party_name='虚构客户')
        d = self.data('deals/{id}', ids={'id': a})
        pid = d['customer']['id']
        b = self.create(conversation_key=KEY)
        self.assertEqual(len(self.data('deals/by-conversation', query={'key': KEY})['deals']), 2)
        cid = self.data('deals/{id}/conversations', 'POST', ids={'id': a}, fields={'conversation_key': KEY})['id']
        self.assertEqual(len(self.data('deals/{id}', ids={'id': a})['conversations']), 1)
        vendor = self.archive.write('party_create', None, dict(kind='vendor', name='虚构配套'), 'fictional-owner')['id']
        for path in ('identities', 'deals/{id}/conversations'):
            response = self.call(path, 'POST', ids={'id': a}, fields={'conversation_key': KEY, 'party_id': str(vendor)})
            self.assertEqual(response.status_code, 409)
        self.data('deals/{id}/participants/{pid}/remove', 'POST', ids={'id': a, 'pid': d['participants'][0]['id']}, fields={})
        self.assertEqual(len(self.data('deals/{id}', ids={'id': b})['conversations']), 1)
        self.assertEqual(self.data('identities/lookup', query={'key': KEY})['party']['id'], pid)
        self.data('identities', 'POST', fields={'conversation_key': KEY, 'party_id': str(vendor), 'confirm': '1'})
        self.assertEqual(self.data('identities/lookup', query={'key': KEY})['party']['id'], vendor)
        self.assertIsNone(self.data('identities/lookup', query={'key': KEY2}))
        participant = self.data('deals/{id}/participants', 'POST', ids={'id': a}, fields={'party_id': str(vendor), 'note': '虚构参与方备注'})['id']
        self.assertGreater(participant, 0)
        cid = self.data('deals/{id}/conversations', 'POST', ids={'id': a}, fields={'conversation_key': KEY2, 'party_id': str(vendor)})['id']
        self.data('deals/{id}/conversations/{cid}/remove', 'POST', ids={'id': a, 'cid': cid}, fields={})

    def test_money_items_tasks_note_stage_status_and_invalid_parameters(self):
        did = self.create()
        ids = {'id': did}
        self.data('deals/{id}/update', 'POST', ids=ids, fields={'value': '12.34', 'currency': 'USD', 'next_action': '虚构行动'})
        iid = self.data('deals/{id}/items', 'POST', ids=ids, fields={'name': '虚构产品', 'target_price': '0.10', 'qty': '4'})['id']
        d = self.data('deals/{id}', ids=ids)
        self.assertEqual((d['value'], d['items'][0]['target_price']), ('12.34', '0.10'))
        self.data('deals/{id}/items/{iid}/remove', 'POST', ids=dict(ids, iid=iid), fields={})
        tid = self.data('deals/{id}/tasks', 'POST', ids=ids, fields={'title': '虚构任务', 'due': '2030-01-01'})['id']
        self.data('deal-tasks/{id}/status', 'POST', ids={'id': tid}, fields={'status': 'done'})
        self.data('deals/{id}/events', 'POST', ids=ids, fields={'note': '虚构时间线'})
        self.assertEqual(self.call('deals/{id}/update', 'POST', ids=ids, fields={'stage': 'paid'}).status_code, 400)
        fixtures.DealServiceTests.confirm_payment(self, did)
        self.assertEqual(self.data('deals/{id}', ids=ids)['status'], 'won')
        self.data('deals/{id}/update', 'POST', ids=ids, fields={'stage': 'sourcing'})
        self.assertEqual(self.data('deals/{id}', ids=ids)['status'], 'open')
        self.assertEqual(self.call('deals/{id}/update', 'POST', ids=ids, fields={'status': 'lost'}).status_code, 400)
        self.data('deals/{id}/update', 'POST', ids=ids, fields={'status': 'lost', 'lost_reason': '虚构原因'})
        for fields in ({'stage': 'bad'}, {'status': 'bad'}, {'value': '0.001'}, {'value': '1e3'}, {'currency': 'usd'},
                       {'next_action_due': '2030-02-31'}, {'lost_reason': '字'*201}, {'notes': '字'*2001}):
            self.assertEqual(self.call('deals/{id}/update', 'POST', ids=ids, fields=fields).status_code, 400)
        for key in ('bad', make_key('customer', 'alibaba', 'fake', 'fake')):
            self.assertEqual(self.call('deals', 'POST', fields={'conversation_key': key}).status_code, 400)
        self.assertEqual(self.call('deals/{id}', ids={'id': 999999}).status_code, 404)
        self.assertEqual(self.call('deals/{id}', ids={'id': '-1'}).status_code, 400)
        self.assertEqual(self.call('deals/{id}/events', 'POST', ids=ids, fields={'note': '字'*501}).status_code, 400)
        self.assertEqual(self.call('deals/{id}/items', 'POST', ids=ids, fields={'name': 'x', 'qty': '0'}).status_code, 400)

    def test_all_paid_stage_create_and_update_bypasses_are_rejected(self):
        from console.deals.stages import KEYS
        did = self.create(stage='awaiting_payment')
        for stage in KEYS[5:]:
            with self.subTest(stage=stage):
                self.assertEqual(self.call('deals', 'POST', fields={'stage': stage}).status_code, 400)
                for fields in ({'stage': stage}, {'stage': stage, 'status': 'won'}):
                    self.assertEqual(self.call('deals/{id}/update', 'POST', ids={'id': did}, fields=fields).status_code, 400)
        self.assertEqual(self.call('deals/{id}/update', 'POST', ids={'id': did}, fields={'status': 'won'}).status_code, 400)
        self.assertEqual(self.data('deals/{id}', ids={'id': did})['status'], 'open')

    def test_filters_pagination_archive_customer_vendor_and_shared_injection(self):
        for name in ('虚构甲', '虚构乙', '虚构丙'):
            self.create(title=name)
        self.assertEqual(self.data('deals', query={'page': '2', 'page_size': '2'})['total'], 3)
        self.assertEqual(len(self.data('deals', query={'page': '2', 'page_size': '2'})['items']), 1)
        self.assertEqual(self.data('deals', query={'q': '乙', 'stage': 'inquiry'})['total'], 1)
        self.assertEqual(self.data('deals/summary')['by_stage'][0]['count'], 3)
        for query in ({'page': '0'}, {'page_size': '101'}, {'party': '-1'}, {'status': 'bad'}, {'stage': 'bad'},
                      {'waiting': 'bad'}, {'q': '字'*501}, QueryParams('q=a&q=b'), {'secret': 'bad'}):
            self.assertEqual(self.call('deals', query=query).status_code, 400)
        routes = archive_api.build(self.app, service=self.archive)
        post = next(r.endpoint for r in routes if r.name == 'archive_party_create')
        get = next(r.endpoint for r in routes if r.name == 'archive_parties_list')
        for kind in ('customer', 'vendor'):
            self.assertEqual(asyncio.run(post(request(fields={'kind': kind, 'name': '广州 Fictional 有限公司'}))).status_code, 200)
            result = asyncio.run(get(request(query={'kind': kind})))
            self.assertEqual(result.status_code, 200)
            self.assertEqual(json.loads(result.body)['items'][0]['short_name'], '广州 Fictional 有限公司')
        with patch.object(api, 'shared_inbox', side_effect=AssertionError('must keep injected inbox')):
            api.build(self.app, archive_path=self.path, inbox=SimpleNamespace(read=lambda *args: {}))

    def test_four_existing_builders_share_one_instance_and_keep_injection(self):
        from console import channels, v3_inbox_api, v3_customers_api, v3_channels_api, v3_outbox_api
        inbound = self.path.parent / 'fictional-inbound.sqlite3'
        with patch.object(channels, '_build_inbox', wraps=channels._build_inbox) as factory:
            for module in (v3_inbox_api, v3_customers_api, v3_channels_api, v3_outbox_api):
                module.build(self.app, inbound_path=inbound)
            self.assertEqual(factory.call_count, 1)
        fake = SimpleNamespace(read=lambda *args: {})
        with patch.object(channels, '_build_inbox', side_effect=AssertionError('injected inbox must be preserved')):
            for module in (v3_inbox_api, v3_customers_api, v3_channels_api, v3_outbox_api):
                module.build(self.app, inbound_path=self.path.parent / 'other-inbound', inbox=fake)
