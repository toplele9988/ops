import contextlib
import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from drafts import draft_once
from receiver import ENDPOINT, Rejected, Store, make_server, validate

ACCOUNT='synthetic-operator';TOKEN='synthetic-token-not-a-real-secret';HOST='example.invalid'

class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.store=Store(Path(self.tmp.name)/'inbound.sqlite3')
        self.payload={'account':ACCOUNT,'conversation_id':'thread-1','buyer_name':'Synthetic Buyer','content':'Do you have 380W moving head lights? Need 16.','url':'https://example.invalid/thread?token=fake','message_id':'msg-1','event_id':'event-1','history':False,'role':'buyer'}
    def tearDown(self):self.tmp.cleanup()
    def clean(self,payload=None):return validate(payload or self.payload,ACCOUNT,{HOST})
    def test_http_auth_archive_and_duplicate(self):
        server=make_server(self.store,ACCOUNT,{HOST},TOKEN);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();url='http://127.0.0.1:'+str(server.server_port)+ENDPOINT
        def request(token,payload):
            req=Request(url,data=json.dumps(payload).encode(),headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'},method='POST')
            with urlopen(req,timeout=3) as response:return response.status,json.loads(response.read())
        try:
            with self.assertRaises(HTTPError) as err:request('invalid',self.payload)
            self.assertEqual(err.exception.code,401)
            status,result=request(TOKEN,self.payload);self.assertEqual((status,result['state']),(201,'archived'))
            status,again=request(TOKEN,self.payload);self.assertEqual((status,again['duplicate'],again['id']),(200,True,result['id']))
            with contextlib.closing(self.store.connect()) as db:
                row=db.execute('SELECT payload FROM inbound').fetchone();self.assertNotIn('token=fake',row[0]);self.assertEqual(db.execute('SELECT count(*) FROM draft_jobs').fetchone()[0],0)
        finally:server.shutdown();server.server_close();thread.join(timeout=3)
    def test_history_seller_and_missing_proof_do_not_queue(self):
        for i,(history,role,validator) in enumerate(((True,'buyer',lambda p:True),(False,'seller',lambda p:True),(False,'buyer',None),(False,'buyer',lambda p:False))):
            p=dict(self.payload,message_id='msg-'+str(i),event_id='evt-'+str(i),history=history,role=role)
            self.assertEqual(self.store.receive(self.clean(p),validator)['state'],'archived')
        with contextlib.closing(self.store.connect()) as db:self.assertEqual(db.execute('select count(*) from draft_jobs').fetchone()[0],0)
    def test_validator_exception_fails_closed_to_archive(self):
        def broken(_):raise RuntimeError('synthetic')
        self.assertEqual(self.store.receive(self.clean(),broken)['state'],'archived')
    def test_draft_pipeline_injected_synthetic_models_only(self):
        row=self.store.receive(self.clean(),lambda p:True)
        calls=[]
        def retrieve(text):calls.append(('retrieve',text));return [{'model':'synthetic-380','power_w':380,'source':'synthetic fixture'}]
        def generate(message,facts):
            calls.append(('generate',facts));return 'Hello, the synthetic example is 380 W. Please confirm your requirements.'
        self.assertTrue(draft_once(self.store,retrieve,generate));self.assertFalse(draft_once(self.store,retrieve,generate))
        with contextlib.closing(self.store.connect()) as db:
            stored=db.execute('select state,draft from inbound where id=?',(row['id'],)).fetchone()
            self.assertEqual(stored['state'],'draft_ready');self.assertIn('380 W',stored['draft']);self.assertEqual(len(calls),2)
    def test_failure_is_recorded_without_provider_details(self):
        row=self.store.receive(self.clean(),lambda p:True)
        def fail(_):raise RuntimeError('synthetic secret provider detail')
        self.assertTrue(draft_once(self.store,fail,lambda m,f:'unused'))
        with contextlib.closing(self.store.connect()) as db:
            job=dict(db.execute('select state,error_code from draft_jobs where inbound_id=?',(row['id'],)).fetchone())
            self.assertEqual(job,{'state':'failed','error_code':'pipeline_failed'})
    def test_idempotency_conflict_and_no_history_upgrade(self):
        initial=dict(self.payload,history=True);row=self.store.receive(self.clean(initial),lambda p:True)
        with self.assertRaises(Rejected) as exc:self.store.receive(self.clean(),lambda p:True)
        self.assertEqual(exc.exception.status,409)
        with self.assertRaises(Rejected):self.store.receive(self.clean(dict(initial,content='different')),lambda p:True)
        with contextlib.closing(self.store.connect()) as db:self.assertEqual(db.execute('select count(*) from draft_jobs').fetchone()[0],0)
    def test_validate_explicit_identity_role_and_https_source(self):
        for changes in ({'account':'other'},{'message_id':None,'event_id':None},{'history':None},{'role':'invalid'},{'url':'https://evil.invalid/path'},{'url':'https://fake@example.invalid/path'},{'event_id':'contains space'}):
            with self.subTest(changes=changes):
                with self.assertRaises(Rejected):self.clean(dict(self.payload,**changes))
    def test_header_event_id_consistency(self):
        with self.assertRaises(Rejected):validate(self.payload,ACCOUNT,{HOST},'different-event')
        self.assertEqual(validate(self.payload,ACCOUNT,{HOST},'event-1')['event_id'],'event-1')
    def test_concurrent_replay_is_single_record(self):
        barrier=threading.Barrier(2);result=[];errors=[]
        def run():
            try:barrier.wait();result.append(self.store.receive(self.clean(),lambda p:True))
            except Exception as exc:errors.append(exc)
        threads=[threading.Thread(target=run) for _ in range(2)]
        for t in threads:t.start()
        for t in threads:t.join(timeout=5)
        self.assertFalse(errors);self.assertEqual(len(result),2);self.assertEqual(len({x['id'] for x in result}),1)
        with contextlib.closing(self.store.connect()) as db:self.assertEqual(db.execute('select count(*) from draft_jobs').fetchone()[0],1)

if __name__=='__main__':unittest.main()
