import json
import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from console.inspiration.enrich import (FALLBACK_MODEL, NO_RELATION, NO_USAGE,
                                        ai_task_runner, enrich_batch, make_prompt)
from inspiration_fixtures import card, enrichment


class EnrichTests(unittest.TestCase):
    def setUp(self):
        self.card = card()

    def run_result(self, result, wrapped=False):
        output = json.dumps([result], ensure_ascii=False)
        if wrapped:
            output = '虚构模型输出\n```json\n' + output + '\n```'
        return enrich_batch([self.card], lambda prompt: output)

    def test_normal_and_fenced_json(self):
        for wrapped in (False, True):
            result = self.run_result(enrichment(self.card), wrapped)
            self.assertEqual(result['errors'], [])
            self.assertEqual(result['model'], FALLBACK_MODEL)
            row = result['results'][0]
            self.assertEqual(row['relevance'], 'ai_dev')
            self.assertEqual(row['items'][0]['name'], 'DemoKit')
            self.assertEqual(row['confidence'], 'high')

    def test_invented_names_are_dropped_and_invented_urls_become_null(self):
        result = self.run_result(enrichment(self.card, items=[
            dict(name='DemoKit', kind='tool', url='https://invented.invalid/no'),
            dict(name='InventedProject', kind='project', url='https://demo.invalid/kit')]))
        self.assertEqual(result['results'][0]['items'], [dict(name='DemoKit', kind='tool', url=None)])

    def test_invalid_enums_and_lengths_are_conservative(self):
        result = self.run_result(enrichment(self.card, relevance='auto', confidence='certain',
            summary='字' * 200, usefulness='用' * 200, how_to_use='法' * 500,
            items=[dict(name='DemoKit', kind='library', url=None)]))['results'][0]
        self.assertEqual(result['relevance'], 'unknown')
        self.assertEqual(result['confidence'], 'low')
        self.assertEqual(result['items'][0]['kind'], 'method')
        self.assertEqual(len(result['summary']), 160)
        self.assertLessEqual(len(result['how_to_use']), 240)
        self.assertEqual(result['usefulness'], NO_RELATION)
        result = self.run_result(enrichment(self.card, usefulness='用' * 200))['results'][0]
        self.assertEqual(len(result['usefulness']), 120)

    def test_missing_usage_always_gets_fixed_text_and_low_confidence(self):
        self.card = card('虚构 DemoKit 是一个开源工具 #AI')
        result = self.run_result(enrichment(self.card, how_to_use='编造的安装操作', confidence='high'))['results'][0]
        self.assertEqual(result['how_to_use'], NO_USAGE)
        self.assertEqual(result['confidence'], 'low')

    def test_how_to_use_comes_from_explicit_source_span_not_model_invention(self):
        self.card = card('虚构 DemoKit。用法：复制到示例目录。 #AI')
        result = self.run_result(enrichment(self.card, how_to_use='先执行编造命令，再自动发品'))['results'][0]
        self.assertEqual(result['how_to_use'], '复制到示例目录')

    def test_business_and_other_are_allowed_and_other_has_no_claimed_usefulness(self):
        for relevance in ('business', 'other'):
            result = self.run_result(enrichment(self.card, relevance=relevance))['results'][0]
            self.assertEqual(result['relevance'], relevance)
            if relevance == 'other':
                self.assertEqual(result['usefulness'], NO_RELATION)

    def test_cross_batch_ids_and_duplicates_are_ignored_missing_cards_get_one_error(self):
        second = card('虚构另一个项目')
        output = json.dumps([enrichment(self.card), enrichment(self.card),
                             dict(card_id='not-in-this-batch', relevance='ai_dev')])
        report = enrich_batch([self.card, second], lambda p: output)
        self.assertEqual(len(report['results']), 1)
        self.assertEqual(report['errors'], [second['card_id']])

    def test_runner_exceptions_timeout_and_malformed_output_never_retry(self):
        for error in (RuntimeError('虚构私有文本'), subprocess.TimeoutExpired('ai-task', 300)):
            runner = Mock(side_effect=error)
            report = enrich_batch([self.card], runner)
            self.assertEqual(report['errors'], [self.card['card_id']])
            self.assertEqual(report['results'], [])
            self.assertEqual(runner.call_count, 1)
            self.assertNotIn('虚构私有文本', str(report))
        for output in ('not JSON', '[]', '[null, {}]', 'null', '{}'):
            self.assertEqual(enrich_batch([self.card], lambda p: output)['errors'], [self.card['card_id']])

    def test_batch_limit_empty_batch_and_prompt_rules(self):
        with self.assertRaises(ValueError):
            enrich_batch([self.card] * 7, Mock())
        runner = Mock()
        self.assertEqual(enrich_batch([], runner)['results'], [])
        runner.assert_not_called()
        prompt = make_prompt([self.card])
        for text in ('只输出 JSON 数组', '名字', '禁止搜索或编造链接', NO_USAGE,
                     NO_RELATION, '舞台灯光出口', '装箱', '倒挂', 'WhatsApp', '独立站'):
            self.assertIn(text, prompt)

    def test_json_envelope_and_actual_model_metadata(self):
        output = json.dumps({'model': 'fictional-cn-model', 'output': json.dumps([enrichment(self.card)])})
        report = enrich_batch([self.card], lambda p: output)
        self.assertEqual(report['errors'], [])
        self.assertEqual(report['model'], 'fictional-cn-model')

    def test_ai_task_uses_fixed_argv_timeout_and_reads_actual_model_from_stderr(self):
        stdout = json.dumps([enrichment(self.card)])
        completed = SimpleNamespace(returncode=0, stdout=stdout,
                                    stderr='虚构日志\n{"metadata":{"model_name":"fictional-model-2"}}')
        with patch('console.inspiration.enrich.subprocess.run', return_value=completed) as run:
            report = enrich_batch([self.card], ai_task_runner)
        args, kw = run.call_args
        self.assertEqual(args[0], ['ai-task', 'run', 'bailian_free', '--model', 'auto'])
        self.assertEqual(kw['timeout'], 300)
        self.assertTrue(kw['text'])
        self.assertTrue(kw['capture_output'])
        self.assertNotIn('shell', kw)
        self.assertEqual(report['model'], 'fictional-model-2')
        self.assertEqual(run.call_count, 1)

    def test_nonzero_exit_does_not_expose_output_or_retry(self):
        completed = SimpleNamespace(returncode=2, stdout='虚构敏感输出', stderr='虚构敏感错误')
        with patch('console.inspiration.enrich.subprocess.run', return_value=completed) as run:
            with self.assertRaisesRegex(RuntimeError, '^模型任务执行失败$'):
                ai_task_runner('虚构提示')
            self.assertEqual(run.call_count, 1)


