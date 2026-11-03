import hashlib
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from console.inspiration.store import InspirationStore
from console.inspiration.sync import SourceSchemaError, WATERMARK_KEY, sync_from_state
from inspiration_fixtures import create_source, payload


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.source = Path(self.temp.name) / 'source.sqlite3'
        self.store = InspirationStore(Path(self.temp.name) / 'store.sqlite3')

    def test_incremental_sync_deduplicates_forwards_and_ignores_unrelated_messages(self):
        create_source(self.source, [('fake-app', 'm1', 10, payload(xml=True)),
                                    ('fake-app', 'm2', 20, payload()),
                                    ('fake-app', 'm3', 30, '{"text":"虚构普通消息"}')])
        initial_digest = hashlib.sha256(self.source.read_bytes()).digest()
        result = sync_from_state(self.source, self.store)
        self.assertEqual(result, dict(scanned=2, cards_found=2, new_cards=1, updated=1))
        self.assertEqual(sync_from_state(self.source, self.store), dict(scanned=0, cards_found=0, new_cards=0, updated=0))
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).digest(), initial_digest)
        with sqlite3.connect(self.source) as connection:
            connection.execute('INSERT INTO messages(app_id,message_id,created_at,payload) VALUES (?,?,?,?)',
                               ('fake-app', 'm4', 40, payload('虚构业务运营 #外贸')))
        self.assertEqual(sync_from_state(self.source, self.store)['new_cards'], 1)
        self.assertEqual(self.store.counts()['total'], 2)

    def test_limit_watermark_ties_and_multiple_apps(self):
        create_source(self.source, [('fake-app-a', 'm1', 10.5, payload()),
                                    ('fake-app-b', 'm1', 10.5, payload('虚构另一张卡')),
                                    ('fake-app-a', 'm2', 10.5, payload('虚构第三张卡'))])
        for expected in (1, 2, 3):
            self.assertEqual(sync_from_state(self.source, self.store, limit=1)['scanned'], 1)
            self.assertEqual(self.store.counts()['total'], expected)
        self.assertEqual(sync_from_state(self.source, self.store, limit=1)['scanned'], 0)
        self.assertEqual(self.store.get_meta(WATERMARK_KEY), [10.5, 'm2', 'fake-app-a'])

    def test_source_without_optional_scope_table_and_empty_descriptions(self):
        create_source(self.source, [('fake-app', 'm1', 1, payload('', '示例号甲', xml=True))])
        self.assertEqual(sync_from_state(self.source, self.store)['new_cards'], 1)
        row = self.store.list_cards()[0][0]
        self.assertEqual(row['source_message_ids'], ['m1'])
        self.assertNotIn('group_id', row)

    def test_candidates_that_parse_to_nothing_still_advance_watermark(self):
        create_source(self.source, [('fake-app', 'm1', 1, '{"text":"虚构 finderFeed 普通文字"}')])
        self.assertEqual(sync_from_state(self.source, self.store)['scanned'], 1)
        self.assertEqual(sync_from_state(self.source, self.store)['scanned'], 0)

    def test_missing_table_and_columns_have_clear_chinese_errors(self):
        with sqlite3.connect(self.source):
            pass
        with self.assertRaisesRegex(SourceSchemaError, '缺少 messages 表'):
            sync_from_state(self.source, self.store)
        with sqlite3.connect(self.source) as connection:
            connection.execute('CREATE TABLE messages(payload TEXT)')
        with self.assertRaisesRegex(SourceSchemaError, '缺少字段：.*created_at.*message_id'):
            sync_from_state(self.source, self.store)

    def test_missing_source_never_creates_a_file(self):
        with self.assertRaisesRegex(SourceSchemaError, '无法只读打开'):
            sync_from_state(self.source, self.store)
        self.assertFalse(self.source.exists())

    def test_source_connection_is_uri_read_only_query_only_and_prefiltered(self):
        create_source(self.source)
        connect = sqlite3.connect
        traces, source_calls = [], []
        def tracking(database, *args, **kw):
            connection = connect(database, *args, **kw)
            if isinstance(database, str) and database.startswith('file:') and 'source.sqlite3' in database:
                source_calls.append((database, kw))
                connection.set_trace_callback(traces.append)
            return connection
        with patch('console.inspiration.sync.sqlite3.connect', side_effect=tracking):
            sync_from_state(self.source, self.store)
        self.assertEqual(len(source_calls), 1)
        self.assertIn('?mode=ro', source_calls[0][0])
        self.assertTrue(source_calls[0][1]['uri'])
        self.assertIn('PRAGMA query_only=ON', traces)
        query = next(sql for sql in traces if sql.startswith('SELECT created_at'))
        self.assertIn("payload LIKE '%finderFeed%' OR payload LIKE '%finder_description%'", query)

    def test_invalid_limit_and_same_file_are_rejected(self):
        create_source(self.source)
        for limit in (0, -1, True):
            with self.assertRaises(ValueError):
                sync_from_state(self.source, self.store, limit)
        with self.assertRaisesRegex(ValueError, '不能使用同一文件'):
            sync_from_state(self.store.path, self.store)


if __name__ == '__main__':
    unittest.main()
