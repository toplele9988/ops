"""Directory coverage uses synthetic sources only; all transport is disabled."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from console.channels.wechat import WechatAdapter
from console.directory import WechatDirectory
from console.directory.sources import read_config, read_discovery, read_state
from console.inbox.model import decode_key, matches, parameters

NOW = 2_000_000_000.0
WEEK = 7 * 86400
SECRET = 'synthetic-sensitive-value-must-not-leak'


def group(number):
    return f'fiction-group-{number}@chatroom'


def fingerprint(path):
    info = path.stat()
    return hashlib.sha256(path.read_bytes()).hexdigest(), info.st_mtime_ns


class DirectoryServiceTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.discovery = self.root / 'discovery.sqlite3'
        self.state = self.root / 'state.sqlite3'
        self.config = self.root / 'config.json'
        self.cache = self.root / 'directory-cache.sqlite3'
        self.now = NOW
        self.groups = {}
        self.serial = 0
        for target in ('socket.socket.connect', 'socket.create_connection'):
            guard = patch(target, side_effect=AssertionError('directory tests must not use network'))
            guard.start()
            self.addCleanup(guard.stop)
        with closing(sqlite3.connect(self.discovery)) as db, db:
            db.execute('CREATE TABLE identities(identity TEXT PRIMARY KEY,kind TEXT,first_seen REAL,last_seen REAL,raw_data TEXT)')
        with closing(sqlite3.connect(self.state)) as db, db:
            db.execute('CREATE TABLE messages(app_id TEXT,message_id TEXT,group_id TEXT,created_at REAL,payload TEXT)')
            db.execute('CREATE TABLE message_scopes(app_id TEXT,message_id TEXT,scope_id TEXT,business_role TEXT)')
            db.execute('CREATE TABLE onboarding_candidates(group_id TEXT PRIMARY KEY,group_name TEXT)')
        self.write_config()

    def write_config(self, extra=None):
        self.config.write_text(json.dumps(dict(groups=self.groups, **(extra or {})), ensure_ascii=False), encoding='utf8')

    def discovered(self, identity, kind='group', first=None, last=None):
        with closing(sqlite3.connect(self.discovery)) as db, db:
            db.execute('INSERT INTO identities VALUES(?,?,?,?,?)',
                       (identity, kind, self.now - 1000 if first is None else first,
                        self.now - 100 if last is None else last, SECRET))

    def configured(self, identity, role='supplier', name=None, enabled=True, **extra):
        self.groups[identity] = dict(enabled=enabled, business_role=role,
                                     auto_collection_source='fictional-test-enrollment', **extra)
        if name is not None:
            self.groups[identity]['group_name'] = name
        self.write_config()

    def named(self, identity, name):
        with closing(sqlite3.connect(self.state)) as db, db:
            db.execute('INSERT OR REPLACE INTO onboarding_candidates VALUES(?,?)', (identity, name))

    def message(self, identity, *, at=None, role='customer', app='fictional-app', mid=None,
                body='虚构商品询价', attachments=None, malformed=False):
        self.serial += 1
        mid = mid or f'fictional-message-{self.serial}'
        at = self.now - 10 if at is None else at
        payload = dict(group_id=identity, message_id=mid, sender_id='fictional-other', own_wxid='fictional-self',
                       text=body, created_at=at, attachments=attachments or [])
        with closing(sqlite3.connect(self.state)) as db, db:
            db.execute('INSERT INTO messages VALUES(?,?,?,?,?)',
                       (app, mid, identity, at, '{invalid synthetic payload' if malformed else json.dumps(payload)))
            if role is not None:
                db.execute('INSERT INTO message_scopes VALUES(?,?,?,?)', (app, mid, identity, role))
        return mid

    def service(self, **kwargs):
        return WechatDirectory(discovery_path=self.discovery, config_path=self.config, state_path=self.state,
                               cache_path=self.cache, clock=lambda: self.now, **kwargs)

    def rows(self, service=None, **values):
        result = (service or self.service()).list(dict(kind='all', page_size=100, **values))
        return {row['id']: row for row in result['items']}

    def test_four_states_include_exact_seven_day_boundary(self):
        for number in range(4):
            self.discovered(group(number))
        for number in range(3):
            self.configured(group(number), role='customer')
        self.message(group(0), at=self.now - WEEK)
        self.message(group(1), at=self.now - WEEK - 1)
        rows = self.rows()
        self.assertEqual([rows[group(n)]['collect_state'] for n in range(4)],
                         ['collecting', 'enrolled_silent', 'enrolled_no_messages', 'not_enrolled'])
        self.assertEqual([rows[group(n)]['has_messages'] for n in range(4)], [True, True, False, False])
        self.assertEqual(rows[group(0)]['last_message_at'], self.now - WEEK)
        self.now += 1
        self.assertEqual(self.rows()[group(0)]['collect_state'], 'enrolled_silent')

    def test_disabled_group_is_registered_with_explicit_reason(self):
        self.discovered(group(1))
        self.configured(group(1), enabled=False)
        row = self.rows()[group(1)]
        self.assertTrue(row['registered'])
        self.assertFalse(row['enabled'])
        self.assertIsNone(row['collect_state'])
        self.assertIn('关闭', row['collect_state_reason'])
        self.assertEqual(self.service().summary()['groups']['disabled_registered'], 1)

    def test_missing_enabled_marker_keeps_registration_but_cannot_confirm_collection(self):
        self.discovered(group(1))
        self.configured(group(1))
        del self.groups[group(1)]['enabled']
        self.write_config()
        self.message(group(1))
        row = self.rows()[group(1)]
        self.assertTrue(row['registered'])
        self.assertIsNone(row['enabled'])
        self.assertIsNone(row['collect_state'])
        self.assertIn('启用标记', row['collect_state_reason'])

    def test_future_message_does_not_hide_an_actual_message_within_seven_days(self):
        self.discovered(group(1))
        self.configured(group(1))
        self.message(group(1), at=self.now - WEEK)
        self.message(group(1), at=self.now + 100)
        row = self.rows()[group(1)]
        self.assertEqual(row['n_messages'], 2)
        self.assertEqual(row['collect_state'], 'collecting')
        self.assertEqual(row['last_message_at'], self.now + 100)

    def test_only_future_or_invalid_message_time_does_not_claim_recent_activity(self):
        for number in range(2):
            self.discovered(group(number))
            self.configured(group(number))
        self.message(group(0), at=self.now + 1)
        self.message(group(1), at='fictional-invalid-timestamp')
        rows = self.rows()
        for number in range(2):
            self.assertEqual(rows[group(number)]['n_messages'], 1)
            self.assertIsNone(rows[group(number)]['collect_state'])

    def test_config_name_precedes_conversation_and_unknown_name_is_plain(self):
        for number in range(4):
            self.discovered(group(number))
        self.configured(group(0), name='SUP001 虚构配置名称')
        self.named(group(0), 'CUS002 虚构旧会话名')
        self.named(group(1), 'CUS003 虚构会话名称')
        self.message(group(0))
        self.message(group(1))
        self.message(group(2))
        self.named(group(3), '微信群 ·room')
        rows = self.rows()
        self.assertEqual((rows[group(0)]['name'], rows[group(0)]['name_source']), ('SUP001 虚构配置名称', 'config'))
        self.assertEqual((rows[group(1)]['name'], rows[group(1)]['name_source']), ('CUS003 虚构会话名称', 'conversation'))
        for number in (2, 3):
            self.assertEqual((rows[group(number)]['name'], rows[group(number)]['name_source']), ('未命名群', 'unknown'))

    def test_sup_cus_forwarder_vendor_naming_and_configuration_conflicts(self):
        fixtures = [
            ('SUP001 虚构供应商', 'customer', 'supplier', True),
            ('CUS002 虚构买方', 'supplier', 'customer', True),
            ('SUP003 虚构快递', 'supplier', 'forwarder', False),
            ('SUP004 虚构包装', 'supplier', 'vendor', False),
            ('虚构快递服务', 'customer', 'forwarder', True),
            ('SUP005 CUS006 虚构双编号', 'supplier', 'supplier', True),
            ('虚构日常联系', 'learning', None, False),
        ]
        for number, (name, role, _, _) in enumerate(fixtures):
            self.discovered(group(number))
            self.configured(group(number), role=role, name=name)
        rows = self.rows()
        for number, (_, role, named, conflict) in enumerate(fixtures):
            with self.subTest(number=number):
                row = rows[group(number)]
                self.assertEqual((row['config_role'], row['named_role'], row['role_conflict']), (role, named, conflict))
                self.assertEqual(bool(row['role_conflict_reason']), conflict)
        self.assertEqual(rows[group(5)]['name_codes'], ['CUS', 'SUP'])
        self.assertIn('快递', rows[group(2)]['named_role_label'])
        self.assertEqual(self.service().summary()['conflicts'], 4)

    def test_configuration_role_does_not_replace_actual_message_role_or_visibility(self):
        self.discovered(group(0))
        self.configured(group(0), role='customer', name='CUS001 虚构客户名')
        self.message(group(0), role='supplier')
        row = self.rows()[group(0)]
        self.assertEqual((row['config_role'], row['role'], row['counterpart_role']), ('customer', 'customer', 'supplier'))
        self.assertFalse(row['inbox_visible'])
        self.assertTrue(row['inbox_visible_with_suppliers'])
        self.assertIn('同时显示供应商群', row['inbox_visibility_reason'])

    def test_visibility_agrees_with_actual_adapter_and_existing_inbox_filters(self):
        for number, role in enumerate(('customer', 'supplier', 'learning', 'internal', 'unclassified', 'unknown')):
            self.discovered(group(number))
            self.message(group(number), role=role)
        self.discovered(group(99))
        rows = self.rows()
        convs = WechatAdapter(self.state).list_conversations(self.now)
        for option, field in (('customer', 'inbox_visible'), ('all', 'inbox_visible_with_suppliers')):
            expected = {decode_key(conv['key'], 'conversation')[2] for conv in convs.values()
                        if matches(conv, parameters({'role': option}))}
            actual = {identity for identity, row in rows.items() if row[field]}
            self.assertEqual(actual, expected)
        self.assertIn('尚无收到的消息', rows[group(99)]['inbox_visibility_reason'])
        self.assertIn('学习', rows[group(2)]['inbox_visibility_reason'])
        self.assertIn('开关也不会显示', rows[group(3)]['inbox_visibility_reason'])

    def test_custom_included_roles_still_obey_default_inbox_customer_filter(self):
        self.discovered(group(1))
        self.message(group(1), role='learning')
        row = self.rows(self.service(include_roles=('learning',)))[group(1)]
        conv = next(iter(WechatAdapter(self.state, include_roles=('learning',)).list_conversations(self.now).values()))
        self.assertEqual(row['inbox_visible'], matches(conv, parameters({'role': 'customer'})))
        self.assertEqual(row['inbox_visible_with_suppliers'], matches(conv, parameters({'role': 'all'})))

    def test_invalid_payload_can_have_archived_records_but_no_inbox_conversation(self):
        self.discovered(group(1))
        self.configured(group(1))
        self.message(group(1), malformed=True)
        row = self.rows()[group(1)]
        self.assertEqual((row['n_messages'], row['collect_state']), (1, 'collecting'))
        self.assertFalse(row['inbox_visible'])
        self.assertFalse(row['inbox_visible_with_suppliers'])
        self.assertIn('尚无符合收件箱规则', row['inbox_visibility_reason'])

    def test_private_and_official_are_listed_without_guessing_collection_authorization(self):
        for identity, kind in (('fiction-person', 'private'), ('gh_fiction-account', 'official')):
            self.discovered(identity, kind)
            self.message(identity)
        rows = self.rows()
        self.assertEqual(set(rows), {'fiction-person', 'gh_fiction-account'})
        for row in rows.values():
            self.assertIsNone(row['registered'])
            self.assertIsNone(row['collect_state'])
            self.assertIsNone(row['n_messages'])
            self.assertFalse(row['inbox_visible'])
            self.assertIn('授权未核验', row['collect_state_reason'])
        self.assertEqual(self.service().list({'kind': 'private'})['total'], 1)
        self.assertEqual(self.service().list({'kind': 'official'})['total'], 1)
        summary = self.service().summary()
        self.assertEqual(summary['by_kind'], dict(group=0, private=1, official=1))
        self.assertEqual(summary['by_state']['unknown'], 2)

    def test_casefold_search_state_kind_filters_and_stable_pagination(self):
        for number in range(6):
            self.discovered(group(number), last=NOW)
            self.named(group(number), f'SUP{number:03d} Fictional Alpha' if number % 2 == 0 else f'CUS{number:03d} 虚构乙方')
            if number < 3:
                self.configured(group(number))
        self.discovered('fiction-person', 'private')
        service = self.service()
        first = service.list({'page': 1, 'page_size': 2})
        second = service.list({'page': 2, 'page_size': 2})
        self.assertEqual((first['total'], first['page'], first['page_size']), (6, 1, 2))
        self.assertEqual([r['id'] for r in first['items'] + second['items']], [group(n) for n in range(4)])
        self.assertEqual(service.list({'page': 99})['items'], [])
        self.assertEqual(service.list({'q': 'fIcTiOnAl aLpHa'})['total'], 3)
        self.assertEqual(service.list({'q': group(5)})['total'], 1)
        result = service.list({'state': 'not_enrolled', 'q': 'ALPHA'})
        self.assertEqual([r['id'] for r in result['items']], [group(4)])
        self.assertEqual(service.list({'kind': 'all'})['total'], 7)

    def test_summary_keeps_discovery_configuration_intersection_and_union_separate(self):
        # Forty-nine synthetic discovered groups, twenty-nine registered groups,
        # and exactly twenty-eight groups in both sets must not become 49=29+21.
        for number in range(49):
            self.discovered(group(number))
        for number in (*range(28), 99):
            self.configured(group(number), role='supplier', name=f'SUP{number:03d} 虚构企业')
        for number in range(26):
            self.message(group(number), role='supplier')
        self.named(group(28), 'SUP028 虚构未登记供应商')
        self.named(group(29), 'CUS029 虚构未登记客户')
        summary = self.service().summary()
        groups = summary['groups']
        self.assertEqual((groups['total'], groups['directory_total'], groups['registered']), (50, 49, 29))
        self.assertEqual((groups['directory_registered'], groups['directory_not_enrolled'], groups['outside_directory']), (28, 21, 1))
        self.assertEqual(groups['not_enrolled_no_messages'], 21)
        self.assertEqual(groups['by_state'], dict(collecting=26, enrolled_silent=0, enrolled_no_messages=3, not_enrolled=21, unknown=0))
        self.assertEqual(summary['not_enrolled_suspected'], dict(supplier=1, customer=1))
        self.assertEqual(summary['by_role']['supplier'], 30)
        self.assertEqual(summary['by_role']['customer'], 1)
        for phrase in ('49 个群', '28 个已登记', '21 个尚未登记', '21 个从未收到消息', '共 50 个群', '共 29 个'):
            self.assertIn(phrase, summary['conclusion'])

    def test_unregistered_historical_message_is_counted_without_claiming_never_received(self):
        self.discovered(group(1))
        self.message(group(1), at=self.now - 30 * 86400)
        row = self.rows()[group(1)]
        self.assertEqual(row['collect_state'], 'not_enrolled')
        self.assertEqual(row['n_messages'], 1)
        self.assertIn('历史消息仍可能保留', row['collect_state_reason'])
        self.assertEqual(self.service().summary()['groups']['not_enrolled_no_messages'], 0)

    def test_message_counts_deduplicate_by_app_group_message(self):
        self.discovered(group(1))
        self.discovered(group(2))
        self.message(group(1), mid='fiction-1', app='fiction-a', at=self.now - 100)
        self.message(group(1), mid='fiction-1', app='fiction-a', at=self.now - 100)
        self.message(group(1), mid='fiction-1', app='fiction-b', at=self.now - 50)
        self.message(group(1), mid='fiction-2', app='fiction-a', at=self.now - 10)
        self.message(group(2), mid='fiction-1', app='fiction-a', at=self.now - 20)
        rows = self.rows()
        self.assertEqual((rows[group(1)]['n_messages'], rows[group(2)]['n_messages']), (3, 1))
        self.assertEqual(rows[group(1)]['last_message_at'], self.now - 10)

    def test_source_projection_never_returns_sensitive_configuration_or_message_fields(self):
        sensitive = {key: SECRET for key in ('api_key', 'authorization', 'callback_secret', 'own_wxid', 'knowledge_files', 'password', 'raw_payload')}
        self.discovered(group(1))
        self.configured(group(1), name='SUP001 虚构单位', **sensitive)
        self.write_config(sensitive)
        self.message(group(1), body=SECRET)
        config = read_config(self.config)
        self.assertEqual(set(config[group(1)]), {'enabled', 'business_role', 'auto_collection_source', 'group_name'})
        outputs = [config, read_discovery(self.discovery), read_state(self.state), self.service().list(), self.service().summary()]
        for output in outputs:
            encoded = json.dumps(output, ensure_ascii=False)
            self.assertNotIn(SECRET, encoded)
            for key in sensitive:
                self.assertNotIn('"' + key + '"', encoded)

    def test_reads_and_refresh_leave_all_sources_hash_and_mtime_unchanged(self):
        self.discovered(group(1))
        self.configured(group(1))
        self.message(group(1), body='', attachments=[{'kind': 'image', 'path': '/fictional/private/image.png'}])
        before = {path: fingerprint(path) for path in (self.discovery, self.state, self.config)}
        existing = set(self.root.iterdir())
        with (patch('console.media.tokens.make', side_effect=AssertionError('directory must not mint media tokens')),
              patch('console.media.tokens._key', side_effect=AssertionError('directory must not create a media key'))):
            service = self.service(contact_fetcher=lambda: dict(ret=200, data=dict(friends=[], chatrooms=[group(2)], ghs=[])))
            self.assertEqual(service.list()['total'], 1)
            service.summary()
            self.assertEqual(set(self.root.iterdir()), existing)
            service.refresh()
            self.assertEqual(service.list()['total'], 2)
        self.assertEqual({path: fingerprint(path) for path in before}, before)
        self.assertEqual(set(self.root.iterdir()) - existing, {self.cache})

    def test_refresh_cache_merges_without_duplicates_and_retains_observation_times(self):
        self.discovered(group(1), first=self.now - 1000, last=self.now - 500)
        self.configured(group(1), name='SUP001 虚构原有群')
        service = self.service(contact_fetcher=lambda: dict(ret=200, data=dict(friends=['fiction-person'], chatrooms=[group(1), group(2)], ghs=['gh_fiction'])))
        service.refresh()
        rows = self.rows(service)
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[group(1)]['sources'], ['config', 'discovery', 'refresh_cache'])
        self.assertEqual((rows[group(1)]['first_seen'], rows[group(1)]['last_seen']), (self.now - 1000, self.now))
        self.assertEqual(rows[group(2)]['sources'], ['refresh_cache'])
        self.assertEqual(rows[group(2)]['collect_state'], 'not_enrolled')
        self.assertTrue(rows[group(2)]['in_directory'])

    def test_missing_config_does_not_mislabel_every_group_as_not_enrolled(self):
        self.discovered(group(1))
        self.config.unlink()
        result = self.service().list()
        self.assertFalse(result['ready'])
        self.assertFalse(result['sources']['config'])
        self.assertIsNone(result['items'][0]['collect_state'])
        self.assertIsNone(result['items'][0]['registered'])
        summary = self.service().summary()
        self.assertEqual(summary['by_state']['not_enrolled'], 0)
        self.assertIn('覆盖数量无法完整确认', summary['conclusion'])
        self.assertFalse(self.config.exists())

    def test_corrupt_messages_do_not_claim_enrolled_group_never_received_anything(self):
        self.discovered(group(1))
        self.configured(group(1))
        self.state.write_bytes(b'fictional corrupt sqlite source')
        row = self.rows()[group(1)]
        self.assertTrue(row['registered'])
        self.assertIsNone(row['collect_state'])
        self.assertIsNone(row['n_messages'])
        self.assertIsNone(row['inbox_visible'])
        self.assertIn('无法确认', row['inbox_visibility_reason'])

    def test_missing_discovery_shows_available_configuration_without_claiming_full_directory(self):
        self.configured(group(1))
        self.discovery.unlink()
        result = self.service().list()
        self.assertEqual(result['total'], 1)
        self.assertFalse(result['ready'])
        self.assertFalse(result['items'][0]['in_directory'])
        summary = self.service().summary()
        self.assertEqual(summary['groups']['directory_total'], 0)
        self.assertIn('本地发现库暂不可读', summary['conclusion'])
        self.assertFalse(self.discovery.exists())

    def test_invalid_configuration_schema_is_unknown_and_never_leaks_failure_details(self):
        self.discovered(group(1))
        self.config.write_text(json.dumps({'groups': [SECRET], 'credential': SECRET}))
        result = self.service().list()
        self.assertFalse(result['sources']['config'])
        self.assertIsNone(result['items'][0]['collect_state'])
        self.assertNotIn(SECRET, json.dumps(result))

    def test_reader_injection_receives_paths_and_does_not_read_fallback_sources(self):
        calls = []
        def discovery(path):
            calls.append(('discovery', path))
            return [dict(id=group(1), kind='group', first_seen=10, last_seen=20)]
        def config(path):
            calls.append(('config', path))
            return {group(1): dict(enabled=True, business_role='customer', group_name='CUS001 虚构注入群', auto_collection_source=None)}
        def state(path, now):
            calls.append(('state', path, now))
            return dict(messages={}, names={})
        def convs(path, now):
            calls.append(('conversations', path, now))
            return {}
        service = self.service(discovery_reader=discovery, config_reader=config, state_reader=state, conversation_reader=convs)
        self.discovery.unlink()
        self.state.unlink()
        self.config.unlink()
        row = self.rows(service)[group(1)]
        self.assertEqual(row['name'], 'CUS001 虚构注入群')
        self.assertEqual(row['collect_state'], 'enrolled_no_messages')
        self.assertEqual(calls, [('discovery', self.discovery), ('config', self.config),
                                 ('state', self.state, self.now), ('conversations', self.state, self.now)])
        self.assertEqual(list(self.root.iterdir()), [])

    def test_explicit_test_source_isolates_unspecified_paths(self):
        service = WechatDirectory(discovery_path=self.discovery)
        self.assertEqual(service.config_path.parent, self.root)
        self.assertEqual(service.state_path.parent, self.root)
        self.assertEqual(service.cache_path.parent, self.root)
        self.assertTrue(service.config_path.name.startswith('disabled-'))
        self.assertTrue(service.state_path.name.startswith('disabled-'))

    def test_cache_must_not_alias_any_source_by_path_symlink_or_hardlink(self):
        for path in (self.discovery, self.state, self.config):
            with self.subTest(path=path.name):
                with self.assertRaisesRegex(ValueError, '独立'):
                    WechatDirectory(discovery_path=self.discovery, config_path=self.config,
                                    state_path=self.state, cache_path=path)
        alias = self.root / 'cache-alias.sqlite3'
        alias.symlink_to(self.state)
        with self.assertRaisesRegex(ValueError, '独立'):
            WechatDirectory(state_path=self.state, cache_path=alias)
        alias.unlink()
        alias.hardlink_to(self.state)
        with self.assertRaisesRegex(ValueError, '独立'):
            WechatDirectory(state_path=self.state, cache_path=alias)


if __name__ == '__main__':
    unittest.main()
