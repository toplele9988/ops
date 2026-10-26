# Synthetic credentials exist only in each temporary test database.
import unittest,tempfile,pathlib,re
from starlette.testclient import TestClient
from console.password_candidate.access_app import Access
from console.password_candidate.password_store import PasswordStore,Identity

class AccountTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();pathlib.Path(self.temp.name).chmod(0o700)
        self.store=PasswordStore(pathlib.Path(self.temp.name)/'db.sqlite3');self.store.provision('fixture_owner','fixture_owner')
    def tearDown(self):self.temp.cleanup()
    def csrf(self,r):return re.search(r'name="csrf" value="([^"]+)"',r.text).group(1)
    def exercise(self,base,audience,secure):
        c=TestClient(Access(self.store,base,b'x'*32,secure=secure).app(),base_url=base,follow_redirects=False)
        login=c.get('/access/login');self.assertEqual(login.status_code,200)
        self.assertEqual(c.post('/access/login',data={'csrf':self.csrf(login),'username':'fixture_owner','password':'fixture_owner'},headers={'origin':base}).status_code,303)
        view=c.get('/access/account');self.assertIn('已有账号 · 1 / 2',view.text);self.assertIn('当前登录',view.text);csrf=self.csrf(view)
        for content,content_type in ((b'csrf=a&csrf=b', 'application/x-www-form-urlencoded'), (b'csrf=\xff', 'application/x-www-form-urlencoded'), (b'x=' + b'a'*4100, 'application/x-www-form-urlencoded'), (b'{}', 'application/json')):
            bad=c.post('/access/user',content=content,headers={'origin':base,'content-type':content_type});self.assertEqual(bad.status_code,400);self.assertIn('输入格式有误',bad.text)
        payload={'csrf':csrf,'current_password':'wrongpass','username':'friend','password':'friendpw','confirm':'friendpw'}
        bad=c.post('/access/user',data=payload,headers={'origin':base});self.assertEqual(bad.status_code,400);self.assertIn('当前密码不正确',bad.text);self.assertNotIn('wrongpass',bad.text);self.assertNotIn('friendpw',bad.text);self.assertRegex(bad.text,r'id="staff-username"[^>]*value="friend"');self.assertRegex(bad.text,r'id="own-username"[^>]*value="fixture_owner"')
        payload['current_password']='fixture_owner';payload['confirm']='different';bad=c.post('/access/user',data=payload,headers={'origin':base});self.assertIn('两次输入的新密码不一致',bad.text);self.assertNotIn('friendpw',bad.text)
        payload.update(username='INVALID',confirm='friendpw');bad=c.post('/access/user',data=payload,headers={'origin':base});self.assertIn('账号格式不正确',bad.text);self.assertRegex(bad.text,r'id="staff-username"[^>]*value="INVALID"')
        payload['username']='bad"<script>';bad=c.post('/access/user',data=payload,headers={'origin':base});self.assertIn('bad&quot;&lt;script&gt;',bad.text);self.assertNotIn('<script>',bad.text);self.assertNotIn('friendpw',bad.text)
        own={'csrf':csrf,'current_password':'wrongpass','username':'owner-next','password':'ownpass','confirm':'ownpass'};bad=c.post('/access/account',data=own,headers={'origin':base});self.assertRegex(bad.text,r'id="own-username"[^>]*value="owner-next"');self.assertNotIn('ownpass',bad.text)
        payload['username']='fixture_owner';bad=c.post('/access/user',data=payload,headers={'origin':base});self.assertIn('该账号已存在',bad.text)
        payload['username']='friend';created=c.post('/access/user',data=payload,headers={'origin':base});self.assertEqual(created.status_code,303)
        view=c.get(created.headers['location']);self.assertIn('已有账号 · 2 / 2',view.text);self.assertIn('friend',view.text);self.assertIn('同事账号已创建',view.text);self.assertIn('当前账号数量已满',view.text);self.assertNotIn('创建同事账号</button>',view.text);self.assertNotIn('friendpw',view.text)
        self.assertNotIn('同事账号已创建',c.get('/access/account').text)
        payload.update(username='third',csrf=self.csrf(view));bad=c.post('/access/user',data=payload,headers={'origin':base});self.assertIn('已达到两个账号的上限',bad.text)
        body={'csrf':self.csrf(view),'current_password':'fixture_owner','username':'fixture_owner','password':'renewed','confirm':'renewed'}
        changed=c.post('/access/account',data=body,headers={'origin':base});self.assertEqual(changed.status_code,303)
        message=c.get(changed.headers['location']);self.assertIn('账号和密码已保存',message.text);self.assertNotIn('renewed',message.text)
        self.assertEqual(c.post('/access/login',data={'csrf':self.csrf(message),'username':'fixture_owner','password':'renewed'},headers={'origin':base}).status_code,303)
        self.assertIsNotNone(self.store.login('friend','friendpw',audience));c.close()
    def test_https(self):self.exercise('https://example.test','https',True)
    def test_lan(self):self.exercise('http://192.0.2.1:8890','lan_http',False)
    def test_auth_list_bound_to_session_and_audience(self):
        token=self.store.login('fixture_owner','fixture_owner','https');self.assertEqual(self.store.list_accounts(token,'https'),['fixture_owner']);self.assertIsNone(self.store.list_accounts(token,'lan_http'));self.store.logout(token);self.assertIsNone(self.store.list_accounts(token,'https'))
    def test_escape_account_values(self):
        a=Access(self.store,'https://example.test',b'x'*32);res=a.account_page(Identity('<script>',1),['<script>','a&b'],'csrf');self.assertNotIn('<script>',res.body.decode());self.assertIn('&lt;script&gt;',res.body.decode());self.assertIn('a&amp;b',res.body.decode())
if __name__=='__main__':unittest.main()
