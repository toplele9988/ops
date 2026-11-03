"""官方余额采用真实适配契约、假 HTTP 回执和临时账本，不访问账户。"""
import base64
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'deploy/kb'))
from backlog import ReviewError
from official_deepseek import OfficialDeepSeek
from physical_gate import PhysicalGate, GateError, QuotaExhausted
from production_adapter import AdapterHub


def config(rate=1000000):
    return {'schema': 'kb-physical-gate-v1', 'interval_seconds': 30,
        'models': {
            'deepseek-flash': {'bucket': 'official', 'frozen': False,
                               'cost_micros_per_1m_tokens': rate},
            'deepseek-v4-pro': {'bucket': 'official', 'frozen': False,
                               'cost_micros_per_1m_tokens': rate},
            'fictional-free': {'bucket': 'free', 'frozen': False,
                               'cost_micros_per_1m_tokens': None}},
        'buckets': {
            'official': {'kind': 'prepaid', 'verified': True, 'max_requests': 20,
                         'max_tokens': 10000, 'max_cost_micros': 10000},
            'free': {'kind': 'free_pool', 'verified': True, 'max_requests': 20,
                     'max_tokens': 10000, 'max_cost_micros': None}}}


def reply(status=200, *, amount='0.000250', available=True, currency='CNY'):
    body = {'is_available': available, 'balance_infos': [
        {'currency': currency, 'total_balance': amount,
         'granted_balance': '0', 'topped_up_balance': amount}]}
    return {'http_status': status, 'body_base64': base64.b64encode(
        json.dumps(body).encode()).decode(), 'retry_after_seconds': 120 if status == 429 else None}


def harness(tmp_path, *, status=200, runner=None, currency='CNY', amount='0.000250', available=True):
    tmp_path.chmod(0o700)
    now, queries = [10000], []
    def fake(packet):
        queries.append(packet)
        return runner(packet) if runner else reply(status, amount=amount, available=available)
    gate = PhysicalGate(tmp_path / 'physical.sqlite3', config(), clock=lambda: now[0])
    official = OfficialDeepSeek(fake, clock=lambda: now[0])
    hub = AdapterHub({}, {}, official=official, official_options={
        'bucket': 'official', 'balance_mode': 'api', 'currency': currency})
    return gate, hub, queries, now


def item(number):
    return {'channel': 'wechat', 'source_id': '虚构来源' + str(number),
            'prompt_sha256': str(number) * 64}


def events(gate):
    with sqlite3.connect(gate.path) as db:
        return [(kind, json.loads(payload)) for kind, payload in
                db.execute('SELECT kind,payload_json FROM gate_audit ORDER BY seq')]


def test_api_balance_is_read_each_round_and_raw_is_saved_before_admission(tmp_path, monkeypatch):
    gate, hub, queries, now = harness(tmp_path)
    original = gate.record_balance
    checked = []
    def after_raw(bucket, balance):
        prior = events(gate)
        assert prior[-1][0] == 'balance_receipt'
        assert prior[-1][1]['receipt'] == balance['receipt']
        with sqlite3.connect(gate.path) as db:
            assert db.execute('SELECT count(*) FROM gate_balances').fetchone()[0] == len(checked)
        checked.append(balance['currency'])
        original(bucket, balance)
    monkeypatch.setattr(gate, 'record_balance', after_raw)
    hub.refresh_balance(gate)
    now[0] += 30
    hub.refresh_balance(gate)
    assert queries == [{'operation': 'balance', 'timeout_seconds': 15}] * 2
    assert checked == ['CNY', 'CNY']
    assert [kind for kind, _ in events(gate)] == [
        'balance_receipt', 'balance_checked', 'balance_receipt', 'balance_checked']


