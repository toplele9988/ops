"""Fictional D1b scan queues; no live model, clocks or channel connections."""
from dataclasses import replace
import threading
from unittest.mock import Mock, patch

import pytest

from console.extract import Config
from console.inbox import ai as inbox_ai
from console.roles.common import Config as RoleConfig
from test_extract_service import Env, answer


@pytest.fixture
def env(tmp_path):
    return Env(tmp_path)


def test_seventeen_conversations_advance_eight_eight_one_without_rule_backfill(env):
    keys = {env.conversation() for _ in range(17)}
    runner = Mock(return_value=answer())
    service = env.service(runner=runner)
    for calls, remaining in ((8, 9), (8, 1), (1, 0)):
        result = service.scan('fictional-owner')
        assert result['ai_calls'] == calls and result['remaining'] == remaining
        assert result['added'] == calls
        assert all(item['source'] == 'ai' for item in result['items'])
        assert all(run['source'] == 'ai' for run in result['runs'])
    assert runner.call_count == 17
    assert {i['conversation_key'] for i in service.read('suggestions')['items']} == keys
    assert service.scan('fictional-owner')['ai_calls'] == 0


def test_configurable_ai_limit_and_no_clue_does_not_use_quota(env):
    for _ in range(4):
        env.conversation()
    env.conversation('虚构联系人：稍后发送资料')
    runner = Mock(return_value=answer())
    result = env.service(runner=runner, config=Config(scan_ai_limit=2)).scan('fictional-owner')
    assert result['ai_calls'] == 2 and runner.call_count == 2
    assert result['remaining'] == 2 and len(result['deferred']) == 2
    assert result['scanned'] == 3


@pytest.mark.parametrize('failure', [TimeoutError(), RuntimeError('fictional private error')])
def test_failed_window_does_not_stop_next_window_and_is_retryable(env, failure):
    for _ in range(3):
        env.conversation()
    runner = Mock(side_effect=[failure, answer(), answer(), answer()])
    service = env.service(runner=runner)
    result = service.scan('fictional-owner')
    assert result['ai_calls'] == 3 and result['remaining'] == 1 and result['added'] == 2
    assert len(result['deferred']) == 1 and '下次再扫' in result['deferred'][0]['reason']
    assert result['runs'][0]['output_json']['item_count'] == 0
    assert all(item['source'] == 'ai' for item in result['items'])
    next_result = service.scan('fictional-owner')
    assert next_result['ai_calls'] == 1 and next_result['remaining'] == 0
    assert service.read('suggestions')['total'] == 3


def test_real_timeout_discards_late_output_and_other_window_runs(env):
    for _ in range(2):
        env.conversation()
    release, done = threading.Event(), threading.Event()
    calls = []
    def runner(prompt):
        calls.append(prompt)
        if len(calls) == 1:
            release.wait(2)
            done.set()
        return answer()
    service = env.service(runner=runner, config=Config(ai_timeout=.02))
    try:
        result = service.scan('fictional-owner')
        assert result['remaining'] == 1 and result['added'] == 1 and len(calls) == 2
    finally:
        release.set()
        assert done.wait(2)
    assert service.read('suggestions')['total'] == 1


def test_total_wait_deadline_shortens_last_call_and_defers_rest(env):
    for _ in range(4):
        env.conversation()
    elapsed, timeouts = [0.0], []
    def invoke(runner, prompt, timeout):
        timeouts.append(timeout)
        elapsed[0] += timeout
        if len(timeouts) == 1:
            return inbox_ai.parse_output(answer())
        raise TimeoutError
    service = env.service(runner=Mock(), timer=lambda: elapsed[0],
                          config=Config(ai_timeout=100, scan_timeout=180))
    with patch('console.extract.service.role_ai.invoke', side_effect=invoke):
        result = service.scan('fictional-owner')
    assert timeouts == [100, 80] and elapsed[0] == 180
    assert result['ai_calls'] == 2 and result['remaining'] == 3 and result['added'] == 1
    assert len(result['deferred']) == 3
    assert all(item['source'] == 'ai' for item in result['items'])


def test_ai_no_quote_is_successful_empty_result_cached_without_fallback(env):
    env.conversation()
    runner = Mock(return_value=answer('无报价'))
    service = env.service(runner=runner)
    result = service.scan('fictional-owner')
    assert result['items'] == [] and result['remaining'] == 0
    assert result['runs'][0]['ai_success'] is True
    service.scan('fictional-owner')
    assert runner.call_count == 1


