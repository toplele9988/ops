"""调用历史围栏只读取虚构私有账本，绝不接触模型和真实业务库。"""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys

import pytest

HERE = Path(__file__).resolve().parents[2] / 'deploy/kb'
sys.path.insert(0, str(HERE))
from history_fence import HistoryFence, HistoryFenceError
from physical_gate import PhysicalGate, QuotaExhausted, canon, digest
from test_physical_gate import config, item


SCHEMA = '''CREATE TABLE calls(
    cache_key TEXT NOT NULL,attempt INTEGER NOT NULL,prompt_sha256 TEXT NOT NULL,
    model TEXT NOT NULL,state TEXT NOT NULL,error_code TEXT,day INTEGER NOT NULL,
    reserved_tokens INTEGER NOT NULL,response_json TEXT,response_sha256 TEXT,
    created_at REAL NOT NULL,updated_at REAL NOT NULL,PRIMARY KEY(cache_key,attempt));
    CREATE TABLE scope(key TEXT PRIMARY KEY,value TEXT NOT NULL);'''


def add_call(path, *, key=None, model='fictional-free', prompt=None, state='complete',
             raw=None, at=10000, reserve=100, attempt=1, error_code='api_timeout'):
    key = key or digest('虚构新增缓存')
    raw = raw if raw is not None else canon({'content': '虚构响应', 'model': model})
    response_sha = hashlib.sha256(raw.encode()).hexdigest()
    with sqlite3.connect(path) as db:
        db.execute('INSERT INTO calls VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                   (key, attempt, prompt or item()['prompt_sha256'], model, state,
                    None if state == 'complete' else error_code, 0, reserve,
                    raw if state == 'complete' else None,
                    response_sha if state == 'complete' else None, at, at))
    return key


class Harness:
    def __init__(self, tmp_path):
        self.directory = tmp_path / 'history-private'
        self.directory.mkdir(mode=0o700)
        self.current = self.directory / 'calls.sqlite3'
        self.approved = self.directory / 'approved-calls.sqlite3'
        self.gate_path = self.directory / 'gate.sqlite3'
        with sqlite3.connect(self.current) as db:
            db.executescript(SCHEMA)
            db.execute("INSERT INTO scope VALUES('hold','api_timeout')")
        self.current.chmod(0o600)
        add_call(self.current, key=digest('虚构旧成功'), at=10)
        add_call(self.current, key=digest('虚构旧未知'), state='hold', at=20)
        with sqlite3.connect(self.current) as db, sqlite3.connect(self.approved) as dest:
            db.backup(dest)
        self.approved.chmod(0o600)
        self.pin = hashlib.sha256(self.approved.read_bytes()).hexdigest()
        self.fence = HistoryFence(self.approved, self.pin)
        self.gate = None

    def legitimate_extra(self):
        self.gate = PhysicalGate(self.gate_path, config(), clock=lambda: 10000)
        value = item()
        model = 'fictional-free'
        response = self.gate.call(value, model, 100,
                                 lambda *a: {'content': '虚构响应', 'model': model}, lambda *a: True)
        raw = canon(response)
        proof = {'cache_key': digest('虚构新增缓存'), 'model': model,
                 'prompt_sha256': value['prompt_sha256'], 'item': value,
                 'response_json': raw, 'response_sha256': hashlib.sha256(raw.encode()).hexdigest(),
                 'gate_request_key': digest({'item': value, 'model': model})}
        add_call(self.current, key=proof['cache_key'], raw=raw)
        self.fence.set_gate(self.gate_path)
        return proof

    def quota_extra(self):
        self.now = 10000
        self.gate = PhysicalGate(self.gate_path, config(), clock=lambda: self.now)
        value, model = item(), 'fictional-free'
        key = digest('虚构新增缓存')
        def rejected(*args):
            add_call(self.current, key=key, state='quota_rejected', error_code='qwen_free_tier_exhausted')
            raise QuotaExhausted('虚构服务明确免费额度已用完')
        with pytest.raises(QuotaExhausted):
            self.gate.call(value, model, 100, rejected, lambda *a: True)
        self.fence.set_gate(self.gate_path)
        return {'cache_key': key, 'model': model, 'prompt_sha256': value['prompt_sha256'],
                'item': value, 'response_json': None, 'response_sha256': None,
                'gate_request_key': digest({'item': value, 'model': model})}


