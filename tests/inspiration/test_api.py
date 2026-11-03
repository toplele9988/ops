import asyncio
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from console.inspiration.store import InspirationStore
from inspiration_fixtures import card, enrichment

try:
    from console import v3_inspiration_api as api
except ModuleNotFoundError as exc:
    if exc.name != 'starlette' and not (exc.name or '').startswith('starlette.'):
        raise
    api = None


def request(*, authenticated=True, principal=True, recheck=True, query=None,
            card_id=None, username='fake-tester', form=None):
    state = SimpleNamespace(password_principal=SimpleNamespace(username=username) if principal else None,
                            password_recheck=(lambda: recheck) if principal else None,
                            session=SimpleNamespace(authenticated=authenticated), form=form)
    return SimpleNamespace(state=state, query_params=query or {}, path_params={'card_id': card_id})


@unittest.skipIf(api is None, 'starlette not installed')
class ApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'cards.sqlite3'
        self.errors = []
        app = SimpleNamespace(error_response=lambda req, code: self.errors.append(code) or code)
        self.routes = api.build(app, self.path)
        self.list_handler, self.detail_handler, self.update_handler = [route.endpoint for route in self.routes]

    def call(self, handler=None, **kw):
        return asyncio.run((handler or self.list_handler)(request(**kw)))

    def seed(self):
        store = InspirationStore(self.path)
        first, second, third = card(), card('虚构跨境业务 #外贸'), card('虚构汽车话题')
        for index, item in enumerate((first, second, third)):
            store.upsert_card(item, f'fake-message-{index}', index + 1)
        store.save_enrichment(first['card_id'], enrichment(first), 'fictional-model')
        store.save_enrichment(second['card_id'], enrichment(second, relevance='business'), 'fictional-model')
        store.save_enrichment(third['card_id'], enrichment(third, relevance='other'), 'fictional-model')
        store.set_status(first['card_id'], 'trying', '虚构备注')
        return store, first, second, third

    def test_update_changes_status_and_note_but_keeps_ai_fields(self):
        store, first, *_ = self.seed()
        before = store.get_card(first['card_id'])
        response = self.call(self.update_handler, card_id=first['card_id'], form={'status': ['adopted']})
        self.assertEqual(response.status_code, 200)
        after = store.get_card(first['card_id'])
        self.assertEqual(after['status'], 'adopted')
        self.assertEqual(after['note'], before['note'])  # a missing field keeps the old value
        self.assertEqual(after['summary'], before['summary'])
        self.call(self.update_handler, card_id=first['card_id'], form={'note': ['新的虚构备注']})
        self.assertEqual(store.get_card(first['card_id'])['note'], '新的虚构备注')
        self.assertEqual(store.get_card(first['card_id'])['status'], 'adopted')

    def test_update_rejects_bad_input_and_unauthenticated(self):
        store, first, *_ = self.seed()
        cid = first['card_id']
        for form in ({'status': ['bogus']}, {'note': ['x' * 501]}, {}, {'status': ['new', 'adopted']}, {'other': ['x']}):
            with self.subTest(form=form):
                self.assertEqual(self.call(self.update_handler, card_id=cid, form=form).status_code, 400)
        self.assertEqual(self.call(self.update_handler, card_id='../etc', form={'status': ['new']}).status_code, 400)
        self.assertEqual(self.call(self.update_handler, card_id='0' * 16, form={'status': ['new']}).status_code, 404)
        for kw in ({'authenticated': False}, {'principal': False}, {'recheck': False}):
            self.assertEqual(self.call(self.update_handler, card_id=cid, form={'status': ['new']}, **kw), 403)
        self.assertEqual(store.get_card(cid)['status'], 'trying')

    def test_all_auth_gates_return_403_on_both_routes(self):
        for handler in (self.list_handler, self.detail_handler):
            for kw in ({'authenticated': False}, {'principal': False}, {'recheck': False}, {'username': ''}):
                with self.subTest(handler=handler, kw=kw):
                    self.assertEqual(self.call(handler, card_id='a' * 16, **kw), 403)
        req = request()
        req.state.password_recheck = 'not-callable'
        self.assertEqual(asyncio.run(self.list_handler(req)), 403)
        req = request()
        req.state.session = None
        self.assertEqual(asyncio.run(self.list_handler(req)), 403)

    def test_recheck_after_read_and_on_parameter_error(self):
        self.seed()
        for query in ({}, {'page': '0'}):
            req = request(query=query)
            seen_threads = []
            def recheck():
                seen_threads.append(threading.get_ident())
                return False
            req.state.password_recheck = recheck
            self.assertEqual(asyncio.run(self.list_handler(req)), 403)
            self.assertEqual(len(seen_threads), 1)
            self.assertNotEqual(seen_threads[0], threading.get_ident())

    def test_missing_database_is_empty_ready_false_and_never_created(self):
        response = self.call()
        body = json.loads(response.body)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(body['items'], [])
        self.assertEqual(body['total'], 0)
        self.assertFalse(body['ready'])
        self.assertEqual(body['counts']['total'], 0)
        detail = self.call(self.detail_handler, card_id='a' * 16)
        self.assertEqual(json.loads(detail.body), {'item': None, 'ready': False})
        self.assertFalse(self.path.exists())
        self.assertEqual(response.headers['cache-control'], 'no-store')
        self.assertEqual(response.headers['x-content-type-options'], 'nosniff')

    def test_invalid_parameters_return_400_json_even_if_database_missing(self):
        for query in ({'relevance': 'bad'}, {'status': 'bad'}, {'page': '0'}, {'page': '-1'},
                      {'page': '1.5'}, {'page': 'x'}, {'page_size': '101'}, {'page_size': '0'},
                      {'page_size': 'NaN'}, {'q': '字' * 501}, {'page': '9' * 5000}):
            with self.subTest(query=query):
                response = self.call(query=query)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(set(json.loads(response.body)), {'error'})
                self.assertEqual(response.headers['cache-control'], 'no-store')
        self.assertEqual(self.call(self.detail_handler, card_id='bad-id').status_code, 400)

    def test_list_pagination_filters_counts_and_public_field_contract(self):
        _, first, second, third = self.seed()
        response = self.call(query={'page': '2', 'page_size': '1'})
        body = json.loads(response.body)
        self.assertEqual((body['total'], body['page'], body['page_size']), (3, 2, 1))
        self.assertTrue(body['ready'])
        self.assertEqual(body['items'][0]['card_id'], second['card_id'])
        self.assertEqual(body['counts']['total'], 3)
        body = json.loads(self.call(query={'relevance': 'ai_dev', 'status': 'trying', 'q': 'DemoKit'}).body)
        self.assertEqual(body['total'], 1)
        item = body['items'][0]
        self.assertEqual(item['card_id'], first['card_id'])
        self.assertEqual(set(item), set(api._FIELDS))
        self.assertNotIn('source_message_ids', item)
        self.assertNotIn('enrich_model', item)
        self.assertTrue(item['enriched'])
        self.assertEqual(body['counts']['total'], 3)  # global top-of-page counters
        self.assertEqual(json.loads(self.call(query={'relevance': 'business'}).body)['total'], 1)
        self.assertEqual(json.loads(self.call(query={'q': '%'}).body)['total'], 0)

    def test_detail_and_missing_id(self):
        _, first, _, _ = self.seed()
        response = self.call(self.detail_handler, card_id=first['card_id'])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.body)['item']['card_id'], first['card_id'])
        response = self.call(self.detail_handler, card_id='a' * 16)
        self.assertEqual(response.status_code, 404)
        self.assertIn('error', json.loads(response.body))

    def test_store_reads_run_in_worker_thread(self):
        self.seed()
        original = InspirationStore.list_cards
        worker_threads = []
        def tracked(store, **kw):
            worker_threads.append(threading.get_ident())
            return original(store, **kw)
        with patch.object(InspirationStore, 'list_cards', tracked):
            self.assertEqual(self.call().status_code, 200)
        self.assertEqual(len(worker_threads), 1)
        self.assertNotEqual(worker_threads[0], threading.get_ident())

    def test_database_errors_are_generic_and_do_not_expose_paths_or_raw_exceptions(self):
        self.path.write_text('虚构的损坏数据库', encoding='utf8')
        response = self.call()
        self.assertEqual(response.status_code, 503)
        self.assertNotIn(str(self.path), response.body.decode())
        self.assertNotIn('database', response.body.decode())
        self.seed_after_remove()
        with patch.object(InspirationStore, 'list_cards', side_effect=ValueError('/fake/private/path')):
            response = self.call()
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('/fake/private/path', response.body.decode())

    def seed_after_remove(self):
        self.path.unlink()
        self.seed()

    def test_only_the_update_route_writes(self):
        self.assertEqual([r.path for r in self.routes], ['/api/v1/inspiration', '/api/v1/inspiration/{card_id}',
                                                         '/api/v1/inspiration/{card_id}/update'])
        for route in self.routes[:2]:
            self.assertEqual(route.methods - {'HEAD'}, {'GET'})
        self.assertEqual(self.routes[2].methods, {'POST'})


if __name__ == '__main__':
    unittest.main()
