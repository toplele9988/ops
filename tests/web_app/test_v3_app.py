"""Auth gating, asset safety and CSRF injection for the web app route (fake app_module)."""
import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

try:
    from console import v3_app as app_routes
except ModuleNotFoundError:  # starlette is only present in the console venv
    app_routes = None

INDEX = '<!doctype html><meta name="csrf-token" content="__CSRF__"><div id="root"></div>'


def request(path='', authenticated=True, principal=True, recheck=True, csrf='tok<"&>'):
    state = SimpleNamespace(
        password_principal=SimpleNamespace(username='tester') if principal else None,
        password_recheck=(lambda: recheck) if principal else None,
        session=SimpleNamespace(authenticated=authenticated), csrf=csrf)
    return SimpleNamespace(state=state, path_params={'path': path})


@unittest.skipIf(app_routes is None, 'starlette not installed')
class WebAppRouteTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.dist = Path(self.dir.name) / 'dist'
        (self.dist / 'assets').mkdir(parents=True)
        (self.dist / 'index.html').write_text(INDEX, encoding='utf8')
        (self.dist / 'assets' / 'app.js').write_text('console.log(1)', encoding='utf8')
        (self.dist / 'secret.txt').write_text('no', encoding='utf8')
        self.codes = []
        app = SimpleNamespace(error_response=lambda req, code, incident=None: self.codes.append(code) or code)
        self.entry = app_routes.build(app, self.dist)[1].endpoint

    def tearDown(self):
        self.dir.cleanup()

    def call(self, **kw):
        return asyncio.run(self.entry(request(**kw)))

    def test_unauthenticated_requests_are_403(self):
        for kw in ({'authenticated': False}, {'principal': False}, {'recheck': False}):
            self.codes.clear()
            self.assertEqual(self.call(**kw), 403)

    def test_index_gets_escaped_csrf_token(self):
        response = self.call()
        body = response.body.decode('utf8')
        self.assertIn('content="tok&lt;&quot;&amp;&gt;"', body)
        self.assertNotIn('__CSRF__', body)
        self.assertEqual(response.headers['cache-control'], 'no-store')

    def test_spa_paths_fall_back_to_index(self):
        self.assertIn('id="root"', self.call(path='inspiration').body.decode('utf8'))

    def test_assets_are_served_with_type(self):
        response = self.call(path='assets/app.js')
        self.assertEqual(response.body, b'console.log(1)')
        self.assertTrue(response.media_type.startswith('text/javascript'))

    def test_assets_reject_traversal_and_unknown_types(self):
        for bad in ('assets/../secret.txt', 'assets/../../etc/passwd', 'assets/missing.js',
                    'assets/app.txt', 'secret.txt'):
            self.codes.clear()
            result = self.call(path=bad)
            if bad == 'secret.txt':  # not under assets/: treated as an SPA route, never the file
                self.assertNotIn('no', getattr(result, 'body', b'').decode('utf8').replace('root', ''))
            else:
                self.assertEqual(result, 404, bad)

    def test_missing_build_is_503(self):
        (self.dist / 'index.html').unlink()
        self.assertEqual(self.call(), 503)


if __name__ == '__main__':
    unittest.main()
