import json
import sqlite3
import tempfile
import unittest
import types
from unittest.mock import patch
from pathlib import Path

from owner_inbox import NativeAuthority, list_conversations, conversation_detail


class History:
    enabled=True
    def check_authority(self, auth):
        if not self.enabled:raise ValueError('revoked')
    def scope_for(self,auth,account,conversation):
        if account!='fixture-account-a':raise ValueError('account_not_registered')
        return 'ali:'+conversation


class FakeRuntime:
    owner_id='owner-a'; group_active=True; history=History(); paths={}; revoke_during_read=False; checks=0
    def __init__(self, owner):
        if owner!=self.owner_id:raise PermissionError('owner_required')
        self.owner=owner;self.wechat_path=self.paths['wechat'];self.ali_path=self.paths['alibaba'];self.wa_path=self.paths['whatsapp']
        self.ali_auth={'owner':owner};self.group_total=2;self.wa_pairs=[(owner,'wa-scope')]
    def group_grants(self,db):
        if not self.group_active:return []
        return [{'channel':'wechat','owner':self.owner,'account':'wx-a','app_id':'app-a','profile':'profile',
                 'scope':'scope-a','source_scope':'group:profile:scope-a','conversation':'group-a',
                 'role':'customer','cutoff':100,'registration':'valid','display_name':'Business Group'}]
    def revalidate(self,db,groups):
        type(self).checks+=1
        if self.revoke_during_read and self.checks>=2:raise PermissionError('authorization_changed')
        if groups!=self.group_grants(db):raise PermissionError('authorization_changed')
    def ali_record(self,row):
        p=json.loads(row['payload'])
        if p.get('account')!='fixture-account-a' or p.get('history') is not True or row['state']!='archived' or p.get('test') or p.get('pwa'):
            raise ValueError('excluded')
        return {'account':'fixture-account-a','scope':'ali:'+p['conversation_id'],'conversation_id':p['conversation_id'],
                'sent_at':None,'body':p['content'],'role':p['role'],'inbound_id':row['id']}


