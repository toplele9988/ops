import os
from pathlib import Path
import sqlite3
import stat
import tempfile
import unittest

from console.inspiration.store import InspirationStore
from inspiration_fixtures import card, enrichment


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'cards.sqlite3'
        self.store = InspirationStore(self.path)
        self.card = card()
        self.store.upsert_card(self.card, 'fake-message-1', 10)

    def test_file_permissions_wal_and_umask_restoration(self):
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        with sqlite3.connect(self.path) as connection:
            self.assertEqual(connection.execute('PRAGMA journal_mode').fetchone()[0], 'wal')
        previous = os.umask(0o022)
        try:
            another = Path(self.temp.name) / 'another.sqlite3'
            InspirationStore(another)
            self.assertEqual(os.umask(0o022), 0o022)
            self.assertEqual(stat.S_IMODE(another.stat().st_mode), 0o600)
        finally:
            os.umask(previous)

    def test_defaults_and_upsert_preserve_all_ai_and_human_fields(self):
        before = self.store.get_card(self.card['card_id'])
        self.assertEqual((before['relevance'], before['status'], before['times_seen']), ('unknown', 'new', 1))
        self.assertFalse(before['enriched'])
        self.store.save_enrichment(self.card['card_id'], enrichment(self.card), 'fictional-model')
        self.store.record_enrich_error(self.card['card_id'])
        self.store.set_status(self.card['card_id'], 'trying', '虚构试用备注')
        before = self.store.get_card(self.card['card_id'])
        altered = dict(self.card, text='不应覆盖的虚构文本', author='示例号乙')
        self.assertFalse(self.store.upsert_card(altered, 'fake-message-2', 20))
        after = self.store.get_card(self.card['card_id'])
        for key in before:
            if key not in ('last_seen', 'times_seen', 'source_message_ids', 'updated_at'):
                self.assertEqual(after[key], before[key], key)
        self.assertEqual(after['last_seen'], 20)
        self.assertEqual(after['times_seen'], 2)
        self.assertEqual(after['source_message_ids'], ['fake-message-1', 'fake-message-2'])
        self.store.upsert_card(self.card, 'fake-message-2', 5)
        self.assertEqual(self.store.get_card(self.card['card_id'])['last_seen'], 20)

    def test_message_ids_are_unique_and_bounded_to_twenty(self):
        for i in range(30):
            self.store.upsert_card(self.card, f'fake-{i}', i + 20)
        self.store.upsert_card(self.card, 'fake-29', 60)
        row = self.store.get_card(self.card['card_id'])
        self.assertEqual(row['source_message_ids'], [f'fake-{i}' for i in range(10, 30)])
        self.assertEqual(row['times_seen'], 32)

    def test_status_enum_note_length_and_missing_card(self):
        for invalid in ('invalid', '', None):
            with self.assertRaises(ValueError):
                self.store.set_status(self.card['card_id'], invalid)
        with self.assertRaises(ValueError):
            self.store.set_status(self.card['card_id'], 'new', '字' * 501)
        self.assertTrue(self.store.set_status(self.card['card_id'], 'adopted', '字' * 500))
        self.assertFalse(self.store.set_status('missing', 'ignored'))

    def test_search_author_body_tags_summary_and_item_names(self):
        self.store.save_enrichment(self.card['card_id'], enrichment(self.card, summary='虚构摘要索引'), 'demo')
        for q in ('示例号甲', 'skills 目录', '开源', '摘要索引', 'DemoKit'):
            self.assertEqual(self.store.list_cards(q=q)[1], 1, q)
        # A name can be in the source tags, then remain searchable in items.
        tagged = card('虚构工具介绍 #TagProject')
        self.store.upsert_card(tagged, 'fake-tagged', 11)
        self.store.save_enrichment(tagged['card_id'], enrichment(tagged, items=[
            {'name': 'TagProject', 'kind': 'project', 'url': None}]), 'demo')
        with sqlite3.connect(self.path) as connection:
            connection.execute("UPDATE cards SET text='',tags_json='[]' WHERE card_id=?", (tagged['card_id'],))
        self.assertEqual(self.store.list_cards(q='TagProject')[1], 1)

    def test_like_percent_underscore_backslash_and_sql_injection_are_literal(self):
        special = card(r'虚构 50% foo_bar C:\demo')
        self.store.upsert_card(special, 'fake-special', 30)
        for query in ('%', '_', '\\'):
            rows, total = self.store.list_cards(q=query)
            self.assertEqual(total, 1)
            self.assertEqual(rows[0]['card_id'], special['card_id'])
        self.assertEqual(self.store.list_cards(q="%' OR 1=1 --")[1], 0)
        self.assertEqual(self.store.list_cards(q='50X')[1], 0)

    def test_pagination_filters_order_and_counts(self):
        other = card('虚构汽车内容')
        self.store.upsert_card(other, 'fake-other', 50)
        self.store.set_status(other['card_id'], 'ignored')
        self.store.save_enrichment(other['card_id'], enrichment(other, relevance='other'), 'demo')
        rows, total = self.store.list_cards(page=2, page_size=1)
        self.assertEqual(total, 2)
        self.assertEqual(rows[0]['card_id'], self.card['card_id'])
        self.assertEqual(self.store.list_cards(relevance='other', status='ignored')[1], 1)
        self.assertEqual(self.store.list_cards(relevance='ai_dev')[1], 0)
        self.assertEqual(self.store.list_cards(order='first_seen')[0][0]['card_id'], other['card_id'])
        counts = self.store.counts()
        self.assertEqual(counts['total'], 2)
        self.assertEqual(counts['relevance']['other'], 1)
        self.assertEqual(counts['status']['ignored'], 1)

    def test_invalid_queries_are_rejected(self):
        for kw in ({'page': 0}, {'page_size': 101}, {'page_size': True}, {'page': '1'},
                   {'relevance': 'bad'}, {'status': 'bad'}, {'order': 'random()'}, {'q': '字' * 501}):
            with self.subTest(kw=kw), self.assertRaises(ValueError):
                self.store.list_cards(**kw)

    def test_pending_errors_and_enrichment_preserve_human_fields(self):
        cid = self.card['card_id']
        self.store.set_status(cid, 'evaluating', '虚构备注')
        self.assertEqual(len(self.store.pending_enrichment(6)), 1)
        for _ in range(3):
            self.store.record_enrich_error(cid)
        self.assertEqual(self.store.pending_enrichment(6), [])
        self.store.save_enrichment(cid, enrichment(self.card), 'fictional-model')
        row = self.store.get_card(cid)
        self.assertEqual((row['status'], row['note']), ('evaluating', '虚构备注'))
        self.assertEqual(row['enrich_model'], 'fictional-model')
        self.assertTrue(row['enriched'])
        self.assertEqual(self.store.pending_enrichment(6), [])

    def test_read_only_store_never_creates_or_mutates_database(self):
        reader = InspirationStore(self.path, read_only=True)
        self.assertEqual(reader.list_cards()[1], 1)
        with self.assertRaises(sqlite3.OperationalError):
            reader.set_status(self.card['card_id'], 'ignored')
        missing = Path(self.temp.name) / 'missing.sqlite3'
        reader = InspirationStore(missing, read_only=True)
        with self.assertRaises(sqlite3.OperationalError):
            reader.counts()
        self.assertFalse(missing.exists())


if __name__ == '__main__':
    unittest.main()
