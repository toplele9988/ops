import hashlib
import json
import os
import sqlite3
import stat
import tempfile
import unittest
from pathlib import Path
from urllib.parse import unquote
from gmail_sync import API, ApiError, IdentityMismatch, InvalidResponse, Store, SyncBusy, sync, retry_failures


def message(mid,text,thread=None,labels=None):
    return {'id':mid,'threadId':thread or 'thread-'+mid,'labelIds':labels or ['INBOX'],
            'payload':{'mimeType':'text/plain','body':{'data':text}},'historyId':'110','internalDate':'1000'}

class FakeTransport:
    def __init__(self,account):
        self.account=account;self.profile_id='100';self.lists={None:{'messages':[]}}
        self.histories={('100',None):{'historyId':'100'}};self.messages={};self.calls=[];self.fail={}
    def get(self,path,params):
        self.calls.append((path,dict(params)))
        key=(path,params.get('pageToken'),params.get('startHistoryId'))
        if key in self.fail: raise self.fail[key]
        if path==API+'/profile':return {'emailAddress':self.account,'historyId':self.profile_id}
        if path==API+'/messages':return self.lists[params.get('pageToken')]
        if path==API+'/history':return self.histories[(params['startHistoryId'],params.get('pageToken'))]
        if path.startswith(API+'/messages/'):
            mid=unquote(path[len(API+'/messages/'):])
            if mid not in self.messages:raise ApiError(404)
            return self.messages[mid]
        raise AssertionError(path)

class GmailSyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.store=Store(Path(self.tmp.name)/'test.sqlite3');self.a='one@example.test';self.f=FakeTransport(self.a)
    def tearDown(self):self.store.close();self.tmp.cleanup()
    def rows(self,account=None):
        return self.store.conn.execute('SELECT account,message_id,message_json,remote_state FROM gmail_messages WHERE account=? ORDER BY message_id',((account or self.a),)).fetchall()
    def multi_page(self):
        self.f.lists={None:{'messages':[{'id':'a'},{'id':'b'}],'nextPageToken':'L2'},'L2':{'messages':[{'id':'b'},{'id':'c'}]}}
        self.f.messages={'a':message('a','original'), 'b':message('b','second'), 'c':message('c','third'), 'd':message('d','during-scan')}
        self.f.histories={('100',None):{'history':[{'messagesAdded':[{'message':{'id':'d'}}], 'messages':[{'id':'d'}]}],'nextPageToken':'H2','historyId':'120'},('100','H2'):{'history':[{'labelsAdded':[{'message':{'id':'a'},'labelIds':['STARRED']}]}],'historyId':'130'}}
    def test_full_pagination_idempotent_dedup_and_scan_race(self):
        self.multi_page();result=sync(self.store,self.f,self.a);self.assertEqual((result['mode'],result['history_id']),('full','130'))
        self.assertEqual([x[1] for x in self.rows()],['a','b','c','d'])
        self.assertEqual(self.store.status(self.a)['checkpoint'],'130')
        self.assertEqual([x for x in self.f.calls if x[0]==API+'/messages'][0][1]['includeSpamTrash'],'true')
        self.assertTrue(all(x[1]['format']=='full' for x in self.f.calls if x[0].startswith(API+'/messages/')))
        self.f.histories[('130',None)]={'historyId':'130'};self.assertEqual(sync(self.store,self.f,self.a)['mode'],'incremental');self.assertEqual(len(self.rows()),4)
    def test_incremental_uses_specific_events_updates_and_preserves_deleted_data(self):
        self.multi_page();sync(self.store,self.f,self.a);self.f.messages['a']=message('a','changed',labels=['STARRED']);del self.f.messages['c']
        self.f.histories[('130',None)]={'history':[{'labelsAdded':[{'message':{'id':'a'}}], 'messagesDeleted':[{'message':{'id':'c'}}],'messages':[{'id':'a'},{'id':'c'}]}], 'historyId':'140'}
        sync(self.store,self.f,self.a);rows={x[1]:x for x in self.rows()};self.assertIn('changed',rows['a'][2]);self.assertIn('third',rows['c'][2]);self.assertEqual(rows['c'][3],'deleted_remote')
    def test_404_history_only_requires_rescan_without_clearing_old_data(self):
        self.multi_page();sync(self.store,self.f,self.a);before=self.rows();self.f.fail[(API+'/history',None,'130')]=ApiError(404)
        r=sync(self.store,self.f,self.a);self.assertEqual(r['status'],'full_required');self.assertEqual(self.rows(),before);self.assertEqual(self.store.status(self.a)['checkpoint'],'130')
        del self.f.fail[(API+'/history',None,'130')];self.f.profile_id='200';self.f.histories[('200',None)]={'historyId':'210'}
        r=sync(self.store,self.f,self.a);self.assertEqual((r['mode'],r['history_id']),('full','210'));self.assertGreaterEqual(len(self.rows()),4)
    def test_404_on_second_history_page_never_commits_intermediate_checkpoint(self):
        self.multi_page();sync(self.store,self.f,self.a);self.f.histories[('130',None)]={'historyId':'140','nextPageToken':'bad'};self.f.fail[(API+'/history','bad','130')]=ApiError(404)
        self.assertEqual(sync(self.store,self.f,self.a)['status'],'full_required');self.assertEqual(self.store.status(self.a)['checkpoint'],'130')
    def test_transient_history_error_does_not_advance_checkpoint(self):
        self.multi_page();sync(self.store,self.f,self.a);self.f.histories[('130',None)]={'historyId':'140','nextPageToken':'next'};self.f.fail[(API+'/history','next','130')]=ApiError(503)
        with self.assertRaises(ApiError):sync(self.store,self.f,self.a)
        self.assertEqual((self.store.status(self.a)['state'],self.store.status(self.a)['checkpoint']),('ready','130'))
    def test_source_account_isolation_and_profile_identity(self):
        self.f.messages={'same':message('same','account-one')};self.f.lists={None:{'messages':[{'id':'same'}]}};sync(self.store,self.f,self.a)
        b='two@example.test';fb=FakeTransport(b);fb.messages={'same':message('same','account-two')};fb.lists={None:{'messages':[{'id':'same'}]}};sync(self.store,fb,b)
        self.assertIn('account-one',self.rows(self.a)[0][2]);self.assertIn('account-two',self.rows(b)[0][2]);before=self.rows()
        with self.assertRaises(IdentityMismatch):sync(self.store,fb,self.a)
        self.assertEqual(before,self.rows())
    def test_full_failure_stays_in_progress_and_rescans_safely(self):
        self.multi_page();self.f.fail[(API+'/messages','L2',None)]=ApiError(503)
        with self.assertRaises(ApiError):sync(self.store,self.f,self.a)
        self.assertEqual(self.store.status(self.a)['state'],'full_in_progress');self.assertIsNone(self.store.status(self.a)['checkpoint'])
        del self.f.fail[(API+'/messages','L2',None)];self.assertEqual(sync(self.store,self.f,self.a)['status'],'ready');self.assertEqual(len(self.rows()),4)
    def test_resumes_failed_full_scan_at_persisted_page_and_anchor(self):
        self.multi_page();self.f.fail[(API+'/messages','L2',None)]=ApiError(503)
        with self.assertRaises(ApiError):sync(self.store,self.f,self.a)
        before=self.store.scan(self.a);self.assertEqual(before['page_token'],'L2');self.assertEqual(before['anchor'],'100')
        self.store.close();self.store=Store(Path(self.tmp.name)/'test.sqlite3')
        del self.f.fail[(API+'/messages','L2',None)];self.f.profile_id='200';self.f.calls.clear()
        self.assertEqual(sync(self.store,self.f,self.a)['status'],'ready')
        list_calls=[q for path,q in self.f.calls if path==API+'/messages']
        self.assertEqual(len(list_calls),1);self.assertEqual(list_calls[0]['pageToken'],'L2')
        self.assertEqual(self.store.status(self.a)['checkpoint'],'130')
    def test_resume_then_catchup_mixed_events_and_queue_without_false_completeness(self):
        self.multi_page();self.f.messages['b']={'id':'b'}
        self.f.fail[(API+'/messages','L2',None)]=ApiError(503)
        with self.assertRaises(ApiError):sync(self.store,self.f,self.a)
        self.assertIsNone(self.store.status(self.a)['checkpoint']);self.assertEqual(self.store.failure_count(self.a),1)
        self.store.close();self.store=Store(Path(self.tmp.name)/'test.sqlite3')
        del self.f.fail[(API+'/messages','L2',None)]
        self.f.histories={('100',None):{'history':[{'messagesAdded':[{'message':{'id':'d'}}],'labelsAdded':[{'message':{'id':'a'}}],'messagesDeleted':[{'message':{'id':'c'}}]}],'historyId':'130'}}
        result=sync(self.store,self.f,self.a);self.assertEqual((result['status'],result['pending_failures']),('ready_with_failures',1))
        self.assertEqual(self.store.status(self.a)['checkpoint'],'130');rows={x[1]:x for x in self.rows()}
        self.assertIn('d',rows);self.assertEqual(rows['c'][3],'deleted_remote');self.assertEqual(self.store.failures(self.a)[0][0],'b')
    def test_bad_one_message_is_queued_and_others_continue_then_retry(self):
        self.multi_page();self.f.messages['b']={'id':'b'}
        result=sync(self.store,self.f,self.a)
        self.assertEqual((result['status'],result['pending_failures']),('ready_with_failures',1))
        self.assertEqual(self.store.failures(self.a),[('b','invalid_thread_id',2)])
        self.assertIn('c',[x[1] for x in self.rows()]);self.assertEqual(self.store.status(self.a)['checkpoint'],'130')
        self.f.messages['b']=message('b','repaired');self.assertEqual(retry_failures(self.store,self.f,self.a),{'remaining':0})
        self.assertIn('b',[x[1] for x in self.rows()])
    def test_oversize_message_queued_instead_of_aborting_batch(self):
        self.multi_page();self.f.fail[(API+'/messages/a',None,None)]=InvalidResponse('response_too_large')
        r=sync(self.store,self.f,self.a);self.assertEqual(r['status'],'ready_with_failures')
        self.assertEqual(self.store.failures(self.a)[0][:2],('a','response_too_large'))
        self.assertTrue({'b','c'}.issubset({x[1] for x in self.rows()}))
    def test_rejected_saved_page_requires_full_restart_without_losing_queue(self):
        self.multi_page();self.f.fail[(API+'/messages','L2',None)]=ApiError(503)
        with self.assertRaises(ApiError):sync(self.store,self.f,self.a)
        self.f.fail[(API+'/messages','L2',None)]=ApiError(400)
        r=sync(self.store,self.f,self.a);self.assertEqual(r['reason'],'listing_cursor_rejected')
        self.assertEqual(self.store.status(self.a)['state'],'full_required')
    def test_retries_do_not_overwrite_confirmed_deleted_state_with_missing(self):
        self.multi_page();self.f.messages['b']={'id':'b'}
        sync(self.store,self.f,self.a);self.f.histories[('130',None)]={'history':[{'messagesDeleted':[{'message':{'id':'b'}}]}],'historyId':'140'}
        sync(self.store,self.f,self.a);del self.f.messages['b'];retry_failures(self.store,self.f,self.a)
        self.assertEqual({x[1]:x[3] for x in self.rows()}['b'],'deleted_remote')
        self.assertEqual(self.store.failures(self.a)[0][1],'fetch_missing')
    def test_auth_and_rate_limit_errors_do_not_become_silent_per_message_failures(self):
        for status in (401,403,429,503):
            self.f.lists={None:{'messages':[{'id':'a'}]}};self.f.fail[(API+'/messages/a',None,None)]=ApiError(status)
            with self.assertRaises(ApiError):sync(self.store,self.f,self.a)
            self.assertEqual(self.store.failure_count(self.a),0);self.assertIsNone(self.store.status(self.a)['checkpoint'])
    def test_empty_mailbox_has_profile_history_anchor(self):
        self.assertEqual(sync(self.store,self.f,self.a)['history_id'],'100');self.assertEqual(self.rows(),[])
    def test_missing_message_preserves_existing_and_does_not_skip_other_records(self):
        self.multi_page();sync(self.store,self.f,self.a);del self.f.messages['b'];self.f.histories[('130',None)]={'history':[{'labelsRemoved':[{'message':{'id':'b'}},{'message':{'id':'a'}}]}],'historyId':'140'}
        sync(self.store,self.f,self.a);rows={x[1]:x for x in self.rows()};self.assertEqual(rows['b'][3],'fetch_missing');self.assertIn('second',rows['b'][2]);self.assertEqual(rows['a'][3],'present')
    def test_invalid_response_and_loop_guard_preserve_cursor(self):
        self.multi_page();sync(self.store,self.f,self.a);self.f.histories[('130',None)]={'nextPageToken':'loop','historyId':'140'};self.f.histories[('130','loop')]={'nextPageToken':'loop','historyId':'140'}
        with self.assertRaises(InvalidResponse):sync(self.store,self.f,self.a)
        self.assertEqual(self.store.status(self.a)['checkpoint'],'130')
    def test_path_segments_and_history_regression_fail_closed(self):
        self.f.lists={None:{'messages':[{'id':'../fake'}]}}
        with self.assertRaises(InvalidResponse):sync(self.store,self.f,self.a)
        self.assertFalse(any(path.endswith('/fake') for path,_ in self.f.calls))
        self.f.lists={None:{'messages':[]}};self.f.histories[('100',None)]={'historyId':'99'}
        with self.assertRaises(InvalidResponse):sync(self.store,self.f,self.a)
        self.assertIsNone(self.store.status(self.a)['checkpoint'])
    def test_initial_catchup_expired_returns_full_required(self):
        self.f.fail[(API+'/history',None,'100')]=ApiError(404)
        self.assertEqual(sync(self.store,self.f,self.a)['reason'],'history_expired_during_full');self.assertEqual(self.store.status(self.a)['state'],'full_required')
    def test_store_is_private_and_never_adopts_unrelated_database(self):
        self.assertEqual(stat.S_IMODE(self.store.path.stat().st_mode),0o600)
        path=Path(self.tmp.name)/'unrelated.sqlite3'
        with sqlite3.connect(path) as db:db.execute('CREATE TABLE private_info(x)')
        os.chmod(path,0o600);before=hashlib.sha256(path.read_bytes()).digest()
        with self.assertRaisesRegex(ValueError,'unrelated_store'):Store(path)
        self.assertEqual(before,hashlib.sha256(path.read_bytes()).digest())
        link=Path(self.tmp.name)/'link.sqlite3';link.symlink_to(self.store.path)
        with self.assertRaises(OSError):Store(link)
    def test_account_file_lock_and_no_transaction_over_http(self):
        with self.store.lock(self.a):
            with self.assertRaises(SyncBusy):sync(self.store,self.f,self.a)
        original=self.f.get
        def checked(path,params):
            self.assertFalse(self.store.conn.in_transaction);return original(path,params)
        self.f.get=checked;sync(self.store,self.f,self.a)
    def test_missing_old_scan_records_not_deleted_or_assumed_gone(self):
        self.multi_page();sync(self.store,self.f,self.a);self.f.fail[(API+'/history',None,'130')]=ApiError(404);sync(self.store,self.f,self.a)
        del self.f.fail[(API+'/history',None,'130')];self.f.profile_id='200';self.f.lists={None:{'messages':[{'id':'a'}]}};self.f.histories[('200',None)]={'historyId':'201'};sync(self.store,self.f,self.a)
        self.assertEqual(len(self.rows()),4);old=self.store.conn.execute("SELECT last_seen_generation FROM gmail_messages WHERE account=? AND message_id='b'",(self.a,)).fetchone()[0]
        new=self.store.conn.execute("SELECT last_seen_generation FROM gmail_messages WHERE account=? AND message_id='a'",(self.a,)).fetchone()[0];self.assertNotEqual(old,new)
        states={r[1]:r[3] for r in self.rows()};self.assertEqual(states['b'],'not_seen_in_scan');self.assertEqual(states['a'],'present')
if __name__=='__main__':unittest.main()