if __name__ == '__main__':
    unittest.main()


class GatewayEnvelopeTests(unittest.TestCase):
    """The real ai-task/gateway reply wraps the model text in an `answer` field."""

    def test_answer_envelope_is_parsed_and_model_recorded(self):
        import json as _json
        from console.inspiration.enrich import gateway_runner
        item = card()
        answer = _json.dumps([enrichment(item)], ensure_ascii=False)
        envelope = _json.dumps({'status': 'ok', 'actual_model': 'fictional-model', 'answer': answer}, ensure_ascii=False)
        completed = SimpleNamespace(returncode=0, stdout=envelope, stderr='')
        with patch('console.inspiration.enrich.subprocess.run', return_value=completed) as run:
            output = gateway_runner('虚构提示词')
        self.assertEqual(run.call_args.args[0], ['/usr/local/libexec/imgw-ai-task'])
        sent = _json.loads(run.call_args.kwargs['input'])
        self.assertEqual((sent['provider'], sent['model']), ('bailian_free', 'auto'))
        result = enrich_batch([item], lambda prompt: output)
        self.assertEqual(result['errors'], [])
        self.assertEqual(result['model'], 'fictional-model')
        self.assertEqual([r['card_id'] for r in result['results']], [item['card_id']])


class IndustryCategoryTests(unittest.TestCase):
    def test_industry_cards_keep_their_usefulness_text(self):
        from console.inspiration.enrich import validate_enrichment
        item = card('虚构灯具厂家介绍一款新产品 #灯光')
        safe = validate_enrichment(item, enrichment(item, relevance='industry'))
        self.assertEqual(safe['relevance'], 'industry')
        self.assertNotEqual(safe['usefulness'], NO_RELATION)
