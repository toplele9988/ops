"""Local HTTP interaction with synthetic identities, conversations and SQLite."""
import importlib,sys,tempfile,types,unittest
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs,quote
from jinja2 import Environment,FileSystemLoader,select_autoescape
from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import HTMLResponse
from starlette.testclient import TestClient
from console import owner_inbox,conversation_notes as notes, v3_owner_inbox as route
import inbox_fixtures as fixtures
FakeRuntime=fixtures.FakeRuntime
ROOT=Path(__file__).resolve().parents[2]/'src'/'console'

class NoteAdapter:
 Conflict=notes.Conflict;ScopeMismatch=notes.ScopeMismatch
 def __init__(self,path):self.path=path
 def read(self,*a,**kw):return notes.read(*a,path=self.path,**kw)
 def save(self,*a,**kw):return notes.save(*a,path=self.path,**kw)
class Store:
 def get_session(self,token):
  if token not in ('admin','staff'):return None
  return SimpleNamespace(authenticated=True,token_hash=token,csrf='t-'+token)
class Backend:
 def owner_page(self,owner,**kwargs):return owner_inbox.owner_page(owner,authority_factory=FakeRuntime,**kwargs)
class NoteRouteTest(unittest.TestCase):
 def setUp(self):
  self.fixture=fixtures.InboxTest('test_owner_and_revocation');self.fixture.setUp();self.tmp=tempfile.TemporaryDirectory();path=Path(self.tmp.name).resolve()/'notes.db';notes.initialize(path);self.path=path
  store=Store();env=Environment(loader=FileSystemLoader(str(ROOT/'templates')),autoescape=select_autoescape(('html','xml'),default=True));env.filters['urlencode']=lambda v:quote(str(v),safe='')
  def render(request,name,nav='',status_code=200,**values):return HTMLResponse(env.get_template(name).render(request=request,nav=nav,csrf=request.state.csrf,**values),status_code=status_code)
  auth=SimpleNamespace(COOKIE_NAME='test-session',load_settings=lambda:SimpleNamespace(enabled=True),proxy_request_allowed=lambda s:False,cf_access_allowed=lambda h,s:True)
  mod=SimpleNamespace(auth=auth,STORE=store,read_json_mapping=lambda p:{'owner':'owner-a'},data=SimpleNamespace(CONFIG='unused'),inquiry_owner=lambda cfg:cfg['owner'],render=render,error_response=lambda req,code:HTMLResponse('error',status_code=code))
  app=Starlette(routes=route.build(mod,Backend(),NoteAdapter(path)))
  async def security(request,call_next):
   token=request.cookies.get('test-session');session=store.get_session(token);request.state.session=session;request.state.proxy_trusted=False
   request.state.password_principal=SimpleNamespace(username=token) if session else None;request.state.csrf=session.csrf if session else '';request.state.form={}
   if request.method=='POST':
    fields=parse_qs((await request.body()).decode(),keep_blank_values=True);request.state.form=fields
    if not session or fields.get('csrf')!=[session.csrf]:return HTMLResponse('denied',status_code=403)
   return await call_next(request)
  app.add_middleware(BaseHTTPMiddleware,dispatch=security);self.client=TestClient(app,follow_redirects=False)
  page=owner_inbox.owner_page('owner-a',authority_factory=FakeRuntime)
  self.key=next(v['key'] for v in page['items'] if v['channel']=='alibaba' and v['conversation_id']=='1')
 def tearDown(self):self.client.close();self.fixture.tearDown();self.tmp.cleanup()
 def login(self,user):self.client.cookies.set('test-session',user)
 def form(self,user='admin',key=None,version='0',content='380W, 16 pcs',status='following'):
  return {'csrf':'t-'+user,'conversation':key or self.key,'version':version,'content':content,'status':status}
 def test_two_users_edit_and_reload(self):
  self.login('admin');r=self.client.get('/v3/workbench',params={'conversation':self.key});self.assertEqual(r.status_code,200);self.assertIn('尚未创建跟进记录',r.text)
  r=self.client.post('/v3/workbench/note',data=self.form());self.assertEqual(r.status_code,303)
  self.login('staff');r=self.client.get(r.headers['location']);self.assertEqual(r.status_code,200);self.assertIn('380W, 16 pcs',r.text);self.assertIn('admin',r.text)
  r=self.client.post('/v3/workbench/note',data=self.form(user='staff',version='1',content='waiting for customer',status='waiting_customer'));self.assertEqual(r.status_code,303)
  self.login('admin');r=self.client.get(r.headers['location']);self.assertIn('waiting for customer',r.text);self.assertIn('staff',r.text)
 def test_conflict_preserves_unsaved_content_and_escape(self):
  self.login('admin');self.assertEqual(self.client.post('/v3/workbench/note',data=self.form()).status_code,303)
  self.login('staff');r=self.client.post('/v3/workbench/note',data=self.form(user='staff',version='0',content='<script>unsaved</script>'));self.assertEqual(r.status_code,409)
  self.assertIn('你的内容尚未保存',r.text);self.assertIn('&lt;script&gt;unsaved&lt;/script&gt;',r.text);self.assertNotIn('<script>unsaved</script>',r.text)
 def test_scope_authentication_and_csrf_fail_closed(self):
  self.assertEqual(self.client.post('/v3/workbench/note',data=self.form()).status_code,403)
  self.login('admin');wrong=self.form();wrong['csrf']='wrong';self.assertEqual(self.client.post('/v3/workbench/note',data=wrong).status_code,403)
  self.assertEqual(self.client.post('/v3/workbench/note',data=self.form(key='0'*64)).status_code,403)
  FakeRuntime.history.enabled=False;self.assertEqual(self.client.post('/v3/workbench/note',data=self.form()).status_code,403)
 def test_validation_and_database_error_preserve_text(self):
  self.login('admin');r=self.client.post('/v3/workbench/note',data=self.form(content='x'*513));self.assertEqual(r.status_code,400);self.assertIn('备注未保存',r.text)
  self.path.unlink();r=self.client.post('/v3/workbench/note',data=self.form(content='unsaved'));self.assertEqual(r.status_code,503);self.assertIn('unsaved',r.text)

 def test_save_retains_selected_search_context_and_no_external_redirect(self):
  self.login('admin');r=self.client.post('/v3/workbench/note?channel=alibaba&q=same&message_q=ali',data=self.form())
  self.assertEqual(r.status_code,303);location=r.headers['location'];self.assertTrue(location.startswith('/v3/workbench?'))
  self.assertIn('channel=alibaba',location);self.assertIn('q=same',location);self.assertIn('message_q=ali',location)
  follow=self.client.get(location);self.assertEqual(follow.status_code,200);self.assertIn('380W, 16 pcs',follow.text)
