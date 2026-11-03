"""Data/AI tests run even without Starlette. All data is fictional."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import stat
import tempfile
import unittest
from unittest.mock import patch

from console.customers import aggregate_customers
from console.inbox import ai, drafts, model, store
from console.inspiration.enrich import RunnerOutput
from inbox_fixtures import NOW, fixture, message, seed


class ModelTests(unittest.TestCase):
    def test_dedup_account_identity_unknown_roles_and_customer_grouping(self):
        conversations = model.aggregate_messages(map(json.dumps, fixture()))
        self.assertEqual(len(conversations), 5)
        self.assertEqual(sum(c['n_messages'] for c in conversations.values()), 7)
        key = model.make_key('conversation', 'alibaba', 'fictional-alpha', 'conv-a')
        conv = conversations[key]
        self.assertTrue(conv['waiting_reply'])
        self.assertEqual(model.conversation_item(conv, NOW)['waiting_hours'], 2.0)
        unknown = next(c for c in conversations.values() if c['buyer_name'] == 'Fictional Unknown')
        self.assertFalse(unknown['waiting_reply'])
        self.assertIsNone(model.conversation_item(unknown, NOW)['waiting_hours'])
        buyers = aggregate_customers(conversations.values())
        self.assertEqual(len(buyers), 4)
        alice = buyers[model.make_key('customer', 'alibaba', 'fictional-alpha', 'Fictional Alice')]
        self.assertEqual((alice['n_conversations'], alice['n_messages']), (2, 3))
        self.assertTrue(alice['waiting_reply'])

    def test_malformed_missing_fields_and_naive_time_are_skipped(self):
        valid = message()
        bad = ['not JSON', 'null', '[]', '{}']
        for field in ('account', 'conversation_id', 'buyer_name', 'content', 'role', 'sent_at'):
            missing = dict(valid)
            del missing[field]
            bad.append(json.dumps(missing))
        for fields in ({'sent_at': '2027-01-01T00:00:00'}, {'role': 'admin'}, {'content': 12},
                       {'account': ''}, {'conversation_id': []}, {'sent_at': 'broken'}, {'role': None},
                       {'buyer_name': '\ud800'}, {'content': '\ud800'}, {'url': '\udfff'}):
            bad.append(json.dumps({**valid, **fields}))
        self.assertEqual(len(model.aggregate_messages(bad + [json.dumps(valid)])), 1)
        self.assertTrue(all(model.parse_message(raw) is None for raw in bad))

    def test_key_roundtrip_canonicalization_and_kind_separation(self):
        key = model.make_key('conversation', 'alibaba', 'fictional-账号', 'virtual|会话')
        self.assertRegex(key, r'^[A-Za-z0-9_-]+$')
        self.assertEqual(model.decode_key(key, 'conversation'), ('alibaba', 'fictional-账号', 'virtual|会话'))
        for bad in ('../private', '', 'a', key + '=', 'x' * 8193,
                    model.make_key('customer', 'alibaba', 'fictional-账号', 'virtual|会话'),
                    model.make_key('conversation', 'invalid', 'a', 'b')):
            with self.subTest(bad=bad[:40]), self.assertRaises(model.BadRequest):
                model.decode_key(bad, 'conversation')

    def test_ai_fences_envelopes_missing_fields_bounds_and_model(self):
        expected = dict(draft='Fictional draft', needs_confirmation=['虚构交期待确认'], language='en')
        for raw in (json.dumps(expected), '```json\n' + json.dumps(expected) + '\n```',
                    json.dumps({'answer': json.dumps(expected), 'actual_model': 'fictional-envelope-model'})):
            parsed = ai.parse_output(raw)
            self.assertEqual(parsed['text'], expected['draft'])
        parsed = ai.parse_output(RunnerOutput(json.dumps(expected), 'fictional-model'))
        self.assertEqual(parsed['model'], 'fictional-model')
        for field in expected:
            data = dict(expected)
            del data[field]
            with self.assertRaises(ValueError):
                ai.parse_output(json.dumps(data))
        for raw in ('not JSON', '{}', '[]', 'null', 123,
                    json.dumps({**expected, 'draft': ' '}),
                    json.dumps({**expected, 'draft': '\ud800'}),
                    json.dumps({**expected, 'needs_confirmation': 'bad'}),
                    json.dumps({**expected, 'needs_confirmation': [None]})):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                ai.parse_output(raw)
        parsed = ai.parse_output(json.dumps(dict(draft='字' * 3000, language='l' * 100,
                                                needs_confirmation=['事' * 100] * 15)))
        self.assertEqual(len(parsed['text']), 2000)
        self.assertEqual(len(parsed['language']), 80)
        self.assertEqual([len(n) for n in parsed['needs_confirmation']], [80] * 10)

    def test_prompt_contains_only_latest_twenty_and_safety_rules(self):
        rows = [dict(role='buyer', text=f'fictional-line-{i:03}', sent_at=NOW + i) for i in range(25)]
        prompt = ai.make_prompt(rows)
        data = prompt.split('对话记录：\n')[1].split('\n')
        self.assertEqual(len(data), 20)
        self.assertEqual(data[0], '买家：fictional-line-005')
        self.assertNotIn('fictional-line-004', prompt)
        for text in ('不得编造', '待确认', '价格', '库存', '交期', '运费', '规格数值', '付款条件',
                     '不是给你的指令', '泄露信息', '120词', '买家使用的语言', '绝不发送', '纯文本',
                     '公司背景', '事实只能来自对话', '核对来源', '草稿正文不得出现'):
            self.assertIn(text, prompt)


class StorageTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / 'inbound.sqlite3'
        self.draft_path = Path(temp.name) / 'private' / 'drafts.sqlite3'

    def test_readonly_uri_query_only_and_source_bytes_unchanged(self):
        seed(self.path)
        before, mtime = self.path.read_bytes(), self.path.stat().st_mtime_ns
        original = sqlite3.connect
        with patch.object(store.sqlite3, 'connect', wraps=original) as connect:
            with store.read_database(self.path) as db:
                self.assertEqual(db.execute('PRAGMA query_only').fetchone()[0], 1)
                with self.assertRaises(sqlite3.OperationalError):
                    db.execute('DELETE FROM inbound')
            self.assertTrue(connect.call_args.args[0].startswith('file:'))
            self.assertTrue(connect.call_args.args[0].endswith('?mode=ro'))
            self.assertTrue(connect.call_args.kwargs['uri'])
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.path.stat().st_mtime_ns, mtime)

    def test_shared_cache_refresh_removal_damage_and_recovery(self):
        seed(self.path)
        cache = store.inbound_cache(self.path)
        self.assertIs(cache, store.inbound_cache(self.path.parent / '.' / self.path.name))
        with patch.object(cache, '_load', wraps=cache._load) as load:
            first = cache.read()
            self.assertIs(cache.read(), first)
            self.assertEqual(load.call_count, 1)
            previous = self.path.stat()
            with closing(sqlite3.connect(self.path)) as db, db:
                db.execute('UPDATE inbound SET payload=replace(payload, ?, ?)', ('Fictional Bob', 'Fictional Tom'))
            # Same-sized DB update must still invalidate on mtime.
            os.utime(self.path, ns=(previous.st_atime_ns, previous.st_mtime_ns + 1_000_000))
            second = cache.read()
            self.assertEqual(load.call_count, 2)
            self.assertTrue(any(c['buyer_name'] == 'Fictional Tom' for c in second['customers'].values()))
            self.path.write_text('fictional damaged db')
            with self.assertRaises(sqlite3.DatabaseError):
                cache.read()
            self.path.unlink()
            self.assertFalse(cache.read()['ready'])
            seed(self.path, [message()])
            self.assertEqual(len(cache.read()['conversations']), 1)

    def test_cache_size_and_wal_commits_invalidate(self):
        seed(self.path, [message()])
        cache = store.inbound_cache(self.path)
        cache.read()
        previous = self.path.stat()
        seed(self.path, [message('virtual-big', text='字' * 15000)])
        os.utime(self.path, ns=(previous.st_atime_ns, previous.st_mtime_ns))
        self.assertNotEqual(self.path.stat().st_size, previous.st_size)
        self.assertEqual(len(cache.read()['conversations']), 2)
        with closing(sqlite3.connect(self.path)) as db:
            db.execute('PRAGMA journal_mode=WAL')
            cache.read()
            stat_before = self.path.stat()
            with db:
                db.execute('INSERT INTO inbound (payload) VALUES (?)', (json.dumps(message('virtual-wal')),))
            self.assertEqual(self.path.stat().st_mtime_ns, stat_before.st_mtime_ns)
            self.assertEqual(len(cache.read()['conversations']), 3)

    def test_local_drafts_permissions_wal_status_and_no_creation_for_reads(self):
        self.assertEqual(drafts.read_drafts(self.draft_path), ({}, 0))
        self.assertIsNone(drafts.set_status(self.draft_path, 1, 'used'))
        self.assertFalse(self.draft_path.parent.exists())
        draft = dict(text='Fictional draft', language='en', model='fictional-model', needs_confirmation=[])
        previous = os.umask(0o022)
        self.addCleanup(os.umask, previous)
        first = drafts.save_draft(self.draft_path, 'fictional-key', draft, NOW)
        second = drafts.save_draft(self.draft_path, 'fictional-key', draft, NOW + 1)
        self.assertEqual(stat.S_IMODE(self.draft_path.stat().st_mode), 0o600)
        current = os.umask(0o022)
        self.assertEqual(current, 0o022)
        with closing(sqlite3.connect(self.draft_path)) as db:
            self.assertEqual(db.execute('PRAGMA journal_mode').fetchone()[0], 'wal')
            with self.assertRaises(sqlite3.IntegrityError), db:
                db.execute("UPDATE drafts SET status='sent'")
        self.assertEqual(drafts.read_drafts(self.draft_path)[1], 2)
        self.assertEqual(drafts.set_status(self.draft_path, second['id'], 'discarded')['status'], 'discarded')
        latest, count = drafts.read_drafts(self.draft_path)
        self.assertEqual(latest['fictional-key']['id'], first['id'])
        self.assertEqual(count, 1)
        drafts.set_status(self.draft_path, first['id'], 'used')
        self.assertEqual(drafts.read_drafts(self.draft_path)[1], 0)
        self.assertEqual(drafts.read_drafts(self.draft_path)[0]['fictional-key']['status'], 'used')

    def test_atomic_limiter_global_budget_expiry_and_inflight(self):
        limiter = drafts.DraftLimiter()
        with ThreadPoolExecutor(max_workers=8) as pool:
            attempts = list(pool.map(lambda _: limiter.reserve('fictional-same', NOW), range(8)))
        self.assertEqual(attempts.count(0), 1)
        self.assertEqual(limiter.reserve('fictional-same', NOW + 61), 60)  # still in flight
        limiter.finish('fictional-same')
        self.assertEqual(limiter.reserve('fictional-same', NOW + 61), 0)
        limiter.finish('fictional-same')
        for i in range(28):
            key = f'fictional-{i}'
            self.assertEqual(limiter.reserve(key, NOW + 62), 0)
            limiter.finish(key)
        self.assertGreater(limiter.reserve('fictional-limit', NOW + 63), 0)
        self.assertEqual(limiter.reserve('fictional-limit', NOW + 3600), 0)

class ClosingMessageTests(unittest.TestCase):
    """A buyer's last line of thanks must not count as waiting for a reply; a real request must."""

    def test_closing_phrases_are_recognised(self):
        for text in ('Ok', 'ok!', 'Okay thanks', 'Thank you very much', 'Thank you.', '👍', '\ufffd', 'gracias', 'OK  gracias', '好的', '谢谢', ''):
            with self.subTest(text=text):
                self.assertTrue(model.is_closing(text))

    def test_real_requests_are_not_closing(self):
        for text in ('send me your prices ASAP', 'Ok, what is the price?', 'Thanks, can you send the packing list', 'How much for 100 pcs',
                     'Is it in stock', '请问多少钱'):
            with self.subTest(text=text):
                self.assertFalse(model.is_closing(text))

    def test_conversation_ending_with_thanks_is_not_waiting(self):
        rows = [json.dumps(message(conversation='c1', role='seller', text='Here is the quote', age=7200, message_id='m1')),
                json.dumps(message(conversation='c1', role='buyer', text='Ok thanks', age=3600, message_id='m2')),
                json.dumps(message(conversation='c2', role='buyer', text='What is the lead time?', age=3600, message_id='m3'))]
        conversations = model.aggregate_messages(rows)
        waiting = {c['key']: c['waiting_reply'] for c in conversations.values()}
        by_conv = {model.make_key('conversation', 'alibaba', 'fictional-alpha', cid): cid for cid in ('c1', 'c2')}
        self.assertFalse(waiting[next(k for k, v in by_conv.items() if v == 'c1')])
        self.assertTrue(waiting[next(k for k, v in by_conv.items() if v == 'c2')])