def test_unchanged_original_rows_hold_and_schema_are_readonly(tmp_path):
    h = Harness(tmp_path)
    before = {p.name: (p.stat().st_mtime_ns, p.read_bytes()) for p in h.directory.iterdir()}
    assert h.fence.verify(h.current)
    assert h.fence.last_request_at == 20
    assert before == {p.name: (p.stat().st_mtime_ns, p.read_bytes()) for p in h.directory.iterdir()}


@pytest.mark.parametrize('created_at', [20, 19, None, 0, float('inf'), float('nan'), True])
def test_never_requested_requires_each_source_admitted_after_all_old_calls(tmp_path, created_at):
    h = Harness(tmp_path)
    with pytest.raises(HistoryFenceError, match='不能证明从未请求'):
        h.fence.assert_never_requested(created_at)
    assert h.fence.assert_never_requested(20.001)


@pytest.mark.parametrize('mutation', [
    "DELETE FROM calls WHERE state='complete'",
    "UPDATE calls SET updated_at=99 WHERE state='complete'",
    "UPDATE calls SET state='complete' WHERE state='hold'",
    "UPDATE calls SET reserved_tokens=1 WHERE state='hold'",
    "UPDATE calls SET response_json='{}' WHERE state='complete'",
])
def test_original_full_rows_cannot_be_deleted_or_rewritten(tmp_path, mutation):
    h = Harness(tmp_path)
    with sqlite3.connect(h.current) as db:
        db.execute(mutation)
    with pytest.raises(HistoryFenceError, match='原请求记录缺失或被修改'):
        h.fence.verify(h.current)


def test_old_hold_cannot_be_cleared_or_changed(tmp_path):
    h = Harness(tmp_path)
    with sqlite3.connect(h.current) as db:
        db.execute('DELETE FROM scope')
    with pytest.raises(HistoryFenceError, match='原 hold'):
        h.fence.verify(h.current)


@pytest.mark.parametrize('state', ['provider_started', 'hold', 'quota_rejected', 'budget_wait'])
def test_added_unknown_or_noncomplete_call_is_never_accepted(tmp_path, state):
    h = Harness(tmp_path)
    add_call(h.current, state=state)
    with pytest.raises(HistoryFenceError, match='不是可靠完成'):
        h.fence.verify(h.current)


def test_other_worker_complete_call_cannot_be_claimed_as_new_source(tmp_path):
    h = Harness(tmp_path)
    add_call(h.current)
    with pytest.raises(HistoryFenceError, match='未经本协调器准入'):
        h.fence.verify(h.current)


def test_only_exact_coordinator_gate_receipt_allows_added_complete_row(tmp_path):
    h = Harness(tmp_path)
    proof = h.legitimate_extra()
    before = {p.name: p.read_bytes() for p in h.directory.iterdir()}
    assert h.fence.verify(h.current, allowed=[proof])
    assert before == {p.name: p.read_bytes() for p in h.directory.iterdir()}


def test_exact_known_quota_receipt_stops_only_free_account_and_paid_can_continue(tmp_path):
    h = Harness(tmp_path)
    proof = h.quota_extra()
    assert h.fence.verify(h.current, allowed=[proof])
    assert h.gate.status()['bucket_stops'] == {'free': 'quota_exhausted'}
    assert h.gate.status()['stop_reason'] is None
    called = []
    h.now += 30
    h.gate.call(item(2, 'alibaba'), 'fictional-paid', 100,
                lambda *a: (called.append('虚构付费请求'), {'content': '虚构响应'})[-1], lambda *a: True)
    assert called == ['虚构付费请求']
    assert h.fence.verify(h.current, allowed=[proof])
    with pytest.raises(QuotaExhausted):
        h.gate.call(item(), 'fictional-free', 100,
                    lambda *a: pytest.fail('明确额度拒绝的请求不能重发'), lambda *a: True)


@pytest.mark.parametrize('field,value', [
    ('error_code', 'api_timeout'), ('state', 'budget_wait'), ('state', 'hold'),
    ('state', 'provider_started'), ('attempt', 2), ('reserved_tokens', 101),
    ('model', 'fictional-other'), ('prompt_sha256', digest('虚构另一提示词')),
    ('response_json', '{}'), ('response_sha256', digest('虚构响应')),
])
def test_quota_proof_does_not_authorize_unknown_retry_or_mismatched_native_row(tmp_path, field, value):
    h = Harness(tmp_path)
    proof = h.quota_extra()
    with sqlite3.connect(h.current) as db:
        db.execute('UPDATE calls SET ' + field + '=? WHERE created_at=10000', (value,))
    with pytest.raises(HistoryFenceError):
        h.fence.verify(h.current, allowed=[proof])


