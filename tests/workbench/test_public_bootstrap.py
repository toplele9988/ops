"""Public candidate must not silently create a default account."""
import pathlib, tempfile, unittest
from console.password_candidate.password_store import PasswordStore

class PublicBootstrapTests(unittest.TestCase):
    def test_empty_store_requires_explicit_provisioning(self):
        with tempfile.TemporaryDirectory() as directory:
            pathlib.Path(directory).chmod(0o700)
            store = PasswordStore(pathlib.Path(directory) / 'accounts.sqlite3')
            with store.conn() as connection:
                self.assertEqual(connection.execute('select count(*) from users').fetchone()[0], 0)
            store.provision('fixture_user', 'fixture_password')
            self.assertIsNotNone(store.login('fixture_user', 'fixture_password'))

    def test_legacy_bootstrap_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            pathlib.Path(directory).chmod(0o700)
            with self.assertRaisesRegex(ValueError, 'explicit_provision_required'):
                PasswordStore(pathlib.Path(directory) / 'accounts.sqlite3', bootstrap=True)