def test_two_official_models_share_balance_and_conservative_cost_reservations(tmp_path):
    gate, hub, queries, now = harness(tmp_path)
    hub.refresh_balance(gate)
    sent = []
    def fake(source, model, reserve):
        sent.append(model)
        return {'content': '虚构已保存响应'}
    gate.call(item(1), 'deepseek-flash', 100, fake, lambda *_: True)
    now[0] += 30
    gate.call(item(2), 'deepseek-v4-pro', 100, fake, lambda *_: True)
    now[0] += 30
    with pytest.raises(QuotaExhausted, match='余额不足'):
        gate.call(item(3), 'deepseek-flash', 100, fake, lambda *_: True)
    assert sent == ['deepseek-flash', 'deepseek-v4-pro'] and len(queries) == 1
    status = gate.status()
    assert status['buckets']['official']['reserved_cost_micros'] == 200
    assert status['buckets']['official']['reserved_tokens'] == 200
    assert status['stop_reason'] is None
    gate.call(item(4), 'fictional-free', 100, fake, lambda *_: True)
    assert sent[-1] == 'fictional-free'


@pytest.mark.parametrize('status,reason', [
    (402, 'insufficient_balance'), (401, 'authentication_failed'), (429, 'rate_limited')])
def test_balance_rejection_stops_only_official_and_does_not_query_again(tmp_path, status, reason):
    gate, hub, queries, now = harness(tmp_path, status=status)
    hub.refresh_balance(gate)
    now[0] += 300
    hub.refresh_balance(gate)
    assert len(queries) == 1
    assert gate.status()['stop_reason'] is None
    assert gate.status()['bucket_stops'] == {'official': reason}
    assert [kind for kind, _ in events(gate)] == ['balance_receipt', 'balance_rejected']
    if status == 429:
        assert events(gate)[-1][1]['retry_after_seconds'] == 120
    gate.call(item(1), 'fictional-free', 100, lambda *_: {'content': '虚构免费响应'}, lambda *_: True)
    with pytest.raises(QuotaExhausted):
        gate.call(item(2), 'deepseek-flash', 100, lambda *_: pytest.fail('官方桶已停'), lambda *_: True)


def test_zero_available_balance_stops_without_model_request_or_repeated_query(tmp_path):
    gate, hub, queries, now = harness(tmp_path, amount='0', available=False)
    hub.refresh_balance(gate)
    hub.refresh_balance(gate)
    assert len(queries) == 1
    assert gate.status()['bucket_stops'] == {'official': 'insufficient_balance'}
    with sqlite3.connect(gate.path) as db:
        assert db.execute('SELECT count(*) FROM gate_calls').fetchone()[0] == 0


def test_balance_get_timeout_conservatively_stops_whole_team(tmp_path):
    def timeout(packet):
        raise subprocess.TimeoutExpired('虚构只读余额查询', 15)
    gate, hub, queries, now = harness(tmp_path, runner=timeout)
    with pytest.raises(ReviewError, match='已停止全队'):
        hub.refresh_balance(gate)
    assert len(queries) == 1 and gate.status()['stop_reason'] == 'unknown'
    with sqlite3.connect(gate.path) as db:
        assert db.execute('SELECT count(*) FROM gate_calls').fetchone()[0] == 0
    with pytest.raises(GateError):
        gate.call(item(1), 'fictional-free', 100, lambda *_: pytest.fail('全队已停'), lambda *_: True)


def test_null_currency_cannot_query_or_guess_exchange_rate(tmp_path):
    gate, hub, queries, now = harness(tmp_path, currency=None)
    with pytest.raises(ReviewError, match='币种尚未核实'):
        hub.refresh_balance(gate)
    assert queries == [] and events(gate) == []


def test_prepaid_rate_zero_cannot_admit_a_request(tmp_path):
    tmp_path.chmod(0o700)
    with pytest.raises(GateError):
        gate = PhysicalGate(tmp_path / 'physical.sqlite3', config(rate=0), clock=lambda: 10000)
        gate.call(item(1), 'deepseek-flash', 100, lambda *_: pytest.fail('零费率不得发送'), lambda *_: True)