def test_rules_cache_does_not_prevent_later_ai_analysis(env):
    env.conversation()
    env.extract.scan('fictional-owner')
    runner = Mock(return_value=answer())
    service = env.service(runner=runner)
    assert service.scan('fictional-owner')['ai_calls'] == 1
    runner.assert_called_once()


def test_shared_role_config_cannot_change_extractor_quota_defaults(env):
    service = env.service(config=RoleConfig(per_minute=3, per_hour=3, timezone='UTC'))
    assert service.config.per_minute == service.config.per_hour == 72
    assert service.config.scan_ai_limit == 8
    assert service.config.ai_timeout == 30 and service.config.scan_timeout == 180
    assert service.config.timezone == 'UTC'


@pytest.mark.parametrize('value', [0, -1, 1.5, True, 101])
def test_ai_scan_limit_validation(value):
    with pytest.raises(ValueError):
        Config(scan_ai_limit=value)


def test_detail_change_with_same_overview_cannot_bypass_ai_call_limit(env):
    for _ in range(3):
        env.conversation()
    service = env.service(runner=Mock(return_value=answer()))
    service.scan('fictional-owner')
    original = env.inbox.detail
    def changed(key, now):
        conv = dict(original(key, now))
        conv['messages'] = [dict(conv['messages'][0], text=conv['messages'][0]['text'] + '；虚构包装资料')]
        return conv
    env.inbox.detail = changed
    service.config = replace(service.config, scan_ai_limit=1)
    result = service.scan('fictional-owner')
    assert result['ai_calls'] == 1 and result['remaining'] == 2
    assert len(result['deferred']) == 2


def test_real_conversation_restatement_merges_sources_and_completes_fields(env):
    key = env.conversation('虚构 ZX-300 追光灯，CNY 1280')
    first = env.extract.scan('fictional-owner')['items'][0]
    env.message(key, '你们报价虚构 ZX-300 追光灯，CNY 1280/台', role='seller')
    # A seller must attribute the price-bearing clause explicitly.
    env.inbox.conversations[key]['messages'][-1]['text'] = '你们报价虚构 ZX-300 追光灯 CNY 1280/台'
    result = env.extract.scan('fictional-owner')
    assert result['added'] == 0 and len(result['items']) == 1
    row = result['items'][0]
    assert row['id'] == first['id'] and row['unit'] == '台'
    assert len(row['source_message_refs']) == 2
    assert row['source_message_refs'][1]['restated'] is True


def test_only_explicit_ai_bundle_is_saved_and_hidden(env):
    env.conversation('18×90+380=2000元')
    assert env.extract.scan('fictional-owner')['added'] == 0
    service = env.service(runner=Mock(return_value=answer(
        '消息：1\n类型：整单报价参考\n价格：2000\n币种：CNY\n单位：无\n产品：无')))
    result = service.scan('fictional-owner')
    assert result['items'] == [] and result['added'] == 1 and result['remaining'] == 0
    low, = service.read('suggestions', {'include_low': True})['items']
    assert low['is_bundle'] and not low['product_text'] and low['confidence'] <= .2
    assert service.read('summary')['pending_quality'] == dict(trusted=0, low=1)


@pytest.mark.parametrize('text,body,expected', [
    ('200W 追光灯 单价550元含税，交期7天，本周有效',
     '消息：1\n类型：产品报价\n价格：550\n产品：200W 追光灯\n币种：CNY\n单位：无',
     dict(kind='offer', price_fen=55000, currency='CNY', unit=None, lead_time_text='交期7天', tax_text='含税')),
    ('空运 45 元/公斤，最低收费 500，到美国，10天内有效',
     '消息：1\n类型：货代运价\n价格：45\n币种：CNY\n单位：公斤',
     dict(kind='freight', price_fen=4500, currency='CNY', unit='kg', mode='air', min_charge_fen=50000,
          destination_country='美国')),
])
def test_good_examples_through_fake_runner_preserve_fields(env, text, body, expected):
    env.conversation(text)
    row, = env.service(runner=Mock(return_value=answer(body))).scan('fictional-owner')['items']
    assert row['source'] == 'ai' and row['suggested_valid_until']
    assert {name: row[name] for name in expected} == expected
