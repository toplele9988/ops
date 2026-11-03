"""只用虚构资料核对单来源计划稳定性、原文覆盖和旧未知行绑定。"""
from copy import deepcopy
import importlib.util
from pathlib import Path
import sqlite3
import sys

import pytest

HERE = Path(__file__).resolve().parents[2] / 'deploy/kb'
if str(HERE) not in sys.path:
    sys.path.append(str(HERE))
SPEC = importlib.util.spec_from_file_location('kb_provenance_test', HERE / 'provenance.py')
p = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(p)


def fictional_group(body='虚构灯具技术资料，不代表实际产品。'):
    core = p.alibaba_algorithms()
    r = {'inbound_id': 'fictional-inbound', 'source_sha256': p.sha('虚构原始来源'),
         'account': 'fictional-account', 'conversation_id': '123456', 'role': 'seller',
         'body': body, 'sent_at': '2026-01-01T00:00:00+00:00',
         'received_at': '2026-01-01T00:00:01+00:00', 'scope': 'fictional-scope',
         'message_id': 'fictional-message', 'receiver_fingerprint': p.sha('虚构接收指纹')}
    g = {'owner': 'fictional-owner', 'account': r['account'], 'conversation_id': r['conversation_id'],
         'records': [r], 'source_version': core.record_version([r])}
    g['key'] = core.group_key(g['owner'], g['account'], g['conversation_id'])
    return core, g, r


def test_single_source_plan_is_stable_when_conversation_grows():
    core, group, record = fictional_group()
    unit = p.alibaba_single_source(group, record, core=core)
    later = dict(record, inbound_id='fictional-later', body='虚构后续消息')
    group['records'].append(later)
    group['source_version'] = core.record_version(group['records'])
    assert p.alibaba_single_source(group, record, core=core) == unit
    assert len(unit['window']['segments']) == 1
    assert core.validate_coverage(unit['group']['records'], [unit['window']['evidence_spans']])['complete']
    assert unit['prompt_sha256'] == p.sha(core.prompt_for(unit['group'], unit['window']))


def test_single_source_accepts_full_eight_thousand_and_rejects_tail():
    core, group, record = fictional_group('虚' * 8000)
    unit = p.alibaba_single_source(group, record, core=core)
    assert sum(len(s['text']) for s in unit['window']['segments']) == 8000
    record['body'] += '构'
    with pytest.raises(ValueError):
        p.alibaba_single_source(group, record, core=core)


@pytest.mark.parametrize('field,value', [('key', 'f' * 64), ('owner', 'different-fictional-owner')])
def test_single_source_rejects_mismatched_owner_binding(field, value):
    core, group, record = fictional_group()
    group[field] = value
    with pytest.raises(ValueError):
        p.alibaba_single_source(group, record, core=core)


def test_document_checks_tail_without_altering_original_twelve_chunks():
    ns = p.wechat.algorithms()
    row = {'evidence_id': 'fictional-document', 'chatroom_id': 'fictional-room',
           'source_created_at': 1000, 'received_at': 1001,
           'body_text': '\n'.join('虚构技术参数' + str(n) + 'x' * 190 for n in range(150))}
    before = deepcopy(ns['_document_units'](row))
    result = p.document_completeness(row, algorithms=ns)
    assert result['omitted_chunks'] > 0 and result['omitted_characters'] > 0
    assert result['decision'] == 'exclude_entire_document'
    assert ns['DOC_CHUNKS'] == 12
    assert ns['_document_units'](row) == before


def test_document_at_limit_does_not_invent_missing_tail():
    ns = p.wechat.algorithms()
    row = {'evidence_id': 'fictional-document', 'chatroom_id': 'fictional-room',
           'source_created_at': 1000, 'received_at': 1001, 'body_text': '虚构完整文档' * 200}
    result = p.document_completeness(row, algorithms=ns)
    assert result['omitted_chunks'] == result['omitted_characters'] == 0
    assert result['decision'] == 'complete_within_limit'


def test_unknown_checklist_binds_all_original_call_columns(tmp_path):
    path = tmp_path / 'private'
    path.mkdir(mode=0o700)
    calls = path / 'calls.sqlite3'
    calls.touch(mode=0o600)
    review = {'wechat_holds': []}
    with sqlite3.connect(calls) as db:
        db.execute('''CREATE TABLE calls(cache_key,attempt,prompt_sha256,model,state,error_code,
            day,reserved_tokens,response_json,response_sha256,created_at,updated_at)''')
        db.execute('CREATE TABLE scope(key,value)')
        db.execute("INSERT INTO scope VALUES('hold','api_timeout')")
        for n in range(3):
            r = (p.sha('虚构缓存' + str(n)), 1, p.sha('虚构提示词' + str(n)),
                 'fictional-model-' + str(n), 'hold', 'api_timeout', 1, 8000 + n, None, None, 1000 + n, 1050 + n)
            db.execute('INSERT INTO calls VALUES(?,?,?,?,?,?,?,?,?,?,?,?)', r)
            review['wechat_holds'].append({'request_sha256': p.sha(p.canonical(r)),
                'prompt_sha256': r[2], 'source_keys': [p.sha('虚构来源' + str(n))]})
    checklist, counts = p.wechat_unknowns(path, review)
    assert len(checklist) == 3 and counts == {'hold': 3}
    assert '虚构' not in p.canonical(checklist)
    with sqlite3.connect(calls) as db:
        db.execute('UPDATE calls SET reserved_tokens=reserved_tokens+1 WHERE model=?', ('fictional-model-1',))
    with pytest.raises(ValueError):
        p.wechat_unknowns(path, review)


def test_algorithm_pin_rejects_changed_source(tmp_path, monkeypatch):
    path = tmp_path / 'fictional.py'
    path.write_text('raise AssertionError("不允许执行")\n')
    monkeypatch.setattr(p, 'CORE', path)
    with pytest.raises(ValueError):
        p.alibaba_algorithms()
