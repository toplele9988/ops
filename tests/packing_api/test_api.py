"""Only fictional products, report files and temporary SQLite databases."""
import asyncio
from copy import deepcopy
import json
import os
from pathlib import Path
import sqlite3
import stat
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.parse import urlencode

from console.supplier_data.report import build_report

try:
    from console import v3_packing_api as api
except ModuleNotFoundError as exc:
    if exc.name != 'starlette' and not (exc.name or '').startswith('starlette.'):
        raise
    api = None

NOW = 1_800_000_000.0
READY = '外箱尺寸 50x40x30 cm，毛重 12 kg，每箱数量 2 台'
ESTIMABLE = '外箱尺寸 50x40x30 cm，毛重 12 kg'


def product(index, supplier, text='', label=None, source='虚构报价.xlsx'):
    return dict(equipment_id=f'{index:064x}', supplier=supplier, fixture_type='beam',
                wattage=230, model=label or f'虚构型号 {index:02}', source_file=source,
                source_type='fictional', chunks=[dict(chunk_id=f'fictional-chunk-{index}', text=text)])


def fixture():
    return build_report([
        product(1, '虚构甲', READY, 'Zulu ready'),
        product(2, '虚构甲', ESTIMABLE, 'Echo estimate', 'FictionalCatalog.XLSX'),
        product(3, '虚构甲', '净重 8 kg', 'Beta partial'),
        product(4, '虚构甲', '', 'Missing entry'),
        product(5, '虚构甲', '外箱尺寸 50x40x30 cm，外箱尺寸 80x40x30 cm，毛重 12 kg，'
                '每箱数量 2 台', 'Alpha multi'),
        product(6, '虚构乙', '外箱尺寸 50x40x30 cm，外箱尺寸 55x40x30 cm，毛重 12 kg，'
                '每箱数量 2 台', 'Bravo mismatch'),
        product(7, '虚构乙', ''), product(8, '虚构乙', ''),
        product(9, '虚构丙', ''), product(10, '虚构丙', ''),
        product(11, '虚构丁', READY),
    ])


def request(*, query=None, form=None, authenticated=True, principal=True,
            recheck=True, username='fictional-tester', body=b''):
    async def read_body():
        return body
    return SimpleNamespace(
        state=SimpleNamespace(password_principal=SimpleNamespace(username=username) if principal else None,
                              password_recheck=(lambda: recheck),
                              session=SimpleNamespace(authenticated=authenticated), form=form),
        query_params=query or {}, body=read_body)


