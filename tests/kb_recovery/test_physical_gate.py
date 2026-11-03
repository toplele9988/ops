"""只使用虚构模型、虚构额度、假 runner 和临时私有 SQLite。"""
from copy import deepcopy
import importlib.util
import json
import multiprocessing
import os
from pathlib import Path
import sqlite3
import threading

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('physical_gate_under_test', ROOT / 'deploy/kb/physical_gate.py')
g = importlib.util.module_from_spec(spec)
spec.loader.exec_module(g)


def config():
    return {'schema': 'kb-physical-gate-v1', 'interval_seconds': 30,
            'models': {
                'fictional-free': {'bucket': 'free', 'frozen': False, 'cost_micros_per_1m_tokens': None},
                'fictional-paid': {'bucket': 'paid', 'frozen': False, 'cost_micros_per_1m_tokens': 1000000},
                'fictional-frozen': {'bucket': 'free', 'frozen': True, 'cost_micros_per_1m_tokens': None}},
            'buckets': {
                'free': {'kind': 'free_pool', 'verified': True, 'max_requests': 5,
                         'max_tokens': 1000, 'max_cost_micros': None},
                'paid': {'kind': 'prepaid', 'verified': True, 'max_requests': 5,
                         'max_tokens': 1000, 'max_cost_micros': 1000}}}


def item(n=1, channel='wechat'):
    return {'channel': channel, 'source_id': '虚构来源' + str(n), 'prompt_sha256': g.digest('虚构提示词' + str(n)),
            'fingerprint': g.digest('虚构来源指纹' + str(n))}


class Harness:
    def __init__(self, tmp_path, value=None):
        self.path = tmp_path / 'private' / 'physical.sqlite3'
        self.path.parent.mkdir(mode=0o700)
        self.value = value or config()
        self.now = 10000
        self.requests = []
        self.gate = self.restart()

    def restart(self):
        return g.PhysicalGate(self.path, self.value, clock=lambda: self.now)

    def run(self, n=1, *, model='fictional-free', channel='wechat', runner=None, check=None, request_key=None):
        return self.gate.call(item(n, channel), model, 100, runner or self.runner,
                              check or (lambda *a: True), request_key=request_key)

    def runner(self, source, model, reserve):
        self.requests.append((source['source_id'], model, reserve))
        return {'content': '虚构响应', 'model': model}


def test_global_interval_applies_to_both_channels_and_survives_restart(tmp_path):
    h = Harness(tmp_path)
    h.run()
    h.now += 29
    h.gate = h.restart()
    with pytest.raises(g.RateLimited) as error:
        h.run(2, model='fictional-paid', channel='alibaba')
    assert error.value.wait_seconds == 1
    assert len(h.requests) == 1
    h.now += 1
    h.run(2, model='fictional-paid', channel='alibaba')
    assert len(h.requests) == 2
    status = h.gate.status()
    assert status['buckets']['free']['reserved_tokens'] == 100
    assert status['buckets']['paid']['reserved_cost_micros'] == 100


def test_interval_hard_floor_cannot_be_changed_by_batch_or_clock(tmp_path):
    value = config()
    value['interval_seconds'] = 29
    with pytest.raises(g.GateError, match='30 秒'):
        Harness(tmp_path, value)


def test_slow_response_does_not_reduce_next_physical_request_interval(tmp_path):
    h = Harness(tmp_path)
    def slow(*args):
        h.now += 20
        return {'content': '虚构慢响应'}
    h.run(runner=slow)
    h.now += 10
    with pytest.raises(g.RateLimited) as error:
        h.run(2, channel='alibaba', model='fictional-paid')
    assert error.value.wait_seconds == 20
    h.now += 20
    h.run(2, channel='alibaba', model='fictional-paid')


def test_cached_complete_checkpoint_is_reused_without_another_call(tmp_path):
    h = Harness(tmp_path)
    first = h.run(request_key=g.digest('虚构读者A'))
    h.gate = h.restart()
    assert h.run(request_key=g.digest('虚构读者A')) == first
    assert len(h.requests) == 1
    with pytest.raises(g.GateError, match='来源或预留已变化'):
        h.run(2, request_key=g.digest('虚构读者A'))


