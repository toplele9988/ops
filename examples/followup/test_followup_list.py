import json,sqlite3,tempfile,threading,unittest
from pathlib import Path
from unittest.mock import patch

import conversation_notes as notes
import followup_list as listing
import index_migration as migration
import native_followup_source as source
import owner_inbox as inbox
import test_owner_inbox as fixtures
FakeRuntime=fixtures.FakeRuntime


class FollowupTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.InboxTest('test_owner_and_revocation');self.fixture.setUp()
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name).resolve()/'notes.sqlite3';notes.initialize(self.path)
        with sqlite3.connect(self.path) as db:migration.install_for_fixture(db,migration.NOTES)
        for channel,sql in (('wechat',migration.WECHAT),('alibaba',migration.ALIBABA),('whatsapp',migration.WHATSAPP)):
            with sqlite3.connect(self.fixture.paths[channel]) as db:
                migration.install_for_fixture(db,sql)
                if channel=='wechat':db.execute('CREATE INDEX test_scope_key ON message_scopes(app_id,message_id)')
        page=inbox.owner_page('owner-a',authority_factory=FakeRuntime)
        self.items=page['items']
        for i,item in enumerate(self.items):
            notes.save(item['key'],item['channel'],item['account'],item['scope'],item['conversation_id'],actor='fixture_operator_a',status=('pending','following','waiting_customer','waiting_supplier')[i],content='note-'+str(i),expected_version=0,path=self.path,clock=lambda:i+100)
    def tearDown(self):self.tmp.cleanup();self.fixture.tearDown()
    def verifier(self):return source.NativeVerifier('owner-a',factory=FakeRuntime)
    def page(self,status='all',cursor=None,limit=30):return listing.list_page(self.verifier(),status,cursor,limit,path=self.path)
    def test_each_status_and_exact_source_links(self):
        allrows=self.page()['items'];self.assertEqual(len(allrows),4)
        self.assertEqual({x['channel'] for x in allrows},{'wechat','alibaba','whatsapp'})
        for status in ('pending','following','waiting_customer','waiting_supplier'):
            page=self.page(status);self.assertEqual(len(page['items']),1);self.assertEqual(page['items'][0]['status'],status)
            self.assertEqual(page['items'][0]['detail_url'],'/v3/workbench?conversation='+page['items'][0]['key'])
        self.assertEqual(self.page('done')['items'],[])
    def test_pagination_status_bound_and_no_duplicates(self):
        seen=[];cursor=None
        while True:
            page=self.page('all',cursor,limit=1);seen.extend(x['key'] for x in page['items']);cursor=page['next_cursor']
            if cursor is None:break
        self.assertEqual(len(seen),4);self.assertEqual(len(set(seen)),4)
        first=self.page('all',limit=1)
        with self.assertRaises(ValueError):self.page('pending',first['next_cursor'])
    def test_group_revoked_and_missing_source_are_hidden(self):
        FakeRuntime.group_active=False
        with sqlite3.connect(self.fixture.paths['whatsapp']) as db:db.execute('DELETE FROM messages')
        page=self.page();self.assertTrue(page['partial']);self.assertTrue(all(x['channel']=='alibaba' for x in page['items']))
        self.assertNotIn('wa-one',str(page))
    def test_alibaba_authority_revocation_hides_notes(self):
        FakeRuntime.history.enabled=False;page=self.page()
        self.assertTrue(page['partial']);self.assertFalse(any(x['channel']=='alibaba' for x in page['items']))
    def test_revoke_during_request_discards_channel(self):
        with patch.object(FakeRuntime,'revalidate',side_effect=PermissionError('revoked')):
            page=self.page();self.assertTrue(page['partial']);self.assertFalse(any(x['channel']=='wechat' for x in page['items']))
    def test_whatsapp_swapped_binding_is_hidden(self):
        with sqlite3.connect(self.fixture.paths['whatsapp']) as db:db.execute("UPDATE binding SET account_id='other'")
        page=self.page();self.assertFalse(any(x['channel']=='whatsapp' for x in page['items']))
    def test_unindexed_sources_do_not_fall_back_to_table_scan(self):
        with sqlite3.connect(self.fixture.paths['alibaba']) as db:db.execute('DROP INDEX followup_alibaba_source')
        page=self.page();self.assertTrue(page['partial']);self.assertFalse(any(x['channel']=='alibaba' for x in page['items']))
        with sqlite3.connect(self.path) as db:db.execute('DROP INDEX followup_all_order')
        with self.assertRaises(listing.MissingIndex):self.page()
    def test_query_plans_use_specific_indexes(self):
        checks=[(self.path,"SELECT key FROM conversation_notes INDEXED BY followup_status_order WHERE status=? AND (updated_at,key)<(?,?) ORDER BY updated_at DESC,key DESC LIMIT ?",('pending',999,'f'*64,31),'followup_status_order'),
            (self.fixture.paths['alibaba'],"SELECT id FROM inbound INDEXED BY followup_alibaba_source WHERE CASE WHEN json_valid(payload) THEN json_extract(payload, '$.account') END=? AND CASE WHEN json_valid(payload) THEN json_extract(payload, '$.conversation_id') END=? AND state='archived' LIMIT ?",('fixture-account-a','1',100),'followup_alibaba_source'),
            (self.fixture.paths['wechat'],"SELECT message_id FROM messages INDEXED BY followup_wechat_source WHERE app_id=? AND group_id=? AND created_at>=? LIMIT ?",('app-a','group-a',0,100),'followup_wechat_source'),
            (self.fixture.paths['whatsapp'],"SELECT content FROM messages INDEXED BY followup_whatsapp_source WHERE session=? AND account_id=? AND owner=? AND scope=? AND chat_id=? LIMIT ?",('s','wa-a','owner-a','wa-scope','chat',100),'followup_whatsapp_source')]
        for path,sql,args,index in checks:
            with sqlite3.connect(path) as db:plan=' '.join(row[3] for row in db.execute('EXPLAIN QUERY PLAN '+sql,args))
            self.assertIn('SEARCH',plan);self.assertIn(index,plan)
    def test_two_operators_optimistic_conflict_and_shared_view(self):
        item=self.items[0];barrier=threading.Barrier(2);result=[];guard=threading.Lock()
        def save(actor,text):
            try:
                barrier.wait();notes.save(item['key'],item['channel'],item['account'],item['scope'],item['conversation_id'],actor=actor,status='done',content=text,expected_version=1,path=self.path)
                with guard:result.append('saved')
            except notes.Conflict:
                with guard:result.append('conflict')
        threads=[threading.Thread(target=save,args=('fixture_operator_a','first')),threading.Thread(target=save,args=('fixture_operator_b','second'))]
        for t in threads:t.start()
        for t in threads:t.join(timeout=5)
        self.assertCountEqual(result,['saved','conflict']);page=self.page('done');self.assertEqual(len(page['items']),1);self.assertIn(page['items'][0]['content'],('first','second'))
    def test_strict_scope_key_cannot_attach_to_other_source(self):
        item=self.items[0]
        with sqlite3.connect(self.path) as db:db.execute("UPDATE conversation_notes SET account='wrong-account' WHERE key=?",(item['key'],))
        page=self.page();self.assertFalse(any(x['key']==item['key'] for x in page['items']));self.assertTrue(page['partial'])
    def test_conflicting_buyer_names_fall_back_to_id(self):
        with sqlite3.connect(self.fixture.paths['alibaba']) as db:
            payload=json.loads(db.execute("SELECT payload FROM inbound WHERE id='a2'").fetchone()[0]);payload['buyer_name']='Different Synthetic Buyer'
            db.execute("UPDATE inbound SET payload=? WHERE id='a2'",(json.dumps(payload),))
        item=next(x for x in self.page()['items'] if x['channel']=='alibaba' and x['conversation_id']=='1')
        self.assertIsNone(item['display_name'])
    def test_large_denied_batch_is_bounded_and_can_continue(self):
        with sqlite3.connect(self.path) as db:
            rows=[(format(i,'064x'),'alibaba','account','scope','c-'+str(i),'pending','fixture',1,float(1000+i),'fixture_operator_a') for i in range(350)]
            db.executemany('INSERT INTO conversation_notes VALUES(?,?,?,?,?,?,?,?,?,?)',rows)
        class Verifier:
            def __init__(self):self.calls=0
            def resolve(self,row,after=None):
                self.calls+=1
                return {'display_name':None} if int(row['key'],16)<150 else None
            def revalidate(self):return set()
        v=Verifier();first=listing.list_page(v,'pending',limit=30,path=self.path)
        self.assertEqual((first['scanned'],v.calls,first['items']),(200,200,[]));self.assertTrue(first['partial']);self.assertIsNotNone(first['next_cursor'])
        second=listing.list_page(Verifier(),'pending',first['next_cursor'],30,path=self.path)
        self.assertGreater(len(second['items']),0);self.assertLessEqual(second['scanned'],listing.MAX_SCAN)
    def test_whatsapp_source_deleted_mid_request_is_hidden(self):
        verifier=self.verifier();original=verifier.revalidate;calls=[]
        def mutate():
            result=original();calls.append(1)
            if len(calls)==1:
                with sqlite3.connect(self.fixture.paths['whatsapp']) as db:db.execute('DELETE FROM messages')
            return result
        verifier.revalidate=mutate;page=listing.list_page(verifier,'all',path=self.path)
        self.assertTrue(page['partial']);self.assertFalse(any(x['channel']=='whatsapp' for x in page['items']))
    def test_status_change_mid_request_is_hidden(self):
        item=self.items[0]
        notes.save(item['key'],item['channel'],item['account'],item['scope'],item['conversation_id'],actor='fixture_operator_b',status='pending',content='pending fixture',expected_version=1,path=self.path)
        verifier=self.verifier();original=verifier.revalidate;calls=[]
        def mutate():
            result=original();calls.append(1)
            if len(calls)==1:
                notes.save(item['key'],item['channel'],item['account'],item['scope'],item['conversation_id'],actor='fixture_operator_a',status='done',content='done fixture',expected_version=2,path=self.path)
            return result
        verifier.revalidate=mutate;page=listing.list_page(verifier,'pending',path=self.path)
        self.assertTrue(page['partial']);self.assertFalse(any(x['key']==item['key'] for x in page['items']))
    def _continuation_fixture(self,channel):
        item=next(x for x in self.items if x['channel']==channel and (channel!='alibaba' or x['conversation_id']=='1'))
        with sqlite3.connect(self.path) as db:db.execute('UPDATE conversation_notes SET updated_at=99999 WHERE key=?',(item['key'],))
        with sqlite3.connect(self.fixture.paths[channel]) as db:
            if channel=='alibaba':
                for i in range(205):
                    payload={'account':'fixture-account-a','conversation_id':'1','content':'synthetic','role':'buyer','history':True,'test':True}
                    db.execute('INSERT INTO inbound VALUES(?,?,?,?,?)',(f'0-{i:04}','%s'%json.dumps(payload),'x','archived',101))
            elif channel=='whatsapp':
                for i in range(205):db.execute('INSERT INTO messages VALUES(?,?,?,?,?,?,?,?,?,?)',(f'0-{i:04}','s','wa-a','chat','inbound',None,101,101,'owner-a','wa-scope'))
            else:
                for i in range(205):
                    mid=f'0-{i:04}';db.execute('INSERT INTO messages VALUES(?,?,?,?,?,?)',('app-a',mid,'group-a','other',100,'{}'))
                    db.execute('INSERT INTO message_scopes VALUES(?,?,?,?,?,?)',('app-a',mid,'wx-a','profile','scope-a','customer'))
        return item
    def test_source_continuations_progress_for_all_channels(self):
        for channel in ('alibaba','whatsapp','wechat'):
            # Fresh test fixture for every channel, preserving exact scopes.
            with self.subTest(channel=channel):
                if channel!='alibaba':self.tearDown();self.setUp()
                item=self._continuation_fixture(channel)
                page=self.page();self.assertTrue(page['partial']);self.assertEqual(len(page['pending']),1)
                self.assertFalse(any(x['key']==item['key'] for x in page['items']))
                after=listing.decode_source_token(page['pending'][0]['token'],'all')[2]
                self.assertTrue(after)
                second=listing.verify_one(self.verifier(),'all',page['pending'][0]['token'],path=self.path)
                self.assertTrue(second['partial']);self.assertEqual(len(second['pending']),1)
                self.assertNotEqual(page['pending'][0]['token'],second['pending'][0]['token'])
                found=listing.verify_one(self.verifier(),'all',second['pending'][0]['token'],path=self.path)
                self.assertFalse(found['partial']);self.assertEqual([x['key'] for x in found['items']],[item['key']]);self.assertFalse(found['pending'])
    def test_continuation_does_not_bypass_authority_or_status(self):
        item=self._continuation_fixture('alibaba');page=self.page();token=page['pending'][0]['token']
        FakeRuntime.history.enabled=False
        result=listing.verify_one(self.verifier(),'all',token,path=self.path);self.assertFalse(result['items'])
        FakeRuntime.history.enabled=True
        with sqlite3.connect(self.path) as db:db.execute('DELETE FROM conversation_notes WHERE key=?',(item['key'],))
        result=listing.verify_one(self.verifier(),'all',token,path=self.path);self.assertFalse(result['items'])

if __name__=='__main__':unittest.main()
