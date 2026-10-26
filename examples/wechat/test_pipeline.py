import contextlib
import hashlib
import hmac
import json
import tempfile
import threading
import unittest
from pathlib import Path

from knowledge import work_once
from receiver import Archive,Rejected,canonical


class Tests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.archive=Archive(Path(self.temp.name)/'fixture.sqlite3')
        self.message={'account_id':'example-account','conversation_id':'example-business-group','message_id':'msg-1','sender_type':'buyer','text':'Need 16 synthetic moving head lights.'}
        self.raw=canonical(self.message).encode();self.fixture_key=b'synthetic-test-key'
        self.signature=hmac.new(self.fixture_key,self.raw,hashlib.sha256).digest()
    def tearDown(self):self.temp.cleanup()
    def verify(self,raw):return hmac.compare_digest(self.signature,hmac.new(self.fixture_key,raw,hashlib.sha256).digest())
    def allow(self,msg):return (msg['account_id'],msg['conversation_id'])==('example-account','example-business-group')
    def test_verified_callback_duplicate_and_scope(self):
        row=self.archive.accept(self.raw,self.verify,self.allow);self.assertTrue(row['queued']);again=self.archive.accept(self.raw,self.verify,self.allow)
        self.assertTrue(again['duplicate']);self.assertEqual(again['id'],row['id'])
        with contextlib.closing(self.archive.connect()) as db:self.assertEqual((db.execute('select count(*) from messages').fetchone()[0],db.execute('select count(*) from knowledge_jobs').fetchone()[0]),(1,1))
    def test_missing_auth_fails_closed(self):
        with self.assertRaises(Rejected):self.archive.accept(self.raw,lambda _:False,self.allow)
        with self.assertRaises(Rejected):self.archive.accept(self.raw,None,self.allow)
        with contextlib.closing(self.archive.connect()) as db:self.assertEqual(db.execute('select count(*) from messages').fetchone()[0],0)
    def test_unapproved_group_archives_without_learning(self):
        row=self.archive.accept(self.raw,self.verify,lambda _:False);self.assertFalse(row['queued'])
        self.assertFalse(work_once(self.archive,lambda _:[]))
    def test_unauthorized_account_and_same_name_are_not_merged(self):
        row=self.archive.accept(self.raw,self.verify,self.allow)
        other=dict(self.message,account_id='another-account');raw=canonical(other).encode()
        result=self.archive.accept(raw,lambda _:True,self.allow);self.assertNotEqual(result['id'],row['id']);self.assertFalse(result['queued'])
    def test_changed_content_same_identity_conflicts(self):
        self.archive.accept(self.raw,self.verify,self.allow)
        changed=canonical(dict(self.message,text='Changed')).encode()
        with self.assertRaises(Rejected):self.archive.accept(changed,lambda _:True,self.allow)
    def test_supported_fact_with_traceable_quote(self):
        row=self.archive.accept(self.raw,self.verify,self.allow)
        self.assertTrue(work_once(self.archive,lambda msg:[{'fact':'Buyer requests 16 lights','source_quote':'Need 16 synthetic moving head lights.'}]))
        self.assertFalse(work_once(self.archive,lambda msg:[]))
        with contextlib.closing(self.archive.connect()) as db:
            self.assertEqual(db.execute('select state from knowledge_jobs where message_pk=?',(row['id'],)).fetchone()[0],'done')
            fact=db.execute('select fact,source_quote from knowledge_facts where message_pk=?',(row['id'],)).fetchone()
            self.assertEqual(tuple(fact),('Buyer requests 16 lights','Need 16 synthetic moving head lights.'))
    def test_unverifiable_quote_fails_without_claim_or_leak(self):
        row=self.archive.accept(self.raw,self.verify,self.allow)
        work_once(self.archive,lambda msg:[{'fact':'fabricated','source_quote':'not in source'}])
        with contextlib.closing(self.archive.connect()) as db:
            self.assertEqual(tuple(db.execute('select state,error_code from knowledge_jobs where message_pk=?',(row['id'],)).fetchone()),('failed','extraction_failed'))
            self.assertEqual(db.execute('select count(*) from knowledge_facts').fetchone()[0],0)
    def test_no_facts_not_misreported_success(self):
        row=self.archive.accept(self.raw,self.verify,self.allow);work_once(self.archive,lambda msg:[])
        with contextlib.closing(self.archive.connect()) as db:self.assertEqual(db.execute('select state from knowledge_jobs where message_pk=?',(row['id'],)).fetchone()[0],'no_supported_facts')
    def test_extraction_runs_without_db_write_transaction(self):
        self.archive.accept(self.raw,self.verify,self.allow)
        def extractor(_):
            with contextlib.closing(self.archive.connect()) as other:
                other.execute('BEGIN IMMEDIATE');other.execute('UPDATE knowledge_jobs SET error_code=NULL');other.commit()
            return []
        self.assertTrue(work_once(self.archive,extractor))
        with contextlib.closing(self.archive.connect()) as db:self.assertEqual(db.execute('select state from knowledge_jobs').fetchone()[0],'no_supported_facts')
    def test_concurrent_callback_deduplicates(self):
        barrier=threading.Barrier(2);results=[];errors=[]
        def run():
            try:barrier.wait();results.append(self.archive.accept(self.raw,self.verify,self.allow))
            except Exception as e:errors.append(e)
        threads=[threading.Thread(target=run) for _ in range(2)]
        for t in threads:t.start()
        for t in threads:t.join(timeout=5)
        self.assertFalse(errors);self.assertEqual(len(results),2);self.assertEqual(len({x['id'] for x in results}),1)

if __name__=='__main__':unittest.main()
