"""Third-round readability contracts. All values and databases are fictitious."""
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import tempfile
import unittest

from console.supplier_data.catalog_reader import CatalogError, read_products
from console.supplier_data.labels import DESCRIPTION_LIMIT, product_label
from console.supplier_data.report import build_report, render_html, write_report
from console.supplier_data.report_html import action_hint, STATUS_LABELS
from test_catalog_report import create_catalog, HTMLAudit
from test_report_round2 import product


HASH = 'equipment:' + 'a0b1c2d3' * 8
FORBIDDEN = re.compile(r'\b(?:ddp_ready|ddp_estimable|estimable|partial|missing|conflict|'
                       r'chunk(?:_id)?|equipment_id|confidence|labeled|inferred)\b', re.I)


class BusinessText(HTMLParser):
    """Read all interface text, excluding data/script/style containers."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.excluded = 0
        self.text = []

    def handle_starttag(self, tag, attrs):
        if tag in ('template', 'script', 'style'):
            self.excluded += 1
        if not self.excluded:
            self.text.extend(value for key, value in attrs
                             if key in ('placeholder', 'aria-label', 'data-label', 'title'))

    def handle_endtag(self, tag):
        if tag in ('template', 'script', 'style'):
            self.excluded -= 1

    def handle_data(self, text):
        if not self.excluded:
            self.text.append(text)


def run_ui(report, events=(), copy_mode='clipboard'):
    audit = HTMLAudit()
    audit.feed(render_html(report))
    result = subprocess.run([shutil.which('node'), str(Path(__file__).with_name('html_runtime.js'))],
                            input=json.dumps({'data': audit.payload, 'script': ''.join(audit.script_text),
                                              'events': events, 'copyMode': copy_mode}),
                            text=True, capture_output=True, timeout=10)
    if result.returncode:
        raise AssertionError(result.stderr)
    return json.loads(result.stdout)


class LabelTests(unittest.TestCase):
    def label(self, **changes):
        return product_label({'equipment_id': HASH, 'fixture_type': '光束灯', 'wattage': 230,
                              'name': '不能代替型号的旧名称', **changes})

    def test_model_has_priority_over_description_and_fallback(self):
        self.assertEqual(self.label(model='  XS-618 利剑图案光束  ', description='虚构描述'),
                         'XS-618 利剑图案光束')

    def test_first_meaningful_description_line_and_whitespace(self):
        self.assertEqual(self.label(model=' ', description='\n\n虚构   利剑光束灯\n第二段不作为名称'),
                         '虚构 利剑光束灯')

    def test_formula_text_is_rejected_in_description_and_model(self):
        for formula in ('=_xlfn.DISPIMG("virtual",1)', '=DISPIMG("virtual", 1)',
                        '＝＿ｘｌｆｎ．ＤＩＳＰＩＭＧ（虚构，１）', '=SUM(A1:A2)'):
            with self.subTest(formula=formula):
                self.assertEqual(self.label(description=formula), '光束灯 230W')
                self.assertEqual(self.label(description=formula + '\n虚构追光灯'), '虚构追光灯')
        for formula in ('=_xlfn.DISPIMG("virtual",1)', '＝＿ｘｌｆｎ．ＤＩＳＰＩＭＧ（虚构，１）'):
            self.assertEqual(self.label(model=formula, description='虚构追光灯'), '虚构追光灯')

    def test_table_headers_separators_and_numeric_cells_are_removed(self):
        text = '| 型号 | 品名 | 功率 |\n| --- | :---: | --- |\n| XS-618 | 利剑图案光束 | 230 |'
        self.assertEqual(self.label(description=text), 'XS-618 利剑图案光束')
        self.assertEqual(self.label(description='型号 | 品名 | …'), '光束灯 230W')
        self.assertEqual(self.label(description='型号 | 品名 | 功率（W）\nXS-618 | 利剑图案光束'),
                         'XS-618 利剑图案光束')
        self.assertEqual(self.label(description='｜XS-618｜虚构图案灯｜'), 'XS-618 虚构图案灯')

    def test_serial_number_prefixes_and_pure_numbers_are_rejected(self):
        for text in ('6.光束角度：120°', '６．光束角度：１２０°', '6、光束角度120°',
                     '12) 电压220V', '1.2. 控制方式', '6 光束角度120°', '12345', '| 123 | 456 |'):
            with self.subTest(text=text):
                self.assertEqual(self.label(description=text), '光束灯 230W')
        self.assertEqual(self.label(description='6.光束角度：120°\n虚构光束灯'), '虚构光束灯')
        self.assertEqual(self.label(description='230W 虚构光束灯'), '230W 虚构光束灯')

    def test_long_description_is_at_most_36_characters(self):
        label = self.label(description='虚构光束灯' * 30)
        self.assertEqual(len(label), DESCRIPTION_LIMIT)
        self.assertEqual(label, ('虚构光束灯' * 30)[:35] + '…')

    def test_fallback_without_power_or_known_fixture_never_uses_hash(self):
        for wattage in (None, '', 0, -1, float('nan'), float('inf'), '非数字'):
            with self.subTest(wattage=wattage):
                self.assertEqual(self.label(fixture_type='beam', wattage=wattage), '光束灯')
        self.assertEqual(self.label(fixture_type='unknown'), '灯具 230W')
        self.assertEqual(product_label({'equipment_id': HASH}), '灯具')


class SourceAssociationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.db = self.root / 'virtual.sqlite3'
        create_catalog(self.db)

    def tearDown(self):
        self.temporary.cleanup()

    def test_left_join_shared_asset_keeps_all_entries_and_exact_evidence(self):
        before = self.db.read_bytes()
        products = list(read_products(self.db))
        self.assertEqual([p['source_file'] for p in products],
                         ['虚构报价表2.0.xlsx', '虚构说明书.docx', '', '虚构报价表2.0.xlsx'])
        self.assertEqual([p['source_type'] for p in products], ['spreadsheet', 'document', '', 'spreadsheet'])
        self.assertEqual(len(products[0]['chunks']), 2)
        self.assertEqual(len(products[3]['chunks']), 1)
        report = write_report(self.db, self.root / 'out')
        record = report['products'][3]
        self.assertEqual((record['source_file'], record['source_type'], record['label']),
                         ('虚构报价表2.0.xlsx', 'spreadsheet', 'VIRTUAL-4'))
        for key in ('equipment_id', 'asset_id', 'supplier', 'model', 'name', 'fixture_type',
                    'wattage', 'exw_price', 'currency', 'facts', 'source_chunks', 'completeness'):
            self.assertIn(key, record)
        self.assertEqual(report['schema_version'], 2)
        self.assertEqual(self.db.read_bytes(), before)

    def test_unmatched_asset_and_null_filename_return_empty_strings(self):
        with sqlite3.connect(self.db) as connection:
            connection.execute("UPDATE equipment_specs SET asset_id='virtual-absent' WHERE equipment_id='virtual-1'")
            connection.execute('UPDATE multimedia_assets SET filename=NULL, source_type=NULL')
        products = list(read_products(self.db))
        self.assertTrue(all(p['source_file'] == p['source_type'] == '' for p in products))

    def test_missing_join_table_has_clear_error(self):
        with sqlite3.connect(self.db) as connection:
            connection.execute('DROP TABLE multimedia_assets')
        with self.assertRaisesRegex(CatalogError, '缺少表：multimedia_assets'):
            list(read_products(self.db))

    def test_missing_each_join_column_has_clear_error_without_output(self):
        for table, column in [('equipment_specs', 'asset_id'), ('multimedia_assets', 'asset_id'),
                              ('multimedia_assets', 'filename'), ('multimedia_assets', 'source_type')]:
            with self.subTest(table=table, column=column):
                with sqlite3.connect(self.db) as connection:
                    connection.execute(f'ALTER TABLE {table} RENAME COLUMN {column} TO virtual_absent')
                before = self.db.read_bytes()
                with self.assertRaisesRegex(CatalogError, f'{table} 缺少列：{column}'):
                    write_report(self.db, self.root / 'no-output')
                self.assertFalse((self.root / 'no-output').exists())
                self.assertEqual(self.db.read_bytes(), before)
                with sqlite3.connect(self.db) as connection:
                    connection.execute(f'ALTER TABLE {table} RENAME COLUMN virtual_absent TO {column}')

    def test_duplicate_asset_identifiers_do_not_multiply_candidates(self):
        with sqlite3.connect(self.db) as connection:
            connection.executescript('''
                ALTER TABLE multimedia_assets RENAME TO virtual_old;
                CREATE TABLE multimedia_assets AS SELECT * FROM virtual_old;
                INSERT INTO multimedia_assets SELECT * FROM virtual_old;
            ''')
        with self.assertRaisesRegex(CatalogError, 'multimedia_assets 的 asset_id 不唯一'):
            list(read_products(self.db))

    def test_description_is_optional_for_older_schemas(self):
        with sqlite3.connect(self.db) as connection:
            connection.execute('ALTER TABLE equipment_specs DROP COLUMN description')
        self.assertEqual(len(list(read_products(self.db))), 4)


class ReadabilityTests(unittest.TestCase):
    def test_action_hint_ranks_by_largest_gap_not_lowest_rate(self):
        entries = [product(0, supplier='虚构小厂')]
        entries += [product(i + 1, supplier='虚构大厂') for i in range(3)]
        entries += [product(i + 4, supplier='虚构中厂') for i in range(2)]
        entries += [product(6, '毛重:10kg 毛重:19kg', '虚构高覆盖厂')]
        hint = action_hint(build_report(entries))
        self.assertEqual(hint, '今天该怎么用：优先联系虚构大厂、虚构中厂、虚构小厂，补充或确认包装资料。'
                              '有 1 条资料疑似混入多个型号，建议拆分后再核对。')

    def test_action_hint_tiny_zero_coverage_supplier_does_not_outrank_big_gap(self):
        entries = [product(0, supplier='虚构微型厂')]
        entries += [product(i + 1, supplier='虚构大缺口厂') for i in range(5)]
        entries += [product(6, '外箱尺寸:60x35x30cm 毛重:12kg 1台/箱', '虚构大缺口厂')]
        hint = action_hint(build_report(entries))
        self.assertTrue(hint.startswith('今天该怎么用：优先联系虚构大缺口厂、虚构微型厂'))

    def test_action_hint_empty_all_ready_and_supplier_escaping(self):
        self.assertEqual(action_hint(build_report([])), '今天该怎么用：暂无资料条目，请先补充供应商资料。')
        ready = build_report([product(0, '外箱尺寸:60x35x30cm 毛重:12kg 1台/箱')])
        self.assertIn('报价前仍须人工核对', action_hint(ready))
        self.assertNotIn('优先联系', action_hint(ready))
        supplier = '</p><img src=x onerror="virtual()">'
        html = render_html(build_report([product(0, supplier=supplier)]))
        self.assertNotIn(supplier, html)
        self.assertIn('&lt;img', html)

    def test_legacy_product_input_gets_new_source_fields_without_losing_ids(self):
        report = build_report([product(0)])
        record = report['products'][0]
        self.assertEqual((record['source_file'], record['source_type']), ('', ''))
        self.assertEqual(record['equipment_id'], 'virtual-0000')

    def test_followup_uses_label_and_filename_for_partial_missing_and_conflict(self):
        for text in ('', '毛重:12kg', '毛重:1.5kg 毛重:19kg'):
            with self.subTest(text=text):
                entry = product(0, text)
                entry.update(equipment_id=HASH, model='XS-618 利剑图案光束', source_file='虚构报价表.xlsx')
                draft = build_report([entry])['followups']['虚构甲厂']
                self.assertIn('虚构报价表.xlsx 中的「XS-618 利剑图案光束」', draft)
                self.assertNotIn(HASH, draft)
                self.assertNotRegex(draft, FORBIDDEN)

    def test_static_interface_uses_chinese_statuses_and_entry_counts(self):
        html = render_html(build_report([product(0, '毛重:12kg')]))
        audit = BusinessText()
        audit.feed(html)
        text = ' '.join(audit.text)
        self.assertNotRegex(text, FORBIDDEN)
        self.assertNotIn('产品总数', text)
        self.assertNotIn('总款数', text)
        self.assertIn('从供应商文档、报价表和聊天中抽出的规格条目，含文档碎片，不等于产品款数', text)
        for label in STATUS_LABELS.values():
            self.assertIn(label, text)
        self.assertIn('今天该怎么用', text)

    @unittest.skipUnless(shutil.which('node'), 'optional offline Node runtime not installed')
    def test_rendered_business_text_excludes_codes_and_hashes_in_every_status(self):
        texts = ['外箱尺寸:60x35x30cm 毛重:12kg 1台/箱', '外箱尺寸:60x35x30cm 毛重:12kg',
                 '毛重:12kg', '', '毛重:10kg 毛重:12kg', '毛重:1.5kg 毛重:19kg']
        entries = [product(i, text) for i, text in enumerate(texts)]
        for i, entry in enumerate(entries):
            entry.update(equipment_id=HASH + str(i), source_file='虚构报价表.xlsx', source_type='document')
        output = run_ui(build_report(entries), [{'id': 'status-filter', 'type': 'change', 'value': 'all'}])
        rows = output['snapshots'][-1]['rows']
        self.assertEqual(len(rows), 6)
        for row in rows:
            self.assertNotRegex(row['businessText'], FORBIDDEN)
            self.assertNotIn('equipment:', row['businessText'])
            self.assertIn('来源文件：虚构报价表.xlsx', row['businessText'])
            self.assertEqual(len(row['technical']), 1)
            self.assertFalse(row['technical'][0]['open'])
            self.assertIn('equipment_id：' + HASH[:18] + '…', row['technical'][0]['text'])
            self.assertIn('复制完整编号', row['technical'][0]['copyButtons'])

    @unittest.skipUnless(shutil.which('node'), 'optional offline Node runtime not installed')
    def test_search_matches_label_filename_supplier_and_incomplete_filter(self):
        entries = [product(0, '毛重:12kg'), product(1, '', '虚构乙厂'), product(2, '外箱尺寸:60x35x30cm 毛重:12kg')]
        entries[0].update(model='虚构利剑光束', source_file='独有虚构报价表.xlsx')
        events = [{'id': 'status-filter', 'type': 'change', 'value': 'all'}]
        events += [{'id': 'search', 'type': 'input', 'value': value}
                   for value in ('虚构利剑', '独有虚构报价表', '虚构乙厂', '')]
        events += [{'id': 'status-filter', 'type': 'change', 'value': 'incomplete'}]
        output = run_ui(build_report(entries), events)
        self.assertEqual([len(s['rows']) for s in output['snapshots']], [2, 3, 1, 1, 1, 3, 2])
        self.assertEqual({row['status'] for row in output['snapshots'][-1]['rows']}, {'partial', 'missing'})

    @unittest.skipUnless(shutil.which('node'), 'optional offline Node runtime not installed')
    def test_source_file_and_type_injections_remain_text_in_actual_dom(self):
        injected = '</template><img src=x onerror="virtual()">&lt;script&gt;中文\u2028\u2029'
        entry = product(0, '毛重:12kg')
        entry.update(source_file=injected, source_type=injected)
        report = build_report([entry])
        self.assertNotIn(injected, render_html(report))
        output = run_ui(report)
        row = output['snapshots'][0]['rows'][0]
        self.assertIn(injected, row['businessText'])
        self.assertIn(injected, row['technical'][0]['text'])
        self.assertFalse({'img', 'script', 'iframe'} & set(output['createdTags']))

    @unittest.skipUnless(shutil.which('node'), 'optional offline Node runtime not installed')
    def test_short_identifier_copies_full_value_and_supports_file_page_fallback(self):
        entry = product(0, '毛重:12kg')
        entry['equipment_id'] = HASH
        report = build_report([entry])
        for mode in ('clipboard', 'fallback', 'manual'):
            with self.subTest(mode=mode):
                output = run_ui(report, [{'id': 'copy-first-id', 'type': 'click'}], mode)
                initial = output['snapshots'][0]['rows'][0]['technical'][0]
                self.assertNotIn(HASH, initial['text'])
                if mode == 'manual':
                    final = output['snapshots'][-1]['rows'][0]['technical'][0]
                    self.assertEqual(final['selectedValues'], [HASH])
                    self.assertIn('请使用系统复制操作', final['text'])
                else:
                    self.assertEqual(output['copied'], [HASH])

    def test_6000_hashed_entries_with_sources_and_estimable_drafts_fit_limit(self):
        def entries():
            for i in range(6000):
                entry = product(i, '外箱尺寸:60x35x30cm 毛重:12kg', f'虚构供应商-{i % 24:02}')
                entry.update(equipment_id=f'equipment:{i:064x}',
                             source_file=f'虚构报价表{i % 48:02}.xlsx', source_type='spreadsheet')
                yield entry
        report = build_report(entries())
        self.assertEqual(report['summary']['ddp_estimable'], 6000)
        self.assertLess(len(render_html(report).encode('utf-8')), 2_500_000)
        self.assertTrue(all('equipment:' not in draft for draft in report['followups'].values()))


if __name__ == '__main__':
    unittest.main()
