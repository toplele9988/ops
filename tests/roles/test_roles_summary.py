from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
import subprocess
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from console.inbox import ai as inbox_ai
from console.roles.common import Config
from console.roles.storage import RateLimited
from .conftest import answer


def test_rules_explain_waiting_and_quote_blockers_with_evidence(env):
    key = env.conv(age=26 * 3600)
    did, rid, lid, *_ = env.good_round(key=key)
    env.qw('line_update', rid, lid, sell_unit_price='0.01')
    result = env.roles.run_secretary(did, 'fictional-owner')
    out = result['output_json']
    assert result['source'] == 'rules' and result['status'] == 'ok'
    assert result['ai_success'] is None and result['model'] is None
    assert '26 小时' in result['output_text'] and 'Hasan' in result['output_text']
    assert out['waiting_on'][0]['who'] == '我方'
    assert out['waiting_on'][0]['waited_hours'] == 26
    assert 1 <= len(out['next_steps']) <= 3
    assert any('倒挂' in r for r in out['risks'])
    assert {'kind': 'quote', 'id': rid} in result['evidence_json']
    assert any(e['kind'] == 'message' and not e['derived'] for e in result['evidence_json'])
    assert env.deals.read('detail', row_id=did)['stage'] == 'quoting'


@pytest.mark.parametrize('role', ['secretary', 'triage'])
def test_prompts_and_output_text_preserve_business_numbers_and_redact_contacts(env, role):
    text = '2026-10-30 USD 1,234.50 3 单；+86 138 0013 8000；fictional@example.invalid'
    key = env.conv(name='虚构新联系人', text=text)
    if role == 'secretary':
        did = env.deal(key=key)
        response = f'现状：{text}\n下一步：1\n风险：无'
    else:
        response = f'建议：新建\n案子：无\n角色：客户\n把握：0.3\n理由：{text}'
    runner = Mock(return_value=answer(response))
    roles = env.service(runner=runner)
    result = roles.run_secretary(did, 'fictional-owner') if role == 'secretary' else roles.scan_triage('fictional-owner')['runs'][0]
    assert result['source'] == 'ai'
    for rendered in (runner.call_args.args[0], result['output_text']):
        for fact in ('2026-10-30', 'USD 1,234.50', '3 单'):
            assert fact in rendered
        assert '〔号码已隐藏〕' in rendered and '〔邮箱已隐藏〕' in rendered
        assert '138 0013' not in rendered and 'fictional@example.invalid' not in rendered


@pytest.mark.parametrize('closing', ['谢谢', '收到', 'Ok', '👍'])
def test_closing_messages_do_not_create_waiting_work(env, closing):
    did = env.deal(key=env.conv(text=closing, age=26 * 3600))
    out = env.roles.run_secretary(did, 'fictional-owner')['output_json']
    assert out['waiting_on'][0]['state'] == 'none'
    assert '在等我方回复' not in out['headline']


def test_unknown_observation_does_not_reset_clock_and_history_is_bounded_redacted(env):
    key = env.conv(text='first synthetic message', age=100 * 3600)
    for n in range(39):
        env.message(key, text=f'虚构消息 {n} contact test@example.invalid token=fictional-secret /fictional/private ' + 'x' * 4000,
                    at=env.clock() - (40 - n) * 3600)
    env.message(key, role='unknown', at=env.clock())
    did = env.deal(key=key)
    runner = Mock(return_value=answer('现状：客户等回复\n下一步：1\n风险：无'))
    roles = env.service(runner=runner)
    result = roles.run_secretary(did, 'fictional-owner')
    assert result['source'] == 'ai' and result['model'] == 'fictional-model' and result['ai_success']
    prompt = runner.call_args.args[0]
    assert 'first synthetic message' not in prompt
    assert 'test@example.invalid' not in prompt and 'fictional-secret' not in prompt and '/fictional/private' not in prompt
    assert 'x' * 1100 not in prompt
    assert sum(e['kind'] == 'message' for e in result['evidence_json']) == 30
    assert result['output_json']['waiting_on'][0]['waited_hours'] == 2
    assert env.hub.calls and all(c[1] == 'customer_reply' for c in env.hub.calls)
    assert dict(kind='knowledge', id='fictional-chunk-1') in result['evidence_json']


@pytest.mark.parametrize('failure', [RuntimeError('fictional private error'), TimeoutError('fictional timeout'), '', 'garbage', answer('现状：空下一步\n下一步：99\n风险：无')])
def test_ai_error_timeout_empty_and_invalid_all_fall_back(env, failure):
    did = env.deal()
    runner = Mock(side_effect=failure) if isinstance(failure, Exception) else Mock(return_value=failure)
    result = env.service(runner=runner).run_secretary(did, 'fictional-owner')
    assert result['source'] == 'rules' and result['status'] == 'fallback'
    assert result['ai_success'] is False and result['error']
    assert 'fictional private error' not in json.dumps(result)
    assert result['output_text'] and result['output_json']['waiting_on'] and result['output_json']['next_steps']
    assert runner.call_count == 1


def test_runner_timeout_ignores_late_result(env):
    release = threading.Event()
    finished = threading.Event()
    def runner(prompt):
        try:
            release.wait(3)
            return answer('现状：不应保存晚到结果\n下一步：1\n风险：无')
        finally:
            finished.set()
    roles = env.service(runner=runner, config=replace(env.roles.config, ai_timeout=.01))
    did = env.deal()
    try:
        result = roles.run_secretary(did, 'fictional-owner')
        assert result['source'] == 'rules' and '超时' in result['error']
    finally:
        release.set()
        assert finished.wait(3)
    assert '不应保存晚到结果' not in roles.read('secretary', row_id=did)['output_text']


