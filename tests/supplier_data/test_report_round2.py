"""Second-round rules and scale checks, using exclusively fictitious products."""
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile
from time import perf_counter
import unittest

from console.supplier_data.report import build_report, render_html, write_report
from test_catalog_report import HTMLAudit


def product(index, text='', supplier='虚构甲厂'):
    return {'equipment_id': f'virtual-{index:04}', 'supplier': supplier,
            'model': f'VIRT-{index:04}', 'name': '虚构测试灯',
            'chunks': [{'chunk_id': f'virtual-source-{index:04}', 'text': text}] if text else []}


def synthetic_catalog(path, count=6000):
    """16% coverage, with realistic proportions; no environment DB access."""
    examples = [
        '外箱尺寸:60x35x30cm 毛重:12kg 1台/箱',
        '包装尺寸:780*350*335mm 毛重:15kg',
        '航空箱尺寸:110x50x80cm 毛重:69kg',
        '外箱尺寸:60x35x30cm 毛重:10kg 毛重:12kg',
        '毛重:1.5kg 毛重:19kg 产品尺寸:21x18x19cm 产品尺寸:57x44x42cm',
        '净重:8kg', '产品尺寸:30x25x20cm', '毛重:10kg',
    ]
    with sqlite3.connect(path) as connection:
        connection.executescript('''
            CREATE TABLE equipment_specs (equipment_id TEXT PRIMARY KEY, supplier TEXT,
                model TEXT, name TEXT, fixture_type TEXT, wattage REAL, exw_price REAL, currency TEXT,
                asset_id TEXT, description TEXT);
            CREATE TABLE multimedia_assets (asset_id TEXT PRIMARY KEY, filename TEXT, source_type TEXT);
            CREATE TABLE knowledge_chunks (chunk_id TEXT PRIMARY KEY, equipment_id TEXT, text TEXT);
        ''')
        connection.executemany('INSERT INTO equipment_specs VALUES(?,?,?,?,?,?,?,?,?,?)', (
            (f'virtual-{i:04}', f'虚构供应商-{i % 24:02}', f'VIRT-{i:04}', '虚构测试灯',
             'beam', 100, 99, 'USD', f'virtual-asset-{i % 48}', '虚构测试光束灯') for i in range(count)))
        connection.executemany('INSERT INTO multimedia_assets VALUES(?,?,?)', (
            (f'virtual-asset-{i}', f'虚构供应商报价表-{i:02}.xlsx', 'spreadsheet') for i in range(48)))
        connection.executemany('INSERT INTO knowledge_chunks VALUES(?,?,?)', (
            (f'virtual-source-{i:04}', f'virtual-{i:04}', examples[i % len(examples)])
            for i in range(count * 16 // 100)))


class ReportRoundTwoTests(unittest.TestCase):
    def audit(self, report):
        audit = HTMLAudit()
        audit.feed(render_html(report))
        return audit

    def test_summary_tiers_and_exclusive_conflict_counts(self):
        report = build_report([
            product(0, '外箱尺寸:60x35x30cm 毛重:12kg 1台/箱'),
            product(1, '外箱尺寸:60x35x30cm 毛重:12kg'),
            product(2, '毛重:10kg'), product(3),
            product(4, '毛重:10kg 毛重:12kg'),
            product(5, '毛重:10kg 毛重:12kg 产品尺寸:21x18x19cm 产品尺寸:57x44x42cm'),
        ])
        summary = report['summary']
        self.assertEqual(summary['ddp_ready'], 1)
        self.assertEqual(summary['ddp_estimable'], 1)
        self.assertEqual(summary['with_missing'], 5)
        self.assertEqual(summary['with_conflict'], 2)
        self.assertEqual(summary['by_status'],
                         {'ready': 1, 'estimable': 1, 'partial': 1, 'missing': 1, 'conflict': 2})
        self.assertEqual(summary['by_conflict_reason'], {'multi_model_suspected': 1, 'value_mismatch': 1})
        mixed = report['products'][5]['facts']
        self.assertEqual(mixed['gross_weight_kg']['conflict_reason'], 'value_mismatch')
        self.assertEqual(mixed['product_size']['conflict_reason'], 'multi_model_suspected')

    def test_coverage_ranking_counts_evidence_and_both_tiers(self):
        report = build_report([
            product(0, supplier='虚构零覆盖'), product(1, supplier='虚构半覆盖'),
            product(2, '净重:8kg', '虚构半覆盖'),
            product(3, '毛重:10kg 毛重:19kg', '虚构全覆盖'),
            product(4, '外箱尺寸:60x35x30cm 毛重:12kg', '虚构全覆盖'),
            product(5, '外箱尺寸:60x35x30cm 毛重:12kg 1台/箱', '虚构全覆盖'),
        ])
        ranking = report['supplier_coverage']
        self.assertEqual([r['supplier'] for r in ranking], ['虚构零覆盖', '虚构半覆盖', '虚构全覆盖'])
        self.assertEqual([r['coverage_rate'] for r in ranking], [0, .5, 1])
        self.assertEqual([r['total'] for r in ranking], [1, 2, 3])
        self.assertEqual([r['with_packing'] for r in ranking], [0, 1, 3])
        self.assertEqual([r['without_packing'] for r in ranking], [1, 1, 0])
        self.assertEqual((ranking[2]['ddp_estimable'], ranking[2]['ddp_ready']), (1, 1))
        self.assertEqual(build_report([])['supplier_coverage'], [])

    def test_all_candidate_sources_and_full_model_context_survive_json(self):
        entry = product(0)
        entry['chunks'] = [
            {'chunk_id': 'virtual-small', 'text': '虚构型号 SMALL 毛重:1.5kg 产品尺寸:21x18x19cm'},
            {'chunk_id': 'virtual-large', 'text': '虚构型号 LARGE 毛重:19kg 产品尺寸:57x44x42cm'},
            {'chunk_id': 'virtual-copy', 'text': '虚构型号 LARGE 旧资料 毛重:19kg'},
        ]
        record = json.loads(json.dumps(build_report([entry]), ensure_ascii=False))['products'][0]
        group = record['facts']['gross_weight_kg']
        self.assertEqual(group['conflict_reason'], 'multi_model_suspected')
        self.assertEqual([candidate['value'] for candidate in group['candidates']], [1.5, 19, 19])
        self.assertEqual([candidate['chunk_id'] for candidate in group['candidates']],
                         ['virtual-small', 'virtual-large', 'virtual-copy'])
        self.assertEqual([candidate['raw'] for candidate in group['candidates']],
                         ['毛重:1.5kg', '毛重:19kg', '毛重:19kg'])
        self.assertEqual(record['source_chunks'], entry['chunks'])
        self.assertNotIn('selected_value', group)

    def test_compact_html_payload_has_all_rows_filters_and_candidate_evidence(self):
        report = build_report([product(i, '外箱尺寸:60x35x30cm 毛重:10kg 毛重:19kg') for i in range(121)]
                              + [product(121)])
        audit = self.audit(report)
        data = audit.payload
        self.assertEqual(data['page_size'], 50)
        self.assertEqual(data['default_filter'], 'action')
        self.assertEqual(len(data['rows']), 122)
        self.assertEqual(audit.statuses, set())
        self.assertEqual(audit.scripts, 1)
        self.assertEqual(audit.external_resources, [])
        weights = next(group for group in data['rows'][0][8] if group[0] == 'gross_weight_kg')
        self.assertEqual(weights[1], 'multi_model_suspected')
        self.assertEqual([candidate[0] for candidate in weights[2]], [10, 19])
        self.assertEqual([data['strings'][candidate[2]] for candidate in weights[2]],
                         ['virtual-source-0000'] * 2)

    def test_template_termination_entities_and_data_text_are_escaped_once(self):
        text = '</template><script>alert("virtual")</script>&lt;img&gt;& 中文\u2028\u2029'
        entry = product(0, '毛重:10kg')
        entry['model'] = text
        entry['supplier'] = text
        report = build_report([entry])
        candidate = report['products'][0]['facts']['gross_weight_kg']['candidates'][0]
        candidate['raw'] = text
        candidate['chunk_id'] = text
        html = render_html(report)
        self.assertFalse(text in html, 'source text must not form HTML tags')
        audit = HTMLAudit()
        audit.feed(html)
        self.assertEqual(audit.scripts, 1)
        self.assertEqual(audit.external_resources, [])
        self.assertIn(text, audit.payload['strings'])
        self.assertIn(text, audit.payload['suppliers'])

    @unittest.skipUnless(shutil.which('node'), 'optional offline Node runtime not installed')
    def test_actual_javascript_pagination_search_filter_and_text_nodes(self):
        products = [product(i, '毛重:10kg') for i in range(107)]
        products += [product(107, '毛重:10kg 毛重:19kg'), product(108, '毛重:10kg 毛重:12kg'),
                     product(109, '外箱尺寸:60x35x30cm 毛重:12kg', '虚构乙厂'),
                     product(110, '外箱尺寸:60x35x30cm 毛重:12kg 1台/箱')]
        products += [product(i) for i in range(111, 118)]
        injected = '<img src=x onerror="virtual()">&lt;script&gt;'
        products[106]['model'] = injected
        products[106]['source_file'] = '虚构唯一来源报价表.xlsx'
        report = build_report(products)
        audit = self.audit(report)
        other_supplier = audit.payload['suppliers'].index('虚构乙厂')
        events = [
            {'id': 'next', 'type': 'click'}, {'id': 'next', 'type': 'click'},
            {'id': 'search', 'type': 'input', 'value': '虚构唯一来源报价表'},
            {'id': 'search', 'type': 'input', 'value': 'does-not-exist'},
            {'id': 'search', 'type': 'input', 'value': ''},
            {'id': 'status-filter', 'type': 'change', 'value': 'missing'},
            {'id': 'status-filter', 'type': 'change', 'value': 'all'},
            {'id': 'status-filter', 'type': 'change', 'value': 'multi_model_suspected'},
            {'id': 'status-filter', 'type': 'change', 'value': 'value_mismatch'},
            {'id': 'status-filter', 'type': 'change', 'value': 'estimable'},
            {'id': 'supplier-filter', 'type': 'change', 'value': str(other_supplier)},
            {'id': 'status-filter', 'type': 'change', 'value': 'ready'},
        ]
        result = subprocess.run([shutil.which('node'), str(Path(__file__).with_name('html_runtime.js'))],
                                input=json.dumps({'data': audit.payload, 'script': ''.join(audit.script_text),
                                                  'events': events}), text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)
        snapshots = output['snapshots']
        self.assertEqual([len(snapshot['rows']) for snapshot in snapshots],
                         [50, 50, 10, 1, 0, 50, 7, 50, 1, 1, 1, 1, 0])
        self.assertTrue(snapshots[0]['previousDisabled'])
        self.assertFalse(snapshots[0]['nextDisabled'])
        self.assertTrue(snapshots[2]['nextDisabled'])
        self.assertIn('第 3 / 3 页', snapshots[2]['count'])
        self.assertIn('第 1 / 1 页', snapshots[3]['count'])
        self.assertIn(injected, snapshots[3]['rows'][0]['text'])
        self.assertTrue(snapshots[4]['empty'])
        self.assertEqual({row['status'] for row in snapshots[6]['rows']}, {'missing'})
        self.assertFalse({'script', 'img', 'iframe'} & set(output['createdTags']))
        self.assertEqual(output['drafts'], [report['followups'][supplier]
                                           for supplier in audit.payload['suppliers']])

    def test_6000_estimable_products_also_keep_html_below_limit(self):
        started = perf_counter()
        report = build_report(product(i, '外箱尺寸:60x35x30cm 毛重:12kg',
                                       f'虚构供应商-{i % 24:02}') for i in range(6000))
        html = render_html(report)
        self.assertEqual(report['summary']['ddp_estimable'], 6000)
        self.assertLess(len(html.encode('utf-8')), 2_500_000)
        self.assertLess(perf_counter() - started, 10)

    def test_6000_synthetic_products_html_size_and_generation_time(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / 'virtual.sqlite3'
            synthetic_catalog(path)
            started = perf_counter()
            report = write_report(path, root / 'reports')
            elapsed = perf_counter() - started
            html_path = root / 'reports' / 'packing_report.html'
            self.assertEqual(report['summary']['total'], 6000)
            self.assertEqual(sum(row['with_packing'] for row in report['supplier_coverage']), 960)
            self.assertLess(html_path.stat().st_size, 2_500_000)
            self.assertLess(elapsed, 10, '6000-row end-to-end report generation regressed')
            audit = HTMLAudit()
            audit.feed(html_path.read_text(encoding='utf-8'))
            self.assertEqual(len(audit.payload['rows']), 6000)
            self.assertEqual(audit.statuses, set())
            # Fully missing products stay complete in JSON, compact in drafts.
            empty = report['products'][-1]
            self.assertEqual(empty['completeness']['status'], 'missing')
            self.assertNotIn(empty['label'], report['followups'][empty['supplier']])


if __name__ == '__main__':
    unittest.main()
