"""Candidate adapter and scheduler; not a deployable production service."""
import os
import stat
import threading
from pathlib import Path

from encrypted_vault import EncryptedVault
from gmail_oauth import OAuthError, RefreshingTransport, TokenProvider
from gmail_sync import IdentityMismatch, Store, canonical_account, sync, retry_failures
from lifecycle import Lifecycle


def file_key_provider(path):
    """Read a private 32-byte key, e.g. a systemd LoadCredential file.

    No key is generated or printed. The private temporary file in tests is not
    a production secret manager. Never use a shell environment variable as key.
    """
    path = Path(path)
    if not path.is_absolute():
        raise ValueError('absolute_key_path_required')
    def read():
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode) or st.st_uid != os.geteuid() or st.st_nlink != 1 or stat.S_IMODE(st.st_mode) not in (0o400, 0o600) or st.st_size != 32:
                raise ValueError('unsafe_key_file')
            key = os.read(fd, 33)
            if len(key) != 32:
                raise ValueError('invalid_key_file')
            return key
        finally:
            os.close(fd)
    return read


class GmailBackend:
    """Fixed account and injected transport support safe synthetic testing."""
    def __init__(self, directory, account, key_path, transport_factory=None):
        self.directory = Path(directory)
        self.account = canonical_account(account)
        self.vault = EncryptedVault(self.directory / 'gmail-credential.enc', file_key_provider(key_path))
        self.transport_factory = transport_factory or (lambda: RefreshingTransport(TokenProvider(self.vault, self.account)))

    def status(self):
        state = Lifecycle(self.directory)
        try:
            present = self.vault.path.is_file() and not self.vault.path.is_symlink()
            current = state.state(self.account)
            result = {'account': self.account, 'state': 'not_configured' if current == 'active' and not present else current,
                      'credential_file_present': present, 'credential_verified_now': False,
                      'last_receipt': state.receipt(self.account), 'sync_database_available': False,
                      'checkpoint': None, 'pending_failures': None}
            path = self.directory / 'gmail.sqlite3'
            if path.is_file() and not path.is_symlink():
                try:
                    store = Store(path)
                    try:
                        sync_state = store.status(self.account)
                        result.update(sync_database_available=True, checkpoint=sync_state['checkpoint'] if sync_state else None,
                                      pending_failures=store.failure_count(self.account))
                    finally:store.close()
                except Exception:
                    pass  # unavailable is explicit; do not leak exception text
            return result
        finally:
            state.close()

    def pause(self):
        state = Lifecycle(self.directory)
        try:
            return state.pause(self.account)
        finally:
            state.close()

    def resume(self):
        state = Lifecycle(self.directory)
        try:
            # Always build a new provider; never reuse cached pre-reauthorization token.
            return state.resume_after_reauthorization(self.account, self.transport_factory())
        finally:
            state.close()

    def poll(self):
        state = Lifecycle(self.directory)
        try:
            def do_sync():
                store = Store(self.directory / 'gmail.sqlite3')
                try:
                    transport = self.transport_factory()
                    result = sync(store, transport, self.account)
                    if result.get('status') in ('ready','ready_with_failures') and store.failure_count(self.account):
                        try:
                            retry = retry_failures(store, transport, self.account, limit=50, max_attempts=5)
                        except (OAuthError, IdentityMismatch):
                            raise
                        except Exception:
                            result.update(status='retry_wait', pending_failures=store.failure_count(self.account), error_code='retry_error')
                        else:
                            result.update(pending_failures=retry['remaining'], retry_attempted=retry['attempted'], retry_exhausted=retry['exhausted'])
                            result['status'] = 'ready_with_failures' if retry['remaining'] else 'ready'
                    return result
                finally:
                    store.close()
            return state.poll(self.account, do_sync)
        finally:
            state.close()


class Scheduler:
    """One account per process; no external network until poll is explicitly run."""
    def __init__(self, backend, interval_seconds=300):
        if type(interval_seconds) is not int or interval_seconds < 1:
            raise ValueError('invalid_interval')
        self.backend = backend
        self.interval = interval_seconds
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.thread = None
        self.last_result = 'not_run'

    def run_once(self):
        with self.lock:
            try:
                result = self.backend.poll()
                self.last_result = result.get('status', 'unknown')
                return result
            except Exception:
                # No exception text: upstream may contain tokens or messages.
                self.last_result = 'error'
                return {'status': 'error'}

    def pause(self):
        with self.lock:
            return self.backend.pause()

    def resume(self):
        with self.lock:
            try:
                return self.backend.resume()
            except Exception:
                return {'status': 'error'}

    def status(self):
        with self.lock:
            result = self.backend.status()
            result['last_result'] = (result['last_receipt']['status'] if self.last_result == 'not_run' and result['last_receipt'] else self.last_result)
            return result

    def start(self):
        if self.thread and self.thread.is_alive():
            raise RuntimeError('scheduler_running')
        self.stop_event.clear()
        def run():
            while not self.stop_event.wait(self.interval):
                self.run_once()
        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=30)
            if self.thread.is_alive():
                raise TimeoutError('scheduler_still_running')