def test_cached_checkpoint_still_requires_current_source_authorization(tmp_path):
    h = Harness(tmp_path)
    h.run()
    with pytest.raises(g.GateStopped, match='原授权'):
        h.run(check=lambda *a: False)
    h.now += 30
    with pytest.raises(g.GateStopped):
        h.run(2, model='fictional-paid')
    assert len(h.requests) == 1


def test_cached_checkpoint_needs_source_guard_but_no_new_quota(tmp_path):
    h = Harness(tmp_path)
    h.run()
    checks = []
    def no_quota(*args):
        raise g.QuotaExhausted('虚构已用完额度')
    def source_guard(*args):
        checks.append('虚构来源检查')
        return True
    reply = h.gate.call(item(), 'fictional-free', 100, h.runner, no_quota, check_cached=source_guard)
    assert reply['content'] == '虚构响应' and checks == ['虚构来源检查']
    assert len(h.requests) == 1 and h.gate.status()['bucket_stops'] == {}


def test_known_channel_block_in_check_does_not_stop_other_channels(tmp_path):
    h = Harness(tmp_path)
    def blocked(*args):
        raise g.GateDeferred('虚构已审渠道熔断仍开启')
    with pytest.raises(g.GateDeferred):
        h.run(check=blocked, channel='alibaba')
    h.run(2)
    assert len(h.requests) == 1 and h.gate.status()['stop_reason'] is None


@pytest.mark.parametrize('error', [TimeoutError('虚构超时'), SystemExit('虚构中断')])
def test_unknown_stops_both_channels_and_restart_never_retries(tmp_path, error):
    h = Harness(tmp_path)
    def unknown(*args):
        h.requests.append('虚构未知请求')
        raise error
    with pytest.raises(g.GateUnknown, match='模型结果未知'):
        h.run(runner=unknown)
    h.gate = h.restart()
    h.now += 100
    with pytest.raises(g.GateUnknown):
        h.run()
    with pytest.raises(g.GateUnknown):
        h.run(2, channel='alibaba', model='fictional-paid')
    assert len(h.requests) == 1
    assert h.gate.status()['stop_reason'] == 'unknown'
    assert h.gate.status()['buckets']['free']['reserved_tokens'] == 100


def test_hard_process_exit_between_marker_and_response_is_unknown(tmp_path):
    h = Harness(tmp_path)
    def child():
        gate = g.PhysicalGate(h.path, h.value, clock=lambda: 10000)
        gate.call(item(), 'fictional-free', 100, lambda *a: os._exit(23), lambda *a: True)
    process = multiprocessing.get_context('fork').Process(target=child)
    process.start()
    process.join(5)
    assert process.exitcode == 23
    # The readonly output treats an interrupted marker as unknown before any
    # constructor performs the durable recovery transition.
    assert g.PhysicalGate.read_status(h.path)['stop_reason'] == 'unknown'
    h.gate = h.restart()
    with pytest.raises(g.GateUnknown):
        h.run()
    assert h.requests == []


def test_exhaustion_stops_only_its_account_and_never_selects_another_model(tmp_path):
    value = config()
    value['buckets']['free']['max_tokens'] = 100
    h = Harness(tmp_path, value)
    h.run()
    h.now += 30
    with pytest.raises(g.QuotaExhausted):
        h.run(2)
    assert len(h.requests) == 1
    h.run(3, channel='alibaba', model='fictional-paid')
    status = h.gate.status()
    assert status['bucket_stops'] == {'free': 'quota_exhausted'}
    assert status['stop_reason'] is None
    assert len(h.requests) == 2


def test_known_provider_quota_rejection_is_not_unknown_and_not_retried(tmp_path):
    h = Harness(tmp_path)
    def exhausted(*args):
        h.requests.append('虚构额度已耗尽响应')
        raise g.QuotaExhausted('虚构明确额度拒绝')
    with pytest.raises(g.QuotaExhausted):
        h.run(runner=exhausted)
    h.now += 30
    h.gate = h.restart()
    with pytest.raises(g.QuotaExhausted):
        h.run(runner=exhausted)
    h.run(2, model='fictional-paid', channel='alibaba')
    assert len(h.requests) == 2
    assert h.gate.status()['stop_reason'] is None


