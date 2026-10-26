"""Isolated candidate: pause a Gmail poller after revoked/expired credentials.

No network client, email sender, credential deletion, or production entry point.
The supplied callbacks must be bound to the same canonical mailbox.
"""
import contextlib
import fcntl
import os
import sqlite3
import stat
import time
from pathlib import Path

from gmail_sync import IdentityMismatch, canonical_account, profile
from gmail_oauth import OAuthError


class Lifecycle:
    def __init__(self, directory):
        self.dir = Path(directory)
        st = self.dir.lstat()
        if self.dir.is_symlink() or not stat.S_ISDIR(st.st_mode) or st.st_uid != os.geteuid() or stat.S_IMODE(st.st_mode) != 0o700:
            raise PermissionError('unsafe_lifecycle_directory')
        self.path = self.dir / 'lifecycle.sqlite3'
        self.lock_path = self.dir / 'lifecycle.lock'
        fd = self._file(self.path)
        os.close(fd)
        self.db = sqlite3.connect(str(self.path), timeout=5)
        with self.db:
            self.db.execute('CREATE TABLE IF NOT EXISTS lifecycle (account TEXT PRIMARY KEY, state TEXT NOT NULL CHECK(state IN (\'active\',\'paused\',\'reauthorization_required\')))')
            self.db.execute('CREATE TABLE IF NOT EXISTS sync_receipts (account TEXT PRIMARY KEY, status TEXT NOT NULL, mode TEXT, history_id TEXT, pending_failures INTEGER, retry_exhausted INTEGER, completed_at REAL NOT NULL, error_code TEXT)')

    @staticmethod
    def _file(path):
        fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_uid != os.geteuid() or st.st_nlink != 1 or stat.S_IMODE(st.st_mode) != 0o600:
            os.close(fd)
            raise PermissionError('unsafe_lifecycle_file')
        return fd

    @contextlib.contextmanager
    def _lock(self):
        fd = self._file(self.lock_path)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)

    def close(self):
        self.db.close()

    def state(self, account):
        account = canonical_account(account)
        row = self.db.execute('SELECT state FROM lifecycle WHERE account=?', (account,)).fetchone()
        return row[0] if row else 'active'

    def _set(self, account, state):
        with self.db:
            self.db.execute('INSERT INTO lifecycle(account,state) VALUES(?,?) ON CONFLICT(account) DO UPDATE SET state=excluded.state', (account, state))

    def receipt(self, account):
        account = canonical_account(account)
        row = self.db.execute('SELECT status,mode,history_id,pending_failures,retry_exhausted,completed_at,error_code FROM sync_receipts WHERE account=?',(account,)).fetchone()
        return dict(zip(('status','mode','history_id','pending_failures','retry_exhausted','completed_at','error_code'),row)) if row else None

    def _save_receipt(self,account,result):
        status=result.get('status') if result.get('status') in ('ready','ready_with_failures','full_required','reauthorization_required','retry_wait','error') else 'error'
        mode=result.get('mode') if result.get('mode') in ('full','incremental') else None
        hid=result.get('history_id');hid=hid if isinstance(hid,str) and hid.isascii() and hid.isdigit() and len(hid)<=128 else None
        def count(name):
            value=result.get(name)
            return value if type(value) is int and 0<=value<=10**9 else None
        code=result.get('error_code') if result.get('error_code') in ('invalid_grant','retry_error','sync_error','identity_mismatch') else None
        with self.db:
            self.db.execute("""INSERT INTO sync_receipts VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(account) DO UPDATE SET
              status=excluded.status,mode=excluded.mode,history_id=excluded.history_id,
              pending_failures=excluded.pending_failures,retry_exhausted=excluded.retry_exhausted,
              completed_at=excluded.completed_at,error_code=excluded.error_code""",
              (account,status,mode,hid,count('pending_failures'),count('retry_exhausted'),time.time(),code))

    def poll(self, account, sync_fn):
        account = canonical_account(account)
        with self._lock():
            current = self.state(account)
            if current != 'active':
                return {'status': current}
            try:
                result = sync_fn()
                self._save_receipt(account,result)
                return result
            except IdentityMismatch:
                self._set(account, 'reauthorization_required')
                self._save_receipt(account,{'status':'reauthorization_required','error_code':'identity_mismatch'})
                return {'status':'reauthorization_required','error_code':'identity_mismatch'}
            except OAuthError as exc:
                if str(exc) != 'invalid_grant':
                    self._save_receipt(account,{'status':'error','error_code':'sync_error'})
                    raise
                self._set(account, 'reauthorization_required')
                self._save_receipt(account,{'status':'reauthorization_required','error_code':'invalid_grant'})
                return {'status': 'reauthorization_required'}
            except Exception:
                self._save_receipt(account,{'status':'error','error_code':'sync_error'})
                raise

    def resume_after_reauthorization(self, account, transport):
        account = canonical_account(account)
        with self._lock():
            # The transport must use the newly saved credential; profile validates
            # the mailbox. Any exception leaves the paused state untouched.
            profile(transport, account)
            self._set(account, 'active')
            return {'status': 'active'}

    def pause(self, account):
        account = canonical_account(account)
        with self._lock():
            current = self.state(account)
            if current == 'active':
                self._set(account, 'paused')
                current = 'paused'
            return {'status': current}
