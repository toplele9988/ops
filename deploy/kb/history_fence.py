"""只读核对受审旧调用账本；新增记录须有可靠响应或明确额度拒绝收据。

受审副本由外部受控窗口创建，完整文件 SHA 由 root 运行配置绑定。这里不做
备份、不建表、不写原账本或副本。它只证明调用覆盖未被其他工作者改变；
来源仍须经过原动态授权、来源隔离和固定时间边界核验。
"""
from __future__ import annotations

from contextlib import closing
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import stat

from physical_gate import GateError, PhysicalGate, canon, digest, private_path, valid_sha

CALL_COLUMNS = ('cache_key', 'attempt', 'prompt_sha256', 'model', 'state', 'error_code',
                'day', 'reserved_tokens', 'response_json', 'response_sha256', 'created_at', 'updated_at')
PROOF_KEYS = {'cache_key', 'model', 'prompt_sha256', 'response_json', 'response_sha256',
              'item', 'gate_request_key'}


class HistoryFenceError(GateError):
    pass


def need(ok, message='旧调用账本或协调器收据不一致，请停止并保留原记录'):
    if not ok:
        raise HistoryFenceError(message)


def file_digest(path):
    path = private_path(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        need(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_uid == os.geteuid()
             and stat.S_IMODE(info.st_mode) == 0o600 and info.st_size <= 1024 * 1024 * 1024,
             '受审账本必须是私有普通文件，请核对权限与文件大小')
        value = hashlib.sha256()
        while chunk := stream.read(1024 * 1024):
            value.update(chunk)
    return value.hexdigest()


def readonly(path, *, immutable=False):
    path = private_path(path)
    suffix = '?mode=ro&immutable=1' if immutable else '?mode=ro'
    db = sqlite3.connect(path.as_uri() + suffix, uri=True)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA query_only=ON')
    db.execute('PRAGMA trusted_schema=OFF')
    db.execute('BEGIN')
    return db


def structure(db):
    schema = tuple(tuple(row) for row in db.execute('PRAGMA table_info(calls)'))
    need(tuple(row[1] for row in schema) == CALL_COLUMNS,
         '调用表结构不符合已核验版本，请停止并重新审阅')
    scope = tuple(tuple(row) for row in db.execute('PRAGMA table_info(scope)'))
    need(tuple(row[1] for row in scope) == ('key', 'value'),
         '调用停止范围结构不符合已核验版本，请保留原 hold')
    return schema, scope


def calls(db):
    result = {}
    for row in db.execute('SELECT * FROM calls ORDER BY cache_key,attempt'):
        key = (row['cache_key'], row['attempt'])
        need(valid_sha(row['cache_key']) and type(row['attempt']) is int and row['attempt'] > 0
             and key not in result)
        result[key] = tuple(row)
    return result


class HistoryFence:
    def __init__(self, approved_calls_path, expected_file_sha256, gate_db_path=None):
        need(valid_sha(expected_file_sha256), '缺少受审旧账本 SHA，请先完成停写窗口盘点')
        self.path = private_path(approved_calls_path)
        self.expected = expected_file_sha256
        self.gate_db_path = Path(gate_db_path) if gate_db_path is not None else None
        need(file_digest(self.path) == self.expected, '受审旧账本文件已变化，请停止并重新盘点')
        # immutable makes the approved bytes authoritative even if an unrelated
        # WAL happens to be present beside the file; no WAL bytes are trusted.
        with closing(readonly(self.path, immutable=True)) as db:
            self.schema = structure(db)
            self.original = calls(db)
            self.scope = tuple(tuple(row) for row in db.execute('SELECT key,value FROM scope ORDER BY key'))
        need(file_digest(self.path) == self.expected, '读取期间受审旧账本发生变化，请停止')
        need(bool(self.original), '受审调用账本为空，无法证明来源从未请求')
        times = [row[CALL_COLUMNS.index('created_at')] for row in self.original.values()]
        need(all(type(value) in (int, float) and math.isfinite(value) and value > 0 for value in times),
             '旧请求发起时间不完整，无法证明来源从未请求')
        self.last_request_at = max(times)
        self.original_cache_keys = {key[0] for key in self.original}
        self.original_row_sha256 = {key: digest(row) for key, row in self.original.items()}

    def set_gate(self, gate_db_path):
        self.gate_db_path = Path(gate_db_path)

    def assert_never_requested(self, created_at):
        need(type(created_at) in (int, float) and math.isfinite(created_at)
             and created_at > self.last_request_at,
             '来源入库不晚于受审旧请求，不能证明从未请求，已排除')
        return True

    def _proofs(self, allowed):
        need(isinstance(allowed, list), '缺少协调器可靠响应清单，请停止并核对')
        if not allowed:
            return {}
        need(self.gate_db_path is not None, '缺少物理请求账本，禁止接受新增旧调用记录')
        result = {}
        with closing(readonly(self.gate_db_path)) as db:
            PhysicalGate._verify_audit(db)
            events = [(row['kind'], json.loads(row['payload_json']))
                      for row in db.execute('SELECT kind,payload_json FROM gate_audit ORDER BY seq')]
            for proof in allowed:
                need(isinstance(proof, dict) and set(proof) == PROOF_KEYS)
                need(all(valid_sha(proof[key]) for key in ('cache_key', 'prompt_sha256', 'gate_request_key')))
                need(isinstance(proof['item'], dict) and proof['item'].get('channel') == 'wechat'
                     and proof['item'].get('prompt_sha256') == proof['prompt_sha256'])
                need(isinstance(proof['model'], str) and bool(proof['model']))
                raw = proof['response_json']
                rejected = raw is None and proof['response_sha256'] is None
                if not rejected:
                    need(isinstance(raw, str) and valid_sha(proof['response_sha256'])
                         and hashlib.sha256(raw.encode()).hexdigest() == proof['response_sha256']
                         and canon(json.loads(raw)) == raw, '协调器响应字节与收据不一致，请保留检查点')
                receipt_state = 'quota_rejected' if rejected else 'complete'
                row = db.execute('SELECT * FROM gate_calls WHERE request_key=?',
                                 (proof['gate_request_key'],)).fetchone()
                need(row is not None and row['state'] == receipt_state
                     and row['model'] == proof['model']
                     and row['binding_sha256'] == digest({'item': proof['item'], 'model': proof['model']})
                     and row['response_json'] == raw and row['response_sha256'] == proof['response_sha256'],
                     '新增旧调用没有本协调器的完整响应，禁止继续或重新请求')
                starts = [payload for kind, payload in events
                          if kind == 'provider_started' and payload.get('request_key') == proof['gate_request_key']]
                saved = [payload for kind, payload in events
                         if kind == 'response_saved' and payload.get('request_key') == proof['gate_request_key']]
                rejected_events = [payload for kind, payload in events
                                   if kind == 'quota_rejected' and payload.get('request_key') == proof['gate_request_key']]
                need(len(starts) == 1 and starts[0].get('model') == proof['model']
                     and starts[0].get('tokens') == row['tokens'] and starts[0].get('bucket') == row['bucket'],
                     '新增调用缺少原物理请求审计，请保留账本并停止')
                if rejected:
                    # NULL response alone is never a rejection receipt. Require
                    # the exact durable known-quota event, not an unknown/error.
                    need(not saved and len(rejected_events) == 1
                         and rejected_events[0].get('bucket') == row['bucket'],
                         '明确额度拒绝缺少原审计，禁止把未知结果改成额度拒绝')
                else:
                    need(not rejected_events and len(saved) == 1
                         and saved[0].get('response_sha256') == proof['response_sha256'],
                         '可靠响应缺少原保存审计，请保留检查点并停止')
                key = proof['cache_key']
                need(key not in result and key not in self.original_cache_keys,
                     '旧请求或重复缓存标识不能借协调器收据重新准入')
                result[key] = (proof, row['tokens'], receipt_state)
        return result

    def verify(self, current_calls_path, allowed=None):
        need(file_digest(self.path) == self.expected, '受审旧账本文件已变化，请保留旧清单并停止')
        with closing(readonly(current_calls_path)) as db:
            need(structure(db) == self.schema, '当前调用表结构已变化，请停止并核对基线')
            actual = calls(db)
            scope = tuple(tuple(row) for row in db.execute('SELECT key,value FROM scope ORDER BY key'))
        need(scope == self.scope, '原 hold 或停止范围已变化，请保留熔断与隔离')
        for key, row in self.original.items():
            need(key in actual and digest(actual[key]) == self.original_row_sha256[key],
                 '原请求记录缺失或被修改，请停止并保留全部审计')
        extras = {key: dict(zip(CALL_COLUMNS, row)) for key, row in actual.items() if key not in self.original}
        if not extras:
            return True
        proofs = self._proofs(allowed if allowed is not None else [])
        for key, row in extras.items():
            need(row['state'] == 'complete' and row['error_code'] is None
                 or row['state'] == 'quota_rejected' and row['error_code'] == 'qwen_free_tier_exhausted'
                 and row['response_json'] is None and row['response_sha256'] is None,
                 '新增旧调用不是可靠完成或明确免费额度拒绝，已停止；未知请求禁止重发')
            need(key[1] == 1 and row['cache_key'] in proofs,
                 '发现未经本协调器准入的新调用，请停止并重新盘点')
            proof, tokens, receipt_state = proofs[row['cache_key']]
            need(row['state'] == receipt_state and all(row[name] == proof[name] for name in
                     ('model', 'prompt_sha256', 'response_json', 'response_sha256')),
                 '新增旧调用与协调器模型、提示词或响应不一致，请停止')
            need(type(row['reserved_tokens']) is int and 0 < row['reserved_tokens'] <= tokens
                 and type(row['created_at']) in (int, float) and math.isfinite(row['created_at'])
                 and row['created_at'] > self.last_request_at
                 and type(row['updated_at']) in (int, float) and math.isfinite(row['updated_at'])
                 and row['updated_at'] >= row['created_at'],
                 '新增调用的预留或时间不符合协调器收据，请保留记录并停止')
        return True