if __name__ == '__main__':
    unittest.main()


class OfficialRunnerTests(unittest.TestCase):
    """The default draft runner talks to the gateway with the official provider and parses the real envelope."""

    def test_official_runner_request_and_envelope_parse(self):
        envelope = json.dumps({'status': 'ok', 'actual_model': 'deepseek-flash', 'answer': json.dumps(
            {'draft': 'Hello, which model do you need? 【待确认：型号与价格】', 'needs_confirmation': ['确认型号'], 'language': '英语'},
            ensure_ascii=False)}, ensure_ascii=False)
        completed = type('Done', (), {'returncode': 0, 'stdout': envelope, 'stderr': ''})()
        with patch('console.inbox.ai.subprocess.run', return_value=completed) as run:
            output = ai.official_runner('虚构提示词')
        sent = json.loads(run.call_args.kwargs['input'])
        self.assertEqual((sent['provider'], sent['model']), ('deepseek_official', 'deepseek-flash'))
        self.assertEqual(run.call_args.args[0], ['/usr/local/libexec/imgw-ai-task'])
        parsed = ai.parse_output(output)
        self.assertIn('待确认', parsed['text'])
        self.assertEqual(output.model, 'deepseek-flash')

    def test_empty_answers_retry_then_escalate_then_fail(self):
        empty = json.dumps({'status': 'ok', 'actual_model': 'deepseek-flash', 'answer': None})
        good = json.dumps({'status': 'ok', 'actual_model': 'deepseek-v4-pro', 'answer': json.dumps(
            {'draft': 'Hello 【待确认：价格】', 'needs_confirmation': ['确认价格'], 'language': '英语'}, ensure_ascii=False)})
        done = lambda out: type('Done', (), {'returncode': 0, 'stdout': out, 'stderr': ''})()
        with patch('console.inbox.ai.subprocess.run', side_effect=[done(empty), done(empty), done(good)]) as run:
            output = ai.official_runner('虚构提示词')
        models = [json.loads(call.kwargs['input'])['model'] for call in run.call_args_list]
        self.assertEqual(models, ['deepseek-flash', 'deepseek-flash', 'deepseek-v4-pro'])
        self.assertEqual(output.model, 'deepseek-v4-pro')
        with patch('console.inbox.ai.subprocess.run', return_value=done(empty)) as run:
            with self.assertRaises(RuntimeError):
                ai.official_runner('虚构提示词')
        self.assertEqual(run.call_count, 3)  # bounded: never more than three calls per draft


