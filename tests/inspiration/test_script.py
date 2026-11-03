import contextlib
import importlib.util
import io
import json
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest
from unittest.mock import Mock

from console.inspiration.store import InspirationStore
from inspiration_fixtures import card, create_source, enrichment, payload

SPEC = importlib.util.spec_from_file_location('inspiration_sync_cli',
    Path(__file__).resolve().parents[2] / 'scripts' / 'inspiration_sync.py')
cli = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cli)


class ScriptTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.source = Path(self.temp.name) / 'source.sqlite3'
        self.path = Path(self.temp.name) / 'store.sqlite3'
        create_source(self.source)
        self.args = ['--state-db', str(self.source), '--store', str(self.path)]

    def run_cli(self, options=(), runner=None):
        output, errors = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            result = cli.main([*self.args, *options], runner=runner)
        return result, output.getvalue(), errors.getvalue()

    def seed_pending(self, count):
        store = InspirationStore(self.path)
        cards = [card(f'虚构 DemoKit 项目 {i}，用法：复制到示例目录') for i in range(count)]
        for i, item in enumerate(cards):
            store.upsert_card(item, f'fake-{i}', i + 1)
        return store, cards

    def test_sync_only_never_calls_model_or_prints_card_text(self):
        with sqlite3.connect(self.source) as connection:
            connection.execute('INSERT INTO messages(app_id,message_id,created_at,payload) VALUES (?,?,?,?)',
                               ('fake-app', 'fake-message', 1, payload()))
        runner = Mock(side_effect=AssertionError('must not call'))
        code, output, errors = self.run_cli(runner=runner)
        self.assertEqual(code, 0)
        self.assertIn('新增 1 张', output)
        self.assertIn('已整理 0 张，失败 0 张', output)
        self.assertNotIn('DemoKit', output + errors)
        runner.assert_not_called()

    def test_failed_runner_records_each_card_once_then_continues_next_batch(self):
        store, cards = self.seed_pending(3)
        runner = Mock(side_effect=[RuntimeError('虚构私有内容'),
                                  json.dumps([enrichment(cards[1])]),
                                  json.dumps([enrichment(cards[2])])])
        code, output, errors = self.run_cli(['--enrich', '--batch-size', '1'], runner)
        self.assertEqual(code, 1)
        self.assertIn('已整理 2 张，失败 1 张', output)
        self.assertEqual(runner.call_count, 3)
        self.assertEqual(store.get_card(cards[0]['card_id'])['enrich_error_count'], 1)
        self.assertTrue(store.get_card(cards[1]['card_id'])['enriched'])
        self.assertNotIn('虚构私有内容', output + errors)

    def test_limit_caps_total_attempted_cards_and_calls_without_retrying_timeouts(self):
        store, cards = self.seed_pending(5)
        runner = Mock(side_effect=subprocess.TimeoutExpired('ai-task', 300))
        code, output, errors = self.run_cli(['--enrich', '--limit', '2', '--batch-size', '1'], runner)
        self.assertEqual(code, 1)
        self.assertEqual(runner.call_count, 2)
        self.assertEqual(sum(store.get_card(item['card_id'])['enrich_error_count'] for item in cards), 2)
        self.assertIn('失败 2 张', output)

    def test_missing_result_is_one_error_while_other_result_is_saved(self):
        store, cards = self.seed_pending(2)
        runner = Mock(return_value=json.dumps([enrichment(cards[0])]))
        code, output, errors = self.run_cli(['--enrich'], runner)
        self.assertEqual(code, 1)
        self.assertIn('已整理 1 张，失败 1 张', output)
        self.assertEqual(store.get_card(cards[1]['card_id'])['enrich_error_count'], 1)
        self.assertEqual(runner.call_count, 1)

    def test_invalid_batch_size_limit_and_same_path_are_rejected_before_execution(self):
        for extra in (['--batch-size', '7'], ['--batch-size', '0'], ['--limit', '0'], ['--limit', '-1'],
                      ['--store', str(self.source)]):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                cli.main([*self.args, *extra])

    def test_missing_source_only_prints_controlled_error(self):
        self.source.unlink()
        code, output, errors = self.run_cli()
        self.assertEqual(code, 1)
        self.assertIn('无法只读打开', errors)
        self.assertNotIn(str(self.source), errors)


if __name__ == '__main__':
    unittest.main()
