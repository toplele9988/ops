"""Auth gating, CSP and safety checks for the console report route (fake app_module)."""
import asyncio
import re
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

try:
    from console import v3_supplier_packing as page
except ModuleNotFoundError:  # starlette is only present in the console venv
    page = None

HTML = ('<!doctype html><html><head><style>body{color:#111}</style></head><body><h1>报告</h1>'
        '<script>document.title="x"</script></body></html>')


def request(authenticated=True, principal=True, recheck=True):
    state = SimpleNamespace(
        password_principal=SimpleNamespace(username='tester') if principal else None,
        password_recheck=(lambda: recheck) if principal else None,
        session=SimpleNamespace(authenticated=authenticated))
    return SimpleNamespace(state=state)


@unittest.skipIf(page is None, 'starlette not installed')
class ConsolePageTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / 'packing_report.html'
        self.errors = []
        app = SimpleNamespace(error_response=lambda req, code, incident=None: self.errors.append(code) or code)
        routes = page.build(app, self.path)
        self.handlers = dict(zip(('page', 'css', 'js'), (r.endpoint for r in routes)))
        self.handler = self.handlers['page']

    def tearDown(self):
        self.dir.cleanup()

    def call(self, **kw):
        return asyncio.run(self.handler(request(**kw)))

    def test_unauthenticated_requests_get_403_on_page_and_assets(self):
        self.path.write_text(HTML, encoding='utf8')
        for handler in self.handlers.values():
            for kw in ({'authenticated': False}, {'principal': False}, {'recheck': False}):
                self.errors.clear()
                self.assertEqual(asyncio.run(handler(request(**kw))), 403)
                self.assertEqual(self.errors, [403])

    def test_page_links_same_origin_assets_and_carries_no_inline_code(self):
        self.path.write_text(HTML, encoding='utf8')
        page = asyncio.run(self.handlers['page'](request()))
        body = page.body.decode('utf8')
        self.assertIn('返回控制台', body)
        self.assertIn('href="/v3/supplier-packing/report.css"', body)
        self.assertIn('src="/v3/supplier-packing/report.js"', body)
        self.assertNotIn('<style>', body)
        self.assertNotIn('document.title', body)
        self.assertEqual(page.headers['cache-control'], 'no-store')
        css = asyncio.run(self.handlers['css'](request()))
        js = asyncio.run(self.handlers['js'](request()))
        self.assertIn('body{color:#111}', css.body.decode('utf8'))
        self.assertTrue(css.media_type.startswith('text/css'))
        self.assertIn('document.title="x"', js.body.decode('utf8'))
        self.assertIn('javascript', js.media_type)

    def test_missing_report_is_404_and_oversized_or_bad_files_are_503(self):
        self.assertEqual(self.call(), 404)
        self.path.write_bytes(b'\xff\xfe\x00bad')
        self.assertEqual(self.call(), 503)
        self.path.write_text(HTML + 'x' * (page.MAX_BYTES + 1), encoding='utf8')
        self.assertEqual(self.call(), 503)

    def test_rejects_external_resources_and_inline_handlers(self):
        for bad in ('<script src="https://evil.example/a.js"></script>',
                    '<img src="http://evil.example/p.png">',
                    '<a href="https://evil.example">x</a>',
                    '<a href="//evil.example">x</a>',
                    '<a href=" javascript:alert(1)">x</a>',
                    '<button onclick="steal()">x</button>'):
            self.path.write_text(HTML.replace('<h1>', bad + '<h1>'), encoding='utf8')
            self.assertEqual(self.call(), 503, bad)

    def test_rejects_scripts_that_fetch_or_extra_code_blocks(self):
        for bad in (HTML.replace('document.title="x"', 'fetch("/x")'),
                    HTML.replace('body{color:#111}', '@import "x.css";body{color:#111}'),
                    HTML.replace('</body>', '<script>1</script></body>')):
            self.path.write_text(bad, encoding='utf8')
            self.assertEqual(self.call(), 503)

    def test_real_generated_report_passes_the_gate(self):
        """The generator's own output must satisfy the strict policy."""
        import sqlite3
        from console.supplier_data.report import write_report
        db = Path(self.dir.name) / 'catalog.sqlite3'
        with sqlite3.connect(db) as c:
            c.executescript('''
                CREATE TABLE equipment_specs (equipment_id TEXT, asset_id TEXT, supplier TEXT,
                  fixture_type TEXT, wattage REAL, description TEXT, exw_price REAL, currency TEXT);
                CREATE TABLE knowledge_chunks (chunk_id TEXT, asset_id TEXT, equipment_id TEXT, text TEXT);
                CREATE TABLE multimedia_assets (asset_id TEXT PRIMARY KEY, filename TEXT, source_type TEXT);
                INSERT INTO equipment_specs VALUES ('e1','a1','虚构供应商','strobe',100,'虚构频闪灯 </script><b>x</b>',NULL,NULL);
                INSERT INTO multimedia_assets VALUES ('a1','虚构.xlsx','supplier');
                INSERT INTO knowledge_chunks VALUES ('c1','a1','e1','外箱尺寸:60x35x30cm 毛重:12kg 1台/箱');
            ''')
        out = Path(self.dir.name) / 'out'
        write_report(str(db), str(out))
        html, css, js = page.split_report((out / 'packing_report.html').read_text(encoding='utf8'))
        self.assertEqual(len(re.findall(r'<script', html)), 1)  # only the external loader
        self.assertNotRegex(html, r'\sstyle\s*=')  # global CSP forbids inline style attributes
        self.assertIn('data-w', html)
        self.assertTrue(css and js)


if __name__ == '__main__':
    unittest.main()
