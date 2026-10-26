import json
import os
import re
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from demo_ui import make_server
from gmail_oauth import OAuthError
from gmail_sync import API
from encrypted_vault import EncryptedVault, VaultError
from runtime import GmailBackend, Scheduler, file_key_provider
from service import read_config


class Fake:
    def __init__(self):
        self.account = 'first@example.test'
        self.invalid = False
        self.calls = []
    def get(self, path, params):
        self.calls.append(path)
        if self.invalid:
            raise OAuthError('invalid_grant')
        if path == API + '/profile':
            return {'emailAddress': self.account, 'historyId': '100'}
        if path == API + '/messages':
            return {'messages': []}
        if path == API + '/history':
            return {'historyId': '100'}
        raise AssertionError(path)


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.key = self.root / 'key'
        self.key.write_bytes(b'k'*32)
        os.chmod(self.key, 0o600)
        self.fake = Fake()
        self.backend = GmailBackend(self.root, 'first@example.test', self.key, lambda: self.fake)
        self.scheduler = Scheduler(self.backend, 1)
    def tearDown(self):
        self.scheduler.stop()
        self.tmp.cleanup()

    def test_key_file_configuration_and_rejections(self):
        self.assertEqual(file_key_provider(self.key)(), b'k'*32)
        os.chmod(self.key, 0o644)
        with self.assertRaises(ValueError): file_key_provider(self.key)()
        os.chmod(self.key, 0o600)
        link = self.root / 'link'
        link.symlink_to(self.key)
        with self.assertRaises(OSError): file_key_provider(link)()
        self.key.write_bytes(b'short')
        with self.assertRaises(ValueError): file_key_provider(self.key)()

    def test_file_key_actually_encrypts_and_fails_without_key(self):
        vault = EncryptedVault(self.root/'synthetic.enc', file_key_provider(self.key))
        vault.create('first@example.test', {'account': 'first@example.test', 'refresh_token': 'synthetic-secret'})
        self.assertNotIn(b'synthetic-secret', (self.root/'synthetic.enc').read_bytes())
        self.assertEqual(vault.load('first@example.test')['refresh_token'], 'synthetic-secret')
        self.key.unlink()
        with self.assertRaises(VaultError): vault.load('first@example.test')

    def test_example_config_is_disabled_and_fixed_account(self):
        with patch.dict(os.environ,{'GMAIL_EXPECTED_ACCOUNT':'mailbox@example.test'}):
            config = read_config(Path(__file__).with_name('config.example.json'))
        self.assertFalse(config['enabled'])
        self.assertEqual(config['account'], 'mailbox@example.test')

    def test_pause_and_resume_verify_account(self):
        self.assertEqual(self.scheduler.run_once()['status'], 'ready')
        self.assertEqual(self.scheduler.pause()['status'], 'paused')
        calls = len(self.fake.calls)
        self.assertEqual(self.scheduler.run_once()['status'], 'paused')
        self.assertEqual(len(self.fake.calls), calls)
        self.fake.account = 'wrong@example.test'
        self.assertEqual(self.scheduler.resume()['status'], 'error')
        self.assertEqual(self.scheduler.status()['state'], 'paused')
        self.fake.account = 'first@example.test'
        self.assertEqual(self.scheduler.resume()['status'], 'active')
        self.assertEqual(self.scheduler.run_once()['mode'], 'incremental')

    def test_expired_token_blocks_poll_until_reauthorized(self):
        self.assertEqual(self.scheduler.run_once()['status'], 'ready')
        self.fake.invalid = True
        self.assertEqual(self.scheduler.run_once()['status'], 'reauthorization_required')
        calls = len(self.fake.calls)
        self.assertEqual(self.scheduler.run_once()['status'], 'reauthorization_required')
        self.assertEqual(len(self.fake.calls), calls)
        self.assertEqual(self.scheduler.resume()['status'], 'error')
        self.fake.invalid = False
        self.assertEqual(self.scheduler.resume()['status'], 'active')
        self.assertEqual(self.scheduler.run_once()['mode'], 'incremental')

    def test_scheduler_calls_and_stops(self):
        self.scheduler.start()
        deadline = time.monotonic() + 4
        while not self.fake.calls and time.monotonic() < deadline:
            time.sleep(.02)
        self.assertTrue(self.fake.calls)
        self.scheduler.stop()
        before = len(self.fake.calls)
        time.sleep(.05)
        self.assertEqual(before, len(self.fake.calls))

    def test_http_buttons_and_csrf(self):
        server = make_server(self.scheduler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f'http://127.0.0.1:{server.server_port}'
        try:
            page = urlopen(base + '/', timeout=3).read().decode()
            token = re.search(r'const token="([^"]+)"', page).group(1)
            def action(name, csrf=token, origin=base):
                request = Request(base+'/api/'+name, data=b'', method='POST', headers={'X-Demo-CSRF': csrf, 'Origin': origin})
                return json.loads(urlopen(request, timeout=3).read())
            with self.assertRaises(HTTPError) as denied:
                action('sync', csrf='wrong')
            self.assertEqual(denied.exception.code, 403)
            with self.assertRaises(HTTPError): action('sync', origin='https://example.test')
            self.assertEqual(action('sync')['status'], 'ready')
            self.assertEqual(action('pause')['status'], 'paused')
            self.assertEqual(action('sync')['status'], 'paused')
            self.assertEqual(action('resume')['status'], 'active')
            self.assertEqual(json.loads(urlopen(base+'/api/status').read())['account'], 'first@example.test')
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)


if __name__ == '__main__': unittest.main()
