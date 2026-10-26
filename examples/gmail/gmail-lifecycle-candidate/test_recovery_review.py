"""Fake transport, fake accounts, temporary databases only."""
import json, os, pathlib, sqlite3, tempfile, unittest
from unittest.mock import patch
from gmail_sync import API, Store
from runtime import GmailBackend, Scheduler
from test_gmail_sync import FakeTransport, message
from service import read_config

class RecoveryReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=pathlib.Path(self.temp.name);os.chmod(self.root,0o700)
        key=self.root/'test.key';key.write_bytes(b'k'*32);os.chmod(key,0o600)
        self.account='first@example.test';self.fake=FakeTransport(self.account)
        self.backend=GmailBackend(self.root,self.account,key,lambda:self.fake)
    def tearDown(self):self.temp.cleanup()
    def setup_failure(self):
        self.fake.lists={None:{'messages':[{'id':'fixture-missing'}]}}
        self.fake.messages={'fixture-missing':{'id':'fixture-missing'}}
    def inspect(self):
        store=Store(self.root/'gmail.sqlite3')
        try:return store.status(self.account),store.failures(self.account),store.conn.execute('SELECT count(*) FROM gmail_messages WHERE account=? AND message_id=? AND remote_state=?',(self.account,'fixture-missing','present')).fetchone()[0]
        finally:store.close()
    def test_service_rejects_misconfigured_non_target_account(self):
        config={'enabled':False,'account':'wrong@example.test','private_dir':str(self.root),'key_file':str(self.root/'test.key'),'poll_interval_seconds':300,'ui_port':18792}
        path=self.root/'config.json';path.write_text(json.dumps(config))
        with patch.dict(os.environ,{'GMAIL_EXPECTED_ACCOUNT':'mailbox@example.test'}):
            with self.assertRaisesRegex(ValueError,'configured_account_mismatch'):read_config(path)
            config['account']='mailbox@example.test';path.write_text(json.dumps(config))
            self.assertEqual(read_config(path)['account'],'mailbox@example.test')
        with patch.dict(os.environ,{},clear=True):
            with self.assertRaisesRegex(ValueError,'expected_account_required'):read_config(path)

    def test_wrong_mailbox_pauses_before_message_access(self):
        self.fake.account='wrong@example.test'
        result=self.backend.poll()
        self.assertEqual((result['status'],result['error_code']),('reauthorization_required','identity_mismatch'))
        self.assertFalse(any(path!=API+'/profile' for path,_ in self.fake.calls))
        self.fake.calls.clear();self.backend.poll();self.assertEqual(self.fake.calls,[])
        self.fake.account=self.account
        self.assertEqual(self.backend.resume()['status'],'active')

    def test_mismatch_during_retry_pauses_after_durable_checkpoint(self):
        self.setup_failure()
        base_get=self.fake.get;profiles=0
        def mismatching_retry(path,params):
            nonlocal profiles
            if path==API+'/profile':
                profiles+=1
                if profiles==2:self.fake.account='wrong@example.test'
            return base_get(path,params)
        self.fake.get=mismatching_retry
        result=self.backend.poll()
        self.assertEqual((result['status'],result['error_code']),('reauthorization_required','identity_mismatch'))
        state,failures,_=self.inspect()
        self.assertEqual((state['checkpoint'],len(failures)),('100',1))
        status=self.backend.status()
        self.assertEqual((status['checkpoint'],status['pending_failures']),('100',1))
        self.assertEqual(status['state'],'reauthorization_required')

    def test_failed_message_is_retried_and_committed_cursor_preserved(self):
        self.setup_failure();first=self.backend.poll()
        self.assertEqual(first['status'],'ready_with_failures');self.assertGreater(first['pending_failures'],0)
        self.fake.messages['fixture-missing']=message('fixture-missing','fixture body')
        self.fake.calls.clear();second=self.backend.poll();state,failures,present=self.inspect()
        self.assertEqual((second['status'],state['checkpoint'],len(failures),present),('ready','100',0,1))
        self.assertTrue(any(path==API+'/messages/fixture-missing' for path,_ in self.fake.calls))
    def test_missing_message_stays_visible_but_stops_after_budget(self):
        self.fake.lists={None:{'messages':[{'id':'fixture-missing'}]}}
        self.fake.messages={}
        for _ in range(8):result=self.backend.poll()
        self.assertEqual(result['status'],'ready_with_failures');self.assertEqual(result['pending_failures'],1);self.assertEqual(result['retry_exhausted'],1)
        self.fake.calls.clear();self.backend.poll()
        self.assertFalse(any(path==API+'/messages/fixture-missing' for path,_ in self.fake.calls))
        self.assertEqual(self.inspect()[1][0][1:2],('fetch_missing',))
    def test_confirmed_deleted_message_is_not_resurrected_by_retry(self):
        self.setup_failure();self.backend.poll()
        self.fake.messages['fixture-missing']=message('fixture-missing','stale response')
        self.fake.histories[('100',None)]={'history':[{'messagesDeleted':[{'message':{'id':'fixture-missing'}}]}],'historyId':'110'}
        self.fake.calls.clear();result=self.backend.poll()
        self.assertEqual(result['status'],'ready_with_failures')
        self.assertEqual(result['retry_exhausted'],1)
        self.assertFalse(any(path==API+'/messages/fixture-missing' for path,_ in self.fake.calls))
        with sqlite3.connect(self.root/'gmail.sqlite3') as db:
            self.assertEqual(db.execute('SELECT remote_state FROM gmail_messages WHERE account=? AND message_id=?',(self.account,'fixture-missing')).fetchone()[0],'deleted_remote')

    def test_receipt_survives_restart_and_unconfigured_is_clear(self):
        scheduler=Scheduler(self.backend,5)
        self.assertEqual(scheduler.status()['state'],'not_configured')
        self.assertIsNone(scheduler.status()['last_receipt'])
        self.assertEqual(scheduler.run_once()['status'],'ready')
        fresh=Scheduler(self.backend,5).status()
        self.assertEqual(fresh['state'],'not_configured')
        self.assertEqual(fresh['last_result'],'ready')
        self.assertEqual((fresh['last_receipt']['status'],fresh['last_receipt']['history_id']),('ready','100'))
    def test_pause_persists_and_retry_does_not_bypass_pause(self):
        self.setup_failure();self.backend.poll();self.backend.pause();self.fake.calls.clear()
        self.assertEqual(self.backend.poll()['status'],'paused');self.assertEqual(self.fake.calls,[])
        self.fake.account='wrong@example.test'
        with self.assertRaises(Exception):self.backend.resume()
        self.assertEqual(self.backend.status()['state'],'paused')
        self.fake.account=self.account
        self.assertEqual(self.backend.resume()['status'],'active')
    def test_receipt_does_not_store_message_text(self):
        self.setup_failure();self.backend.poll()
        with sqlite3.connect(self.root/'lifecycle.sqlite3') as conn:
            row=conn.execute('SELECT * FROM sync_receipts').fetchone()
        self.assertNotIn('fixture-missing',repr(row))
        self.assertNotIn('fixture body',repr(row))