@pytest.mark.parametrize('change', ['balance', 'price', 'verified'])
def test_unknown_paid_balance_or_price_never_uses_free_account(tmp_path, change):
    value = config()
    if change == 'balance':
        value['buckets']['paid']['max_cost_micros'] = None
    elif change == 'price':
        value['models']['fictional-paid']['cost_micros_per_1m_tokens'] = None
    else:
        value['buckets']['paid']['verified'] = False
    h = Harness(tmp_path, value)
    with pytest.raises(g.QuotaExhausted, match='尚未核实'):
        h.run(model='fictional-paid')
    assert h.requests == []
    h.run(2)
    assert len(h.requests) == 1


def test_frozen_model_is_rejected_without_closing_other_models_in_same_pool(tmp_path):
    h = Harness(tmp_path)
    with pytest.raises(g.QuotaExhausted, match='冻结'):
        h.run(model='fictional-frozen')
    h.run(2)
    assert h.gate.status()['bucket_stops'] == {}
    assert len(h.requests) == 1


def test_two_channels_cannot_enter_runner_concurrently(tmp_path):
    h = Harness(tmp_path)
    entered, release = threading.Event(), threading.Event()
    errors = []
    def first():
        try:
            h.run(runner=lambda *a: (entered.set(), release.wait(5), {'content': '虚构响应'})[-1])
        except BaseException as error:
            errors.append(error)
    thread = threading.Thread(target=first)
    thread.start()
    assert entered.wait(5)
    try:
        with pytest.raises(g.GateDeferred, match='已有物理请求写者'):
            h.run(2, channel='alibaba', model='fictional-paid')
    finally:
        release.set()
        thread.join(5)
    assert not errors
    assert h.gate.status()['buckets']['free']['reserved_tokens'] == 100


def test_local_failure_stops_whole_team_permanently_across_restart(tmp_path):
    h = Harness(tmp_path)
    h.gate.stop_local()
    h.gate = h.restart()
    with pytest.raises(g.GateStopped):
        h.gate.assert_running()
    with pytest.raises(g.GateStopped):
        h.run(1)
    with pytest.raises(g.GateStopped):
        h.run(2, channel='alibaba', model='fictional-paid')
    assert h.gate.status()['stop_reason'] == 'local_failure'


def test_status_is_readonly_and_does_not_emit_sources_or_responses(tmp_path):
    h = Harness(tmp_path)
    h.run()
    before = {path.name: (path.stat().st_mtime_ns, path.read_bytes()) for path in h.path.parent.iterdir()}
    output = json.dumps(g.PhysicalGate.read_status(h.path), ensure_ascii=False)
    after = {path.name: (path.stat().st_mtime_ns, path.read_bytes()) for path in h.path.parent.iterdir()}
    assert before == after
    assert '虚构来源' not in output and '虚构提示词' not in output and '虚构响应' not in output


def test_config_change_and_audit_tampering_fail_closed(tmp_path):
    h = Harness(tmp_path)
    h.run()
    value = deepcopy(h.value)
    value['buckets']['free']['max_tokens'] += 1
    with pytest.raises(g.GateError, match='策略已变化'):
        g.PhysicalGate(h.path, value)
    with sqlite3.connect(h.path) as db:
        db.execute("UPDATE gate_audit SET payload_json='{}' WHERE seq=1")
    with pytest.raises(g.GateError, match='审计损坏'):
        g.PhysicalGate.read_status(h.path)


def test_status_rejects_symlink_hardlink_and_nonprivate_paths(tmp_path):
    h = Harness(tmp_path)
    link = h.path.parent / 'linked.sqlite3'
    link.symlink_to(h.path)
    with pytest.raises(g.GateError, match='符号链接'):
        g.PhysicalGate.read_status(link)
    link.unlink()
    os.link(h.path, link)
    with pytest.raises(g.GateError, match='硬链接'):
        g.PhysicalGate.read_status(h.path)
    link.unlink()
    h.path.chmod(0o644)
    with pytest.raises(g.GateError, match='0600'):
        g.PhysicalGate.read_status(h.path)