class InboxTest(unittest.TestCase):
    def setUp(self):
        self.t=tempfile.TemporaryDirectory();root=Path(self.t.name)
        self.paths={x:str(root/(x+'.db')) for x in ('wechat','alibaba','whatsapp')}
        with sqlite3.connect(self.paths['wechat']) as d:
            d.executescript('''CREATE TABLE messages(app_id TEXT,message_id TEXT,group_id TEXT,sender_id TEXT,created_at REAL,payload TEXT);
              CREATE TABLE message_scopes(app_id TEXT,message_id TEXT,own_wxid TEXT,profile_id TEXT,scope_id TEXT,business_role TEXT);''')
            def row(mid,app='app-a',group='group-a',own='wx-a',role='customer',ts=101,content='ok',payload_own=None):
                payload={'app_id':app,'message_id':mid,'group_id':group,'sender_id':'other','created_at':ts,'own_wxid':payload_own or own,'text':content}
                d.execute('INSERT INTO messages VALUES(?,?,?,?,?,?)',(app,mid,group,'other',ts,json.dumps(payload)))
                d.execute('INSERT INTO message_scopes VALUES(?,?,?,?,?,?)',(app,mid,own,'profile','scope-a',role))
            row('w1',content='wx-one');row('w2',content='wx-two')
            row('bad-app',app='other-app',content='secret-app');row('bad-own',own='wx-b',content='secret-own')
            row('bad-group',group='group-b',content='secret-group');row('bad-role',role='supplier',content='secret-role')
            row('bad-time',ts=99,content='secret-old');row('bad-payload',payload_own='wx-b',content='secret-payload')
        with sqlite3.connect(self.paths['alibaba']) as d:
            d.execute('CREATE TABLE inbound(id TEXT,payload TEXT,fingerprint TEXT,state TEXT,created_at REAL)')
            for mid,account,cid,body,extra in [('a1','fixture-account-a','1','ali-one',{}),('a2','fixture-account-a','1','ali-two',{}),
                ('a3','fixture-account-a','2','ali-other',{}),('a4','Other','1','secret-other',{}),('test','fixture-account-a','1','secret-test',{'test':True}),
                ('pwa','fixture-account-a','1','secret-pwa',{'pwa':True})]:
                p={'account':account,'conversation_id':cid,'content':body,'role':'buyer','history':True,'buyer_name':'Same Buyer',**extra}
                d.execute('INSERT INTO inbound VALUES(?,?,?,?,?)',(mid,json.dumps(p),'x','archived',101))
        with sqlite3.connect(self.paths['whatsapp']) as d:
            d.executescript('''CREATE TABLE binding(session TEXT,account_id TEXT,owner TEXT,scope TEXT);
            CREATE TABLE messages(message_key TEXT,session TEXT,account_id TEXT,chat_id TEXT,direction TEXT,content TEXT,sent_at REAL,received REAL,owner TEXT,scope TEXT);''')
            d.execute('INSERT INTO binding VALUES(?,?,?,?)',('s','wa-a','owner-a','wa-scope'))
            d.execute('INSERT INTO messages VALUES(?,?,?,?,?,?,?,?,?,?)',('wa1','s','wa-a','chat','inbound','wa-one',101,101,'owner-a','wa-scope'))
            d.execute('INSERT INTO messages VALUES(?,?,?,?,?,?,?,?,?,?)',('wa2','s','wa-a','chat','inbound','secret-owner',101,101,'owner-b','wa-scope'))
        FakeRuntime.paths=self.paths;FakeRuntime.group_active=True;FakeRuntime.history.enabled=True
        FakeRuntime.revoke_during_read=False;FakeRuntime.checks=0
    def tearDown(self):self.t.cleanup()

    def test_exact_source_boundaries_and_no_synthetic_or_pwa(self):
        r=list_conversations('owner-a',authority_factory=FakeRuntime)
        self.assertEqual(len(r['items']),4)
        self.assertEqual(r['channels']['wechat']['status'],'partial')
        self.assertNotIn('secret-',str(r))
        combined=[]
        for item in r['items']:
            detail=conversation_detail('owner-a',item['key'],authority_factory=FakeRuntime)
            combined += [x['text'] for x in detail['items']]
        self.assertEqual(set(combined),{'wx-one','wx-two','ali-one','ali-two','ali-other','wa-one'})

    def test_owner_and_revocation(self):
        with self.assertRaises(PermissionError):list_conversations('owner-b',authority_factory=FakeRuntime)
        r=list_conversations('owner-a',authority_factory=FakeRuntime)
        key=next(x['key'] for x in r['items'] if x['channel']=='wechat')
        FakeRuntime.group_active=False
        self.assertEqual(conversation_detail('owner-a',key,authority_factory=FakeRuntime)['status'],'unavailable')
        FakeRuntime.history.enabled=False
        r=list_conversations('owner-a',authority_factory=FakeRuntime)
        self.assertEqual(r['channels']['alibaba']['status'],'unavailable')
        self.assertFalse(any(x['channel']=='alibaba' for x in r['items']))

    def test_list_and_detail_keyset_pagination(self):
        full=list_conversations('owner-a',authority_factory=FakeRuntime)
        cursor=None;keys=[]
        for i in range(8):
            r=list_conversations('owner-a',limit=1,cursor=cursor,authority_factory=FakeRuntime)
            keys.extend(x['key'] for x in r['items']);cursor=r['next_cursor']
            if not cursor:break
        self.assertEqual(set(keys),{x['key'] for x in full['items']});self.assertEqual(len(keys),len(set(keys)))
        ali=next(x for x in full['items'] if x['channel']=='alibaba' and x['conversation_id']=='1')
        first=conversation_detail('owner-a',ali['key'],limit=1,authority_factory=FakeRuntime)
        second=conversation_detail('owner-a',ali['key'],limit=1,cursor=first['next_cursor'],authority_factory=FakeRuntime)
        self.assertEqual({first['items'][0]['text'],second['items'][0]['text']},{'ali-one','ali-two'})
        other=next(x['key'] for x in full['items'] if x['key']!=ali['key'])
        with self.assertRaises(ValueError):conversation_detail('owner-a',other,cursor=first['next_cursor'],authority_factory=FakeRuntime)

    def test_revocation_during_other_source_scan_fails_closed(self):
        FakeRuntime.revoke_during_read=True
        result=list_conversations('owner-a',authority_factory=FakeRuntime)
        self.assertEqual(result['channels']['wechat']['status'],'unavailable')
        self.assertFalse(any(x['channel']=='wechat' for x in result['items']))
        self.assertEqual(result['channels']['alibaba']['status'],'ok')
        self.assertIsNone(result['next_cursor'])

    def test_whatsapp_scope_owner_binding_mismatch_does_not_leak(self):
        with sqlite3.connect(self.paths['whatsapp']) as d:
            d.execute("UPDATE binding SET owner='owner-b'")
        result=list_conversations('owner-a',authority_factory=FakeRuntime)
        self.assertEqual(result['channels']['whatsapp']['status'],'unavailable')
        self.assertFalse(any(x['channel']=='whatsapp' for x in result['items']))



    def test_source_bound_display_names_without_merging(self):
        page=list_conversations('owner-a',authority_factory=FakeRuntime)
        ali=[x for x in page['items'] if x['channel']=='alibaba']
        self.assertEqual(len(ali),2)
        self.assertEqual({x['display_name'] for x in ali},{'Same Buyer'})
        self.assertTrue(all(x['latest_time_basis']=='received' for x in ali))
        self.assertNotEqual(ali[0]['key'],ali[1]['key'])
        group=next(x for x in page['items'] if x['channel']=='wechat')
        self.assertEqual(group['display_name'],'Business Group')
        self.assertEqual(group['latest_time_basis'],'source')
        detail=conversation_detail('owner-a',ali[0]['key'],authority_factory=FakeRuntime)
        self.assertEqual(detail['display_name'],'Same Buyer')
        self.assertEqual(detail['items'][0]['time_basis'],'received')
        group_detail=conversation_detail('owner-a',group['key'],authority_factory=FakeRuntime)
        self.assertEqual(group_detail['items'][0]['time_basis'],'source')

    def test_conflicting_names_fall_back_to_identifier(self):
        with sqlite3.connect(self.paths['alibaba']) as d:
            row=d.execute("SELECT payload FROM inbound WHERE id='a2'").fetchone()
            payload=json.loads(row[0]);payload['buyer_name']='Other Buyer'
            d.execute("UPDATE inbound SET payload=? WHERE id='a2'",(json.dumps(payload),))
        page=list_conversations('owner-a',authority_factory=FakeRuntime)
        item=next(x for x in page['items'] if x['channel']=='alibaba' and x['conversation_id']=='1')
        self.assertIsNone(item['display_name'])
        self.assertIsNone(conversation_detail('owner-a',item['key'],authority_factory=FakeRuntime)['display_name'])

    def test_invalid_names_never_replace_source_id(self):
        from owner_inbox import _display_name
        for name in ('unknown','None','   ','Foo\nBar','\u202eSpoof', 'x'*129, None):
            self.assertIsNone(_display_name(name))
        self.assertEqual(_display_name(' 示例 名称 '),'示例 名称')

    def test_uncertain_group_failure_stops_pagination(self):
        with patch.object(FakeRuntime,'group_grants',side_effect=sqlite3.OperationalError('temporary')):
            result=list_conversations('owner-a',limit=1,authority_factory=FakeRuntime)
        self.assertEqual(result['channels']['wechat']['status'],'unavailable')
        self.assertFalse(result['pagination_complete'])
        self.assertIsNone(result['next_cursor'])
        self.assertFalse(any(x['channel']=='wechat' for x in result['items']))

    def test_whatsapp_binding_removed_or_swapped_during_scan(self):
        original=FakeRuntime.revalidate
        for change in ('delete','swap'):
            calls=[0]
            def mutate(runtime,db,groups):
                original(runtime,db,groups);calls[0]+=1
                if calls[0]==2:
                    with sqlite3.connect(self.paths['whatsapp']) as w:
                        if change=='delete':w.execute('DELETE FROM binding')
                        else:w.execute("UPDATE binding SET account_id='new-account'")
            with patch.object(FakeRuntime,'revalidate',new=mutate):
                result=list_conversations('owner-a',authority_factory=FakeRuntime)
            self.assertEqual(result['channels']['whatsapp']['status'],'unavailable')
            self.assertFalse(any(x['channel']=='whatsapp' for x in result['items']))
            self.assertIsNone(result['next_cursor'])
            with sqlite3.connect(self.paths['whatsapp']) as w:
                w.execute('DELETE FROM binding')
                w.execute('INSERT INTO binding VALUES(?,?,?,?)',('s','wa-a','owner-a','wa-scope'))



if __name__=='__main__':unittest.main()