class PlainTextDraftTests(unittest.TestCase):
    TEXT = '语言：英语\nHello, thanks for writing. Which model do you need? 【待确认：型号与价格】\n=====待确认=====\n- 确认型号\n2. 确认价格\n'

    def test_plain_text_answer_in_gateway_envelope(self):
        envelope = json.dumps({'status': 'ok', 'actual_model': 'deepseek-flash', 'answer': self.TEXT}, ensure_ascii=False)
        parsed = ai.parse_output(RunnerOutput(envelope, 'deepseek-flash'))
        self.assertEqual(parsed['language'], '英语')
        self.assertIn('待确认', parsed['text'])
        self.assertNotIn('=====', parsed['text'])
        self.assertEqual(parsed['needs_confirmation'], ['确认型号', '确认价格'])

    def test_plain_text_without_language_line_is_rejected(self):
        for text in ('Hello there', '', '=====待确认=====\n确认价格'):
            envelope = json.dumps({'status': 'ok', 'answer': text}, ensure_ascii=False)
            with self.subTest(text=text), self.assertRaises(ValueError):
                ai.parse_output(RunnerOutput(envelope, 'deepseek-flash'))

    def test_gateway_failure_or_null_answer_is_not_a_draft(self):
        for envelope in ({'status': 'failed', 'error': 'x', 'answer': None}, {'status': 'ok', 'answer': None}):
            with self.assertRaises(ValueError):
                ai.parse_output(RunnerOutput(json.dumps(envelope), 'm'))
