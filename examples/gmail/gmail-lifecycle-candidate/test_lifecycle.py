import os
import tempfile
import unittest
from pathlib import Path

from gmail_oauth import OAuthError
from gmail_sync import IdentityMismatch, Store, sync, API
from test_gmail_sync import FakeTransport, message
from lifecycle import Lifecycle


class Profile:
    def __init__(self, email='first@example.test', error=None):
        self.email, self.error = email, error

    def get(self, path, params):
        if self.error:
            raise self.error
        return {'emailAddress': self.email, 'historyId': '12'}


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.life = Lifecycle(self.root)
        self.account = 'first@example.test'

    def tearDown(self):
        self.life.close()
        self.tmp.cleanup()

    def denied(self):
        raise OAuthError('invalid_grant')

    def test_persists_pause_and_poll_stays_offline(self):
        self.assertEqual(self.life.poll(self.account, self.denied)['status'], 'reauthorization_required')
        self.life.close()
        self.life = Lifecycle(self.root)
        def should_not_run():
            raise AssertionError('poll must remain paused')
        self.assertEqual(self.life.poll(self.account, should_not_run)['status'], 'reauthorization_required')

    def test_resume_verifies_identity_before_unpausing(self):
        self.life.poll(self.account, self.denied)
        with self.assertRaises(IdentityMismatch):
            self.life.resume_after_reauthorization(self.account, Profile('other@example.test'))
        self.assertEqual(self.life.state(self.account), 'reauthorization_required')
        with self.assertRaises(OAuthError):
            self.life.resume_after_reauthorization(self.account, Profile(error=OAuthError('invalid_grant')))
        self.assertEqual(self.life.state(self.account), 'reauthorization_required')
        self.assertEqual(self.life.resume_after_reauthorization(self.account, Profile()), {'status': 'active'})
        self.assertEqual(self.life.poll(self.account, lambda: {'status': 'ready', 'checkpoint': '12'}), {'status': 'ready', 'checkpoint': '12'})

    def test_temporary_or_client_error_does_not_disable(self):
        def transient():
            raise OAuthError('token_network_error')
        with self.assertRaises(OAuthError):
            self.life.poll(self.account, transient)
        self.assertEqual(self.life.state(self.account), 'active')

    def test_account_state_isolated(self):
        self.life.poll(self.account, self.denied)
        self.assertEqual(self.life.poll('other@example.test', lambda: {'status': 'ready'}), {'status': 'ready'})
        self.assertEqual(self.life.state(self.account), 'reauthorization_required')

    def test_resume_keeps_original_checkpoint_and_messages(self):
        store = Store(self.root / 'gmail.sqlite3')
        fake = FakeTransport(self.account)
        fake.lists = {None: {'messages': [{'id': 'm1'}]}}
        fake.messages = {'m1': message('m1', 'first')}
        try:
            self.assertEqual(self.life.poll(self.account, lambda: sync(store, fake, self.account))['history_id'], '100')
            fake.fail[(API + '/profile', None, None)] = OAuthError('invalid_grant')
            self.assertEqual(self.life.poll(self.account, lambda: sync(store, fake, self.account))['status'], 'reauthorization_required')
            self.assertEqual(store.status(self.account)['checkpoint'], '100')
            self.assertEqual(store.conn.execute('select count(*) from gmail_messages').fetchone()[0], 1)
            del fake.fail[(API + '/profile', None, None)]
            self.life.resume_after_reauthorization(self.account, fake)
            fake.histories[('100', None)] = {'historyId': '110'}
            result = self.life.poll(self.account, lambda: sync(store, fake, self.account))
            self.assertEqual((result['mode'], result['history_id']), ('incremental', '110'))
            self.assertEqual(store.conn.execute('select count(*) from gmail_messages').fetchone()[0], 1)
        finally:
            store.close()

    def test_unsafe_file_and_directory_rejected(self):
        self.life.close()
        os.chmod(self.root / 'lifecycle.sqlite3', 0o644)
        with self.assertRaises(PermissionError):
            Lifecycle(self.root)
        os.chmod(self.root / 'lifecycle.sqlite3', 0o600)
        self.life = Lifecycle(self.root)


if __name__ == '__main__':
    unittest.main()