def test_null_response_proof_cannot_relabel_success_or_unknown_gate_as_quota_rejection(tmp_path):
    h = Harness(tmp_path)
    proof = h.legitimate_extra()
    proof.update(response_json=None, response_sha256=None)
    with sqlite3.connect(h.current) as db:
        db.execute("UPDATE calls SET state='quota_rejected',error_code='qwen_free_tier_exhausted',"
                   'response_json=NULL,response_sha256=NULL WHERE created_at=10000')
    with pytest.raises(HistoryFenceError, match='完整响应'):
        h.fence.verify(h.current, allowed=[proof])
    # Changing only the mutable gate row is insufficient: the original audit
    # still proves a saved response rather than a known provider rejection.
    with sqlite3.connect(h.gate_path) as db:
        db.execute("UPDATE gate_calls SET state='quota_rejected',response_json=NULL,response_sha256=NULL")
    with pytest.raises(HistoryFenceError, match='额度拒绝缺少原审计'):
        h.fence.verify(h.current, allowed=[proof])


@pytest.mark.parametrize('field', ['cache_key', 'model', 'prompt_sha256', 'response_json', 'response_sha256'])
def test_extra_calls_model_prompt_and_response_bytes_must_match_exactly(tmp_path, field):
    h = Harness(tmp_path)
    proof = h.legitimate_extra()
    changed = 'fictional-other' if field == 'model' else '{}' if field == 'response_json' else digest('虚构篡改')
    with sqlite3.connect(h.current) as db:
        db.execute('UPDATE calls SET ' + field + '=? WHERE created_at=10000', (changed,))
    with pytest.raises(HistoryFenceError):
        h.fence.verify(h.current, allowed=[proof])


@pytest.mark.parametrize('field', ['item', 'gate_request_key', 'response_json', 'response_sha256'])
def test_proof_cannot_claim_unrelated_or_modified_gate_receipt(tmp_path, field):
    h = Harness(tmp_path)
    proof = h.legitimate_extra()
    if field == 'item':
        proof['item'] = deepcopy(proof['item'])
        proof['item']['source_id'] += '虚构另一份'
    elif field == 'response_json':
        proof[field] = '{}'
        proof['response_sha256'] = hashlib.sha256(b'{}').hexdigest()
    else:
        proof[field] = digest('虚构另一份')
    with pytest.raises(HistoryFenceError):
        h.fence.verify(h.current, allowed=[proof])


def test_gate_unknown_or_missing_receipt_cannot_authorize_extra_row(tmp_path):
    h = Harness(tmp_path)
    proof = h.legitimate_extra()
    with sqlite3.connect(h.gate_path) as db:
        db.execute("UPDATE gate_calls SET state='provider_started',response_json=NULL,response_sha256=NULL")
    with pytest.raises(HistoryFenceError, match='完整响应'):
        h.fence.verify(h.current, allowed=[proof])


def test_approved_full_file_sha_is_checked_again_on_each_verification(tmp_path):
    h = Harness(tmp_path)
    with sqlite3.connect(h.approved) as db:
        db.execute("UPDATE calls SET updated_at=999 WHERE state='complete'")
    with pytest.raises(HistoryFenceError, match='受审旧账本文件已变化'):
        h.fence.verify(h.current)
    with pytest.raises(HistoryFenceError, match='受审旧账本文件已变化'):
        HistoryFence(h.approved, h.pin)


def test_schema_change_cannot_be_silently_adopted(tmp_path):
    h = Harness(tmp_path)
    with sqlite3.connect(h.current) as db:
        db.execute('ALTER TABLE calls ADD COLUMN fictional TEXT')
    with pytest.raises(HistoryFenceError, match='调用表结构'):
        h.fence.verify(h.current)


def test_approved_path_rejects_links_permissions_and_traversal(tmp_path):
    h = Harness(tmp_path)
    link = h.directory / 'linked.sqlite3'
    link.symlink_to(h.approved)
    with pytest.raises(ValueError, match='符号链接'):
        HistoryFence(link, h.pin)
    link.unlink()
    os.link(h.approved, link)
    with pytest.raises(ValueError, match='硬链接'):
        HistoryFence(h.approved, h.pin)
    link.unlink()
    h.approved.chmod(0o644)
    with pytest.raises(ValueError, match='0600'):
        HistoryFence(h.approved, h.pin)
