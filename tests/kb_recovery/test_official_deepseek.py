"""官方读者只接假 HTTP 收据，虚构逐字证据和临时物理账本。"""
import base64
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'deploy/kb'))
import official_deepseek as official
from physical_gate import PhysicalGate, GateUnknown, QuotaExhausted

MODEL = 'deepseek-flash'
TEXT = '虚构灯具功率为12瓦'
SCHEMA = {'type': 'object', 'additionalProperties': False, 'required': ['quote'],
          'properties': {'quote': {'type': 'string', 'maxLength': 50}}}


def packet(value=None, status=200, retry=None):
    if value is None:
        value = {'model': MODEL, 'choices': [{'finish_reason': 'stop',
            'message': {'content': json.dumps({'quote': '功率为12瓦'}, ensure_ascii=False)}}],
            'usage': {'prompt_tokens': 30, 'completion_tokens': 20, 'total_tokens': 50}}
    raw = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False).encode()
    return {'http_status': status, 'body_base64': base64.b64encode(raw).decode(),
            'retry_after_seconds': retry}


def request(value=None, status=200, retry=None):
    reader = official.OfficialDeepSeek(lambda _: packet(value, status, retry), clock=lambda: 10000)
    return reader.request(TEXT, SCHEMA, MODEL)


def evidence(content):
    return json.loads(content)['quote'] in TEXT


def validate(receipt, schema=SCHEMA):
    return official.validate_response(receipt, MODEL, schema, evidence, reservation_tokens=100)


def test_normal_extraction_keeps_requested_actual_usage_and_verbatim_evidence():
    response = validate(request())
    assert json.loads(response['content']) == {'quote': '功率为12瓦'}
    assert response['metadata']['requested_model'] == response['metadata']['actual_model'] == MODEL
    assert response['metadata']['provider'] == 'deepseek_official'
    assert response['metadata']['usage']['total_tokens'] == 50
    assert response['metadata']['attempts'] == 1


@pytest.mark.parametrize('status,code,retry', [
    (401, 'authentication_failed', None), (403, 'authentication_failed', None),
    (402, 'insufficient_balance', None), (429, 'rate_limited', 90),
    (400, 'invalid_response', None), (500, 'invalid_response', None)])
def test_known_http_rejection_preserves_raw_receipt_without_retry(status, code, retry):
    calls = []
    reader = official.OfficialDeepSeek(lambda value: calls.append(value) or packet(
        {'error': {'message': '虚构错误信息'}}, status, retry), clock=lambda: 10000)
    raw = reader.request(TEXT, SCHEMA, MODEL)
    with pytest.raises(official.ReceiptRejected) as error:
        validate(raw)
    assert error.value.code == code
    assert error.value.receipt == raw
    assert error.value.retry_after_seconds == retry
    assert len(calls) == 1


def test_model_name_drift_is_rejected_even_with_valid_json_and_usage():
    value = json.loads(base64.b64decode(packet()['body_base64']))
    value['model'] = 'deepseek-v4-pro'
    with pytest.raises(official.ReceiptRejected) as error:
        validate(request(value))
    assert error.value.code == 'model_drift'


@pytest.mark.parametrize('mutation', [
    lambda v: v['usage'].update(total_tokens=51),
    lambda v: v['usage'].update(prompt_tokens=True),
    lambda v: v['usage'].pop('completion_tokens'),
    lambda v: v['choices'][0].update(finish_reason='length'),
    lambda v: v['choices'][0]['message'].update(tool_calls=[{'function': '虚构函数'}]),
    lambda v: v['choices'][0]['message'].update(content='{"quote":"虚构不存在的证据"}'),
    lambda v: v['choices'][0]['message'].update(content='{"quote":"功率为12瓦","quote":"功率为12瓦"}'),
    lambda v: v['choices'][0]['message'].update(content='{"quote":12}'),
    lambda v: v['choices'][0]['message'].update(content='{"quote":"功率为12瓦","extra":true}'),
])
def test_structured_output_and_verbatim_evidence_fail_closed(mutation):
    value = json.loads(base64.b64decode(packet()['body_base64']))
    mutation(value)
    with pytest.raises(official.ReceiptRejected) as error:
        validate(request(value))
    assert error.value.code == 'invalid_response'


def test_unused_schema_branch_cannot_hide_unsupported_contract():
    schema = deepcopy(SCHEMA)
    schema['properties']['unused'] = {'type': 'string', 'format': 'email'}
    with pytest.raises(official.ReceiptRejected):
        validate(request(), schema)


def gate_config():
    return {'schema': 'kb-physical-gate-v1', 'interval_seconds': 30,
            'models': {MODEL: {'bucket': 'official', 'frozen': False,
                              'cost_micros_per_1m_tokens': 1000000}},
            'buckets': {'official': {'kind': 'prepaid', 'verified': True,
                'max_requests': 10, 'max_tokens': 10000, 'max_cost_micros': 10000}}}


def test_raw_response_is_durable_before_evidence_is_used_and_restart_never_resends(tmp_path):
    tmp_path.chmod(0o700)
    gate = PhysicalGate(tmp_path / 'physical.sqlite3', gate_config(), clock=lambda: 10000)
    item = {'channel': 'wechat', 'source_id': '虚构来源', 'prompt_sha256': 'a' * 64}
    calls = []
    def runner(*args):
        calls.append(1)
        return request()
    def after_saved(item, model, raw):
        with sqlite3.connect(gate.path) as db:
            saved, state = db.execute('SELECT response_json,state FROM gate_calls').fetchone()
        assert state == 'complete' and json.loads(saved) == raw
        return validate(raw)
    first = gate.call(item, MODEL, 100, runner, lambda *_: True, validate_response=after_saved)
    gate = PhysicalGate(gate.path, gate_config(), clock=lambda: 10030)
    second = gate.call(item, MODEL, 100, runner, lambda *_: True, validate_response=after_saved)
    assert first == second and calls == [1]


