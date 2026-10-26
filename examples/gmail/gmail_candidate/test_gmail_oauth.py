import base64,contextlib,hashlib,io,json,os,re,stat,tempfile,threading,traceback,unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import parse_qs,urlsplit,urlencode
from urllib.request import Request,urlopen
import gmail_oauth as go
from encrypted_vault import EncryptedVault,VaultError,read_file,unseal

TOKEN='test-access-secret'; REFRESH='test-refresh-secret'; CLIENT='test-client-secret'
class Reply:
 def __init__(self,obj,status=200):self.status=status;self.body=json.dumps(obj).encode();self.headers={}
 def __enter__(self):return self
 def __exit__(self,*a):pass
 def read(self,n):return self.body[:n]
class Opener:
 def __init__(self,respond):self.respond=respond;self.calls=[]
 def open(self,req,timeout):
  self.calls.append((req.full_url,req.method,dict(req.headers),req.data,timeout));return self.respond(req)
class OAuthTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.path=self.root/'gmail-credential.enc';self.vault=EncryptedVault(self.path,lambda:b'K'*32);self.client_vault=EncryptedVault(self.root/'desktop-client.enc',lambda:b'K'*32)
  self.client={'client_id':'fake.apps.googleusercontent.com','client_secret':CLIENT};self.account='demo@example.test'
  self.token={'access_token':TOKEN,'token_type':'Bearer','expires_in':3600,'scope':go.READ_SCOPE,'refresh_token':REFRESH}
 def tearDown(self):self.tmp.cleanup()
 def profile(self,email=None):return Opener(lambda req:Reply({'emailAddress':email or self.account,'historyId':'100'}))
 def saved(self):return go.save_authorized(self.vault,self.client,self.account,self.token,profile_opener=self.profile())
 def test_pkce_authorization_url_has_pinned_endpoints(self):
  url,state,verifier,redirect=go.new_authorization(self.client['client_id'],18792);u=urlsplit(url);q=parse_qs(u.query)
  self.assertEqual(u.scheme+'://'+u.netloc+u.path,go.AUTH);self.assertEqual(q['scope'],[go.READ_SCOPE]);self.assertEqual(q['state'],[state]);self.assertEqual(q['code_challenge_method'],['S256']);self.assertEqual(q['access_type'],['offline']);self.assertEqual(q['redirect_uri'],[redirect]);self.assertNotIn(verifier,url)
  self.assertEqual(q['code_challenge'][0],base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode())
 def test_account_and_scopes_verified_before_save(self):
  with self.assertRaisesRegex(go.OAuthError,'account_mismatch'):go.save_authorized(self.vault,self.client,self.account,self.token,profile_opener=self.profile('other@example.test'))
  self.assertFalse(self.path.exists())
  with self.assertRaisesRegex(go.OAuthError,'required_scope_denied'):go.save_authorized(self.vault,self.client,self.account,dict(self.token,scope='other'),profile_opener=self.profile())
  self.assertFalse(self.path.exists());self.assertEqual(self.saved()['credential_saved'],True)
  self.assertEqual(stat.S_IMODE(self.path.stat().st_mode),0o600);self.assertNotIn(TOKEN.encode(),self.path.read_bytes());self.assertNotIn(REFRESH.encode(),self.path.read_bytes());self.assertEqual(self.vault.load(self.account)['refresh_token'],REFRESH)
  with self.assertRaises(VaultError):self.saved()
 def test_desktop_client_requires_pinned_google_urls_and_private_files(self):
  config={'account':self.account,'installed':{**self.client,'token_uri':go.TOKEN,'auth_uri':'https://accounts.google.com/o/oauth2/auth'}}
  self.client_vault.create(self.account,config);self.assertEqual(go.desktop_client(self.client_vault,self.account),self.client)
  self.assertNotIn(CLIENT.encode(),self.client_vault.path.read_bytes());os.chmod(self.client_vault.path,0o644)
  with self.assertRaisesRegex(VaultError,'unsafe_vault_file'):go.desktop_client(self.client_vault,self.account)
  os.chmod(self.client_vault.path,0o600);bad={'account':self.account,'installed':{**config['installed'],'token_uri':'https://evil.test'}}
  self.client_vault.replace(self.account,config,bad)
  with self.assertRaisesRegex(go.OAuthError,'invalid_desktop_client'):go.desktop_client(self.client_vault,self.account)
 def test_symlink_and_hardlink_rejected(self):
  self.saved();link=self.root/'link';link.symlink_to(self.path);vault=EncryptedVault(link,lambda:b'K'*32)
  with self.assertRaises(VaultError):vault.load(self.account)
  link.unlink();os.link(self.path,link)
  with self.assertRaises(VaultError):self.vault.load(self.account)
 def test_token_exchange_offline_endpoint_and_error_sanitization(self):
  fake=Opener(lambda req:Reply(self.token));client=go.TokenClient(fake)
  form={**self.client,'code':'fake-code','code_verifier':'fake-verifier','grant_type':'authorization_code','redirect_uri':'http://127.0.0.1:18792/oauth/callback'}
  self.assertEqual(client.post(form)['access_token'],TOKEN);url,method,headers,body,_=fake.calls[0]
  self.assertEqual((url,method),(go.TOKEN,'POST'));self.assertIn(b'code_verifier',body)
  evil=dict(form,redirect_uri='http://evil.test/oauth/callback')
  with self.assertRaises(go.OAuthError):client.post(evil)
  def fail(req):raise HTTPError(req.full_url,400,'fake-secret',{},io.BytesIO(b'{"error":"invalid_grant","error_description":"do-not-log"}'))
  with self.assertRaises(go.OAuthError) as e:go.TokenClient(Opener(fail)).post(form)
  self.assertEqual(str(e.exception),'invalid_grant');self.assertNotIn('do-not-log',''.join(traceback.format_exception(type(e.exception),e.exception,e.exception.__traceback__)))
 def test_refresh_cache_and_rotation_with_backup(self):
  self.saved();calls=[]
  class Client:
   def post(self,form):calls.append(form);return {'access_token':'refreshed','token_type':'Bearer','expires_in':300,'refresh_token':'new-refresh'}
  provider=go.TokenProvider(self.vault,self.account,Client(),clock=lambda:1000)
  self.assertEqual(provider(),'refreshed');self.assertEqual(provider(),'refreshed');self.assertEqual(len(calls),1)
  self.assertEqual(go.load_credentials(self.vault,self.account)['refresh_token'],'new-refresh')
  backups=list(self.root.glob('gmail-credential.enc.backup-*'));self.assertEqual(len(backups),1);self.assertEqual(unseal(read_file(backups[0]),self.account,lambda:b'K'*32)['refresh_token'],REFRESH);self.assertEqual(stat.S_IMODE(backups[0].stat().st_mode),0o600)
 def test_explicit_reauthorization_backs_up_encrypted_and_rejects_stale(self):
  self.saved();prior=go.load_credentials(self.vault,self.account)
  replacement=dict(self.token,refresh_token='replacement-refresh')
  result=go.save_authorized(self.vault,self.client,self.account,replacement,profile_opener=self.profile(),prior=prior)
  self.assertTrue(result['prior_backed_up']);self.assertEqual(go.load_credentials(self.vault,self.account)['refresh_token'],'replacement-refresh')
  backup=list(self.root.glob('gmail-credential.enc.backup-*'));self.assertEqual(len(backup),1)
  self.assertNotIn(REFRESH.encode(),backup[0].read_bytes());self.assertEqual(unseal(read_file(backup[0]),self.account,lambda:b'K'*32)['refresh_token'],REFRESH)
  before=self.path.read_bytes()
  with self.assertRaisesRegex(VaultError,'vault_changed'):go.save_authorized(self.vault,self.client,self.account,self.token,profile_opener=self.profile(),prior=prior)
  self.assertEqual(before,self.path.read_bytes())
 def test_credential_binding_and_denied_refresh_preserve_file(self):
  self.saved();before=self.path.read_bytes()
  with self.assertRaisesRegex(VaultError,'account_binding_invalid'):go.load_credentials(self.vault,'other@example.test')
  class Client:
   def post(self,form):raise go.OAuthError('invalid_grant')
  with self.assertRaisesRegex(go.OAuthError,'invalid_grant'):go.TokenProvider(self.vault,self.account,Client())()
  self.assertEqual(before,self.path.read_bytes())
 def test_local_callback_rejects_wrong_state_then_accepts_one_code(self):
  import socket
  with socket.socket() as s:s.bind(('127.0.0.1',0));port=s.getsockname()[1]
  state='a'*43;server,result=go.callback_server(port,state)
  thread=threading.Thread(target=lambda:[server.handle_request(),server.handle_request()],daemon=True);thread.start()
  try:
   with self.assertRaises(HTTPError):urlopen(f'http://127.0.0.1:{port}/oauth/callback?'+urlencode({'state':'b'*43,'code':'bad'}),timeout=2)
   with urlopen(f'http://127.0.0.1:{port}/oauth/callback?'+urlencode({'state':state,'code':'good-code'}),timeout=2) as response:
    body=response.read().decode();self.assertNotIn('good-code',body);self.assertEqual(response.headers['Cache-Control'],'no-store')
   thread.join(timeout=2);self.assertEqual(result['code'],'good-code')
  finally:server.server_close()
 def test_full_authorize_and_first_sync_are_mocked_and_token_stays_private(self):
  config={'account':self.account,'installed':{**self.client,'token_uri':go.TOKEN,'auth_uri':'https://accounts.google.com/o/oauth2/auth'}}
  self.client_vault.create(self.account,config)
  class Server:
   def __init__(self,result):self.result=result
   def __enter__(self):return self
   def __exit__(self,*a):pass
   def handle_request(self):self.result['code']='synthetic-code'
  def callback(port,state):
   result={'code':None,'denied':False};return Server(result),result
  class TokenClient:
   def post(self,form):return dict(self_outer.token)
  self_outer=self; original_save=go.save_authorized
  def save(vault,client,account,data,prior=None):return original_save(vault,client,account,data,profile_opener=self.profile(),prior=prior)
  output=io.StringIO()
  with patch.object(go,'callback_server',callback),patch.object(go,'TokenClient',TokenClient),patch.object(go,'save_authorized',save),contextlib.redirect_stdout(output):
   self.assertTrue(go.authorize(self.root,self.account,18792,vault=self.vault,client_vault=self.client_vault)['credential_saved'])
  for secret in (TOKEN,REFRESH,CLIENT,'synthetic-code'):self.assertNotIn(secret,output.getvalue())
  class Refresh:
   def post(self,form):return {'access_token':'refreshed-access','token_type':'Bearer','expires_in':300}
  provider=go.TokenProvider(self.vault,self.account,Refresh(),clock=lambda:1000)
  def gmail(req):
   u=urlsplit(req.full_url)
   if u.path==go.API+'/profile':return Reply({'emailAddress':self.account,'historyId':'100'})
   if u.path==go.API+'/messages':return Reply({'messages':[{'id':'msg1'}]})
   if u.path==go.API+'/messages/msg1':return Reply({'id':'msg1','threadId':'thread1','payload':{'mimeType':'text/plain'}})
   if u.path==go.API+'/history':return Reply({'historyId':'101'})
   raise AssertionError('unexpected fake path')
  store=go.Store(self.root/'gmail.sqlite3')
  try:self.assertEqual(go.sync(store,go.RefreshingTransport(provider,opener=Opener(gmail)),self.account)['status'],'ready')
  finally:store.close()
  self.assertNotIn(TOKEN.encode(),self.path.read_bytes());self.assertNotIn(b'refreshed-access',self.path.read_bytes())
 def test_401_refreshes_once_but_other_errors_do_not_retry(self):
  self.saved();refresh_calls=[]
  class Client:
   def post(self,form):refresh_calls.append(1);return {'access_token':'access-'+str(len(refresh_calls)),'token_type':'Bearer','expires_in':300}
  provider=go.TokenProvider(self.vault,self.account,Client(),clock=lambda:1000);counter=[]
  def respond(req):
   counter.append(req.get_header('Authorization'))
   if len(counter)==1:raise HTTPError(req.full_url,401,'not logged',{},io.BytesIO())
   return Reply({'emailAddress':self.account,'historyId':'100'})
  self.assertEqual(go.RefreshingTransport(provider,opener=Opener(respond)).get(go.API+'/profile',{})['historyId'],'100')
  self.assertEqual(counter,['Bearer access-1','Bearer access-2']);self.assertEqual(len(refresh_calls),2)
  attempts=[]
  def denied(req):attempts.append(1);raise HTTPError(req.full_url,401,'not logged',{},io.BytesIO())
  with self.assertRaises(go.GmailHTTPError):go.RefreshingTransport(provider,opener=Opener(denied)).get(go.API+'/profile',{})
  self.assertEqual(len(attempts),2)
 def test_credential_lock_excludes_a_second_process(self):
  import select,subprocess,sys
  code="import sys;from gmail_oauth import credential_lock\nwith credential_lock(sys.argv[1]): print('acquired',flush=True)"
  child=None
  try:
   with go.credential_lock(self.path):
    child=subprocess.Popen([sys.executable,'-c',code,str(self.path)],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    self.assertFalse(select.select([child.stdout],[],[],0.2)[0])
   self.assertTrue(select.select([child.stdout],[],[],3)[0])
   self.assertEqual(child.stdout.readline().strip(),b'acquired');self.assertEqual(child.wait(timeout=3),0)
  finally:
   if child is not None:
    if child.poll() is None:child.kill();child.wait()
    child.stdout.close();child.stderr.close()
 def test_diagnostics_are_useful_and_never_return_exception_text(self):
  self.assertEqual(go.safe_failure(go.GmailHTTPError(403,12)),{'status':'failed','error':'gmail_http_error','http_status':403,'category':'forbidden_or_quota','retry_after_seconds':12})
  self.assertEqual(go.safe_failure(go.GmailHTTPError(429,2))['category'],'rate_limited')
  self.assertEqual(go.safe_failure(go.TransportError('network_error'))['error'],'network_error')
  self.assertNotIn('SECRET',str(go.safe_failure(RuntimeError('SECRET'))))
  self.assertEqual(go.safe_failure(go.OAuthError('invalid_grant'))['recovery'],'reauthorize')
 def test_missing_or_reduced_scope_rejected(self):
  for data in [dict(self.token,scope='other'),{k:v for k,v in self.token.items() if k!='scope'},{k:v for k,v in self.token.items() if k!='refresh_token'}]:
   with self.assertRaises(go.OAuthError):go.checked_token(data,True)
if __name__=='__main__':unittest.main()
