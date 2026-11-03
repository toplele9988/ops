"""Starlette endpoint contracts; no HTTP server or real gateway is used."""
import asyncio
from contextlib import closing
import json
import sqlite3
import threading
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.parse import urlencode
import unittest

from console.outbox.model import DISABLED, UNKNOWN
from outbox_fixtures import CHAT, SECRET, IsolatedTest, fake_stat, form, request, seed_config

try:
    from console import v3_inbox_api, v3_outbox_api
    from starlette.responses import JSONResponse
except ModuleNotFoundError as exc:
    if exc.name != 'starlette' and not (exc.name or '').startswith('starlette.'):
        raise
    v3_inbox_api = v3_outbox_api = None


@unittest.skipIf(v3_outbox_api is None, 'starlette not installed')
class ApiTests(IsolatedTest):
    def setUp(self):
        super().setUp()
        self.app = SimpleNamespace(error_response=lambda req, code: JSONResponse({'error': '无权访问'}, status_code=code))
        self.routes = v3_outbox_api.build(self.app, outbox=self.service)
        self.handlers = {r.name: r.endpoint for r in self.routes}
        self.handlers.update({r.name: r.endpoint for r in v3_inbox_api.build(self.app,
            inbound_path=self.root / 'fictional-inbound.sqlite3', drafts_path=self.root / 'drafts.sqlite3',
            inbox=self.hub, outbox=self.service, runner=Mock(side_effect=AssertionError('AI not needed')))})

    def call(self, name='outbox_send', *, fields=None, **options):
        options.setdefault('key', self.key)
        if fields is not None:
            options['form_data'] = {k: [v] for k, v in fields.items()}
        return asyncio.run(self.handlers[name](request(**options)))

    def data(self, name='outbox_send', **options):
        result = self.call(name, **options)
        self.assertEqual(result.status_code, 200, result.body.decode())
        self.assertEqual(result.headers['cache-control'], 'no-store')
        self.assertEqual(result.headers['x-content-type-options'], 'nosniff')
        return json.loads(result.body)

    def test_routes_and_all_authentication_gates_403_before_any_write_or_network(self):
        self.assertEqual(len(self.routes), 7)
        for route in self.routes:
            self.assertEqual(route.methods - {'HEAD'}, {'POST'} if route.name in (
                'outbox_send', 'outbox_settings_update', 'outbox_attachment_candidate',
                'outbox_attachment_send') else {'GET'})
        for route in self.routes:
            for options in ({'authenticated': False}, {'principal': False}, {'username': ''}, {'recheck': False}):
                result = self.call(route.name, fields=form(), **options)
                self.assertEqual(result.status_code, 403)
                self.assertEqual(result.headers['cache-control'], 'no-store')
                self.assertEqual(result.headers['x-content-type-options'], 'nosniff')
            for name, value in [('password_recheck', None), ('password_recheck', 'not callable'),
                                ('password_recheck', Mock(side_effect=RuntimeError(SECRET))), ('session', None)]:
                req = request(self.key)
                setattr(req.state, name, value)
                self.assertEqual(asyncio.run(self.handlers[route.name](req)).status_code, 403)
        self.assertFalse(self.path.exists())
        self.assertEqual(self.net.calls, [])

    def test_post_recheck_before_body_and_database_or_gateway_mutations(self):
        for existing in (False, True):
            if existing:
                self.enable()
            before = self.path.read_bytes() if self.path.exists() else None
            for name in ('outbox_send', 'outbox_settings_update', 'outbox_attachment_candidate', 'outbox_attachment_send'):
                req = request(self.key, recheck=False)
                async def bad_body():
                    self.fail('must not read unauthorized body')
                req.body = bad_body
                with patch.object(self.service.store, '_write', side_effect=AssertionError('must not write')):
                    self.assertEqual(asyncio.run(self.handlers[name](req)).status_code, 403)
            self.assertEqual(self.path.read_bytes() if self.path.exists() else None, before)
        self.assertEqual(self.net.calls, [])

    def test_get_recheck_after_reads_and_after_invalid_or_unavailable_results(self):
        events = []
        original = self.service.recent
        def recent(key=None):
            events.append('read')
            return original(key)
        # GET closures look up the shared service dynamically.
        req = request(self.key)
        req.state.password_recheck = lambda: events.append('recheck') or False
        with patch.object(self.service, 'recent', recent):
            self.assertEqual(asyncio.run(self.handlers['outbox_recent'](req)).status_code, 403)
        self.assertEqual(events, ['read', 'recheck'])
        self.assertEqual(self.call('outbox_list', recheck=False, query={'conversation': '!!!'}).status_code, 403)
        with patch.object(self.service, 'settings', side_effect=RuntimeError(SECRET + '/fictional/private')):
            self.assertEqual(self.call('outbox_settings', recheck=False).status_code, 403)

    def test_settings_post_forms_cached_and_parse_qs_fallback_and_error_headers(self):
        self.assertFalse(self.data('outbox_settings')['channels'][0]['enabled'])
        self.assertFalse(self.path.exists())
        result = self.data('outbox_settings_update', fields=dict(channel='whatsapp', enabled='1'))
        self.assertTrue(result['channels'][0]['enabled'])
        for channel in ('alibaba', 'wechat', 'website'):
            response = self.call('outbox_settings_update', fields=dict(channel=channel, enabled='1'))
            self.assertEqual(response.status_code, 400)
            self.assertIn('reason', json.loads(response.body))
            self.assertEqual(response.headers['cache-control'], 'no-store')
        result = self.data('outbox_settings_update', body=b'channel=whatsapp&enabled=0')
        self.assertFalse(result['channels'][0]['enabled'])
        from starlette.datastructures import FormData
        self.data('outbox_settings_update', form_data=FormData({'channel': 'whatsapp', 'enabled': '1'}))
        for raw in (b'channel=whatsapp&channel=wechat&enabled=1', b'&'.join([b'x=y'] * 17)):
            self.assertEqual(self.call('outbox_settings_update', body=raw).status_code, 400)
        for cached in ({'channel': 'whatsapp'}, {'channel': ['whatsapp', 'wechat']}, {'enabled': [1]}):
            self.assertEqual(self.call('outbox_settings_update', form_data=cached).status_code, 400)
        self.assertEqual(self.net.calls, [])

    def test_send_response_success_failure_unknown_replay_and_retry_after(self):
        self.enable()
        first = self.data(fields=form())
        self.assertEqual(first['message']['state'], 'sent')
        self.assertEqual(self.data(body=urlencode(form()).encode()), first)
        self.assertEqual(len(self.net.sends), 1)
        self.net.answers = [(409, {'error': 'recipient_not_bound'})]
        failed = self.data(fields=form(request_id='fictional-api-failed'))
        self.assertEqual(failed['message']['state'], 'failed')
        self.assertTrue(failed['needs_new_recipient_confirmation'])
        self.assertEqual(failed['error_text'], '这位联系人还没有绑定')
        self.net.answers = [TimeoutError(SECRET)]
        uncertain = self.data(fields=form(request_id='fictional-api-timeout'))
        self.assertEqual(uncertain['message']['state_text'], UNKNOWN)
        self.assertEqual(self.data(fields=form(request_id='fictional-api-timeout')), uncertain)
        for i in range(2):
            self.data(fields=form(request_id=f'fictional-api-quota-{i}'))
        response = self.call(fields=form(request_id='fictional-api-quota-extra'))
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.headers['retry-after'], '60')
        self.assertEqual(json.loads(response.body)['retry_after'], 60)

    def test_validation_chinese_and_no_outbox_rows_or_gateway_calls(self):
        response = self.call(fields=form())
        self.assertEqual((response.status_code, json.loads(response.body)['error']), (403, DISABLED))
        self.enable()
        for fields, phrase in [(form(confirm='0'), '确认发送'), (form(text='【待确认：虚构】'), '草稿里还有'),
                               (form(request_id=''), '请求标识'), (form(text=' '), '1–4000')]:
            response = self.call(fields=fields)
            self.assertEqual(response.status_code, 400)
            self.assertIn(phrase, json.loads(response.body)['error'])
        response = self.call(fields=form(), key=self.hub.key('alibaba'))
        self.assertEqual(response.status_code, 400)
        self.assertIn('该渠道暂不支持后台发送', json.loads(response.body)['error'])
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM outbox').fetchone()[0], 0)
        self.assertEqual(self.net.calls, [])

    def test_inbox_can_send_send_state_and_copy_hint_follow_settings_and_config(self):
        detail = self.data('inbox_detail')
        self.assertFalse(detail['conversation']['can_send'])
        self.assertEqual(detail['send_state'], dict(enabled=False, available=True, reason=DISABLED))
        self.assertIn('复制', detail['conversation']['copy_hint'])
        self.enable()
        detail = self.data('inbox_detail')
        self.assertTrue(detail['conversation']['can_send'])
        self.assertEqual(detail['send_state'], dict(enabled=True, available=True, reason=''))
        self.assertIn('确认', detail['conversation']['copy_hint'])
        self.config.chmod(0o644)
        detail = self.data('inbox_detail')
        self.assertFalse(detail['conversation']['can_send'])
        self.assertEqual(detail['send_state']['enabled'], True)
        self.assertEqual(detail['send_state']['available'], False)
        self.assertIn('配置不可用', detail['send_state']['reason'])
        for channel in ('alibaba', 'wechat', 'website'):
            detail = self.data('inbox_detail', key=self.hub.key(channel))
            self.assertFalse(detail['conversation']['can_send'])
            self.assertFalse(detail['send_state']['enabled'])
            self.assertFalse(detail['send_state']['available'])
            self.assertIn('复制', detail['conversation']['copy_hint'])
        self.assertEqual(self.net.calls, [])

    def test_get_list_public_fields_and_bad_queries(self):
        self.enable()
        self.data(fields=form())
        rows = self.data('outbox_list', query={'conversation': self.key})['items']
        self.assertEqual(rows, self.data('outbox_recent')['items'])
        self.assertEqual(set(rows[0]), {'id', 'conversation_key', 'channel', 'text', 'state', 'state_text',
                                        'error_text', 'created_at'})
        for value in (SECRET, CHAT, 'idempotency_key', 'chat_ref'):
            self.assertNotIn(value, json.dumps(rows))
        from starlette.datastructures import QueryParams
        for params in ({}, {'conversation': '!!!'}, QueryParams('conversation=a&conversation=b')):
            result = self.call('outbox_list', query=params)
            self.assertEqual(result.status_code, 400)
            self.assertEqual(result.headers['x-content-type-options'], 'nosniff')

    def test_exceptions_sanitized_and_finished_send_never_reissued_after_db_failure(self):
        self.enable()
        with patch.object(self.service.store, 'finish', side_effect=RuntimeError(SECRET + '/fictional/private')):
            response = self.call(fields=form())
        self.assertEqual(response.status_code, 503)
        for value in (SECRET, '/fictional/private', CHAT):
            self.assertNotIn(value, response.body.decode())
        again = self.data(fields=form())
        self.assertEqual(again['message']['state'], 'sending')
        self.assertEqual(len(self.net.sends), 1)
        with patch.object(self.service.store, 'enabled', side_effect=RuntimeError(SECRET)):
            response = self.call('outbox_settings')
        self.assertEqual(response.status_code, 503)
        self.assertNotIn(SECRET, response.body.decode())
        with patch.object(self.service, 'reject', side_effect=RuntimeError(SECRET)):
            response = self.call(form_data={'text': ['a', 'b']})
        self.assertEqual(response.status_code, 503)
        self.assertNotIn(SECRET, response.body.decode())

    def test_build_parameters_pass_fake_transports_and_api_override(self):
        handlers = {r.name: r.endpoint for r in v3_outbox_api.build(self.app,
            inbox=self.hub, outbox_path=self.path, config_path=self.config, post=self.net.post, get=self.net.get,
            stat_fn=fake_stat, group_gid=731, clock=lambda: self.now)}
        setting = request(form_data={'channel': ['whatsapp'], 'enabled': ['1']})
        self.assertEqual(asyncio.run(handlers['outbox_settings_update'](setting)).status_code, 200)
        req = request(self.key, form_data={k: [v] for k, v in form().items()})
        self.assertEqual(json.loads(asyncio.run(handlers['outbox_send'](req)).body)['message']['state'], 'sent')
        self.assertEqual(len(self.net.sends), 1)

    def test_actual_whatsapp_adapter_and_two_builders_share_settings(self):
        from console.channels import build_inbox
        from outbox_fixtures import NOW, SESSION
        seed_config(self.config, waha_url='http://127.0.0.1:12345')
        fetch = Mock()
        def read_waha(method, url, headers, timeout):
            self.assertEqual((method, headers, timeout), ('GET', {'X-Api-Key': 'fictional-waha-key'}, 5))
            if '/api/sessions/' in url:
                return dict(status='WORKING', me={'id': 'fictional-owner@c.us'})
            if '/chats/overview?' in url:
                return [dict(id=CHAT, name='虚构联系人', lastMessage=dict(body='虚构询问', fromMe=False, timestamp=NOW))]
            if '/messages?' in url:
                return [dict(id='fictional-message', body='虚构询问', fromMe=False, timestamp=NOW)]
            self.fail('unexpected adapter URL')
        fetch.side_effect = read_waha
        inbound = self.root / 'inbound.sqlite3'
        hub = build_inbox(inbound, whatsapp_config_path=self.config, fetch=fetch,
                          stat_fn=fake_stat, group_gid=731)
        key = next(iter(hub.read()['conversations']))
        read_handlers = {r.name: r.endpoint for r in v3_inbox_api.build(self.app, inbound,
            self.root / 'drafts.sqlite3', inbox=hub, outbox_path=self.path, bridge=self.bridge)}
        send_handlers = {r.name: r.endpoint for r in v3_outbox_api.build(self.app, inbox=hub,
            outbox_path=self.path, config_path=self.config, post=self.net.post, get=self.net.get,
            stat_fn=fake_stat, group_gid=731)}
        self.assertFalse(json.loads(asyncio.run(read_handlers['inbox_detail'](request(key))).body)['conversation']['can_send'])
        asyncio.run(send_handlers['outbox_settings_update'](request(form_data={'channel': ['whatsapp'], 'enabled': ['1']})))
        self.assertTrue(json.loads(asyncio.run(read_handlers['inbox_detail'](request(key))).body)['conversation']['can_send'])
        result = asyncio.run(send_handlers['outbox_send'](request(key, form_data={k: [v] for k, v in form().items()})))
        self.assertEqual(json.loads(result.body)['message']['state'], 'sent')
        self.assertEqual(self.net.sends[0][2]['chat_id'], CHAT)

    def test_explicit_test_inbound_isolates_default_config_and_store(self):
        inbound = self.root / 'fictional-isolated.sqlite3'
        with patch('console.outbox.bridge_client.local_get', side_effect=AssertionError('default GET forbidden')) as get, \
             patch('console.outbox.bridge_client.local_post', side_effect=AssertionError('default POST forbidden')) as post:
            handlers = {r.name: r.endpoint for r in v3_outbox_api.build(self.app, inbound_path=inbound)}
            response = asyncio.run(handlers['outbox_settings'](request()))
            self.assertEqual(response.status_code, 200)
            self.assertFalse(json.loads(response.body)['channels'][0]['enabled'])
            self.assertFalse((self.root / 'outbox.sqlite3').exists())
            self.assertFalse((self.root / 'disabled-whatsapp.json').exists())
            get.assert_not_called()
            post.assert_not_called()