def test_timeout_is_unknown_and_never_retried_after_restart(tmp_path):
    tmp_path.chmod(0o700)
    gate = PhysicalGate(tmp_path / 'physical.sqlite3', gate_config(), clock=lambda: 10000)
    item = {'channel': 'wechat', 'source_id': '虚构来源', 'prompt_sha256': 'a' * 64}
    calls = []
    def timeout(packet):
        calls.append(1)
        raise subprocess.TimeoutExpired('虚构调用', 45)
    reader = official.OfficialDeepSeek(timeout)
    for attempt in range(2):
        with pytest.raises(GateUnknown):
            gate.call(item, MODEL, 100, lambda *_: reader.request(TEXT, SCHEMA, MODEL),
                      lambda *_: True, validate_response=lambda i, m, r: validate(r))
        gate = PhysicalGate(gate.path, gate_config(), clock=lambda: 10100)
    assert calls == [1]
    assert gate.status()['stop_reason'] == 'unknown'


@pytest.mark.parametrize('status,code', [(402, 'insufficient_balance'), (429, 'rate_limited')])
def test_balance_or_rate_rejection_stops_only_official_bucket(tmp_path, status, code):
    tmp_path.chmod(0o700)
    gate = PhysicalGate(tmp_path / 'physical.sqlite3', gate_config(), clock=lambda: 10000)
    item = {'channel': 'wechat', 'source_id': '虚构来源', 'prompt_sha256': 'a' * 64}
    with pytest.raises(official.ReceiptRejected):
        gate.call(item, MODEL, 100, lambda *_: request({}, status, 90 if status == 429 else None),
                  lambda *_: True, validate_response=lambda i, m, r: validate(r))
    assert gate.status()['stop_reason'] is None
    assert gate.status()['bucket_stops'] == {'official': code}
    with pytest.raises(QuotaExhausted):
        gate.call(item, MODEL, 100, lambda *_: pytest.fail('禁止重发'), lambda *_: True)


def test_balance_is_saved_before_use_and_never_converts_currency():
    calls, saved = [], []
    value = {'is_available': True, 'balance_infos': [
        {'currency': 'CNY', 'total_balance': '12.50', 'granted_balance': '2.50', 'topped_up_balance': '10.00'},
        {'currency': 'USD', 'total_balance': '1.00', 'granted_balance': '0.00', 'topped_up_balance': '1.00'}]}
    reader = official.OfficialDeepSeek(lambda p: calls.append(p) or packet(value), clock=lambda: 10000)
    result = reader.read_balance(currency='CNY', persist_receipt=saved.append)
    assert len(saved) == 1 and result['receipt'] == saved[0]
    assert calls == [{'operation': 'balance', 'timeout_seconds': 15}]
    assert result['currency'] == 'CNY' and result['total_micros'] == 12500000
    assert result['topped_up_micros'] == 10000000


def test_balance_cannot_be_used_when_raw_persistence_fails():
    reader = official.OfficialDeepSeek(lambda _: packet({'is_available': False, 'balance_infos': []}))
    def fail(receipt):
        raise OSError('虚构磁盘失败')
    with pytest.raises(OSError):
        reader.read_balance(currency='CNY', persist_receipt=fail)


def test_subprocess_is_single_fixed_argv_and_task_stays_on_stdin(monkeypatch):
    calls = []
    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=0, stdout=json.dumps(packet()))
    monkeypatch.setattr(official.subprocess, 'run', run)
    reader = official.OfficialDeepSeek(clock=lambda: 10000)
    validate(reader.request(TEXT, SCHEMA, MODEL))
    assert len(calls) == 1
    argv, kwargs = calls[0]
    assert argv == [sys.executable, '-I', str(Path(official.__file__).absolute()), '--worker']
    assert TEXT not in ' '.join(argv) and json.loads(kwargs['input'])['prompt'] == TEXT
    assert kwargs['timeout'] == 45 and kwargs['check'] is False and 'shell' not in kwargs


def test_reservation_uses_complete_frozen_request_payload_without_loading_provider(monkeypatch):
    def blocked(*args, **kwargs):
        pytest.fail('估算不得加载模型客户端或调用传输')
    monkeypatch.setattr(official.importlib.util, 'spec_from_file_location', blocked)
    monkeypatch.setattr(official.subprocess, 'run', blocked)
    expected = {'model': MODEL, 'messages': [
        {'role': 'system', 'content': 'Only return a JSON object conforming to this JSON Schema:\n' +
            json.dumps(SCHEMA, ensure_ascii=False, sort_keys=True, separators=(',', ':'))},
        {'role': 'user', 'content': TEXT}], 'stream': False, 'max_tokens': 4096,
        'temperature': 0, 'response_format': {'type': 'json_object'}, 'thinking': {'type': 'disabled'}}
    actual = official.reservation_tokens(TEXT, SCHEMA, MODEL)
    assert actual == len(json.dumps(expected, ensure_ascii=False, separators=(',', ':')).encode()) + 4096 + 1024
    assert actual > len(TEXT.encode()) + 4096 + 1024
    assert official.reservation_tokens(TEXT, SCHEMA, MODEL, overhead_tokens=2048) == actual + 1024