@unittest.skipIf(api is None, 'starlette not installed')
class PackingApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.report_path = Path(self.temp.name) / 'fictional-report.json'
        self.state_path = Path(self.temp.name) / 'state' / 'actions.sqlite3'
        self.app = SimpleNamespace(error_response=lambda request, code: code)
        self.routes = api.build(self.app, self.report_path, self.state_path)
        self.handlers = {route.path.rsplit('/', 1)[-1]: route.endpoint for route in self.routes}

    def seed(self, report=None):
        report = fixture() if report is None else report
        self.report_path.write_text(json.dumps(report, ensure_ascii=False, allow_nan=False), encoding='utf8')
        return report

    def call(self, kind='summary', **kw):
        return asyncio.run(self.handlers[kind](request(**kw)))

    def data(self, kind='summary', **kw):
        response = self.call(kind, **kw)
        self.assertEqual(response.status_code, 200, response.body.decode())
        self.assertEqual(response.headers['cache-control'], 'no-store')
        self.assertEqual(response.headers['x-content-type-options'], 'nosniff')
        return json.loads(response.body)

    def contact(self, supplier='虚构乙', note=None):
        form = {'supplier': [supplier]}
        if note is not None:
            form['note'] = [note]
        return self.data('contacted', form=form)

    def test_routes_and_methods(self):
        self.assertEqual([route.path for route in self.routes], [
            '/api/v1/packing/summary', '/api/v1/packing/items',
            '/api/v1/packing/followup', '/api/v1/packing/contacted'])
        for route in self.routes[:3]:
            self.assertEqual(route.methods - {'HEAD'}, {'GET'})
        self.assertEqual(self.routes[3].methods, {'POST'})

    def test_all_auth_gates_on_every_get_and_post_never_create_database(self):
        self.seed()
        for kind in self.handlers:
            for kwargs in ({'authenticated': False}, {'principal': False},
                           {'username': ''}, {'recheck': False}):
                with self.subTest(kind=kind, kwargs=kwargs):
                    self.assertEqual(self.call(kind, query={'supplier': '虚构甲'},
                                               form={'supplier': ['虚构甲']}, **kwargs), 403)
                    self.assertFalse(self.state_path.exists())
            for field, value in (('password_recheck', 'not callable'), ('session', None),
                                 ('session', SimpleNamespace())):
                req = request(query={'supplier': '虚构甲'}, form={'supplier': ['虚构甲']})
                setattr(req.state, field, value)
                self.assertEqual(asyncio.run(self.handlers[kind](req)), 403)
            req = request(query={'supplier': '虚构甲'}, form={'supplier': ['虚构甲']})
            req.state.password_recheck = lambda: (_ for _ in ()).throw(RuntimeError('fictional private'))
            self.assertEqual(asyncio.run(self.handlers[kind](req)), 403)
        self.assertFalse(self.state_path.parent.exists())

    def test_post_recheck_false_does_not_modify_existing_database_or_read_body(self):
        self.seed()
        self.contact()
        before = self.state_path.read_bytes()
        req = request(recheck=False)
        async def forbidden_body():
            self.fail('unauthorized POST read the body')
        req.body = forbidden_body
        with patch.object(api, '_record_contact', side_effect=AssertionError('must not write')):
            self.assertEqual(asyncio.run(self.handlers['contacted'](req)), 403)
        self.assertEqual(self.state_path.read_bytes(), before)
        with sqlite3.connect(self.state_path) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM supplier_followups').fetchone()[0], 1)

    def test_get_recheck_happens_after_read_even_for_errors_and_missing_report(self):
        self.seed()
        for kind, query in (('summary', {}), ('items', {'page': '0'}),
                            ('followup', {'supplier': '虚构不存在'})):
            events = []
            original = api._ReportCache.read
            def tracked(cache):
                events.append('read')
                return original(cache)
            req = request(query=query)
            def recheck():
                events.append('recheck')
                return False
            req.state.password_recheck = recheck
            with patch.object(api._ReportCache, 'read', tracked):
                self.assertEqual(asyncio.run(self.handlers[kind](req)), 403)
            self.assertEqual(events[-1], 'recheck')
            if kind != 'items':
                self.assertEqual(events, ['read', 'recheck'])
        self.report_path.unlink()
        self.assertEqual(self.call(recheck=False), 403)
        self.report_path.write_text('broken fictional JSON', encoding='utf8')
        self.assertEqual(self.call(recheck=False), 403)

    def test_summary_counts_coverage_priority_and_top_three_actions(self):
        self.seed()
        data = self.data()
        self.assertTrue(data['ready'])
        self.assertEqual(data['notice'], '抽取结果须人工核对，非已确认规格')
        self.assertEqual(data['totals'], dict(entries=11, ready=2, estimable=1, partial=1,
                                             missing=5, conflict=2, multi_model=1, value_mismatch=1))
        # Missing counts tie: lower coverage wins. Then a one-entry gap outranks
        # the fully covered ready supplier, despite its lower total volume.
        self.assertEqual([s['supplier'] for s in data['suppliers']], ['虚构丙', '虚构乙', '虚构甲', '虚构丁'])
        self.assertEqual([s['priority'] for s in data['suppliers']], [1, 2, 3, 4])
        self.assertEqual([a['supplier'] for a in data['next_actions']], ['虚构丙', '虚构乙', '虚构甲'])
        self.assertEqual(data['next_actions'][0], dict(supplier='虚构丙', reason='2 条资料没有任何包装信息', count=2))
        supplier = next(s for s in data['suppliers'] if s['supplier'] == '虚构甲')
        self.assertEqual((supplier['with_packing'], supplier['without_packing']), (4, 1))
        self.assertEqual(supplier['coverage_rate'], 4 / 5)
        self.assertEqual((supplier['ready'], supplier['estimable']), (1, 1))
        self.assertIsNone(supplier['last_contacted'])
        self.assertIsNone(supplier['contacted_note'])
        self.assertFalse(self.state_path.parent.exists())

    def test_priority_gap_before_coverage_then_supplier_name_and_action_limit(self):
        records = []
        for index, supplier in enumerate(('虚构 A', '虚构 B', '虚构 C', '虚构 D')):
            records.append(product(index * 10 + 1, supplier))
        records.extend(product(50 + i, '虚构大组') for i in range(3))
        records.extend(product(100 + i, '虚构大组', READY) for i in range(5))
        self.seed(build_report(records))
        data = self.data()
        self.assertEqual([s['supplier'] for s in data['suppliers']],
                         ['虚构大组', '虚构 A', '虚构 B', '虚构 C', '虚构 D'])
        self.assertEqual([a['supplier'] for a in data['next_actions']], ['虚构大组', '虚构 A', '虚构 B'])

    def test_recent_contact_moves_supplier_after_others_and_reason_shows_days(self):
        self.seed()
        with patch.object(api.time, 'time', return_value=NOW - 2 * 86400):
            self.contact('虚构乙')
        with patch.object(api.time, 'time', return_value=NOW):
            data = self.data()
        self.assertEqual([s['supplier'] for s in data['suppliers']], ['虚构丙', '虚构甲', '虚构丁', '虚构乙'])
        self.assertEqual([a['supplier'] for a in data['next_actions']], ['虚构丙', '虚构甲', '虚构乙'])
        self.assertIn('2 天前已催过', data['next_actions'][2]['reason'])
        with patch.object(api.time, 'time', return_value=NOW + 5 * 86400):
            data = self.data()
        self.assertEqual([s['supplier'] for s in data['suppliers']][:2], ['虚构丙', '虚构乙'])
        self.assertNotIn('已催过', data['next_actions'][1]['reason'])

    def test_summary_empty_all_ready_and_nonmissing_followup_counts(self):
        self.seed(build_report([]))
        self.assertEqual(self.data()['totals']['entries'], 0)
        self.assertEqual(self.data()['next_actions'], [])
        self.seed(build_report([product(1, '虚构甲', READY)]))
        self.assertEqual(self.data()['next_actions'], [])
        self.seed(build_report([product(1, '虚构甲', ESTIMABLE), product(2, '虚构甲', '净重 8 kg')]))
        action = self.data()['next_actions'][0]
        self.assertEqual(action['count'], 2)
        self.assertEqual(action['reason'], '2 条资料的包装信息仍须补充或确认')

    def test_items_default_status_order_pagination_and_contract(self):
        self.seed()
        data = self.data('items')
        self.assertEqual((data['total'], data['page'], data['page_size']), (6, 1, 30))
        self.assertEqual([p['status'] for p in data['items']],
                         ['conflict', 'conflict', 'estimable', 'partial', 'ready', 'ready'])
        self.assertEqual([p['label'] for p in data['items'][:2]], ['Alpha multi', 'Bravo mismatch'])
        paged = self.data('items', query={'page': '2', 'page_size': '2'})
        self.assertEqual(paged['items'], data['items'][2:4])
        self.assertEqual(paged['total'], 6)
        self.assertEqual(self.data('items', query={'page': '99'})['items'], [])
        ready = self.data('items', query={'status': 'ready', 'supplier': '虚构甲'})['items'][0]
        self.assertEqual(set(ready), {'id', 'label', 'supplier', 'source_file', 'fixture_type', 'wattage',
                                      'status', 'status_text', 'conflict_reason', 'conflict_text',
                                      'facts', 'n_candidates', 'missing', 'assumption'})
        self.assertEqual(ready['facts'], dict(carton_cm=[50, 40, 30], gross_kg=12, net_kg=None,
                                             units_per_carton=2, product_cm=None))
        self.assertEqual(ready['status_text'], '可直接报价（资料齐全）')
        self.assertIsNone(ready['assumption'])
        estimated = paged['items'][0]
        self.assertEqual(estimated['assumption'], '按每箱 1 台估算，须向供应商确认')
        self.assertEqual(estimated['missing'], ['每箱数量'])
        self.assertIsNone(estimated['facts']['units_per_carton'])

    def test_items_explicit_status_supplier_and_case_insensitive_substrings(self):
        report = self.seed()
        for status, count in (('missing', 5), ('partial', 1), ('estimable', 1), ('ready', 2), ('conflict', 2)):
            with self.subTest(status=status):
                data = self.data('items', query={'status': status})
                self.assertEqual(data['total'], count)
                self.assertTrue(all(p['status'] == status for p in data['items']))
        self.assertEqual(self.data('items', query={'supplier': '虚构甲'})['total'], 4)
        for query, expected in (('echo', 1), ('fictionalCATALOG', 1), ('虚构乙', 1),
                                ('%', 0), ('_', 0), (report['products'][0]['equipment_id'], 0)):
            self.assertEqual(self.data('items', query={'q': query})['total'], expected)
        self.assertEqual(self.data('items', query={'status': 'missing', 'supplier': '虚构乙', 'q': '型号'})['total'], 2)

    def test_conflicting_kinds_null_with_candidate_counts_and_reason(self):
        self.seed()
        items = self.data('items', query={'status': 'conflict'})['items']
        for item, reason in zip(items, ('multi_model_suspected', 'value_mismatch')):
            self.assertIsNone(item['facts']['carton_cm'])
            self.assertEqual(item['n_candidates']['carton_size'], 2)
            self.assertEqual(item['facts']['gross_kg'], 12)
            self.assertEqual(item['conflict_reason'], reason)
            self.assertEqual(item['conflict_text'], api.CONFLICT_TEXT[reason])
            self.assertEqual(item['status_text'], '资料有冲突')
        self.seed(build_report([product(1, '虚构甲', READY + '，净重 8 kg，净重 10 kg')]))
        item = self.data('items')['items'][0]
        self.assertIsNone(item['facts']['net_kg'])
        self.assertEqual(item['n_candidates']['net_weight_kg'], 2)
        self.assertEqual(item['facts']['carton_cm'], [50, 40, 30])

    def test_candidate_display_preserves_order_and_does_not_alias_flightcase(self):
        self.seed(build_report([
            product(1, '虚构甲', READY + '，外箱尺寸 50.5x40x30 cm，产品尺寸 20x10x5 cm，净重 8 kg'),
            product(2, '虚构甲', '航空箱尺寸 60x50x40 cm，毛重 20 kg，每箱数量 2 台'),
        ]))
        items = self.data('items')['items']
        self.assertEqual(items[0]['facts']['carton_cm'], [50, 40, 30])
        self.assertEqual(items[0]['n_candidates']['carton_size'], 2)
        self.assertEqual(items[0]['facts']['product_cm'], [20, 10, 5])
        self.assertEqual(items[0]['facts']['net_kg'], 8)
        self.assertEqual(items[1]['status'], 'ready')
        self.assertIsNone(items[1]['facts']['carton_cm'])
        self.assertEqual(items[1]['n_candidates']['flightcase_size'], 1)

    def test_invalid_items_parameters_are_400_even_when_report_missing(self):
        cases = ({'status': 'all'}, {'status': 'incomplete'}, {'page': '0'}, {'page': '-1'},
                 {'page': '1.1'}, {'page': 'x'}, {'page': '9' * 5000}, {'page_size': '101'},
                 {'page_size': '0'}, {'page_size': '-1'}, {'page_size': 'NaN'}, {'q': '字' * 501},
                 {'supplier': '字' * 501})
        for query in cases:
            with self.subTest(query=query):
                response = self.call('items', query=query)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(set(json.loads(response.body)), {'error'})
                self.assertEqual(response.headers['cache-control'], 'no-store')
        from starlette.datastructures import QueryParams
        self.assertEqual(self.call('items', query=QueryParams('status=ready&status=missing')).status_code, 400)
        self.assertEqual(self.call('followup').status_code, 400)
        self.seed()
        self.assertEqual(self.call('items', query={'supplier': '虚构未知'}).status_code, 400)

    def test_followup_uses_existing_draft_and_never_inserts_hash_identifiers(self):
        report = self.seed()
        data = self.data('followup', query={'supplier': '虚构甲'})
        self.assertEqual(data['lines'], report['followups']['虚构甲'].splitlines())
        self.assertEqual(data['text'], '\n'.join(data['lines']))
        self.assertIn('虚构报价.xlsx', data['text'])
        self.assertIn('Alpha multi', data['text'])
        self.assertIn('单台一箱', data['text'])
        for product_row in report['products']:
            self.assertNotIn(product_row['equipment_id'], data['text'])
        self.assertIsNone(data['last_contacted'])
        self.assertFalse(self.state_path.exists())
        response = self.call('followup', query={'supplier': '虚构未知'})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(set(json.loads(response.body)), {'error'})
        ready = self.data('followup', query={'supplier': '虚构丁'})
        self.assertEqual((ready['lines'], ready['text']), ([], ''))

    def test_contacted_appends_latest_record_and_reads_back_without_editing_report(self):
        self.seed()
        before = self.report_path.read_bytes()
        previous_umask = os.umask(0o022)
        self.addCleanup(os.umask, previous_umask)
        with patch.object(api.time, 'time', return_value=NOW):
            first = self.contact(note='虚构人工电话记录；系统不发送消息')
        with patch.object(api.time, 'time', return_value=NOW + 10):
            second = self.contact(note='字' * 200)
        self.assertEqual(first, dict(ok=True, supplier='虚构乙', last_contacted=NOW))
        self.assertEqual(second['last_contacted'], NOW + 10)
        self.assertEqual(stat.S_IMODE(self.state_path.stat().st_mode), 0o600)
        mask = os.umask(0o022)
        self.assertEqual(mask, 0o022)
        with sqlite3.connect(self.state_path) as db:
            self.assertEqual(db.execute('PRAGMA journal_mode').fetchone()[0], 'wal')
            rows = db.execute('SELECT supplier, contacted_at, note FROM supplier_followups ORDER BY id').fetchall()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[-1], ('虚构乙', NOW + 10, '字' * 200))
        # A new build reads the shared persisted state, rather than an in-memory value.
        fresh = api.build(self.app, self.report_path, self.state_path)
        followup = json.loads(asyncio.run(fresh[2].endpoint(request(query={'supplier': '虚构乙'}))).body)
        self.assertEqual((followup['last_contacted'], followup['contacted_note']), (NOW + 10, '字' * 200))
        supplier = next(s for s in self.data()['suppliers'] if s['supplier'] == '虚构乙')
        self.assertEqual(supplier['last_contacted'], NOW + 10)
        self.assertEqual(supplier['contacted_note'], '字' * 200)
        self.assertEqual(self.report_path.read_bytes(), before)

    def test_post_uses_middleware_form_and_fallback_urlencoded_body(self):
        self.seed()
        req = request(form={'supplier': ['虚构甲'], 'note': ['虚构备注']})
        async def forbidden_body():
            self.fail('middleware form must avoid a second body read')
        req.body = forbidden_body
        response = asyncio.run(self.handlers['contacted'](req))
        self.assertEqual(response.status_code, 200)
        self.data('contacted', body=urlencode({'supplier': '虚构乙', 'note': '虚构 + & % 备注'}).encode())
        self.assertEqual(self.data('followup', query={'supplier': '虚构乙'})['contacted_note'], '虚构 + & % 备注')
        self.data('contacted', body=urlencode({'supplier': '虚构丙', 'note': ''}).encode())
        self.assertIsNone(self.data('followup', query={'supplier': '虚构丙'})['contacted_note'])

    def test_bad_post_note_unknown_supplier_duplicates_and_field_limit_do_not_create_db(self):
        self.seed()
        forms = ({}, {'supplier': ['']}, {'supplier': ['虚构未知']}, {'supplier': ['虚构甲'], 'note': ['字' * 201]},
                 {'supplier': ['虚构甲', '虚构乙']}, {'supplier': ['虚构甲'], 'note': ['一', '二']},
                 {'supplier': '虚构甲'}, {'supplier': ['虚构甲'], 'note': [None]})
        for form in forms:
            with self.subTest(form=form):
                response = self.call('contacted', form=form)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(set(json.loads(response.body)), {'error'})
        body = urlencode([('supplier', '虚构甲')] + [('note', '一')] * 17).encode()
        self.assertEqual(self.call('contacted', body=body).status_code, 400)
        self.assertFalse(self.state_path.parent.exists())

    def test_missing_report_ready_false_no_database_and_post_cannot_write(self):
        for kind in ('summary', 'items', 'followup'):
            self.assertEqual(self.data(kind, query={'supplier': '虚构甲'}), {'ready': False})
        self.assertEqual(self.call('contacted', form={'supplier': ['虚构甲']}).status_code, 503)
        self.assertFalse(self.state_path.parent.exists())

    def test_existing_actions_database_without_followup_table_get_does_not_create_table(self):
        self.seed()
        self.state_path.parent.mkdir()
        with sqlite3.connect(self.state_path) as db:
            db.execute('CREATE TABLE fictional_other_actions (value TEXT)')
        before = self.state_path.read_bytes()
        self.assertIsNone(self.data()['suppliers'][0]['last_contacted'])
        self.assertIsNone(self.data('followup', query={'supplier': '虚构甲'})['last_contacted'])
        self.assertEqual(self.state_path.read_bytes(), before)

    def test_broken_report_wrong_schema_and_oversize_are_generic_503(self):
        valid = fixture()
        bad_shape = deepcopy(valid)
        bad_shape['products'][0]['facts']['carton_size']['candidates'][0]['value'] = 'fictional private data'
        for text in ('broken /fictional/private/path', '{}', '[]',
                     json.dumps({**valid, 'schema_version': 1}), json.dumps(bad_shape),
                     json.dumps(valid).replace('12.0', 'NaN', 1),
                     json.dumps(valid).replace('230', '1e999', 1)):
            self.report_path.write_text(text, encoding='utf8')
            for kind in ('summary', 'items', 'followup', 'contacted'):
                with self.subTest(kind=kind, text=text[:20]):
                    response = self.call(kind, query={'supplier': '虚构甲'}, form={'supplier': ['虚构甲']})
                    self.assertEqual(response.status_code, 503)
                    self.assertEqual(set(json.loads(response.body)), {'error'})
                    self.assertNotIn(str(self.report_path), response.body.decode())
                    self.assertNotIn('/fictional/private/path', response.body.decode())
        self.assertFalse(self.state_path.exists())
        self.seed()
        self.data()  # Populate cache before replacing with a huge file.
        with self.report_path.open('wb') as handle:
            handle.truncate(api.MAX_BYTES + 1)
        self.assertEqual(self.call().status_code, 503)

    def test_bounded_read_even_if_initial_stat_size_is_stale(self):
        self.seed()
        real_stat = self.report_path.stat()
        stale_stat = SimpleNamespace(st_size=1, st_mtime_ns=real_stat.st_mtime_ns,
                                     st_dev=real_stat.st_dev, st_ino=real_stat.st_ino)
        with patch.object(api, 'MAX_BYTES', 16), patch.object(Path, 'stat', return_value=stale_stat):
            self.assertEqual(self.call().status_code, 503)

    def test_state_errors_are_generic_and_do_not_leak_exceptions(self):
        self.seed()
        self.state_path.parent.mkdir()
        self.state_path.write_text('fictional damaged database', encoding='utf8')
        for kind in ('summary', 'followup', 'contacted'):
            response = self.call(kind, query={'supplier': '虚构甲'}, form={'supplier': ['虚构甲']})
            self.assertEqual(response.status_code, 503)
            self.assertNotIn(str(self.state_path), response.body.decode())
            self.assertNotIn('database', response.body.decode())

    def test_report_mtime_cache_refresh_removal_corruption_and_recovery(self):
        report = self.seed()
        original = api.json.loads
        with patch.object(api.json, 'loads', wraps=original) as loads:
            self.call()
            self.call('items')
            self.call('followup', query={'supplier': '虚构甲'})
            self.assertEqual(loads.call_count, 1)
            old_stat = self.report_path.stat()
            report['notice'] = '虚构更新提醒'
            self.seed(report)
            os.utime(self.report_path, ns=(old_stat.st_atime_ns, old_stat.st_mtime_ns + 1_000_000))
            self.assertEqual(original(self.call().body)['notice'], '虚构更新提醒')
            self.assertEqual(loads.call_count, 2)
            # Same length, different mtime also invalidates the cache.
            old_stat = self.report_path.stat()
            report['notice'] = '虚构再次提醒'
            self.seed(report)
            os.utime(self.report_path, ns=(old_stat.st_atime_ns, old_stat.st_mtime_ns + 1_000_000))
            self.assertEqual(original(self.call().body)['notice'], '虚构再次提醒')
            self.assertEqual(loads.call_count, 3)
        self.report_path.write_text('broken', encoding='utf8')
        self.assertEqual(self.call().status_code, 503)
        self.report_path.unlink()
        self.assertEqual(self.data(), {'ready': False})
        self.seed()
        self.assertEqual(self.data()['totals']['entries'], 11)

    def test_io_and_rechecks_run_in_worker_threads(self):
        self.seed()
        main_thread, workers, events = threading.get_ident(), [], []
        original_read, original_contacts = api._ReportCache.read, api._read_contacts
        original_write = api._record_contact
        def report_read(cache):
            workers.append(threading.get_ident())
            events.append('report')
            return original_read(cache)
        def state_read(path):
            workers.append(threading.get_ident())
            return original_contacts(path)
        def state_write(path, supplier, note):
            workers.append(threading.get_ident())
            events.append('write')
            return original_write(path, supplier, note)
        def recheck():
            workers.append(threading.get_ident())
            events.append('recheck')
            return True
        with patch.object(api._ReportCache, 'read', report_read), patch.object(api, '_read_contacts', state_read), \
                patch.object(api, '_record_contact', state_write):
            req = request()
            req.state.password_recheck = recheck
            asyncio.run(self.handlers['summary'](req))
            self.assertEqual(events, ['report', 'recheck'])
            events.clear()
            req = request(form={'supplier': ['虚构甲']})
            req.state.password_recheck = recheck
            asyncio.run(self.handlers['contacted'](req))
            self.assertEqual(events, ['recheck', 'report', 'write'])
        self.assertGreaterEqual(len(workers), 5)
        self.assertTrue(all(worker != main_thread for worker in workers))


if __name__ == '__main__':
    unittest.main()
