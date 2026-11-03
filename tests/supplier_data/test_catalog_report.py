"""Temporary synthetic catalogs only; never load an environment database."""
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from console.supplier_data.catalog_reader import CatalogError, open_catalog, read_products
from console.supplier_data.report import NOTICE, render_html, write_report


INJECTED_MODEL = '<script>alert("virtual")</script>'
INJECTED_SUPPLIER = '</textarea><script>alert("supplier")</script>'


def create_catalog(path):
    with sqlite3.connect(path) as connection:
        connection.executescript('''
            CREATE TABLE equipment_specs (
                equipment_id TEXT PRIMARY KEY, supplier TEXT, model TEXT, name TEXT,
                fixture_type TEXT, wattage REAL, exw_price REAL, currency TEXT,
                asset_id TEXT, description TEXT);
            CREATE TABLE multimedia_assets (asset_id TEXT PRIMARY KEY, filename TEXT, source_type TEXT);
            CREATE TABLE knowledge_chunks (chunk_id TEXT PRIMARY KEY, equipment_id TEXT, text TEXT);
        ''')
        connection.executemany('INSERT INTO equipment_specs VALUES(?,?,?,?,?,?,?,?,?,?)', [
            ('virtual-1', '虚构甲厂', INJECTED_MODEL, '虚构染色灯', 'wash', 100, 88, 'USD', 'virtual-asset-1', '虚构染色灯说明'),
            ('virtual-2', '虚构甲厂', 'VIRTUAL-2', '虚构频闪灯', 'strobe', 200, 120, 'USD', 'virtual-asset-2', '虚构频闪灯说明'),
            ('virtual-3', INJECTED_SUPPLIER, 'VIRTUAL-3', '无资料虚构灯', 'spot', 300, 160, 'USD', None, None),
            ('virtual-4', '虚构甲厂', 'VIRTUAL-4', '有冲突虚构灯', 'beam', 100, 90, 'USD', 'virtual-asset-1', '虚构光束灯说明'),
        ])
        connection.executemany('INSERT INTO multimedia_assets VALUES(?,?,?)', [
            ('virtual-asset-1', '虚构报价表2.0.xlsx', 'spreadsheet'),
            ('virtual-asset-2', '虚构说明书.docx', 'document'),
        ])
        connection.executemany('INSERT INTO knowledge_chunks VALUES(?,?,?)', [
            ('virtual-c1', 'virtual-1', '外箱尺寸:60x35x30cm'),
            ('virtual-c2', 'virtual-1', '毛重:12kg 1台/箱'),
            ('virtual-c3', 'virtual-2', '净重:3000g'),
            ('virtual-c4', 'virtual-4', '外箱尺寸:60x35x30cm 毛重:10kg 毛重:12kg'),
        ])


