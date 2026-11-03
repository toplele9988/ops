"""虚构提示词与来源：估算不输出原文，未知别名和不完整文档不计入预算。"""
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys

import pytest

from test_inventory_wechat import synthetic_snapshot

HERE = Path(__file__).resolve().parents[2] / 'deploy/kb'
if str(HERE) not in sys.path:
    sys.path.append(str(HERE))
SPEC = importlib.util.spec_from_file_location('kb_estimate_test', HERE / 'estimate_usage.py')
e = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(e)


def snapshot_with_fingerprints(tmp_path):
    snapshot = synthetic_snapshot(tmp_path)
    with sqlite3.connect(snapshot / 'catalog.sqlite3') as db:
        db.execute('ALTER TABLE channel_business_evidence ADD COLUMN source_sha256')
        db.execute('UPDATE channel_business_evidence SET source_sha256=?', (e.wechat.sha('虚构原始指纹'),))
    return snapshot


def test_aggregate_has_no_source_text_and_counts_each_reader_output():
    prompts = ['虚构私有资料' * 10, '虚构私有资料' * 100]
    result = e.summarize(prompts, readers=2, output_tokens=4096)
    assert result['requests'] == 4 and result['prompt_characters']['count'] == 2
    assert result['output_tokens_reserved'] == 4 * 4096
    assert result['physical_reservation_tokens'] >= result['total_tokens_high'] >= result['total_tokens_low']
    assert '虚构私有资料' not in json.dumps(result, ensure_ascii=False)
    assert result['prompt_characters']['p95'] == len(prompts[1])


def test_full_request_schema_and_system_message_are_reserved():
    payload, schemas = e.request_contracts()
    prompt = '虚构资料'
    body = payload(prompt, schemas[0], {'provider': 'deepseek', 'model': 'deepseek-v4-pro'})
    assert body['thinking'] == {'type': 'disabled'}
    assert body['messages'][1]['content'] == prompt
    assert json.loads(body['messages'][0]['content'].split('\n', 1)[1]) == schemas[0]
    size = len(json.dumps(body, ensure_ascii=False).encode())
    result = e.summarize([prompt], readers=2, output_tokens=4096, payload_bytes=[size])
    assert result['physical_reservation_tokens'] == 2 * (size + 4096 + 1024)
    assert result['physical_reservation_tokens'] > result['prompt_only_physical_reservation_tokens']


def test_request_contract_rejects_source_change_before_loading(tmp_path, monkeypatch):
    source = tmp_path / 'fictional-client.py'
    source.write_text('raise AssertionError("不能加载")\n')
    monkeypatch.setattr(e, 'CLIENT', source)
    with pytest.raises(ValueError):
        e.request_contracts()


def test_new_source_gets_original_complete_prompt(tmp_path):
    snapshot = snapshot_with_fingerprints(tmp_path)
    prompts, counts = e.wechat_prompts(snapshot, [], 1)
    assert counts['sources'] == 1 and len(prompts) == 1
    assert prompts[0] == e.wechat.algorithms()['PROMPT'] + '[1] 虚构参数3'


@pytest.mark.parametrize('field', ['source_keys', 'source_sha256', 'content_sha256'])
def test_new_identifier_is_excluded_if_it_crosses_any_unknown_fingerprint(tmp_path, field):
    snapshot = snapshot_with_fingerprints(tmp_path)
    hold = {'source_keys': [], 'source_sha256': [], 'content_sha256': []}
    hold[field] = [{'source_keys': e.wechat.sha('fictional-source-3'),
                    'source_sha256': e.wechat.sha('虚构原始指纹'),
                    'content_sha256': e.wechat.sha('虚构参数3')}[field]]
    prompts, counts = e.wechat_prompts(snapshot, [hold], 0)
    assert prompts == [] and counts['excluded']['unknown_cross'] == 1


def test_capped_document_is_excluded_instead_of_counting_twelve_blocks(tmp_path):
    snapshot = snapshot_with_fingerprints(tmp_path)
    with sqlite3.connect(snapshot / 'catalog.sqlite3') as db:
        db.execute('UPDATE channel_business_evidence SET body_text=? WHERE evidence_id=?',
                   ('虚构完整性待核参数\n' * 5000, 'fictional-source-3'))
    prompts, counts = e.wechat_prompts(snapshot, [], 0)
    assert prompts == [] and counts['excluded']['document_incomplete'] == 1


def test_changed_candidate_count_fails_instead_of_reusing_old_budget(tmp_path):
    snapshot = snapshot_with_fingerprints(tmp_path)
    with pytest.raises(ValueError):
        e.wechat_prompts(snapshot, [], 1600)


@pytest.mark.parametrize('option', [{'factor_high': float('nan')}, {'factor_high': .5},
                                  {'input_margin': .9}, {'wrapper_tokens': -1}])
def test_invalid_estimate_assumptions_fail(option):
    with pytest.raises(ValueError):
        e.summarize(['虚构文本'], readers=2, output_tokens=4096, **option)
