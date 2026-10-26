import io,json,ssl,tempfile,traceback,unittest
from pathlib import Path
from urllib.error import HTTPError,URLError
from urllib.parse import urlsplit,parse_qs
from gmail_sync import API, InvalidResponse, Store, sync
from gmail_transport import BASE, GmailHTTPError, GmailRestTransport, NoRedirect, TransportError

SECRET='synthetic-access-token-DO-NOT-LOG'
PRIVATE='synthetic-message-DO-NOT-LOG'

class Response:
 def __init__(self,status=200,body=b'{}',headers=None):self.status=status;self.body=body;self.headers=headers or {};self.closed=False
 def __enter__(self):return self
 def __exit__(self,*args):self.closed=True
 def read(self,n):return self.body[:n]

class FakeOpener:
 def __init__(self,answer):self.answer=answer;self.calls=[]
 def open(self,request,timeout):
  u=urlsplit(request.full_url)
  self.calls.append({'scheme':u.scheme,'host':u.netloc,'path':u.path,'query':parse_qs(u.query),'auth':request.get_header('Authorization'),'method':request.method,'timeout':timeout})
  return self.answer(request,self.calls[-1])

def ok(obj):return Response(body=json.dumps(obj).encode())

class TransportTests(unittest.TestCase):
 def transport(self,answer,**opts):
  fake=FakeOpener(answer);return GmailRestTransport(lambda:SECRET,opener=fake,**opts),fake
 def test_exact_https_endpoints_query_and_token_rotation(self):
  tokens=iter(['first','second','third','fourth']);fake=FakeOpener(lambda req,call:ok({'ok':True}));t=GmailRestTransport(lambda:next(tokens),opener=fake)
  t.get(API+'/profile',{});t.get(API+'/messages',{'maxResults':500,'pageToken':'a+/=','includeSpamTrash':'true'});t.get(API+'/history',{'startHistoryId':'123','pageToken':'p','maxResults':500});t.get(API+'/messages/abc123',{'format':'full'})
  self.assertEqual([x['auth'] for x in fake.calls],['Bearer first','Bearer second','Bearer third','Bearer fourth'])
  self.assertTrue(all(x['scheme']=='https' and x['host']=='gmail.googleapis.com' and x['method']=='GET' for x in fake.calls))
  self.assertEqual(fake.calls[1]['query']['pageToken'],['a+/=']);self.assertEqual(fake.calls[3]['query']['format'],['full'])
  self.assertNotIn('first',repr(t))
 def test_all_requested_http_errors_are_sanitized(self):
  for code,category in [(401,'authentication'),(403,'forbidden_or_quota'),(404,'not_found'),(429,'rate_limited'),(500,'server_error'),(503,'server_error'),(302,'redirect_denied')]:
   with self.subTest(code=code):
    def fail(req,call):raise HTTPError(req.full_url,code,SECRET,{'Retry-After':'12'},io.BytesIO(PRIVATE.encode()))
    t,fake=self.transport(fail)
    try:t.get(API+'/profile',{})
    except GmailHTTPError as exc:
     self.assertEqual((exc.status,exc.category,exc.retry_after_seconds),(code,category,12))
     self.assertNotIn(SECRET,repr(exc));self.assertNotIn(PRIVATE,''.join(traceback.format_exception(type(exc),exc,exc.__traceback__)))
     self.assertIsNone(exc.__context__)
    else:self.fail('expected error')
    self.assertEqual(len(fake.calls),1)
 def test_fake_returned_non_200_and_retry_after_bound(self):
  t,_=self.transport(lambda r,c:Response(429,headers={'Retry-After':'999999999'}))
  with self.assertRaises(GmailHTTPError) as e:t.get(API+'/profile',{})
  self.assertEqual(e.exception.category,'rate_limited');self.assertIsNone(e.exception.retry_after_seconds)
 def test_network_and_bad_token_sanitized(self):
  t,_=self.transport(lambda r,c:(_ for _ in ()).throw(URLError(SECRET)))
  with self.assertRaises(TransportError) as e:t.get(API+'/profile',{})
  self.assertEqual(str(e.exception),'network_error');self.assertIsNone(e.exception.__context__);self.assertNotIn(SECRET,''.join(traceback.format_exception(type(e.exception),e.exception,e.exception.__traceback__)))
  for provider in (lambda:'bad\ntoken',lambda:(_ for _ in ()).throw(ValueError(SECRET)),lambda:''):
   fake=FakeOpener(lambda r,c:ok({}));t=GmailRestTransport(provider,opener=fake)
   with self.assertRaises(TransportError) as e:t.get(API+'/profile',{})
   self.assertEqual(str(e.exception),'access_token_unavailable');self.assertEqual(fake.calls,[])
 def test_json_and_size_fail_closed_without_body_leak(self):
  for body,code in [(b'{"message":"'+PRIVATE.encode()+b'"','invalid_json_response'),(b'[1]','invalid_json_response'),(b'x'*100,'response_too_large'),(b'\xff','invalid_json_response')]:
   t,_=self.transport(lambda r,c:Response(body=body),max_bytes=64)
   with self.assertRaises(InvalidResponse) as e:t.get(API+'/profile',{})
   self.assertEqual(str(e.exception),code);self.assertNotIn(PRIVATE,''.join(traceback.format_exception(type(e.exception),e.exception,e.exception.__traceback__)))
 def test_url_allowlist_and_parameters(self):
  t,fake=self.transport(lambda r,c:ok({}))
  for path,params in [('https://evil.test',{}),(API+'/messages/send',{'format':'raw'}),(API+'/messages/..',{'format':'full'}),(API+'/messages/%2Fetc',{'format':'full'}),(API+'/profile',{'x':'bad'}),(API+'/history',{'startHistoryId':'x'}),(API+'/messages',{'maxResults':501}),(API+'/messages',{'includeSpamTrash':'yes'}),(API+'/messages/x',{'format':'raw'})]:
   with self.subTest(path=path):
    with self.assertRaises(ValueError):t.get(path,params)
  self.assertEqual(fake.calls,[])
 def test_redirect_handler_and_verified_tls_defaults(self):
  self.assertIsNone(NoRedirect().redirect_request(None,None,302,'',{},'https://evil.test/'))
  t=GmailRestTransport(lambda:SECRET)
  from urllib.request import HTTPSHandler
  h=next(h for h in t._opener.handlers if isinstance(h,HTTPSHandler));self.assertEqual(h._context.verify_mode,ssl.CERT_REQUIRED);self.assertTrue(h._context.check_hostname)
  self.assertTrue(any(isinstance(h,NoRedirect) for h in t._opener.handlers))
 def test_sync_with_fake_https_only_and_404_scope(self):
  requests=[]
  def respond(req,call):
   requests.append(call)
   if call['path']==API+'/profile':return ok({'emailAddress':'demo@example.test','historyId':'100'})
   if call['path']==API+'/messages':return ok({'messages':[{'id':'m1'}]})
   if call['path']==API+'/messages/m1':return ok({'id':'m1','threadId':'t1','payload':{'body':{'data':'FAKE'}}})
   if call['path']==API+'/history':return ok({'historyId':'101'})
   raise AssertionError('unknown')
  t,_=self.transport(respond)
  with tempfile.TemporaryDirectory() as d:
   store=Store(Path(d)/'mail.sqlite3')
   try:
    self.assertEqual(sync(store,t,'demo@example.test')['status'],'ready');self.assertEqual(len(requests),4)
    self.assertTrue(all(c['host']=='gmail.googleapis.com' for c in requests));self.assertEqual(store.conn.execute('select count(*) from gmail_messages').fetchone()[0],1)
   finally:store.close()
 def test_http_404_is_classified_by_operation(self):
  def respond(req,call):
   if call['path']==API+'/profile':return ok({'emailAddress':'demo@example.test','historyId':'100'})
   if call['path']==API+'/messages':return ok({'messages':[{'id':'m1'}]})
   if call['path']==API+'/messages/m1':raise HTTPError(req.full_url,404,'not found',{},io.BytesIO())
   if call['path']==API+'/history':return ok({'historyId':'101'})
   raise AssertionError('unknown')
  t,_=self.transport(respond)
  with tempfile.TemporaryDirectory() as d:
   store=Store(Path(d)/'mail.sqlite3')
   try:
    self.assertEqual(sync(store,t,'demo@example.test')['status'],'ready_with_failures')
    self.assertEqual(store.conn.execute('SELECT remote_state FROM gmail_messages').fetchone()[0],'fetch_missing')
    def expired(req,call):
     if call['path']==API+'/profile':return ok({'emailAddress':'demo@example.test','historyId':'110'})
     if call['path']==API+'/history':raise HTTPError(req.full_url,404,'expired',{},io.BytesIO())
     raise AssertionError('unknown')
    t2,_=self.transport(expired)
    self.assertEqual(sync(store,t2,'demo@example.test')['status'],'full_required')
    self.assertEqual(store.status('demo@example.test')['checkpoint'],'101')
   finally:store.close()
if __name__=='__main__':unittest.main()
