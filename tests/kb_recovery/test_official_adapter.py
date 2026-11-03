"""官方读者接真实提取、证据校验及 Catalog 函数；仅使用虚构数据。"""
import base64
from copy import deepcopy
import json
from pathlib import Path
import socket
import sqlite3
import sys

import pytest

HERE = Path(__file__).resolve().parents[2] / 'deploy/kb'
sys.path.insert(0, str(HERE))
import official_deepseek as official
import physical_gate as g
import production_adapter as adapter
from test_production_adapter import alibaba, wechat


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError('虚构集成测试禁止连接网络')
    monkeypatch.setattr(socket.socket, 'connect', blocked)
    monkeypatch.setattr(socket, 'create_connection', blocked)


def fake_service(content):
    requests = []
    def runner(packet):
        requests.append(deepcopy(packet))
        assert packet['operation'] == 'completion'
        body = {'model': packet['model'], 'choices': [{'finish_reason': 'stop',
            'message': {'content': json.dumps(content, ensure_ascii=False)}}],
            'usage': {'prompt_tokens': 20, 'completion_tokens': 30, 'total_tokens': 50}}
        return {'http_status': 200, 'body_base64': base64.b64encode(
            json.dumps(body, ensure_ascii=False).encode()).decode(), 'retry_after_seconds': None}
    return official.OfficialDeepSeek(runner, clock=lambda: 10000), requests


def physical(tmp_path):
    folder = tmp_path / 'official-gate'
    folder.mkdir(mode=0o700)
    clock = [10000]
    config = {'schema': 'kb-physical-gate-v1', 'interval_seconds': 30,
        'models': {model: {'bucket': 'official', 'frozen': False,
                          'cost_micros_per_1m_tokens': 1000000} for model in official.MODELS},
        'buckets': {'official': {'kind': 'prepaid', 'verified': True, 'max_requests': 5,
                    'max_tokens': 1000000, 'max_cost_micros': 1000000}}}
    return g.PhysicalGate(folder / 'calls.sqlite3', config, clock=lambda: clock[0]), clock


def request(gate, value, item, model):
    return gate.call(item, model, 65536, value.request, value.check_request,
                     validate_response=value.validate_response)


def test_wechat_official_two_readers_keep_verbatim_fact_and_reuse_saved_raw(wechat, tmp_path):
    f, value = wechat
    f.add(1, 5000, text='虚构灯具 SYN-7 功率200W')
    item, = value.discover_live(1000)
    fact = {'msg': 1, 'type': 'spec', 'attr': 'power_w',
            'product': '虚构灯具', 'model': 'SYN-7', 'quote': '功率200W'}
    value.official, calls = fake_service({'facts': [fact]})
    gate, now = physical(tmp_path)
    first = request(gate, value, item, 'deepseek-v4-pro')
    now[0] += 30
    second = request(gate, value, item, 'deepseek-flash')
    value.apply_local(item, [first, second])
    value.apply_local(item, [first, second])
    assert len(calls) == 2 and f.requests == []
    assert f.db.execute('SELECT count(*) FROM kb_fact').fetchone()[0] == 1
    cached = request(gate, value, item, 'deepseek-v4-pro')
    assert cached == first and len(calls) == 2
    with sqlite3.connect(gate.path) as db:
        rows = db.execute('SELECT response_json FROM gate_calls').fetchall()
    assert len(rows) == 2
    assert all(json.loads(row[0])['schema'] == official.RECEIPT_SCHEMA for row in rows)


def test_wechat_non_verbatim_official_fact_is_refused_after_raw_saved(wechat, tmp_path):
    f, value = wechat
    f.add(1, 5000, text='虚构灯具 SYN-7 功率200W')
    item, = value.discover_live(1000)
    value.official, calls = fake_service({'facts': [{'msg': 1, 'type': 'spec', 'attr': 'power_w',
        'product': '虚构灯具', 'model': 'SYN-7', 'quote': '功率999W'}]})
    gate, _ = physical(tmp_path)
    with pytest.raises(g.ProviderRejected) as caught:
        request(gate, value, item, 'deepseek-v4-pro')
    assert caught.value.code == 'invalid_response'
    assert len(calls) == 1 and f.requests == []
    assert f.db.execute('SELECT count(*) FROM kb_fact').fetchone()[0] == 0
    with sqlite3.connect(gate.path) as db:
        state, raw = db.execute('SELECT state,response_json FROM gate_calls').fetchone()
    assert state == 'provider_rejected' and json.loads(raw)['provider'] == 'deepseek_official'
    assert gate.status()['stop_reason'] is None


def test_alibaba_official_valid_claim_uses_original_catalog_plan_idempotently(alibaba, tmp_path):
    f = alibaba
    f.value.official, calls = fake_service(json.loads(f.reply()['content']))
    gate, _ = physical(tmp_path)
    reply = request(gate, f.value, f.item, 'deepseek-flash')
    f.value.apply_local(f.item, [reply])
    f.value.apply_local(f.item, [reply])
    assert len(calls) == 1 and f.calls == []
    assert f.frozen_checks
    with sqlite3.connect(f.catalog_path) as db:
        raw, = db.execute('SELECT extraction_snapshot_json FROM multimedia_assets').fetchone()
        document = json.loads(raw)
        assert db.execute('SELECT count(*) FROM multimedia_assets').fetchone()[0] == 1
        assert db.execute('SELECT count(*) FROM knowledge_chunks').fetchone()[0] == 1
    assert document['recovery_execution']['requested_model'] == 'deepseek-flash'
    assert document['recovery_execution']['provider_metadata']['provider'] == 'deepseek_official'
    assert document['analysis_scope'] == 'single_source_recovery_not_complete_conversation'
    assert document['model_fingerprint'] == adapter.digest({
        'contract': 'kb-official-deepseek-v1', 'model': 'deepseek-flash',
        'schema_sha256': adapter.digest(f.value.pool.SCHEMA),
        'source_sha256': adapter.digest(official.PINS)})
    assert document['auto_send'] is False and document['current_quote_verified'] is False


def test_alibaba_official_unbound_evidence_is_refused_before_catalog(alibaba, tmp_path):
    f = alibaba
    content = json.loads(f.reply()['content'])
    content['claims'][0]['evidence_ids'] = ['fictional_missing_evidence']
    f.value.official, calls = fake_service(content)
    gate, _ = physical(tmp_path)
    with pytest.raises(g.ProviderRejected) as caught:
        request(gate, f.value, f.item, 'deepseek-flash')
    assert caught.value.code == 'invalid_response'
    assert len(calls) == 1 and f.calls == []
    with sqlite3.connect(f.catalog_path) as db:
        assert db.execute('SELECT count(*) FROM multimedia_assets').fetchone()[0] == 0
        assert db.execute('SELECT count(*) FROM knowledge_chunks').fetchone()[0] == 0
    with sqlite3.connect(gate.path) as db:
        state, raw = db.execute('SELECT state,response_json FROM gate_calls').fetchone()
    assert state == 'provider_rejected' and json.loads(raw)['provider'] == 'deepseek_official'
    assert gate.status()['stop_reason'] is None
