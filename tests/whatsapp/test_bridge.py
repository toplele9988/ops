"""Use synthetic messages and a fake WAHA client; no network or real accounts."""
import hashlib
import hmac
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from whatsapp import bridge


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.config = {
            'db': str(Path(self.tmp.name) / 'bridge.db'),
            'session': 'fixture_session', 'owner': 'fixture_owner',
            'profile_id': 'fixture_profile', 'webhook_key': 'fixture_key',
            'auto_send': False,
        }
        bridge.initialize(self.config)
        self.sent = []

    def tearDown(self):
        self.tmp.cleanup()

    def api(self, config, path, body=None, **kwargs):
        if path.startswith('/api/sessions/'):
            return {'status': 'WORKING', 'me': {'id': '100001@c.us'}}
        self.sent.append((path, body))
        return {'id': 'fixture_provider_receipt'}

    def archive(self):
        event = {'id': 'event-1', 'event': 'message.any', 'session': self.config['session'],
                 'payload': {'id': 'message-1', 'fromMe': False,
                             'from': '200002@c.us', 'body': 'Fixture inquiry',
                             'hasMedia': False}}
        raw = json.dumps(event).encode()
        signature = hmac.new(self.config['webhook_key'].encode(), raw, hashlib.sha512).hexdigest()
        headers = {'X-Webhook-Hmac-Algorithm': 'sha512', 'X-Webhook-Hmac': signature}
        return raw, headers

    def test_inbound_archive_is_deduplicated(self):
        raw, headers = self.archive()
        one = bridge.receive(self.config, raw, headers, self.api)
        two = bridge.receive(self.config, raw, headers, self.api)
        self.assertEqual(one['message_key'], two['message_key'])
        with sqlite3.connect(self.config['db']) as db:
            self.assertEqual(db.execute('select count(*) from messages').fetchone()[0], 1)
            self.assertEqual(db.execute('select count(*) from knowledge_jobs').fetchone()[0], 1)

    def test_invalid_signature_has_no_effect(self):
        raw, headers = self.archive()
        headers['X-Webhook-Hmac'] = 'incorrect'
        with self.assertRaisesRegex(ValueError, 'webhook_auth_failed'):
            bridge.receive(self.config, raw, headers, self.api)
        with sqlite3.connect(self.config['db']) as db:
            self.assertEqual(db.execute('select count(*) from messages').fetchone()[0], 0)

    def test_explicit_send_replay_does_not_send_twice(self):
        raw, headers = self.archive()
        bridge.receive(self.config, raw, headers, self.api)
        packet = {'idempotency_key': 'fixture-send-1', 'session': 'fixture_session',
                  'account_id': '100001@c.us', 'chat_id': '200002@c.us',
                  'actor': 'approved_operator', 'text': 'Synthetic reply'}
        self.assertEqual(bridge.send(self.config, 'sendText', packet, self.api)['state'], 'accepted')
        self.assertTrue(bridge.send(self.config, 'sendText', packet, self.api)['replayed'])
        self.assertEqual(len(self.sent), 1)

    def test_automatic_send_disabled(self):
        packet = {'idempotency_key': 'fixture-send-2', 'session': 'fixture_session',
                  'account_id': '100001@c.us', 'chat_id': '200002@c.us',
                  'actor': 'model_auto', 'auto_send': True, 'text': 'Synthetic reply'}
        with self.assertRaisesRegex(ValueError, 'automatic_send_scope_denied'):
            bridge.send(self.config, 'sendText', packet, self.api)
        self.assertEqual(self.sent, [])

    def test_uncertain_send_is_not_retried(self):
        raw, headers = self.archive()
        bridge.receive(self.config, raw, headers, self.api)
        calls = []
        def interrupted(config, path, body=None, **kwargs):
            if path.startswith('/api/sessions/'):
                return self.api(config, path, body, **kwargs)
            calls.append(path)
            raise TimeoutError('synthetic provider timeout')
        packet = {'idempotency_key': 'fixture-send-3', 'session': 'fixture_session',
                  'account_id': '100001@c.us', 'chat_id': '200002@c.us',
                  'actor': 'approved_operator', 'text': 'Synthetic reply'}
        self.assertEqual(bridge.send(self.config, 'sendText', packet, interrupted)['state'], 'unknown')
        self.assertTrue(bridge.send(self.config, 'sendText', packet, interrupted)['replayed'])
        self.assertEqual(len(calls), 1)


if __name__ == '__main__':
    unittest.main()
