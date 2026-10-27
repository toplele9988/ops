import html,importlib,json,re,sqlite3,sys,tempfile,types,unittest
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote
from jinja2 import Environment,FileSystemLoader,select_autoescape
from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import HTMLResponse
from starlette.testclient import TestClient
import conversation_notes as notes,index_migration as migration,owner_inbox
import native_followup_source as source
import test_owner_inbox as fixtures

ROOT=Path(__file__).resolve().parent
pkg=types.ModuleType('followup_console');pkg.__path__=[str(ROOT)];sys.modules[pkg.__name__]=pkg
route=importlib.import_module('followup_console.v3_owner_inbox')

class Store:
    def __init__(self):self.allowed={'fixture_operator_a','fixture_operator_b'}
    def get_session(self,token):
        return SimpleNamespace(authenticated=True,token_hash=token,csrf='t-'+token) if token in self.allowed else None

class RouteTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.InboxTest('test_owner_and_revocation');self.fixture.setUp();self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name).resolve()/'notes.db';notes.initialize(self.path)
        with sqlite3.connect(self.path) as db:migration.install_for_fixture(db,migration.NOTES)
        for channel,sql in (('wechat',migration.WECHAT),('alibaba',migration.ALIBABA),('whatsapp',migration.WHATSAPP)):
            with sqlite3.connect(self.fixture.paths[channel]) as db:
                migration.install_for_fixture(db,sql)
                if channel=='wechat':db.execute('CREATE INDEX test_scope_key ON message_scopes(app_id,message_id)')
        self.store=Store();env=Environment(loader=FileSystemLoader(str(ROOT/'templates')),autoescape=select_autoescape(('html','xml'),default=True));env.filters['urlencode']=lambda v:quote(str(v),safe='')
        def render(request,name,nav='',status_code=200,**values):return HTMLResponse(env.get_template(name).render(request=request,nav=nav,csrf=request.state.csrf,**values),status_code=status_code)
        auth=SimpleNamespace(COOKIE_NAME='test-session',load_settings=lambda:SimpleNamespace(enabled=True),proxy_request_allowed=lambda s:False,cf_access_allowed=lambda h,s:True)
        mod=SimpleNamespace(auth=auth,STORE=self.store,read_json_mapping=lambda p:{'owner':'owner-a'},data=SimpleNamespace(CONFIG='unused'),inquiry_owner=lambda cfg:cfg['owner'],render=render,error_response=lambda req,code:HTMLResponse('error',status_code=code))
        notes_adapter=SimpleNamespace(DB=self.path,STATES=notes.STATES,Conflict=notes.Conflict,ScopeMismatch=notes.ScopeMismatch)
        verifier_module=importlib.import_module('followup_console.native_followup_source')
        vf=lambda owner:verifier_module.NativeVerifier(owner,factory=fixtures.FakeRuntime)
        app=Starlette(routes=route.build(mod,notes=notes_adapter,verifier_factory=vf))
        async def security(request,call_next):
            token=request.cookies.get('test-session');session=self.store.get_session(token);request.state.session=session;request.state.proxy_trusted=False
            request.state.password_principal=SimpleNamespace(username=token) if session else None;request.state.csrf=session.csrf if session else ''
            return await call_next(request)
        app.add_middleware(BaseHTTPMiddleware,dispatch=security);self.client=TestClient(app,follow_redirects=False)
        page=owner_inbox.owner_page('owner-a',authority_factory=fixtures.FakeRuntime)
        self.item=next(x for x in page['items'] if x['channel']=='alibaba' and x['conversation_id']=='1')
        notes.save(self.item['key'],self.item['channel'],self.item['account'],self.item['scope'],self.item['conversation_id'],actor='fixture_operator_a',status='following',content='<script>synthetic</script>',expected_version=0,path=self.path)
    def tearDown(self):self.client.close();self.fixture.tearDown();self.tmp.cleanup()
    def test_two_sessions_filter_shared_note_and_escape(self):
        self.assertEqual(self.client.get('/v3/followups').status_code,403)
        for user in ('fixture_operator_a','fixture_operator_b'):
            self.client.cookies.set('test-session',user);r=self.client.get('/v3/followups?status=following')
            self.assertEqual(r.status_code,200);self.assertIn('&lt;script&gt;synthetic&lt;/script&gt;',r.text);self.assertNotIn('<script>synthetic</script>',r.text)
            self.assertIn('/v3/workbench?conversation='+self.item['key'],r.text);self.assertEqual(r.headers['cache-control'],'no-store')
        r=self.client.get('/v3/followups?status=done');self.assertEqual(r.status_code,200);self.assertNotIn('synthetic&lt;',r.text)
    def test_invalid_filters_and_duplicate_parameters(self):
        self.client.cookies.set('test-session','fixture_operator_a')
        self.assertEqual(self.client.get('/v3/followups?status=invalid').status_code,400)
        self.assertEqual(self.client.get('/v3/followups?status=all&status=done').status_code,400)
        self.assertEqual(self.client.get('/v3/followups?status=all&cursor=bogus').status_code,400)
    def test_revocation_hides_note_and_shows_partial(self):
        self.client.cookies.set('test-session','fixture_operator_b');fixtures.FakeRuntime.history.enabled=False
        r=self.client.get('/v3/followups?status=following');self.assertEqual(r.status_code,200);self.assertNotIn('synthetic&lt;',r.text);self.assertIn('相关记录暂不显示',r.text);self.assertIn('从第一页重新筛选',r.text);self.assertIn('列表不是固定快照',r.text)
    def test_partial_recovery_and_source_continuation_links(self):
        self.client.cookies.set('test-session','fixture_operator_a')
        fixtures.FakeRuntime.history.enabled=False
        partial=self.client.get('/v3/followups?status=following')
        self.assertIn('从第一页重新筛选',partial.text)
        fixtures.FakeRuntime.history.enabled=True
        recovered=self.client.get('/v3/followups?status=following')
        self.assertIn('&lt;script&gt;synthetic&lt;/script&gt;',recovered.text)
        with sqlite3.connect(self.fixture.paths['alibaba']) as db:
            for i in range(205):
                payload={'account':'fixture-account-a','conversation_id':'1','content':'synthetic','role':'buyer','history':True,'test':True}
                db.execute('INSERT INTO inbound VALUES(?,?,?,?,?)',(f'0-{i:04}',json.dumps(payload),'x','archived',101))
        page=self.client.get('/v3/followups?status=following')
        for unused in range(2):
            match=re.search(r'<a href="([^"]+)">继续检查此记录',page.text)
            self.assertIsNotNone(match)
            page=self.client.get(html.unescape(match.group(1)))
        self.assertEqual(page.status_code,200)
        self.assertIn('&lt;script&gt;synthetic&lt;/script&gt;',page.text)
    def test_absent_index_fails_closed(self):
        self.client.cookies.set('test-session','fixture_operator_a')
        with sqlite3.connect(self.path) as db:db.execute('DROP INDEX followup_status_order')
        self.assertEqual(self.client.get('/v3/followups?status=following').status_code,503)

if __name__=='__main__':unittest.main()
