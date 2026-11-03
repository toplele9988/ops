"""Authenticated endpoint tests using fictional SQLite sources and fake AI only."""
import asyncio
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from urllib.parse import urlencode

from console.inbox import drafts, store
from console.inbox.model import make_key
from console.inspiration.enrich import RunnerOutput
from inbox_fixtures import NOW, message, paths, request, seed

try:
    from console import v3_inbox_api as inbox, v3_customers_api as customers, v3_channels_api as channels
except ModuleNotFoundError as exc:
    if exc.name != 'starlette' and not (exc.name or '').startswith('starlette.'):
        raise
    inbox = customers = channels = None


@unittest.skipIf(inbox is None, 'starlette not installed')
class ApiTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.inbound, self.drafts, self.wechat, self.whatsapp = paths(temp.name)
        self.key = make_key('conversation', 'alibaba', 'fictional-alpha', 'conv-a')
        self.customer_key = make_key('customer', 'alibaba', 'fictional-alpha', 'Fictional Alice')
        self.runner = Mock(return_value=RunnerOutput(json.dumps(dict(draft='Fictional reply 【待确认：交期】',
            language='en', needs_confirmation=['交期待确认'])), 'fictional-model'))
        self.app = SimpleNamespace(error_response=lambda req, code: code)
        self.routes = (inbox.build(self.app, self.inbound, self.drafts, runner=self.runner)
            + customers.build(self.app, self.inbound)
            + channels.build(self.app, self.inbound, self.wechat, self.whatsapp))
        self.handlers = {r.name: r.endpoint for r in self.routes}
        clock = patch('time.time', return_value=NOW)
        clock.start()
        self.addCleanup(clock.stop)
        limiter = patch.object(drafts, 'LIMITER', drafts.DraftLimiter())
        limiter.start()
        self.addCleanup(limiter.stop)

    def call(self, kind='inbox_summary', **kwargs):
        kwargs.setdefault('key', self.customer_key if kind == 'customers_detail' else self.key)
        return asyncio.run(self.handlers[kind](request(**kwargs)))

    def data(self, kind='inbox_summary', **kwargs):
        result = self.call(kind, **kwargs)
        self.assertEqual(result.status_code, 200, result.body.decode())
        self.assertEqual(result.headers['cache-control'], 'no-store')
        self.assertEqual(result.headers['x-content-type-options'], 'nosniff')
        return json.loads(result.body)

    def test_routes_methods_and_auth_all_endpoints(self):
        seed(self.inbound)
        self.assertEqual(len(self.routes), 11)
        for route in self.routes:
            self.assertEqual(route.methods - {'HEAD'}, {'POST'} if route.name in (
                'inbox_draft', 'inbox_draft_status', 'inbox_ops_action', 'inbox_ops_supplier_draft') else {'GET'})
        for kind in self.handlers:
            for options in ({'authenticated': False}, {'principal': False}, {'username': ''}, {'recheck': False}):
                with self.subTest(kind=kind, options=options):
                    self.assertEqual(self.call(kind, form={'status': ['used']}, **options), 403)
            for field, value in (('password_recheck', None), ('password_recheck', 'not callable'),
                                 ('session', None), ('session', SimpleNamespace())):
                req = request(key=self.key)
                setattr(req.state, field, value)
                self.assertEqual(asyncio.run(self.handlers[kind](req)), 403)
            req = request(key=self.key)
            req.state.password_recheck = Mock(side_effect=RuntimeError('fictional private exception'))
            self.assertEqual(asyncio.run(self.handlers[kind](req)), 403)
        self.runner.assert_not_called()
        self.assertFalse(self.drafts.parent.exists())

    def test_post_recheck_before_body_runner_and_any_write(self):
        seed(self.inbound)
        self.data('inbox_draft')
        self.runner.reset_mock()
        before = self.drafts.read_bytes()
        for kind in ('inbox_draft', 'inbox_draft_status'):
            req = request(key=self.key, recheck=False)
            async def forbidden_body():
                self.fail('unauthorized POST read body')
            req.body = forbidden_body
            with patch.object(drafts, 'save_draft', side_effect=AssertionError('must not write')), \
                    patch.object(drafts, 'set_status', side_effect=AssertionError('must not write')):
                self.assertEqual(asyncio.run(self.handlers[kind](req)), 403)
        self.runner.assert_not_called()
        self.assertEqual(self.drafts.read_bytes(), before)
        self.assertEqual(self.data()['draft_new'], 1)

    def test_get_recheck_after_read_error_missing_and_invalid_parameter(self):
        seed(self.inbound)
        events = []
        cache = store.inbound_cache(self.inbound)
        original = cache.read
        def read():
            events.append('read')
            return original()
        req = request()
        req.state.password_recheck = lambda: events.append('recheck') or False
        with patch.object(cache, 'read', read):
            self.assertEqual(asyncio.run(self.handlers['inbox_summary'](req)), 403)
        self.assertEqual(events, ['read', 'read', 'recheck'])
        for kind in ('inbox_conversations', 'customers_list'):
            self.assertEqual(self.call(kind, recheck=False, query={'page': '0'}), 403)
        self.inbound.unlink()
        self.assertEqual(self.call(recheck=False), 403)
        self.inbound.write_text('fictional damaged DB')
        self.assertEqual(self.call(recheck=False), 403)

    def test_summary_aggregation_waiting_sort_hours_q_filters_and_pagination(self):
        seed(self.inbound)
        summary = self.data()
        self.assertEqual((summary['waiting_total'], summary['new_24h'], summary['draft_new']), (3, 6, 0))
        self.assertEqual([c['conversations'] for c in summary['channels']], [4, 1])
        self.assertEqual(sum(c['messages'] for c in summary['channels']), 7)
        rows = self.data('inbox_conversations')['items']
        self.assertEqual([r['waiting_reply'] for r in rows], [True, True, True, False, False])
        self.assertEqual(rows[0]['account'], 'fictional-beta')
        self.assertEqual(rows[1]['key'], self.key)
        self.assertEqual(rows[1]['waiting_hours'], 2.0)
        self.assertIsNone(rows[-1]['waiting_hours'])
        self.assertEqual(self.data('inbox_conversations', query={'state': 'waiting'})['total'], 3)
        self.assertEqual(self.data('inbox_conversations', query={'state': 'replied'})['total'], 2)
        for query, total in (({'q': 'aLiCe'}, 3), ({'q': 'SELLER answer'}, 1), ({'q': '%'}, 0),
                             ({'channel': 'wechat'}, 0), ({'account': 'fictional-beta'}, 1)):
            self.assertEqual(self.data('inbox_conversations', query=query)['total'], total)
        page = self.data('inbox_conversations', query={'page': '2', 'page_size': '2'})
        self.assertEqual(page['items'], rows[2:4])
        self.assertEqual((page['total'], page['page'], page['page_size']), (5, 2, 2))
        self.assertEqual(self.data('inbox_conversations', query={'page': '10'})['items'], [])
        self.assertEqual(self.data('inbox_conversations')['page_size'], 30)
        self.assertFalse(self.drafts.parent.exists())

    def test_invalid_parameters_are_400_for_both_lists(self):
        from starlette.datastructures import QueryParams
        for kind in ('inbox_conversations', 'customers_list'):
            for params in ({'page': '0'}, {'page': '-1'}, {'page': '1.5'}, {'page': '字'},
                           {'page': '9' * 5000}, {'page_size': '101'}, {'page_size': '0'},
                           {'page_size': 'NaN'}, {'state': 'bad'}, {'state': ''}, {'channel': 'bad'},
                           {'q': '字' * 501}, {'account': 'a' * 1001}, {'q': 1},
                           QueryParams('state=all&state=waiting')):
                with self.subTest(kind=kind, params=str(params)[:50]):
                    result = self.call(kind, query=params)
                    self.assertEqual(result.status_code, 400)
                    self.assertEqual(set(json.loads(result.body)), {'error'})
                    self.assertEqual(result.headers['cache-control'], 'no-store')

    def test_invalid_and_missing_keys(self):
        seed(self.inbound)
        for kind in ('inbox_detail', 'inbox_draft', 'customers_detail'):
            for key in ('!!!', '..', 'a', 'x' * 9000, self.key + '='):
                self.assertEqual(self.call(kind, key=key).status_code, 400)
            identity = 'customer' if kind == 'customers_detail' else 'conversation'
            missing = make_key(identity, 'alibaba', 'fictional-missing', 'missing')
            self.assertEqual(self.call(kind, key=missing).status_code, 404)
        self.runner.assert_not_called()
        self.assertFalse(self.drafts.parent.exists())

    def test_detail_full_chronology_and_https_only(self):
        seed(self.inbound, [message(age=400 - i, message_id=f'virtual-{i}', text='字' * 4200)
                            for i in range(310)])
        data = self.data('inbox_detail')
        self.assertEqual(data['conversation']['n_messages'], 310)
        self.assertEqual(len(data['messages']), 310)
        self.assertEqual(data['messages'][0]['sent_at'], NOW - 400)
        self.assertEqual(len(data['messages'][0]['text']), 4200)
        self.assertEqual(data['conversation']['source_url'], 'https://example.invalid/chat')
        self.assertIsNone(data['draft'])
        self.assertEqual(len(self.data('inbox_conversations')['items'][0]['last']['text']), 140)
        for url in ('http://example.invalid', 'javascript:alert(1)', 'file:///fictional/private', '//example.invalid', ''):
            seed(self.inbound, [{**message(age=1, message_id=f'virtual-url-{url}'), 'url': url}])
            self.assertIsNone(self.data('inbox_detail')['conversation']['source_url'])

    def test_corrupt_rows_skipped_and_full_last_text_search(self):
        seed(self.inbound, ['broken JSON', '{}', 'null', {**message(), 'sent_at': 'bad'},
                            {**message(), 'buyer_name': None}, message(text='a' * 200 + 'FictionalNeedle')])
        self.assertEqual(self.data('inbox_conversations', query={'q': 'fictionalneedle'})['total'], 1)
        self.assertEqual(self.data()['channels'][0]['messages'], 1)

    def test_all_reads_and_draft_writes_leave_inbound_bytes_unchanged(self):
        seed(self.inbound)
        before, stamp = self.inbound.read_bytes(), self.inbound.stat().st_mtime_ns
        original = store.sqlite3.connect
        with patch.object(store.sqlite3, 'connect', wraps=original) as connect:
            for kind in self.handlers:
                if 'POST' not in next(r for r in self.routes if r.name == kind).methods:
                    self.data(kind)
            self.data('inbox_draft')
            self.data('inbox_draft_status', form={'status': ['used']})
        source_calls = [c for c in connect.call_args_list if str(self.inbound) in str(c.args[0])]
        self.assertEqual(len(source_calls), 1)
        self.assertTrue(all(c.args[0].endswith('?mode=ro') and c.kwargs['uri'] for c in source_calls))
        self.assertEqual(self.inbound.read_bytes(), before)
        self.assertEqual(self.inbound.stat().st_mtime_ns, stamp)

    def test_draft_success_prompt_recent_twenty_isolated_model_persistence(self):
        rows = [message(text=f'fictional-private-{i:03}', age=100 - i, message_id=f'virtual-{i}') for i in range(25)]
        rows.append(message('virtual-other', text='fictional-other-secret', message_id='other'))
        seed(self.inbound, rows)
        events = []
        req = request(key=self.key, form={})
        req.state.password_recheck = lambda: events.append('recheck') or True
        self.runner.side_effect = lambda prompt: events.append('runner') or self.runner.return_value
        result = asyncio.run(self.handlers['inbox_draft'](req))
        self.assertEqual(result.status_code, 200)
        self.assertEqual(events, ['recheck', 'runner'])
        prompt = self.runner.call_args.args[0]
        messages = prompt.split('对话记录：\n')[1].split('\n')
        self.assertEqual(len(messages), 20)
        self.assertTrue(messages[0].endswith('fictional-private-005'))
        self.assertNotIn('fictional-other-secret', prompt)
        self.assertNotIn('fictional-private-004', prompt)
        self.assertIn('不得编造', prompt)
        self.assertIn('【待确认', prompt)
        draft = json.loads(result.body)['draft']
        self.assertEqual(draft['model'], 'fictional-model')
        self.assertEqual(draft['needs_confirmation'], ['交期待确认'])
        self.assertEqual(draft['created_at'], NOW)
        self.assertEqual(self.data()['draft_new'], 1)
        self.assertTrue(next(r for r in self.data('inbox_conversations')['items'] if r['key'] == self.key)['has_draft'])
        self.assertEqual(self.data('inbox_detail')['draft']['id'], draft['id'])
        fresh = inbox.build(self.app, self.inbound, self.drafts, runner=self.runner)
        detail = asyncio.run(next(r.endpoint for r in fresh if r.name == 'inbox_detail')(request(key=self.key)))
        self.assertEqual(json.loads(detail.body)['draft']['id'], draft['id'])

    def test_fenced_overlong_and_envelope_ai_outputs(self):
        seed(self.inbound)
        expected = dict(draft='字' * 2500, needs_confirmation=['事' * 100] * 12, language='en')
        for raw in ('```json\n' + json.dumps(expected) + '\n```',
                    json.dumps({'answer': json.dumps(expected), 'actual_model': 'fictional-envelope'})):
            with patch.object(drafts, 'LIMITER', drafts.DraftLimiter()):
                self.runner.return_value = raw
                result = self.data('inbox_draft')['draft']
                self.assertEqual(len(result['text']), 2000)
                self.assertEqual(len(result['needs_confirmation']), 10)
                self.assertEqual(len(result['needs_confirmation'][0]), 80)

    def test_missing_fields_non_json_and_runner_exception_502_no_db_no_retry(self):
        seed(self.inbound)
        for raw in ('not JSON', '{}', json.dumps({'draft': 'Incomplete'}),
                    json.dumps({'draft': 'Incomplete', 'needs_confirmation': []})):
            with patch.object(drafts, 'LIMITER', drafts.DraftLimiter()):
                self.runner.return_value = raw
                result = self.call('inbox_draft')
                self.assertEqual(result.status_code, 502)
                self.assertEqual(json.loads(result.body), {'error': 'AI 暂时没有给出可用草稿，请稍后再试'})
                self.assertFalse(self.drafts.parent.exists())
        self.runner.reset_mock()
        self.runner.side_effect = RuntimeError('fictional-private-exception /fictional/private')
        result = self.call('inbox_draft')
        self.assertEqual(result.status_code, 502)
        self.assertEqual(self.runner.call_count, 1)
        self.assertNotIn('fictional-private', result.body.decode())
        self.assertFalse(self.drafts.parent.exists())

    def test_per_conversation_global_and_cross_build_rate_limits(self):
        seed(self.inbound)
        with patch('time.monotonic', return_value=10000):
            self.data('inbox_draft')
        with patch('time.monotonic', return_value=10010):
            result = self.call('inbox_draft')
            self.assertEqual(result.status_code, 429)
            self.assertEqual(json.loads(result.body)['retry_after'], 50)
            self.assertEqual(result.headers['retry-after'], '50')
            rebuilt = inbox.build(self.app, self.inbound, self.drafts, runner=self.runner)
            self.assertEqual(asyncio.run(next(r.endpoint for r in rebuilt if r.name == 'inbox_draft')(request(key=self.key))).status_code, 429)
        with patch('time.monotonic', return_value=10060):
            self.data('inbox_draft')
        self.assertEqual(self.runner.call_count, 2)
        with patch.object(drafts, 'LIMITER', drafts.DraftLimiter()), patch('time.monotonic', return_value=20000):
            for i in range(31):
                conv = f'virtual-global-{i}'
                seed(self.inbound, [message(conv, message_id=f'virtual-global-message-{i}')])
                key = make_key('conversation', 'alibaba', 'fictional-alpha', conv)
                result = self.call('inbox_draft', key=key)
                self.assertEqual(result.status_code, 200 if i < 30 else 429)

    def test_middleware_form_fallback_status_update_and_bad_forms(self):
        seed(self.inbound)
        req = request(key=self.key, form={})
        async def forbidden_body():
            self.fail('middleware form must avoid rereading the body')
        req.body = forbidden_body
        self.assertEqual(asyncio.run(self.handlers['inbox_draft'](req)).status_code, 200)
        self.data('inbox_draft_status', body=urlencode({'status': 'used'}).encode())
        self.assertEqual(self.data()['draft_new'], 0)
        self.assertEqual(self.data('inbox_detail')['draft']['status'], 'used')
        self.data('inbox_draft_status', form={'status': ['discarded']})
        self.assertIsNone(self.data('inbox_detail')['draft'])
        self.assertFalse(next(c for c in self.data('inbox_conversations')['items'] if c['key'] == self.key)['has_draft'])
        self.assertEqual(self.call('inbox_draft_status', draft_id='999', form={'status': ['used']}).status_code, 404)
        for form in ({}, {'status': ['new']}, {'status': ['sent']}, {'status': ['used', 'discarded']},
                     {'status': 'used'}, {'status': [None]}):
            self.assertEqual(self.call('inbox_draft_status', form=form).status_code, 400)
        for draft_id in ('0', '-1', 'bad', '9' * 100):
            self.assertEqual(self.call('inbox_draft_status', draft_id=draft_id, form={'status': ['used']}).status_code, 400)
        self.assertEqual(self.call('inbox_draft', body=b'x=1&' * 17).status_code, 400)

    def test_customers_contract_filters_recent_sort_and_shared_cache_invalidation(self):
        seed(self.inbound)
        cache = store.inbound_cache(self.inbound)
        with patch.object(cache, '_load', wraps=cache._load) as load:
            self.data()
            listing = self.data('customers_list')
            self.assertEqual(listing['total'], 4)
            rows = listing['items']
            self.assertEqual([r['last_seen'] for r in rows], sorted((r['last_seen'] for r in rows), reverse=True))
            alice = self.data('customers_detail')
            self.assertEqual((alice['n_conversations'], alice['n_messages']), (2, 3))
            self.assertEqual(len(alice['conversations']), 2)
            self.assertTrue(alice['waiting_reply'])
            self.assertEqual(self.data('customers_list', query={'q': 'ALICE'})['total'], 2)
            self.assertEqual(self.data('customers_list', query={'state': 'waiting'})['total'], 2)
            self.assertEqual(self.data('customers_list', query={'state': 'replied'})['total'], 2)
            self.assertEqual(self.data('customers_list', query={'account': 'fictional-beta'})['total'], 1)
            self.assertEqual(self.data('customers_list', query={'page': '2', 'page_size': '1'})['items'], rows[1:2])
            self.data('system_channels')
            self.assertEqual(load.call_count, 1)
            seed(self.inbound, [message('virtual-new', buyer='Fictional New', message_id='virtual-new-message', age=1)])
            self.assertEqual(self.data('customers_list')['total'], 5)
            self.assertEqual(load.call_count, 2)

    def test_channel_counters_state_boundaries_notes_and_source_readonly(self):
        seed(self.inbound, [message(account='fictional-current', age=86400),
                            message(account='fictional-quiet', age=86401),
                            message(account='fictional-week', age=7 * 86400),
                            message(account='fictional-stale', age=7 * 86400 + 1)])
        with closing(sqlite3.connect(self.wechat)) as db, db:
            db.execute('CREATE TABLE messages (created_at REAL)')
            db.executemany('INSERT INTO messages VALUES (?)', [(NOW - 10,), (NOW - 86400 - 1,), (NOW - 100,)])
        self.whatsapp.mkdir()
        wa = self.whatsapp / 'fictional-events.sqlite3'
        with closing(sqlite3.connect(wa)) as db, db:
            db.execute('CREATE TABLE events (event_key TEXT PRIMARY KEY, received REAL)')
            db.executemany('INSERT INTO events VALUES (?, ?)', [('virtual-1', NOW), ('virtual-2', NOW)])
        before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in (self.inbound, self.wechat, wa)}
        data = self.data('system_channels')
        ali, wx, whatsapp = data['channels']
        self.assertTrue(all(c['ready'] for c in data['channels']))
        self.assertEqual(data['generated_at'], NOW)
        self.assertEqual({a['name']: a['state'] for a in ali['accounts']}, dict(
            **{'fictional-current': 'ok', 'fictional-quiet': 'quiet', 'fictional-week': 'quiet', 'fictional-stale': 'stale'}))
        self.assertTrue(any('共有 6 个账号' in n and '当前只有 4 个账号有数据' in n
                            for n in data['notes']))
        self.assertNotIn('其余账号的询盘不会出现在系统里', json.dumps(data, ensure_ascii=False))
        self.assertEqual((wx['accounts'][0]['messages'], wx['messages_24h'], wx['last_message_at']), (3, 2, NOW - 10))
        self.assertEqual(whatsapp['accounts'][0]['messages'], 2)
        self.assertIsNone(whatsapp['messages_24h'])
        explicit = channels.build(self.app, self.inbound, self.wechat, wa)
        self.assertTrue(json.loads(asyncio.run(explicit[0].endpoint(request())).body)['channels'][2]['ready'])
        for p, (content, mtime) in before.items():
            self.assertEqual(p.read_bytes(), content)
            self.assertEqual(p.stat().st_mtime_ns, mtime)

    def test_missing_sources_ready_false_no_read_side_creation(self):
        self.assertFalse(self.data()['ready'])
        for kind in ('inbox_conversations', 'customers_list'):
            data = self.data(kind)
            self.assertFalse(data['ready'])
            self.assertEqual(data['items'], [])
        self.assertTrue(all(not c['ready'] for c in self.data('system_channels')['channels']))
        self.assertEqual(self.call('inbox_detail').status_code, 404)
        self.assertEqual(self.call('inbox_draft').status_code, 404)
        self.assertEqual(self.call('inbox_draft_status', form={'status': ['used']}).status_code, 404)
        self.assertTrue(any('当前只有 0 个账号有数据' in n for n in self.data('system_channels')['notes']))
        self.assertFalse(self.drafts.parent.exists())
        self.assertFalse(self.inbound.exists())
        self.assertFalse(self.wechat.exists())
        self.assertFalse(self.whatsapp.exists())

    def test_database_errors_have_fixed_messages_no_exception_or_path(self):
        self.inbound.write_text('fictional damaged source')
        for kind in ('inbox_summary', 'inbox_conversations', 'inbox_detail', 'customers_list', 'customers_detail', 'inbox_draft'):
            result = self.call(kind)
            self.assertEqual(result.status_code, 503)
            self.assertNotIn(str(self.inbound), result.body.decode())
            self.assertNotIn('database', result.body.decode())
        # One unavailable source does not hide the other channel states.
        self.assertFalse(self.data('system_channels')['channels'][0]['ready'])
        self.inbound.unlink()
        seed(self.inbound)
        self.drafts.parent.mkdir()
        self.drafts.write_text('fictional damaged state')
        self.assertEqual(self.call('inbox_summary').status_code, 503)
        self.assertEqual(self.call('inbox_draft').status_code, 503)

    def test_blocking_reads_rechecks_runner_and_writes_use_worker_threads(self):
        seed(self.inbound)
        main, workers = threading.get_ident(), []
        cache = store.inbound_cache(self.inbound)
        read, save = cache.read, drafts.save_draft
        def tracked_read():
            workers.append(threading.get_ident())
            return read()
        def tracked_save(*args):
            workers.append(threading.get_ident())
            return save(*args)
        def recheck():
            workers.append(threading.get_ident())
            return True
        self.runner.side_effect = lambda p: workers.append(threading.get_ident()) or self.runner.return_value
        with patch.object(cache, 'read', tracked_read), patch.object(drafts, 'save_draft', tracked_save):
            for kind in ('inbox_summary', 'inbox_draft'):
                req = request(key=self.key)
                req.state.password_recheck = recheck
                self.assertEqual(asyncio.run(self.handlers[kind](req)).status_code, 200)
        self.assertGreaterEqual(len(workers), 6)
        self.assertTrue(all(t != main for t in workers))


if __name__ == '__main__':
    unittest.main()