class HTMLAudit(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.scripts = 0
        self.external_resources = []
        self.copy_buttons = 0
        self.statuses = set()
        self.in_template = False
        self.payload_text = []
        self.in_script = False
        self.script_text = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'script':
            self.scripts += 1
            self.in_script = True
        if tag == 'template' and attrs.get('id') == 'report-data':
            self.in_template = True
        if tag in ('script', 'link', 'img', 'iframe'):
            self.external_resources.extend(attrs[key] for key in ('src', 'href') if key in attrs)
        if tag == 'button' and 'data-copy' in attrs:
            self.copy_buttons += 1
        if tag == 'tr' and 'data-status' in attrs:
            self.statuses.add(attrs['data-status'])

    def handle_endtag(self, tag):
        if tag == 'template':
            self.in_template = False
        if tag == 'script':
            self.in_script = False

    def handle_data(self, value):
        if self.in_template:
            self.payload_text.append(value)
        if self.in_script:
            self.script_text.append(value)

    @property
    def payload(self):
        return json.loads(''.join(self.payload_text))


class CatalogReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        # URI escaping for space, question mark, hash and non-ASCII path names.
        self.db = self.root / '虚构 catalog?#.sqlite3'
        create_catalog(self.db)

    def tearDown(self):
        self.tmp.cleanup()

    def test_reader_aggregates_chunks_and_retains_no_chunk_product(self):
        products = list(read_products(self.db))
        self.assertEqual(len(products), 4)
        self.assertEqual(products[0]['model'], INJECTED_MODEL)
        self.assertEqual(products[0]['exw_price'], 88)
        self.assertEqual(products[0]['currency'], 'USD')
        self.assertEqual([chunk['chunk_id'] for chunk in products[0]['chunks']],
                         ['virtual-c1', 'virtual-c2'])
        self.assertEqual(products[2]['chunks'], [])
        self.assertEqual(self.db.read_bytes()[:16], b'SQLite format 3\x00')

    def test_ro_uri_and_query_only_both_prevent_writes(self):
        before = self.db.read_bytes()
        with patch('console.supplier_data.catalog_reader.sqlite3.connect', wraps=sqlite3.connect) as connect:
            with open_catalog(self.db) as connection:
                self.assertEqual(connection.execute('PRAGMA query_only').fetchone()[0], 1)
                with self.assertRaises(sqlite3.OperationalError):
                    connection.execute("INSERT INTO equipment_specs(equipment_id) VALUES('bad-write')")
                # Even disabling query_only cannot make the URI connection writable.
                connection.execute('PRAGMA query_only=0')
                with self.assertRaises(sqlite3.OperationalError):
                    connection.execute("INSERT INTO equipment_specs(equipment_id) VALUES('bad-write')")
            args, kwargs = connect.call_args
            self.assertTrue(args[0].endswith('?mode=ro'))
            self.assertTrue(kwargs['uri'])
        self.assertEqual(self.db.read_bytes(), before)

    def test_report_json_and_input_bytes_unchanged(self):
        before = self.db.read_bytes()
        out = self.root / 'new' / 'reports'
        report = write_report(self.db, out)
        parsed = json.loads((out / 'packing_report.json').read_text(encoding='utf-8'))
        self.assertEqual(parsed['summary'], report['summary'])
        self.assertEqual(parsed['summary']['total'], 4)
        self.assertEqual(parsed['summary']['ddp_ready'], 1)
        self.assertEqual(parsed['summary']['with_missing'], 3)
        self.assertEqual(parsed['summary']['with_conflict'], 1)
        self.assertEqual(parsed['notice'], NOTICE)
        self.assertIn('+00:00', parsed['generated_at'])
        candidate = parsed['products'][0]['facts']['carton_size']['candidates'][0]
        self.assertEqual(candidate['value'], [60, 35, 30])
        self.assertEqual(candidate['chunk_id'], 'virtual-c1')
        self.assertEqual(candidate['raw'], '外箱尺寸:60x35x30cm')
        self.assertEqual(parsed['schema_version'], 2)
        self.assertEqual(parsed['products'][0]['source_chunks'], [
            {'chunk_id': 'virtual-c1', 'text': '外箱尺寸:60x35x30cm'},
            {'chunk_id': 'virtual-c2', 'text': '毛重:12kg 1台/箱'}])
        self.assertEqual(self.db.read_bytes(), before)

    def test_html_escapes_product_supplier_and_draft_payload(self):
        out = self.root / 'reports'
        write_report(self.db, out)
        document = (out / 'packing_report.html').read_text(encoding='utf-8')
        self.assertNotIn(INJECTED_MODEL, document)
        self.assertNotIn(INJECTED_SUPPLIER, document)
        self.assertTrue('&lt;script&gt;alert(' in document)
        self.assertTrue('&lt;/textarea&gt;&lt;script&gt;' in document)
        self.assertIn(NOTICE, document)
        audit = HTMLAudit()
        audit.feed(document)
        self.assertEqual(audit.scripts, 1)  # The sole script is our static UI code.
        self.assertEqual(audit.external_resources, [])
        self.assertEqual(audit.copy_buttons, 2)
        self.assertEqual(audit.statuses, set())  # No hidden, pre-rendered product rows.
        self.assertEqual({row[2] for row in audit.payload['rows']},
                         {'ready', 'partial', 'missing', 'conflict'})
        self.assertTrue(any(INJECTED_MODEL in value for value in audit.payload['strings']))

    def test_html_escapes_evidence_and_chunk_id(self):
        report = write_report(self.db, self.root / 'reports')
        candidate = report['products'][0]['facts']['carton_size']['candidates'][0]
        candidate['raw'] = '<img src=x onerror=alert("raw")>'
        candidate['chunk_id'] = '<script>virtual-chunk</script>'
        document = render_html(report)
        self.assertNotIn('<img src=x', document)
        self.assertNotIn('<script>virtual-chunk', document)
        self.assertTrue('&lt;img src=x onerror=alert(' in document)
        self.assertTrue('&lt;script&gt;virtual-chunk&lt;/script&gt;' in document)
        audit = HTMLAudit()
        audit.feed(document)
        self.assertIn(candidate['raw'], audit.payload['strings'])
        self.assertIn(candidate['chunk_id'], audit.payload['strings'])
        self.assertEqual(audit.scripts, 1)

    def test_reports_have_per_supplier_conflict_drafts(self):
        report = write_report(self.db, self.root / 'reports')
        draft = report['followups']['虚构甲厂']
        self.assertIn('VIRTUAL-4', draft)
        self.assertIn('10 kg', draft)
        self.assertIn('12 kg', draft)
        self.assertIn('每箱数量', draft)
        self.assertNotIn(INJECTED_MODEL, draft)  # ready product has no follow-up

    def test_dropped_diagnostics_are_reported(self):
        with sqlite3.connect(self.db) as connection:
            connection.execute('INSERT INTO knowledge_chunks VALUES(?,?,?)', (
                'virtual-invalid', 'virtual-3', '毛重:999kg 外箱尺寸:1x3x4cm 外箱尺寸(mm):20x30x40(cm)'))
        report = write_report(self.db, self.root / 'reports')
        self.assertEqual(report['summary']['dropped_out_of_range'], 2)
        self.assertEqual(report['summary']['dropped_ambiguous'], 1)

    def test_missing_database_is_not_created(self):
        absent = self.root / 'does-not-exist.db'
        with self.assertRaisesRegex(CatalogError, '不存在'):
            list(read_products(absent))
        self.assertFalse(absent.exists())

    def test_missing_table_error_names_table(self):
        with sqlite3.connect(self.db) as connection:
            connection.execute('DROP TABLE knowledge_chunks')
        with self.assertRaisesRegex(CatalogError, '缺少表：knowledge_chunks'):
            list(read_products(self.db))

    def test_missing_columns_error_names_table_and_columns(self):
        with sqlite3.connect(self.db) as connection:
            connection.execute('ALTER TABLE equipment_specs DROP COLUMN currency')
        with self.assertRaisesRegex(CatalogError, 'equipment_specs 缺少列：currency'):
            list(read_products(self.db))

    def test_invalid_database_has_clear_error(self):
        self.db.write_text('synthetic non-database', encoding='utf-8')
        with self.assertRaisesRegex(CatalogError, '读取知识目录库失败'):
            list(read_products(self.db))

    def test_empty_catalog(self):
        with sqlite3.connect(self.db) as connection:
            connection.execute('DELETE FROM equipment_specs')
        report = write_report(self.db, self.root / 'reports')
        self.assertEqual(report['summary']['total'], 0)
        self.assertEqual(report['summary']['ddp_ready'], 0)
        self.assertIn('0%', render_html(report))

    def test_optional_display_columns_are_not_required(self):
        with sqlite3.connect(self.db) as connection:
            connection.execute('ALTER TABLE equipment_specs DROP COLUMN model')
            connection.execute('ALTER TABLE equipment_specs DROP COLUMN name')
        products = list(read_products(self.db))
        self.assertEqual(len(products), 4)
        self.assertEqual(products[0]['equipment_id'], 'virtual-1')

    def test_same_path_input_is_not_overwritten(self):
        for name in ('packing_report.json', 'packing_report.html'):
            with self.subTest(name=name):
                source = self.root / name
                create_catalog(source)
                before = source.read_bytes()
                with self.assertRaisesRegex(ValueError, '拒绝覆盖输入库'):
                    write_report(source, self.root)
                self.assertEqual(source.read_bytes(), before)

    def test_symlink_or_hardlink_output_to_input_is_rejected(self):
        for hardlink in (False, True):
            with self.subTest(hardlink=hardlink):
                out = self.root / str(hardlink)
                out.mkdir()
                target = out / 'packing_report.json'
                if hardlink:
                    os.link(self.db, target)
                else:
                    target.symlink_to(self.db)
                before = self.db.read_bytes()
                with self.assertRaisesRegex(ValueError, '拒绝覆盖输入库'):
                    write_report(self.db, out)
                self.assertEqual(self.db.read_bytes(), before)

    def test_cli_works_without_pythonpath_and_preserves_input(self):
        before = self.db.read_bytes()
        script = Path(__file__).resolve().parents[2] / 'scripts' / 'm1_packing_report.py'
        result = subprocess.run([sys.executable, str(script), '--catalog', str(self.db),
                                 '--out-dir', str(self.root / 'cli')], cwd=self.root,
                                env={key: value for key, value in os.environ.items() if key != 'PYTHONPATH'},
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('资料条目 4；可直接报价（资料齐全）1；可估算（须向供应商确认装箱数）0；必需资料有缺项 3；冲突 1', result.stdout)
        self.assertTrue((self.root / 'cli' / 'packing_report.html').is_file())
        self.assertEqual(self.db.read_bytes(), before)

    def test_cli_error_is_clear_and_creates_no_output(self):
        script = Path(__file__).resolve().parents[2] / 'scripts' / 'm1_packing_report.py'
        out = self.root / 'no-output'
        result = subprocess.run([sys.executable, str(script), '--catalog', str(self.root / 'missing.db'),
                                 '--out-dir', str(out)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn('知识目录库不存在', result.stderr)
        self.assertFalse(out.exists())


if __name__ == '__main__':
    unittest.main()
