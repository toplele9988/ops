"""Read production code, patch a workspace copy, exercise the real auth adapters.

No production imports, settings, credentials or stores. Run with the console's
Python environment, from the workspace. All temporary state stays in artifacts/.
"""
import ast
import contextvars
import hashlib
import hmac
import importlib
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import MutableHeaders
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response
from starlette.routing import Route
from starlette.testclient import TestClient

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
WORKSPACE = HERE.parents[1]
PRODUCTION = Path('/opt/imgw/wechat')


class LegacyStore:
    def __init__(self, path):
        self.path = path
        with self.transaction() as db:
            db.execute('CREATE TABLE sessions(token_hash TEXT PRIMARY KEY, csrf TEXT, expires_at REAL, authenticated INTEGER)')

    @contextmanager
    def transaction(self):
        with sqlite3.connect(self.path) as db:
            yield db

    def get_session(self, token):
        if not token:
            return None
        with self.transaction() as db:
            row = db.execute('SELECT csrf,expires_at,authenticated FROM sessions WHERE token_hash=?', (hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
        return SimpleNamespace(csrf=row[0], authenticated=bool(row[2])) if row and row[1] > time.time() else None


class Entrypoints(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        artifacts = WORKSPACE / 'artifacts' / 'ux-root'
        artifacts.mkdir(parents=True, exist_ok=True)
        cls.scratch = tempfile.TemporaryDirectory(prefix='source-', dir=artifacts)
        cls.copy = Path(cls.scratch.name)
        for line in (HERE / 'baseline.sha256').read_text().splitlines():
            digest, name = line.split()
            source = PRODUCTION / name
            assert hashlib.sha256(source.read_bytes()).hexdigest() == digest, f'Baseline changed: {name}'
            target = cls.copy / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        (cls.copy / 'console' / '__init__.py').touch()
        (cls.copy / 'console' / 'password_candidate' / '__init__.py').touch()
        command = ['patch', '--batch', '--fuzz=0', '-p1', '-i', str(HERE / 'production-entrypoints.patch')]
        subprocess.run(command + ['--dry-run'], cwd=cls.copy, check=True, capture_output=True)
        subprocess.run(command, cwd=cls.copy, check=True, capture_output=True)
        for file in (cls.copy / 'console').rglob('*.py'):
            compile(file.read_text(), str(file), 'exec')
        sys.path.insert(0, str(cls.copy))
        cls.PasswordMux = importlib.import_module('console.password_candidate.application').PasswordMux
        cls.PasswordStore = importlib.import_module('console.password_candidate.password_store').PasswordStore
        cls.web = importlib.import_module('console.v3_app')
        # Use exact root Route declarations and SecurityMiddleware from patched app.py.
        # Loading the complete production app would open its real stores/settings.
        cls.tree = ast.parse((cls.copy / 'console/app.py').read_text())
        cls.code = [node for node in cls.tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name in ('_to_new_app', '_read_json_post', 'SecurityMiddleware')]
        cls.root_routes = next(node.value for node in cls.tree.body if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'routes' for t in node.targets))
        cls.root_routes.elts = [node for node in cls.root_routes.elts if isinstance(node, ast.Call) and node.args[0].value in ('/', '/legacy')]

    @classmethod
    def tearDownClass(cls):
        sys.path.remove(str(cls.copy))
        cls.scratch.cleanup()

    def setUp(self):
        self.state = tempfile.TemporaryDirectory(prefix='state-', dir=WORKSPACE / 'artifacts/ux-root')
        self.addCleanup(self.state.cleanup)
        state = Path(self.state.name)
        state.chmod(0o700)
        self.password = self.PasswordStore(state / 'identity.sqlite3')
        self.password.provision('test_owner', 'fictional-pass-123')
        legacy = LegacyStore(state / 'legacy.sqlite3')
        settings = SimpleNamespace(enabled=True)
        auth = SimpleNamespace(
            COOKIE_NAME='imgw_console', PROXY_HEADERS=contextvars.ContextVar('proxy'),
            SECURITY_HEADERS={'Content-Security-Policy': "script-src 'self'; style-src 'self'"},
            load_settings=lambda: settings, cf_access_allowed=lambda headers, cfg: True,
            proxy_request_allowed=lambda cfg: False, batch_post=lambda *args: False,
            csrf_matches=lambda expected, actual: hmac.compare_digest(expected, actual),
        )
        def error_response(request, status, *args):
            return Response('denied', status_code=status)
        async def overview(request):
            return HTMLResponse('<h1>Legacy overview</h1>')
        env = dict(Route=Route, RedirectResponse=RedirectResponse, overview=overview,
                   Request=Request, MutableHeaders=MutableHeaders, Response=Response,
                   run_in_threadpool=run_in_threadpool, auth=auth, STORE=legacy,
                   re=re, parse_qs=parse_qs, JSON_POST_PATH=re.compile(r'^/api/json$'), JSON_POST_LIMIT=8192,
                   error_response=error_response, _panel_failure=lambda *args: 'synthetic')
        exec(compile(ast.Module(body=self.code, type_ignores=[]), 'app.py', 'exec'), env)
        routes = eval(compile(ast.Expression(self.root_routes), 'app.py', 'eval'), env)
        self.hits = 0
        async def write(request):
            self.hits += 1
            return Response('saved')
        async def old_workbench(request):
            return HTMLResponse('<h1>Old workbench</h1>')
        dist = state / 'dist'
        dist.mkdir()
        (dist / 'index.html').write_text('<meta name="csrf-token" content="__CSRF__"><h1>New overview</h1>')
        routes += self.web.build(SimpleNamespace(error_response=error_response), dist)
        routes += [Route('/api/write', write, methods=['POST']), Route('/api/json', write, methods=['POST']), Route('/v3/workbench', old_workbench)]
        secured = env['SecurityMiddleware'](Starlette(routes=routes))
        self.secured, self.legacy, self.auth = secured, legacy, auth
        self.client = TestClient(self.PasswordMux(secured, legacy, auth, self.password, 'https://console.test', b'x' * 32), base_url='https://console.test', follow_redirects=False)
        self.addCleanup(self.client.close)

    def login(self, password='fictional-pass-123'):
        page = self.client.get('/access/login')
        csrf = re.search(r'name="csrf" value="([^"]+)"', page.text)[1]
        return self.client.post('/access/login', headers={'Origin': 'https://console.test'}, data={'csrf': csrf, 'username': 'test_owner', 'password': password})

    def test_guest_root_app_and_legacy_use_original_login_gate(self):
        for path in ('/', '/app/', '/legacy', '/v3/workbench'):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual((response.status_code, response.headers['location']), (303, '/access/login'))

    def test_login_chain_reaches_new_app_and_injects_csrf(self):
        response = self.login()
        self.assertEqual((response.status_code, response.headers['location']), (303, '/'))
        self.assertIn('HttpOnly', response.headers['set-cookie'])
        root = self.client.get('/')
        self.assertEqual((root.status_code, root.headers['location']), (302, '/app/'))
        page = self.client.get('/app/')
        self.assertEqual(page.status_code, 200)
        self.assertIn('New overview', page.text)
        self.assertNotIn('__CSRF__', page.text)
        self.assertRegex(page.text, r'content="[0-9a-f]{64}"')

    def test_login_csrf_origin_and_password_checks_remain(self):
        self.assertEqual(self.client.post('/access/login', data={}).status_code, 403)
        self.assertEqual(self.client.post('/access/login', headers={'Origin': 'https://console.test'}, data={'csrf': 'bad'}).status_code, 400)
        self.assertEqual(self.login('wrong-password').status_code, 401)
        self.assertEqual(self.client.get('/').status_code, 303)

    def test_lan_ip_port_login_uses_same_new_entrypoint(self):
        origin = 'http://192.168.100.221:8890'
        mux = self.PasswordMux(self.secured, self.legacy, self.auth, self.password, origin, b'x' * 32,
                               lan_bind='192.168.100.221', lan_cidr='192.168.100.0/24')
        with TestClient(mux, base_url=origin, client=('192.168.100.20', 50000), follow_redirects=False) as client:
            self.assertEqual(client.get('/').headers['location'], '/access/login')
            form = client.get('/access/login')
            csrf = re.search(r'name="csrf" value="([^"]+)"', form.text)[1]
            response = client.post('/access/login', headers={'Origin': origin}, data={'csrf': csrf, 'username': 'test_owner', 'password': 'fictional-pass-123'})
            self.assertEqual((response.status_code, response.headers['location']), (303, '/'))
            self.assertEqual(client.get('/').headers['location'], '/app/')
            self.assertEqual(client.get('/app/').status_code, 200)
            self.assertIn('imgw_221_lan=', response.headers['set-cookie'])

    def test_old_pages_remain_under_auth(self):
        self.login()
        self.assertIn('Legacy overview', self.client.get('/legacy').text)
        self.assertIn('Old workbench', self.client.get('/v3/workbench').text)

    def test_form_and_json_csrf_are_not_bypassed(self):
        self.login()
        token = re.search(r'content="([0-9a-f]{64})"', self.client.get('/app/').text)[1]
        for path, payload in (('/api/write', {'data': {'note': 'fictional'}}), ('/api/json', {'json': {'note': 'fictional'}})):
            with self.subTest(path=path):
                self.assertEqual(self.client.post(path, **payload).status_code, 403)
                self.assertEqual(self.client.post(path, headers={'X-CSRF-Token': 'bad'}, **payload).status_code, 403)
                self.assertEqual(self.client.post(path, headers={'X-CSRF-Token': token}, **payload).status_code, 200)
        self.assertEqual(self.hits, 2)

    def test_password_reset_revokes_root_legacy_and_app(self):
        self.login()
        self.password.provision('test_owner', 'changed-fictional-pass')
        for path in ('/', '/legacy', '/app/'):
            response = self.client.get(path)
            self.assertEqual((response.status_code, response.headers['location']), (303, '/access/login'))

    def test_app_recheck_rejects_revoked_principal(self):
        calls = []
        async def auth_context(scope, receive, send):
            scope['state'] = dict(password_principal=SimpleNamespace(username='test_owner'), session=SimpleNamespace(authenticated=True), password_recheck=lambda: calls.append('recheck') or False)
            await Starlette(routes=self.web.build(SimpleNamespace(error_response=lambda r, status: Response('denied', status_code=status)), Path(self.state.name) / 'dist'))(scope, receive, send)
        with TestClient(auth_context) as client:
            self.assertEqual(client.get('/app/').status_code, 403)
        self.assertEqual(calls, ['recheck'])

    def test_v3_redirect_and_patch_rollback(self):
        tree = ast.parse((self.copy / 'console/v3.py').read_text())
        build = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'build')
        redirect = next(node for node in build.body if isinstance(node, ast.AsyncFunctionDef) and node.name == 'redirect')
        env = dict(RedirectResponse=RedirectResponse)
        exec(compile(ast.Module(body=[redirect], type_ignores=[]), 'v3.py', 'exec'), env)
        with TestClient(Starlette(routes=[Route('/v3', env['redirect']), Route('/v3/', env['redirect'])]), follow_redirects=False) as client:
            for path in ('/v3', '/v3/'):
                response = client.get(path)
                self.assertEqual((response.status_code, response.headers['location']), (302, '/app/'))
        rollback = Path(self.state.name) / 'rollback'
        shutil.copytree(self.copy, rollback)
        subprocess.run(['patch', '--batch', '--fuzz=0', '-R', '-p1', '-i', str(HERE / 'production-entrypoints.patch')], cwd=rollback, check=True, capture_output=True)
        for line in (HERE / 'baseline.sha256').read_text().splitlines():
            digest, name = line.split()
            self.assertEqual(hashlib.sha256((rollback / name).read_bytes()).hexdigest(), digest)


if __name__ == '__main__':
    unittest.main(verbosity=2)
