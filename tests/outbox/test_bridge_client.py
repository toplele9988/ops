"""Actual gateway response shapes and urllib policy, with injected fakes only."""
from io import BytesIO
import json
import os
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.error import HTTPError

from console.outbox.bridge_client import (BRIDGE_API_URL, BridgeClient, NoRedirect, TIMEOUT,
    local_get, local_post, local_url)
from console.outbox.model import UNKNOWN
from outbox_fixtures import CHAT, SECRET, IsolatedTest, fake_stat, seed_config


class BridgeClientTests(IsolatedTest):
    def deliver(self, confirmed=False):
        self.audit = Mock()
        return self.bridge.deliver(CHAT, '虚构内容', 'ops-fictional-idempotency', confirmed, self.audit)

    def test_actual_accepted_rejected_replayed_and_inflight_shapes(self):
        for state, code, expected in [
            ('accepted', None, ('sent', None)),
            ('rejected', 'provider_http_400', ('failed', 'provider_http_400')),
            ('rejected', 'provider_http_401', ('failed', 'provider_http_401')),
            ('unknown', 'send_outcome_unknown', ('unknown', 'send_outcome_unknown')),
            ('in_flight', None, ('unknown', 'send_outcome_unknown')),
            ('rejected', SECRET, ('unknown', 'send_outcome_unknown')),
            ('sent', None, ('unknown', 'send_outcome_unknown')),
        ]:
            with self.subTest(state=state, code=code):
                self.net.answers = [lambda packet: (200, dict(idempotency_key=packet['idempotency_key'],
                                                              state=state, error_code=code, replayed=True))]
                self.assertEqual(self.deliver(), expected)
        self.assertEqual(len(self.net.sends), 7)
        self.assertEqual(self.net.binds, [])

    def test_transport_errors_server_errors_redirect_and_bad_payload_are_unknown_once(self):
        for response in [TimeoutError(SECRET), ConnectionError(CHAT), OSError('/fictional/private/path'),
                         ValueError('invalid json ' + SECRET), (500, {'error': 'text_invalid'}),
                         (503, {'error': 'request_not_completed'}), (302, {'error': 'text_invalid'}),
                         (400, {'error': 'unrecognised_refusal'}), (400, {'error': {'secret': SECRET}}),
                         (200, {'state': 'accepted'}), (200, {'idempotency_key': 'different', 'state': 'accepted'}),
                         (200, {'idempotency_key': 'ops-fictional-idempotency', 'state': 'accepted', 'error': 'text_invalid'}),
                         (200, []), (200, None), 'invalid', (200, {'error': 'recipient_not_bound'}),
                         (200, {'idempotency_key': 'ops-fictional-idempotency', 'state': 'rejected'})]:
            with self.subTest(response=str(response)[:60]):
                before = len(self.net.sends)
                self.net.answers = [response]
                self.assertEqual(self.deliver(True), ('unknown', 'send_outcome_unknown'))
                self.assertEqual(len(self.net.sends), before + 1)
                self.assertEqual(self.net.binds, [])

    def test_binding_get_must_match_session_and_account_and_never_sends_on_failure(self):
        for response in [(200, {'bound': False}), (200, {'session': 'other', 'account_id': 'fictional@c.us'}),
                         (200, {'session': 'fictional-session', 'account_id': '../bad'}),
                         (503, {'error': 'text_invalid'}), TimeoutError(SECRET), (200, [])]:
            self.net.binding = response
            self.assertEqual(self.deliver(), ('unknown', 'send_outcome_unknown'))
        self.net.binding = (401, {'error': 'api_auth_failed'})
        self.assertEqual(self.deliver(), ('failed', 'api_auth_failed'))
        self.assertEqual(self.net.sends, [])

    def test_only_exact_409_unbound_allows_bind_and_second_failure_stops(self):
        self.net.answers = [(400, {'error': 'recipient_not_bound'})]
        self.assertEqual(self.deliver(True), ('failed', 'recipient_not_bound'))
        self.assertEqual(self.net.binds, [])
        for second, expected in [((409, {'error': 'recipient_not_bound'}), ('failed', 'recipient_not_bound')),
                                 (TimeoutError(SECRET), ('unknown', 'send_outcome_unknown')),
                                 ((503, {'error': 'request_not_completed'}), ('unknown', 'send_outcome_unknown'))]:
            before = len(self.net.sends)
            before_binds = len(self.net.binds)
            self.net.answers = [(409, {'error': 'recipient_not_bound'}), second]
            self.assertEqual(self.deliver(True), expected)
            self.assertEqual(len(self.net.sends), before + 2)
            self.assertEqual(len(self.net.binds), before_binds + 1)
        self.assertEqual(len(self.net.sends), 7)

    def test_bind_failure_timeout_unconfirmed_or_redirect_never_resends(self):
        for response, expected in [(TimeoutError(SECRET), ('unknown', 'send_outcome_unknown')),
            ((503, {'error': 'request_not_completed'}), ('unknown', 'send_outcome_unknown')),
            ((400, {'error': 'binding_reason_required'}), ('failed', 'binding_reason_required')),
            ((200, {'bound': False}), ('unknown', 'send_outcome_unknown')),
            ((200, {'bound': 1}), ('unknown', 'send_outcome_unknown')),
            ((302, {'bound': True}), ('unknown', 'send_outcome_unknown'))]:
            self.net.bind_answer = response
            self.net.answers = [(409, {'error': 'recipient_not_bound'})]
            before = len(self.net.sends)
            self.assertEqual(self.deliver(True), expected)
            self.assertEqual(len(self.net.sends), before + 1)
            if isinstance(response, BaseException):
                self.audit.assert_any_call('recipient_bind_result', state='unknown', code='send_outcome_unknown')
        self.assertEqual(len(self.net.binds), 6)

    def test_configuration_metadata_symlinks_bad_values_and_open_identity_checked(self):
        self.assertTrue(self.bridge.available())
        self.assertEqual(self.net.calls, [])
        for mode in (0o600, 0o644, 0o660, 0o740):
            self.config.chmod(mode)
            self.assertFalse(self.bridge.available())
        self.config.chmod(0o640)
        for field, value in [('st_uid', 1000), ('st_gid', 999), ('st_ino', -1), ('st_dev', -1)]:
            def wrong(path):
                info = fake_stat(path)
                setattr(info, field, value)
                return info
            client = BridgeClient(self.config, post=self.net.post, get=self.net.get, stat_fn=wrong, group_gid=731)
            self.assertFalse(client.available())
        link = self.root / 'symlink.json'
        link.symlink_to(self.config)
        self.assertFalse(BridgeClient(link, post=self.net.post, get=self.net.get,
                                     stat_fn=fake_stat, group_gid=731).available())
        for override in [dict(bridge_api_key=''), dict(bridge_api_key=123), dict(session=''),
                         dict(session='bad\nheader'), dict(api='http://example.invalid'),
                         dict(api='http://localhost/api'), dict(api='http://localhost/?query=1')]:
            seed_config(self.config, **override)
            self.assertFalse(self.bridge.available())
            self.assertEqual(self.deliver(), ('unknown', 'send_outcome_unknown'))
        self.config.write_text('not json ' + SECRET)
        self.assertFalse(self.bridge.available())
        self.config.write_text(' ' * 65537)
        self.assertFalse(self.bridge.available())
        self.config.unlink()
        self.assertFalse(self.bridge.available())
        self.assertEqual(self.net.calls, [])

    def test_real_fstat_metadata_rechecked_after_open(self):
        seed_config(self.config)
        before_open = fake_stat(self.config)
        actual_fstat = os.fstat
        def bad_metadata(fd):
            info = actual_fstat(fd)
            return SimpleNamespace(st_ino=info.st_ino, st_dev=info.st_dev, st_size=info.st_size,
                                   st_mode=info.st_mode, st_uid=999, st_gid=731)
        # Keep stat_fn=None, so the production fstat permission check is exercised
        # regardless of the test process's real uid/gid.
        with patch('pathlib.Path.lstat', return_value=before_open), \
             patch('console.outbox.bridge_client.os.fstat', side_effect=bad_metadata) as checked:
            client = BridgeClient(self.config, post=self.net.post, get=self.net.get, group_gid=731)
            self.assertFalse(client.available())
            checked.assert_called_once()
        self.assertEqual(self.net.calls, [])

    def test_loopback_default_config_override_and_build_override(self):
        self.assertEqual((BRIDGE_API_URL, TIMEOUT), ('http://127.0.0.1:8788', 20))
        for address in ('https://localhost', 'http://example.invalid', 'http://127.0.0.1.evil',
                        'http://localhost@evil', 'http://user:credential@localhost', 'http://[::1]',
                        'http://localhost/#fragment', 'http://localhost:bad', 'http://localhost/?x=1',
                        'http://localhost/\napi', 'http://localhost:70000'):
            with self.subTest(address=address), self.assertRaises(ValueError) as error:
                local_url(address, base=True)
            self.assertNotIn('credential', str(error.exception))
        for address in ('http://localhost:2222', 'http://127.0.0.1:3333'):
            self.assertEqual(local_url(address, base=True), address)
        seed_config(self.config, api='http://localhost:2222')
        fake_get = Mock(return_value=(200, {'bound': False}))
        client = BridgeClient(self.config, post=Mock(), get=fake_get, stat_fn=fake_stat, group_gid=731)
        client.deliver(CHAT, '虚构', 'ops-fictional', False, Mock())
        self.assertEqual(fake_get.call_args.args[0], 'http://localhost:2222/api/binding')
        fake_get.reset_mock()
        client = BridgeClient(self.config, api='http://127.0.0.1:3333', post=Mock(), get=fake_get,
                              stat_fn=fake_stat, group_gid=731)
        client.deliver(CHAT, '虚构', 'ops-fictional', False, Mock())
        self.assertEqual(fake_get.call_args.args[0], 'http://127.0.0.1:3333/api/binding')
        with self.assertRaises(ValueError):
            NoRedirect().redirect_request(None, None, 302, '', {}, 'http://example.invalid')

    def test_urllib_defaults_only_use_injected_fake_opener_without_network(self):
        class FakeResponse(BytesIO):
            code = 200
        opener = Mock()
        opener.open.side_effect = [FakeResponse(b'{"bound":false}'), FakeResponse(b'{"bound":true}')]
        with patch('console.outbox.bridge_client.build_opener', return_value=opener) as factory:
            self.assertEqual(local_get(BRIDGE_API_URL + '/api/binding', {}, None, 20), (200, {'bound': False}))
            self.assertEqual(local_post(BRIDGE_API_URL + '/api/recipients/bind', {}, {'confirmed': True}, 20),
                             (200, {'bound': True}))
        for call in factory.call_args_list:
            proxy, redirect = call.args
            self.assertEqual(proxy.proxies, {})
            self.assertIsInstance(redirect, NoRedirect)
        self.assertEqual(opener.open.call_count, 2)
        self.assertEqual(opener.open.call_args.args[0].get_method(), 'POST')
        self.assertEqual(opener.open.call_args.kwargs['timeout'], 20)
        self.assertEqual(json.loads(opener.open.call_args.args[0].data), {'confirmed': True})

    def test_urllib_4xx_reads_error_without_retry_and_response_bounds(self):
        error = HTTPError(BRIDGE_API_URL, 409, '', {}, BytesIO(b'{"error":"recipient_not_bound"}'))
        opener = Mock()
        opener.open.side_effect = error
        with patch('console.outbox.bridge_client.build_opener', return_value=opener):
            self.assertEqual(local_post(BRIDGE_API_URL + '/api/sendText', {}, {}, 20),
                             (409, {'error': 'recipient_not_bound'}))
        self.assertEqual(opener.open.call_count, 1)
        class LargeResponse(BytesIO):
            code = 200
        for raw in (b'x' * 65537, b'not json'):
            opener.open.side_effect = None
            opener.open.return_value = LargeResponse(raw)
            with patch('console.outbox.bridge_client.build_opener', return_value=opener), self.assertRaises(ValueError):
                local_get(BRIDGE_API_URL + '/api/binding', {}, None, 20)