def test_fingerprint_cache_and_changes_invalidate(env):
    key = env.conv()
    did = env.deal(key=key)
    runner = Mock(return_value=answer('现状：等待核对\n下一步：1\n风险：无'))
    roles = env.service(runner=runner)
    first = roles.run_secretary(did, 'fictional-owner')
    assert roles.run_secretary(did, 'fictional-owner')['id'] == first['id']
    assert roles.run_secretary(did, 'fictional-owner')['reused']
    assert runner.call_count == 1
    env.message(key, 'New fictional requirement')
    assert roles.run_secretary(did, 'fictional-owner')['id'] != first['id']
    assert runner.call_count == 2
    env.clock.advance(3600)
    assert not roles.run_secretary(did, 'fictional-owner')['reused']


def test_shared_database_quota_counts_failures_and_cached_run_bypasses_it(env):
    config = Config(timezone='UTC', per_minute=3, per_hour=3)
    runner = Mock(side_effect=RuntimeError('fictional error'))
    roles = env.service(runner=runner, config=config)
    first_id, second_id = env.deal(), env.deal()
    first = roles.run_secretary(first_id, 'fictional-owner')
    assert roles.run_secretary(first_id, 'fictional-owner')['id'] == first['id']
    other = env.service(runner=runner, config=config)
    with pytest.raises(RateLimited) as captured:
        other.run_secretary(second_id, 'fictional-owner')
    assert captured.value.retry_after == 3600
    assert captured.value.result['source'] == 'rules' and captured.value.result['output_text']
    assert runner.call_count == 1
    env.clock.advance(3600)
    third = env.deal()
    roles.run_secretary(third, 'fictional-owner')
    assert runner.call_count == 2


def test_official_runner_is_called_once_and_gateway_uses_at_most_three_attempts(env, monkeypatch):
    did = env.deal()
    outputs = [SimpleNamespace(returncode=0, stdout=json.dumps({'answer': ''})) for _ in range(2)]
    outputs.append(SimpleNamespace(returncode=0, stdout=json.dumps({'answer': str(answer('现状：客户等回复\n下一步：1\n风险：无'))})))
    fake_gateway = Mock(side_effect=outputs)
    monkeypatch.setattr(subprocess, 'run', fake_gateway)
    roles = env.service(runner=inbox_ai.official_runner)
    result = roles.run_secretary(did, 'fictional-owner')
    assert result['model'] == 'deepseek-v4-pro'
    models = [json.loads(c.kwargs['input'])['model'] for c in fake_gateway.call_args_list]
    assert models == ['deepseek-flash', 'deepseek-flash', 'deepseek-v4-pro']
    assert all(c.args[0] == [inbox_ai.GATEWAY] and c.kwargs['timeout'] == 120 for c in fake_gateway.call_args_list)


def test_no_double_retry_when_official_attempts_are_empty(env, monkeypatch):
    did = env.deal()
    fake = Mock(return_value=SimpleNamespace(returncode=0, stdout='{"answer":""}'))
    monkeypatch.setattr(subprocess, 'run', fake)
    result = env.service(runner=inbox_ai.official_runner).run_secretary(did, 'fictional-owner')
    assert fake.call_count == 3 and result['source'] == 'rules'


def test_concurrent_builders_reuse_one_output_and_one_ai_call(env):
    did = env.deal()
    runner = Mock(return_value=answer('现状：待核对\n下一步：1\n风险：无'))
    services = [env.service(runner=runner) for _ in range(4)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda s: s.run_secretary(did, 'fictional-owner'), services))
    assert len({r['id'] for r in results}) == 1 and runner.call_count == 1


def test_ai_cannot_replace_next_steps_with_customer_leaks(env):
    did = env.deal(key=env.conv())
    runner = Mock(return_value=answer('现状：请核对\n下一步：1\n风险：无\n客户原话：成本13.47利润供应商FICTIONAL-SECRET'))
    result = env.service(runner=runner).run_secretary(did, 'fictional-owner')
    steps = json.dumps(result['output_json']['next_steps'], ensure_ascii=False)
    assert '13.47' not in steps and 'FICTIONAL-SECRET' not in steps


def test_unavailable_inbox_still_returns_rule_steps(env):
    key = env.conv()
    did = env.deal(key=key)
    env.inbox.missing.add(key)
    result = env.roles.run_secretary(did, 'fictional-owner')
    assert result['output_json']['next_steps']
    assert any('无法读取' in r for r in result['output_json']['risks'])


def test_minute_quota_expires_at_boundary_independently_of_hour(env):
    runner = Mock(return_value=answer('现状：请核对\n下一步：1\n风险：无'))
    roles = env.service(runner=runner, config=Config(timezone='UTC', per_minute=3, per_hour=60))
    roles.run_secretary(env.deal(), 'fictional-owner')
    with pytest.raises(RateLimited) as captured:
        roles.run_secretary(env.deal(), 'fictional-owner')
    assert captured.value.retry_after == 60
    env.clock.advance(60)
    roles.run_secretary(env.deal(), 'fictional-owner')
    assert runner.call_count == 2
