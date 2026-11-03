"""恢复协调器的生产候选适配；导入不读配置、不打开库、不调用模型。

微信沿用 live_loader 的受审模块图、kb_authorized 与原双读者落库函数。
阿里提供原始单来源拆分核验；既有 profile 熔断和整会话发布契约没有旁路。
每个 request 只调用一次 completion，限速、持久预留与读者检查点由协调器管理。
"""
from __future__ import annotations

from contextlib import closing, contextmanager
from copy import deepcopy
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import stat
import sys
import time
from types import SimpleNamespace

from backlog import (ReviewError, canon, digest, is_sha, need, response_digest,
                     sha, validate_entry)
import source_gate
from physical_gate import GateDeferred, QuotaExhausted, ProviderRejected
import official_deepseek as official_contract

WECHAT_MODELS = ('deepseek-v4-pro-0813', 'deepseek-v4.1-flash')
ALIBABA_MODEL = 'deepseek-v4.1-flash'
LOADER = Path('/opt/imgw/wechat-kb-dual-live-v1/live_loader.py')
ALI_CORE = Path('/opt/imgw/scripts/icbu-conversation-analysis/analysis_core.py')
ALI_CORE_SHA = '023433809d08e917b1cc11ef6154e9521f1a602c9a830596b3913db36943f82a'
SOURCE_FIELDS = ('evidence_id', 'chatroom_id', 'price_kind', 'source_created_at',
                 'received_at', 'created_at', 'source_kind', 'source_sha256',
                 'classification_id', 'owner', 'source_scope')


class ChannelBlocked(GateDeferred):
    pass


def checked_code(path, expected):
    """仅加载已绑定的 root 源码；测试通过显式对象注入，不放宽生产权限。"""
    need(is_sha(expected), '缺少源码摘要，请先核对受审版本')
    path = Path(path)
    need(path.is_absolute() and '..' not in path.parts)
    for component in (path, *path.parents):
        info = component.lstat()
        need(not stat.S_ISLNK(info.st_mode) and info.st_uid == 0
             and not info.st_mode & 0o022, '生产源码权限不符，请核对受审安装目录')
    info = path.lstat()
    need(stat.S_ISREG(info.st_mode) and info.st_nlink == 1
         and info.st_size <= 1048576)
    raw = path.read_bytes()
    need(sha(raw) == expected, '生产源码已变化，请停止并重新核对基线')
    return raw


def read_only(path):
    db = sqlite3.connect(Path(path).absolute().as_uri() + '?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA query_only=ON')
    return db


def wechat_prompt(extract, unit):
    return (extract.LEARNING_PROMPT if unit['business_role'] == 'learning'
            else extract.PROMPT) + unit['text']


def stable_wechat_units(extract, rows, ctx):
    """使用 R3-C 的每证据 KL/KD 规则；没有重新拼接旧 KC 窗口。"""
    rows = [dict(row) for row in rows if extract._eligible(row)]
    selected = {row['evidence_id']: row for row in rows}
    # units() 会再次执行真实 kb_authorized；这两个键只缩小范围，不授予权限。
    context = dict(ctx, _kb_live_ids=frozenset(selected), _kb_live_rows=selected)
    return context, sorted({row['chatroom_id'] for row in rows})


def restrict_context(ctx, allowed):
    supplier = dict(ctx['_supplier_context'], channels={
        room: name for room, name in ctx['_supplier_context']['channels'].items() if room in allowed})
    return dict(ctx, channels=supplier['channels'], _supplier_context=supplier,
                _learning={room: row for room, row in ctx.get('_learning', {}).items() if room in allowed})


def wechat_entry(extract, unit, rows, *, reason='never_requested', checkpoints=None,
                 completed_steps=None):
    by_id = {row['evidence_id']: dict(row) for row in rows}
    originals = [by_id[key] for key in sorted({r['evidence_id'] for r in unit['rows']})]
    need(originals and all(row['source_created_at'] is not None for row in originals),
         '资料缺少来源时间，请先核对归档')
    metadata = [{**{key: row[key] for key in SOURCE_FIELDS},
                 'body_sha256': sha(row['body_text'])} for row in originals]
    origins = [row['source_sha256'] for row in originals]
    bodies = [row['body_text'] for row in originals]
    result = {'channel': 'wechat', 'source_id': unit['unit_id'],
              'fingerprint': digest({'rows': metadata, 'business_role': unit['business_role'],
                                     'unit_sha256': unit['unit_sha256']}),
              'source_sha256': origins[0] if len(origins) == 1 else digest(origins),
              'content_sha256': sha(bodies[0]) if len(bodies) == 1 else digest(bodies),
              'prompt_sha256': sha(wechat_prompt(extract, unit)),
              'source_at': min(row['source_created_at'] for row in originals),
              'admitted_at': max(row['created_at'] for row in originals),
              'reason': reason, 'checkpoints': deepcopy(checkpoints or []),
              'completed_steps': list(completed_steps or [])}
    return validate_entry(result)


def single_alibaba_unit(core, group, record):
    """复用真实 normalize/plan/prompt；大于一窗的来源明确排除，不截断。"""
    record = core.normalize_record(record)
    need(record in group['records'], '阿里来源不在当前授权会话，请重新盘点')
    single = {key: group[key] for key in ('key', 'owner', 'account', 'conversation_id')}
    single.update(records=[record], source_version=core.record_version([record]))
    windows = core.plan_windows(single)
    need(len(windows) == 1, '阿里来源需要多个单元，本轮未批准拆分，已排除该来源')
    core.validate_coverage(single['records'], [windows[0]['evidence_spans']])
    prompt = core.prompt_for(single, windows[0])
    # 与父会话新增、记录顺序、模型选择无关；不重置原会话分析版本或窗口。
    return {'source_id': record['inbound_id'], 'group': single, 'window': windows[0],
            'prompt': prompt, 'prompt_sha256': sha(prompt),
            'unit_sha256': digest({'source_sha256': record['source_sha256'],
                                   'window': windows[0]})}


class WechatAdapter:
    def __init__(self, live, matrix, context, quarantine, *, models=WECHAT_MODELS,
                 request_tokens=65536, settle_seconds=120, clock=time.time, history_fence=None,
                 gate_path=None, official=None, prepared_ttl=0):
        self.live, self.extract, self.matrix = live, live.kb_extract, matrix
        self.context, self.quarantine = context, quarantine
        self.models = tuple(models)
        self.request_tokens, self.settle_seconds, self.clock = request_tokens, settle_seconds, clock
        self.history_fence, self.gate_path = history_fence, gate_path
        self.official = official
        # 生产可设短时缓存以免每次校验都重建全部来源；默认0即每次现场重建。
        self.prepared_ttl, self._prepared = prepared_ttl, None
        need(len(self.models) == 2 and len(set(self.models)) == 2,
             '微信必须配置两名不同读者，请核对模型清单')

    def preflight(self):
        self.matrix.preflight()
        current = self.matrix.source_boundary()
        need(digest(current) == digest(self.quarantine), '未知来源隔离已变化，请保留原账本')
        frozen = {row['model'] for row in self.matrix.manifest['holds']}
        need(not frozen.intersection(self.models), '所选读者已冻结，请保留原未知预留')
        need(set(self.models) <= set(self.matrix.models), '所选读者没有现行技术契约')
        return current

    def check_channel(self, item):
        need(item['channel'] == 'wechat')

    def check_request(self, item, model):
        self.read_source(item)
        if model in official_contract.MODELS:
            need(self.official is not None, '官方读者尚未装配，请核对受审配置')
            self.check_reservation(item, model, self.request_tokens)
            return True
        need(model in self.models, '模型不在受审读者清单，请核对配置')
        row = self.matrix.matrix._read(locked=False)['models'][model]
        if row.get('blocked_reason') in ('free_tier_exhausted', 'model_expired'):
            raise QuotaExhausted('百炼免费模型额度已用完，停止该账户请求，不自动换模型')
        need(not row.get('blocked_reason') and not row.get('in_flight')
             and not row.get('unknown_reserved_tokens'), '模型出现新的未知状态，请保留预留并停止')
        self.check_reservation(item, model, self.request_tokens)
        return True

    def _verify_history(self, db, ctx, rows):
        if self.history_fence is None:
            return  # 仅显式注入的虚构对象不使用生产fence；build_adapter强制提供。
        allowed = []
        gate = None
        if self.gate_path is not None and Path(self.gate_path).exists():
            gate = read_only(self.gate_path)
        try:
            for unit in self._units(db, ctx, rows):
                ids = {row['evidence_id'] for row in unit['rows']}
                originals = [row for row in rows if row['evidence_id'] in ids]
                if any(row['created_at'] <= self.history_fence.last_request_at for row in originals):
                    continue
                item = wechat_entry(self.extract, unit, originals)
                for model in self.models:
                    request_key = digest({'item': item, 'model': model})
                    saved = gate.execute("SELECT * FROM gate_calls WHERE request_key=? AND state IN ('complete','quota_rejected')",
                                         (request_key,)).fetchone() if gate is not None else None
                    if saved is None:
                        continue
                    key = sha(canon({'prompt': wechat_prompt(self.extract, unit), 'schema': self.extract.SCHEMA,
                                    'preferred_model': model, 'version': self.matrix.policy['schema'], 'output': 4096}))
                    allowed.append({'cache_key': key, 'model': model, 'prompt_sha256': item['prompt_sha256'],
                                    'response_sha256': saved['response_sha256'], 'response_json': saved['response_json'],
                                    'gate_request_key': request_key, 'item': item})
            self.history_fence.verify(self.matrix.root / 'calls.sqlite3', allowed=allowed)
        finally:
            if gate is not None:
                gate.close()

    def _units(self, db, ctx, rows):
        # 原12块上限曾截断全文；整份排除，不能把原12块当全文已完成。
        rows = [row for row in rows if len(self.extract._document_units(row)) < self.extract.DOC_CHUNKS]
        context, rooms = stable_wechat_units(self.extract, rows, ctx)
        return self.extract.units(db, context, rooms)

    def _check_aliases(self, db, rows):
        # 使用已有平台编号精确交叉；不按群名/正文相似度合并身份。
        for source in rows:
            if not source.get('message_id'):
                continue
            originals = db.execute('SELECT evidence_id,body_text,source_sha256 '
                'FROM channel_business_evidence WHERE owner=? AND source_scope=? AND message_id=?',
                (source['owner'], source['source_scope'], source['message_id'])).fetchall()
            source_gate.check([dict(row) for row in originals], self.quarantine)

    def _held(self, db, row):
        try:
            source_gate.check([row], self.quarantine)
            self._check_aliases(db, [row])
        except ValueError:
            return True
        return False

    def discover_live(self, after):
        self.preflight()
        result = []
        with self.context(writable=False) as (db, state, ctx):
            rows = [dict(row) for row in self.live.kb_authorized(db, ctx)]
            self._verify_history(db, ctx, rows)
            selected = [row for row in rows
                        if row['source_created_at'] is not None and row['created_at'] is not None
                        and row['source_created_at'] > after and row['created_at'] > after
                        and row['source_kind'] != 'historical_message'
                        and self.clock() - max(row['source_created_at'], row['created_at']) >= self.settle_seconds]
            # 命中永久隔离的新增来源逐条排除，不进入单元，也不借别名重发；其余来源照常处理。
            selected = [row for row in selected if not self._held(db, row)]
            self.matrix.check_sources(selected)
            self._check_aliases(db, selected)
            for unit in self._units(db, ctx, selected):
                original = [row for row in selected if row['evidence_id'] in {
                    r['evidence_id'] for r in unit['rows']}]
                self.matrix.check_sources(original, sha(wechat_prompt(self.extract, unit)))
                _, table = self.extract.kb_tables(unit)
                prior = db.execute('SELECT unit_sha256,status FROM ' + table + ' WHERE unit_id=?',
                                   (unit['unit_id'],)).fetchone()
                # 已有终态由原账本保留；失败和单读者不能借新队列获得重发许可。
                if prior is not None:
                    need(prior['unit_sha256'] == unit['unit_sha256'], '旧单元指纹已变化，请核对来源')
                    need(prior['status'] in ('done', 'no_facts'), '旧单元未完成，请先核对原响应')
                    continue
                result.append(wechat_entry(self.extract, unit, original))
        return result

    def _prepared_sources(self, db, ctx):
        now = self.clock()
        if self.prepared_ttl and self._prepared is not None and 0 <= now - self._prepared[0] < self.prepared_ttl:
            return self._prepared[1]
        rows = [dict(row) for row in self.live.kb_authorized(db, ctx)]
        self._verify_history(db, ctx, rows)
        value = (rows, self._units(db, ctx, rows))
        self._prepared = (now, value) if self.prepared_ttl else None
        return value

    def _find(self, db, ctx, item, prepared=None):
        rows, units = prepared or self._prepared_sources(db, ctx)
        matches = [unit for unit in units if unit['unit_id'] == item['source_id']]
        # 历史 KC 单元必须按原 anchor/count 重建；完整 SHA/提示词仍由清单校验。
        if not matches and item['source_id'].startswith('KC:'):
            anchor, count = item['source_id'][3:].rsplit(':', 1)
            need(count.isdigit() and 0 < int(count) <= self.extract.UNIT_MESSAGES)
            original = next((row for row in rows if row['evidence_id'] == anchor), None)
            need(original is not None, '原单元不再通过动态授权，请停止并核对')
            ordered = sorted((row for row in rows if row['chatroom_id'] == original['chatroom_id']),
                             key=lambda row: (self.extract.cpq_windows._time(row), row['evidence_id']))
            offset = next(i for i, row in enumerate(ordered) if row['evidence_id'] == anchor)
            selected = ordered[offset:offset + int(count)]
            need(len(selected) == int(count))
            need(all(self.extract._eligible(row)
                     and len(self.extract._document_units(row)) < self.extract.DOC_CHUNKS
                     for row in selected), '原来源不合准入或全文被截断，已排除整个单元')
            unit = self.extract._make_unit(item['source_id'], original['chatroom_id'], [
                {'evidence_id': row['evidence_id'], 'body': self.extract.cpq_windows._one_line(row['body_text']),
                 'at': self.extract.cpq_windows._time(row)} for row in selected])
            unit['business_role'] = 'learning' if original['chatroom_id'] in ctx.get('_learning', {}) else 'supplier'
            unit['unit_sha256'] = sha(unit['business_role'] + '\0' + unit['unit_sha256'])
            matches = [unit]
        need(len(matches) == 1, '原稳定单元未找到，请重新核对来源，不能重新拼窗')
        unit = matches[0]
        ids = {row['evidence_id'] for row in unit['rows']}
        originals = [row for row in rows if row['evidence_id'] in ids]
        if item['reason'] == 'never_requested' and self.history_fence is not None:
            need(all(row['created_at'] > self.history_fence.last_request_at for row in originals),
                 '来源没有通过旧请求覆盖排除核验，请保留待确认，禁止重新请求')
        self.matrix.check_sources(originals, sha(wechat_prompt(self.extract, unit)))
        self._check_aliases(db, originals)
        rebuilt = wechat_entry(self.extract, unit, originals, reason=item['reason'],
                               checkpoints=item['checkpoints'], completed_steps=item['completed_steps'])
        need(rebuilt == item, '来源、授权或稳定单元已变化，请停止并核对')
        return unit

    def read_source(self, item):
        self.preflight()
        with self.context(writable=False) as (db, state, ctx):
            self._find(db, ctx, item)
        return deepcopy(item)

    def read_sources(self, items):
        # 同一轮发现的来源共用一次授权与单元重建；逐条核验与 read_source 完全相同。
        self.preflight()
        with self.context(writable=False) as (db, state, ctx):
            rows = [dict(row) for row in self.live.kb_authorized(db, ctx)]
            self._verify_history(db, ctx, rows)
            prepared = (rows, self._units(db, ctx, rows))
            for item in items:
                self._find(db, ctx, item, prepared)
        return deepcopy(items)

    def plan(self, item):
        self.read_source(item)
        # 计划固定逻辑读者；额度在每次物理准入时核对，不能因本地余额改计划。
        with self.context(writable=False) as (db, state, ctx):
            unit = self._find(db, ctx, item)
            need(self.request_tokens >= len(wechat_prompt(self.extract, unit).encode()) + 4096,
                 '物理请求保守预留不足，请先重新核对预算')
        return [{'model': model, 'reservation': self.request_tokens} for model in self.models]

    def check_reservation(self, item, model, reservation):
        with self.context(writable=False) as (db, state, ctx):
            unit = self._find(db, ctx, item)
            prompt = wechat_prompt(self.extract, unit)
            if model in official_contract.MODELS:
                need(self.official is not None and reservation >= official_contract.reservation_tokens(
                    prompt, self.extract.SCHEMA, model, max_output_tokens=4096),
                    '官方请求完整提示词的保守预留不足，请重新核对预算')
                return
            hint = self.matrix.matrix.select_model_readonly(prompt, self.extract.SCHEMA,
                        max_output_tokens=4096, preferred_model=model)
            if hint.get('model') is None:
                raise QuotaExhausted('百炼免费模型额度不足，停止该账户请求，不自动换模型')
            need(hint['model'] == model)
            need(type(reservation) is int and reservation >= max(len(prompt.encode()) + 4096, hint['reservation_tokens']),
                 '物理请求的保守预留小于原模型契约，请先重新核对预算')

    def request(self, item, model, reservation):
        self.check_request(item, model)
        self.check_reservation(item, model, reservation)
        need(model in self.models or self.official is not None and model in official_contract.MODELS,
             '模型不在受审读者清单，请停止并核对')
        with self.context(writable=False) as (db, state, ctx):
            unit = self._find(db, ctx, item)
            prompt = wechat_prompt(self.extract, unit)
            need(reservation >= len(prompt.encode()) + 4096, '保守预留不足，请重新核对预算')
            if model in official_contract.MODELS:
                return self.official.request(prompt, self.extract.SCHEMA, model,
                                             timeout_seconds=45, max_output_tokens=4096)
            # 不调用 run_units；不会在函数里换模型、补读者或重试。
            try:
                return self.matrix.completion(prompt, self.extract.SCHEMA, provider='dashscope',
                    preferred_model=model, timeout_seconds=45, max_output_tokens=4096, role='crosscheck')
            except Exception as error:
                if getattr(error, 'code', None) == 'qwen_free_tier_exhausted':
                    raise ProviderRejected('free_tier_exhausted', {
                        'provider': 'dashscope', 'requested_model': model,
                        'error_code': 'qwen_free_tier_exhausted',
                        'prompt_sha256': item['prompt_sha256']}) from None
                raise

    def validate_response(self, item, model, receipt):
        if model not in official_contract.MODELS:
            return receipt  # 百炼由原 Matrix 验证、持久化；本地仍执行原逐字校验。
        with self.context(writable=False) as (db, state, ctx):
            unit = self._find(db, ctx, item)
            def evidence(content):
                facts = self.extract._facts({'content': content}, unit['rows'],
                                            business_role=unit['business_role'])
                need(len(facts) == len(json.loads(content)['facts']),
                     '官方读者存在不能逐字核对的事实，已拒绝该响应')
                return True
            return official_contract.validate_response(receipt, model, self.extract.SCHEMA, evidence,
                         max_output_tokens=4096, reservation_tokens=self.request_tokens)

    def load_checkpoint(self, item):
        self.read_source(item)
        result = []
        with closing(read_only(self.matrix.root / 'calls.sqlite3')) as db:
            for checkpoint in item['checkpoints']:
                rows = db.execute('SELECT * FROM calls WHERE cache_key=? ORDER BY attempt DESC',
                                  (checkpoint['cache_key'],)).fetchall()
                matched = [row for row in rows if digest(tuple(row)) == checkpoint['checkpoint_sha256']]
                need(len(matched) == 1, '原响应检查点未找到，请保留原记录')
                row = matched[0]
                need(row['state'] == 'complete' and row['model'] == checkpoint['model']
                     and row['prompt_sha256'] == item['prompt_sha256']
                     and sha(row['response_json']) == row['response_sha256'] == checkpoint['response_sha256'],
                     '原响应检查点不完整，请保留隔离并核对')
                answer = json.loads(row['response_json'])
                metadata = answer.get('metadata', {})
                need(metadata.get('requested_model') == metadata.get('actual_model') == row['model'],
                     '原响应没有可靠模型身份，请保留检查点并核对')
                result.append({'response': answer,
                               'response_sha256': row['response_sha256'],
                               'checkpoint_sha256': checkpoint['checkpoint_sha256']})
        return result

    def apply_local(self, item, replies):
        self.preflight()
        # 协调器持久检查点格式与原响应复用格式都只解包一次，不请求模型。
        answers = [reply['response'] if isinstance(reply, dict) and 'response' in reply else reply
                   for reply in replies]
        need(len(answers) == 2, '微信缺少两名可靠读者的响应，请核对原检查点')
        names = [self.extract.cpq_windows._model_name(reply) for reply in answers]
        need(all(name and name != 'unknown' for name in names) and len(set(names)) == 2,
             '微信两份响应不是独立读者，请核对原检查点')
        with self.context(writable=True) as (db, state, ctx):
            unit = self._find(db, ctx, item)
            _, table = self.extract.kb_tables(unit)
            prior = db.execute('SELECT unit_sha256,status FROM ' + table + ' WHERE unit_id=?',
                               (unit['unit_id'],)).fetchone()
            if prior is not None and prior['unit_sha256'] == unit['unit_sha256'] and prior['status'] in ('done', 'no_facts'):
                rebuilt = self.live.kb_profile.rebuild(db, ctx['_supplier_context'], int(self.clock()), matrix=None)
                need(not rebuilt.get('stopped_by'), '知识索引尚未完成，请保留响应核对本地步骤')
                return
            left, right = [self.extract._facts(reply, unit['rows'], business_role=unit['business_role'])
                           for reply in answers]
            kept, seen = [], set()
            for fact in left:
                key = (fact['msg'], fact['type'], fact['attr'], fact['product_key'], canon(fact['value']))
                if key not in seen and any(self.extract._agrees(fact, other) for other in right):
                    seen.add(key)
                    kept.append(fact)
            self.extract._record(db, unit, int(self.clock()), 'done' if kept else 'no_facts',
                                 *names, {'a_items': len(left), 'b_items': len(right),
                                          'kept': len(kept), 'dropped': len(left) - len(kept)}, kept)
            rebuilt = self.live.kb_profile.rebuild(db, ctx['_supplier_context'], int(self.clock()), matrix=None)
            need(not rebuilt.get('stopped_by'), '知识索引尚未完成，请保留响应核对本地步骤')

    def review_entries(self, after):
        """只读生成积压审阅输入；动态授权、旧请求fence及全文排除逐项执行。"""
        need(self.history_fence is not None, '缺少受审旧请求副本，不能判定从未请求')
        self.preflight()
        entries, excluded = [], 0
        with self.context(writable=False) as (db, state, ctx):
            rows = [dict(row) for row in self.live.kb_authorized(db, ctx)]
            self._verify_history(db, ctx, rows)
            selected = []
            for row in rows:
                if (row['source_created_at'] is None or row['created_at'] <= self.history_fence.last_request_at
                        or row['source_created_at'] > after and row['created_at'] > after):
                    continue
                try:
                    source_gate.check([row], self.quarantine)
                    self._check_aliases(db, [row])
                    need(len(self.extract._document_units(row)) < self.extract.DOC_CHUNKS)
                except ValueError:
                    excluded += 1
                    continue
                selected.append(row)
            for unit in self._units(db, ctx, selected):
                entries.append(wechat_entry(self.extract, unit, selected))
            # 旧来源只接已有两个独立持久响应；没有两个可靠响应就留在待核区。
            with closing(read_only(self.matrix.root / 'calls.sqlite3')) as calls:
                for unit in self.extract.units(db, ctx, sorted({row['chatroom_id'] for row in rows})):
                    ids = {row['evidence_id'] for row in unit['rows']}
                    originals = [row for row in rows if row['evidence_id'] in ids]
                    if not originals or any(row['created_at'] > self.history_fence.last_request_at for row in originals):
                        continue
                    if any(len(self.extract._document_units(row)) >= self.extract.DOC_CHUNKS for row in originals):
                        continue
                    try:
                        source_gate.check(originals, self.quarantine, sha(wechat_prompt(self.extract, unit)))
                    except ValueError:
                        excluded += 1
                        continue
                    _, table = self.extract.kb_tables(unit)
                    prior = db.execute('SELECT unit_sha256,status FROM ' + table + ' WHERE unit_id=?', (unit['unit_id'],)).fetchone()
                    if prior is not None and prior['unit_sha256'] == unit['unit_sha256'] and prior['status'] in ('done', 'no_facts'):
                        continue
                    checkpoints = []
                    seen = set()
                    for call in calls.execute("SELECT * FROM calls WHERE state='complete' AND prompt_sha256=? ORDER BY created_at,cache_key,attempt",
                                              (sha(wechat_prompt(self.extract, unit)),)):
                        if call['model'] in seen or not call['response_json'] or sha(call['response_json']) != call['response_sha256']:
                            continue
                        answer = json.loads(call['response_json'])
                        metadata = answer.get('metadata', {})
                        if metadata.get('requested_model') != call['model'] or metadata.get('actual_model') != call['model']:
                            continue
                        seen.add(call['model'])
                        checkpoints.append({'cache_key': call['cache_key'], 'model': call['model'],
                            'response_sha256': call['response_sha256'], 'checkpoint_sha256': digest(tuple(call))})
                        if len(checkpoints) == 2:
                            break
                    if len(checkpoints) == 2:
                        entries.append(wechat_entry(self.extract, unit, originals, reason='local_pending',
                                                    checkpoints=checkpoints, completed_steps=['response']))
        return entries, excluded


class AlibabaAdapter:
    """单来源恢复资产走原 Catalog 契约；不改原整会话版本、窗口或熔断。"""
    def __init__(self, core, runtime, pool, matrix, groups, authority, boundary,
                 history, engine, quarantine, *, receipts_path,
                 analysis_path='/var/lib/imgw/icbu/conversation-analysis/analysis.sqlite3',
                 model=ALIBABA_MODEL, request_tokens=65536, clock=time.time, frozen=None,
                 require_new_source=None, official=None):
        self.core, self.runtime, self.pool, self.matrix = core, runtime, pool, matrix
        self.groups, self.auth, self.boundary, self.history = groups, authority, boundary, history
        self.engine, self.quarantine, self.clock = engine, quarantine, clock
        self.path, self.receipts_path = Path(analysis_path), Path(receipts_path)
        self.model, self.request_tokens = model, request_tokens
        self.frozen, self.require_new_source = frozen, require_new_source
        self.official = official

    def preflight(self):
        self.boundary()
        source_gate.verify(self.quarantine)
        with closing(read_only(self.path)) as db:
            fields = {row[1] for row in db.execute('PRAGMA table_info(provider_circuits)')}
            need({'scope', 'model', 'state'} <= fields, '阿里熔断结构不匹配，请核对基线')
            # 原三条微信 + 一条阿里未知由 root 隔离清单绑定；新 processing 一律停。
            need(not db.execute("SELECT 1 FROM windows WHERE state='processing'").fetchone(),
                 '阿里出现未结束请求，请保留预留并停止全队')
            need(not db.execute("SELECT 1 FROM model_attempts WHERE status IN ('processing','interrupted')").fetchone(),
                 '阿里出现未结束请求，请保留预留并停止全队')
            holds = []
            for row in db.execute("SELECT payload_json,event_sha256 FROM routing_audit WHERE kind='quota_pool_failed'"):
                need(sha(row['payload_json']) == row['event_sha256'], '阿里原请求审计已变化，请停止并核对')
                failure = json.loads(row['payload_json'])['payload']
                if failure.get('matrix_error_code') == 'qwen_free_tier_exhausted':
                    continue
                need(failure.get('matrix_error_code') == 'api_timeout', '阿里存在未核实失败结果，请停止全队')
                key = (failure['version'], failure['window_index'], failure['attempt'])
                attempt = db.execute('SELECT * FROM model_attempts WHERE version=? AND window_index=? AND attempt=?', key).fetchone()
                execution = db.execute('SELECT * FROM execution_attempts WHERE version=? AND window_index=? AND attempt=?', key).fetchone()
                need(attempt is not None and execution is not None)
                holds.append((digest({'attempt': dict(attempt), 'execution': dict(execution)}), execution['prompt_sha256']))
            expected = [(row['request_sha256'], row['prompt_sha256']) for row in self.quarantine['holds'] if row['channel'] == 'alibaba']
            need(holds == expected, '阿里未知请求与永久隔离不一致，请停止全队')
        return deepcopy(self.quarantine)

    def check_channel(self, item):
        self.preflight()
        with closing(read_only(self.path)) as db:
            opened = db.execute("SELECT 1 FROM provider_circuits WHERE scope='profile' "
                                "AND model='existing-qwen-credential-profile' AND state='open'").fetchone()
            if opened is not None:
                raise ChannelBlocked('阿里原 profile 熔断仍开启，请保留审计和未知预留；阿里停止请求')

    def _find(self, item):
        found = [(group, record) for group in self.groups().values() for record in group['records']
                 if record['inbound_id'] == item['source_id']]
        need(len(found) == 1, '阿里原来源不再通过授权对账，请停止并核对')
        group, record = found[0]
        need(callable(self.require_new_source) and self.frozen is not None,
             '缺少阿里原来源冻结契约，请先核对受审装配')
        # 必须核对原父会话版本；单来源重算的新版本不能绕过原471冻结集。
        self.require_new_source(self.frozen, group['key'], group['source_version'])
        unit = single_alibaba_unit(self.core, group, record)
        source_gate.check([{'evidence_id': record['inbound_id'], 'body_text': record['body'],
                            'source_sha256': record['source_sha256']}], self.quarantine, unit['prompt_sha256'])
        expected = self._entry(unit, record)
        expected.update(reason=item['reason'], checkpoints=item['checkpoints'], completed_steps=item['completed_steps'])
        need(expected == item, '阿里来源、授权或单元已变化，请停止并核对')
        return unit

    def _entry(self, unit, record):
        from datetime import datetime
        parse = lambda value: datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()
        return validate_entry({'channel': 'alibaba', 'source_id': record['inbound_id'],
            'fingerprint': digest({'record': record, 'owner': self.auth['owner'],
                                   'binding_id': self.auth['binding_id'],
                                   'authority_sha': self.auth['authority_sha'],
                                   'unit_sha256': unit['unit_sha256']}),
            'source_sha256': record['source_sha256'], 'content_sha256': sha(record['body']),
            'prompt_sha256': unit['prompt_sha256'],
            'source_at': parse(record['sent_at']) if record['sent_at'] else None,
            'admitted_at': parse(record['received_at']),
            'reason': 'never_requested' if record['sent_at'] else 'time_unconfirmed',
            'checkpoints': [], 'completed_steps': []})

    def discover_live(self, after):
        self.preflight()
        result = []
        for group in self.groups().values():
            for record in group['records']:
                from datetime import datetime
                parse = lambda value: datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()
                if (not record.get('sent_at') or parse(record['sent_at']) <= after
                        or parse(record['received_at']) <= after):
                    continue
                unit = single_alibaba_unit(self.core, group, record)
                item = self._entry(unit, record)
                if item['source_at'] is not None and item['source_at'] > after and item['admitted_at'] > after:
                    self._find(item)
                    result.append(item)
        return result

    def read_source(self, item):
        self.preflight()
        self._find(item)
        return deepcopy(item)

    def plan(self, item):
        self.check_channel(item)
        self.read_source(item)
        return [{'model': self.model, 'reservation': self.request_tokens}]

    def check_reservation(self, item, model, reservation):
        unit = self._find(item)
        if model in official_contract.MODELS:
            need(self.official is not None and reservation >= official_contract.reservation_tokens(
                unit['prompt'], self.pool.SCHEMA, model, max_output_tokens=self.pool.OUTPUT),
                '阿里官方请求完整提示词的保守预留不足，请核对预算')
            return
        selected = self.matrix.select_model_readonly(unit['prompt'], self.pool.SCHEMA,
                    max_output_tokens=self.pool.OUTPUT, preferred_model=model)
        if selected.get('model') is None:
            raise QuotaExhausted('百炼免费池额度不足，已停止该账户请求')
        need(type(reservation) is int and reservation >= selected['reservation_tokens'],
             '阿里物理请求保守预留小于原模型契约，请核对预算')

    def _old_requests(self, item, unit):
        with closing(read_only(self.path)) as db:
            for row in db.execute('SELECT plan_json,group_json FROM windows w JOIN analyses a ON a.version=w.version WHERE EXISTS('
                    'SELECT 1 FROM model_attempts a WHERE a.version=w.version AND a.window_index=w.window_index)'):
                prior_group = json.loads(row['group_json'])
                prior = {r['inbound_id']: r for r in prior_group['records']}
                for segment in json.loads(row['plan_json'])['segments']:
                    old = prior[segment['inbound_id']]
                    record = unit['group']['records'][0]
                    same_platform = (old.get('message_id') and record.get('message_id')
                                     and (prior_group['account'], prior_group['conversation_id'], old['message_id'])
                                     == (unit['group']['account'], unit['group']['conversation_id'], record['message_id']))
                    need(segment['inbound_id'] != item['source_id']
                         and segment['source_sha256'] != item['source_sha256'] and not same_platform,
                         '阿里来源已有旧请求，禁止借新单元重新发送')
        need(bool(unit['group']['records'][0].get('message_id')),
             '阿里来源缺少可核验的平台消息编号，已排除自动请求')

    def check_request(self, item, model):
        self.check_channel(item)
        unit = self._find(item)
        self._old_requests(item, unit)
        if model in official_contract.MODELS:
            need(self.official is not None, '阿里官方读者尚未装配，请核对配置')
            self.check_reservation(item, model, self.request_tokens)
            return True
        need(model == self.model and model in self.pool.MODELS, '阿里模型没有受审契约')
        selected = self.matrix.select_model_readonly(unit['prompt'], self.pool.SCHEMA,
                    max_output_tokens=self.pool.OUTPUT, preferred_model=model)
        if selected.get('model') is None:
            raise QuotaExhausted('百炼免费池额度不足，已停止该账户请求，不自动切换模型')
        need(selected['model'] == model, '阿里读者与冻结计划不一致，请停止并核对')
        self.check_reservation(item, model, self.request_tokens)
        return True

    def review_entries(self, after):
        entries, excluded = [], 0
        self.preflight()
        for group in self.groups().values():
            for record in group['records']:
                try:
                    unit = single_alibaba_unit(self.core, group, record)
                    item = self._entry(unit, record)
                    if item['source_at'] is None or item['source_at'] > after and item['admitted_at'] > after:
                        continue
                    self._find(item)
                    self._old_requests(item, unit)
                except ValueError:
                    excluded += 1
                    continue
                entries.append(item)
        return entries, excluded

    def request(self, item, model, reservation):
        self.check_request(item, model)
        self.check_reservation(item, model, reservation)
        unit = self._find(item)
        need(reservation >= len(unit['prompt'].encode()) + self.pool.OUTPUT,
             '阿里保守预留不足，请核对预算')
        if model in official_contract.MODELS:
            return self.official.request(unit['prompt'], self.pool.SCHEMA, model,
                                         timeout_seconds=45, max_output_tokens=self.pool.OUTPUT)
        try:
            return self.matrix.completion(unit['prompt'], self.pool.SCHEMA, provider='dashscope',
                preferred_model=model, max_output_tokens=self.pool.OUTPUT, timeout_seconds=45, role='structured')
        except Exception as error:
            if getattr(error, 'code', None) == 'qwen_free_tier_exhausted':
                raise ProviderRejected('free_tier_exhausted', {
                    'provider': 'dashscope', 'requested_model': model,
                    'error_code': 'qwen_free_tier_exhausted',
                    'prompt_sha256': item['prompt_sha256']}) from None
            raise

    def validate_response(self, item, model, receipt):
        if model not in official_contract.MODELS:
            return receipt
        unit = self._find(item)
        return official_contract.validate_response(receipt, model, self.pool.SCHEMA,
            lambda content: self.core.validate_response(content, unit['group'], unit['window']),
            max_output_tokens=self.pool.OUTPUT, reservation_tokens=self.request_tokens)

    def load_checkpoint(self, item):
        # R3-C 已验证阿里可靠响应待本地续做为零；不猜测旧版缓存映射。
        raise ReviewError('阿里旧响应未有已审复用映射，请先核对原审计；禁止重新请求')

    def apply_local(self, item, replies):
        self.read_source(item)
        need(len(replies) == 1, '阿里单元应只有一名读者，请核对检查点')
        reply = replies[0].get('response', replies[0])
        model = reply['metadata'].get('requested_model')
        if model in official_contract.MODELS:
            need(self.official is not None and reply['metadata'].get('provider') == 'deepseek_official'
                 and reply['metadata'].get('actual_model') == model)
            metadata = deepcopy(reply['metadata'])
        else:
            need(model == self.model)
            metadata = self.pool.provider_metadata(reply['metadata'], model)
        unit = self._find(item)
        result = self.core.validate_response(reply['content'], unit['group'], unit['window'])
        version = digest({'contract': 'icbu-source-recovery-v1', 'unit': unit['unit_sha256'],
                          'prompt_sha256': unit['prompt_sha256'], 'model': model,
                          'response_sha256': sha(reply['content'])})
        group, window = unit['group'], unit['window']
        document = {'schema_version': self.core.SCHEMA, 'parser_revision': self.core.PARSER,
            'analysis_version': version, 'source_version': group['source_version'],
            'owner': group['owner'], 'account': group['account'], 'conversation_id': group['conversation_id'],
            'conversation_key': group['key'], 'model_fingerprint': (
                digest({'contract': 'kb-official-deepseek-v1', 'model': model,
                        'schema_sha256': digest(self.pool.SCHEMA), 'source_sha256': digest(official_contract.PINS)})
                if model in official_contract.MODELS else self.pool.fingerprint(model, self.pool.APPROVED_POLICY_SHA)),
            'embedding_model_version': self.engine.model_version, 'visibility': 'owner_private',
            'verification_status': 'candidate', 'historical_status': 'historical_unverified',
            'current_quote_verified': False, 'auto_send': False, 'untrusted_model_output': True,
            'claims': result['claims'], 'gaps': result['gaps'], 'conflicts': self.core.conflicts(result['claims']),
            'coverage': self.core.validate_coverage(group['records'], [window['segments']]),
            'evidence_span_coverage': self.core.validate_coverage(group['records'], [window['evidence_spans']]),
            'window_bindings': [{'window': 0, 'window_sha256': window['window_sha256'],
                                  'registry_sha256': window['registry_sha256'],
                                  'evidence_spans': len(window['evidence_spans'])}],
            'analysis_scope': 'single_source_recovery_not_complete_conversation',
            'window_count': 1, 'source_message_count': 1,
            'utc_order_policy': 'sent_at_else_received_at_then_received_at_inbound_id_payload_sha',
            'source_manifest': [{key: group['records'][0].get(key) for key in
                                ('inbound_id', 'source_sha256', 'role', 'sent_at', 'received_at')}],
            'recovery_execution': {'requested_model': model, 'provider_metadata': metadata,
                                   'prompt_sha256': unit['prompt_sha256'],
                                   'response_sha256': sha(reply['content'])}}
        row = {'account': group['account'], 'cid': group['conversation_id'],
               'conversation_key': group['key'], 'version': version}
        receipt = {'version': version, 'source_sha256': item['source_sha256'],
                   'prompt_sha256': item['prompt_sha256'], 'result_sha256': digest(result),
                   'asset_id': None, 'asset_sha256': None, 'state': 'no_supported_claims'}
        if result['claims']:
            from video_knowledge.document_ingest_worker import embedding_for
            snapshot, metadata, parts = self.runtime.catalog_plan(row, document, self.auth)
            vectors = [embedding_for(self.engine, part['text']) for part in parts]
            self.boundary()
            self._find(item)
            catalog = self.history.open_catalog(self.auth['catalog_path'])
            try:
                asset = catalog.asset_id(snapshot)
                if catalog.has_asset(snapshot):
                    self.runtime.verify_catalog(catalog, asset, snapshot, metadata, parts, self.engine.model_version)
                else:
                    with catalog.connection:
                        catalog.put_asset(snapshot, summary='阿里单来源恢复候选，需核对原文与日期。',
                            snapshot_json=metadata, extraction_status='evidence_bound_analysis',
                            domain='customer_conversation', logical_asset_id='icbu-analysis-recovery-source:' + sha(item['source_id']),
                            created_at=group['records'][0]['sent_at'] or group['records'][0]['received_at'])
                        for i, (part, vector) in enumerate(zip(parts, vectors)):
                            evidence = [dict(e, claim_id=part['claim_id'], analysis_version=version,
                                analysis_char_start=part['analysis_char_start'], analysis_char_end=part['analysis_char_end'])
                                for e in part['evidence']]
                            catalog.put_chunk(asset, parent=i, child=0, text=part['text'], embedding=vector, evidence=evidence)
                        self.runtime.verify_catalog(catalog, asset, snapshot, metadata, parts, self.engine.model_version)
                        self.boundary()
                receipt.update(asset_id=asset, asset_sha256=snapshot['sha256'], state='indexed')
            finally:
                catalog.close()
        self._find(item)
        # Catalog已提交而收据未提交时，重启先verify_catalog再补收据，不再请求模型。
        from backlog import private_path
        path = private_path(self.receipts_path, exists=False)
        lock_path = private_path(str(path) + '.lock', exists=False)
        fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if not path.exists():
                new = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                os.close(new)
            with closing(sqlite3.connect(path)) as db:
                db.execute('PRAGMA synchronous=FULL')
                db.execute('BEGIN IMMEDIATE')
                db.execute('CREATE TABLE IF NOT EXISTS source_receipts(source_key TEXT PRIMARY KEY,receipt_json TEXT NOT NULL,receipt_sha256 TEXT NOT NULL)')
                raw = canon(receipt)
                previous = db.execute('SELECT receipt_json,receipt_sha256 FROM source_receipts WHERE source_key=?', (sha(item['source_id']),)).fetchone()
                need(previous is None or previous == (raw, sha(raw)), '阿里既有收据已变化，请停止并核对')
                db.execute('INSERT OR IGNORE INTO source_receipts VALUES(?,?,?)', (sha(item['source_id']), raw, sha(raw)))
                db.commit()
        finally:
            os.close(fd)


class AdapterHub:
    def __init__(self, channels, quarantine, *, execution_session=None, official=None, official_options=None):
        self.channels, self.quarantine = dict(channels), quarantine
        self._execution_session = execution_session
        self.official, self.official_options = official, official_options

    @contextmanager
    def execution_session(self):
        need(callable(self._execution_session), '生产运行必须持有原写者锁，请核对装配')
        with self._execution_session():
            yield

    def preflight(self):
        for adapter in self.channels.values():
            adapter.preflight()
        return deepcopy(self.quarantine)

    def discover_live(self, after):
        self.preflight()
        return [item for adapter in self.channels.values() for item in adapter.discover_live(after)]

    def _channel(self, item):
        need(item['channel'] in self.channels, '来源渠道没有生产适配，请停止并核对')
        return self.channels[item['channel']]

    def read_source(self, item):
        return self._channel(item).read_source(item)

    def read_sources(self, items):
        result = {}
        for item in items:
            result.setdefault(item['channel'], []).append(item)
        checked = {}
        for channel, group in result.items():
            adapter = self._channel(group[0])
            if hasattr(adapter, 'read_sources'):
                done = adapter.read_sources(group)
            else:
                done = [adapter.read_source(item) for item in group]
            checked.update({id(item): value for item, value in zip(group, done)})
        return [checked[id(item)] for item in items]

    def load_checkpoint(self, item):
        return self._channel(item).load_checkpoint(item)

    def apply_local(self, item, replies):
        return self._channel(item).apply_local(item, replies)

    def request(self, item, model, reservation):
        return self._channel(item).request(item, model, reservation)

    def plan(self, item):
        return self._channel(item).plan(item)

    def check_channel(self, item):
        return self._channel(item).check_channel(item)

    def check_request(self, item, model):
        return self._channel(item).check_request(item, model)

    def validate_response(self, item, model, receipt):
        return self._channel(item).validate_response(item, model, receipt)

    def refresh_balance(self, gate):
        if self.official is None:
            return
        options = self.official_options
        bucket = options['bucket']
        if bucket in gate.status()['bucket_stops']:
            return  # 明确拒绝后保留退避/停止，不在下一轮重查或重发。
        need(options.get('balance_mode') == 'api' and options.get('currency') in ('CNY', 'USD'),
             '官方余额币种尚未核实，请由总指挥填写受审配置')
        try:
            balance = self.official.read_balance(currency=options['currency'],
                persist_receipt=lambda receipt: gate.record_balance_receipt(bucket, receipt))
            gate.record_balance(bucket, balance)
        except ProviderRejected as error:
            gate.stop_bucket(bucket, error)
        except Exception:
            gate.stop_global('unknown')
            raise ReviewError('官方余额查询结果不明确，已停止全队；请保留回执核查') from None

    def channel_status(self):
        result = {}
        for channel, adapter in self.channels.items():
            try:
                adapter.check_channel({'channel': channel})
                result[channel] = {'state': 'ready', 'reason': None}
            except ChannelBlocked:
                result[channel] = {'state': 'blocked', 'reason': '原渠道熔断仍开启，已停止该渠道请求'}
        return result

    def inventory(self, manifest, quarantine):
        valid, invalid = 0, 0
        for item in manifest['items']:
            try:
                self.read_source(item)
                valid += 1
            except (ValueError, OSError, RuntimeError, sqlite3.Error):
                invalid += 1
        return {'结果': '只读盘点完成，未请求模型、未创建进度库',
                '渠道状态': self.channel_status(), '冻结单元数': len(manifest['items']),
                '隔离单元数': len(manifest['excluded']), '未知请求数': len(quarantine['holds']),
                '通过来源核对数': valid, '未通过来源核对数': invalid, '模型请求': 0, '生产写入': 0}

    def plan_review(self, after, snapshots):
        entries, excluded = [], {}
        for channel, adapter in self.channels.items():
            candidates, excluded[channel] = adapter.review_entries(after)
            entries.extend(candidates)
        # 输出只交给0700私有目录的写者；此对象包含原始来源ID，不可打印/入仓库。
        return {'schema': 'kb-backlog-review-v1', 'after': after,
                'snapshots': deepcopy(snapshots), 'entries': entries}, excluded


def reviewed_json(path, expected):
    need(is_sha(expected), '缺少受审清单摘要，请先完成私有清单审阅')
    path = Path(path)
    need(path.is_absolute() and '..' not in path.parts)
    for component in (path, *path.parents):
        info = component.lstat()
        need(not stat.S_ISLNK(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022,
             '清单必须由 root 持有且不能被工作进程改写')
    info = path.lstat()
    need(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size <= 32 * 1024 * 1024)
    raw = path.read_bytes()
    value = json.loads(raw)
    need(digest(value) == expected, '私有清单摘要不一致，请停止并重新审阅')
    return value


def build_adapter(config, *, inventory_only=False, gate_path=None):
    """明确的微信生产装配入口；仅由已审启动配置调用，默认 CLI 不调用它。

    config['adapter'] 必须包含已安装补丁 loader 的 sha256 及 channel 列表。
    安装后的 loader 自行核对模块/边界/四未知隔离 SHA，不能指向任意 factory。
    """
    from backlog import validate_manifest
    manifest = reviewed_json(config['manifest_path'], config['manifest_sha256'])
    quarantine = reviewed_json(config['quarantine_path'], config['quarantine_sha256'])
    validate_manifest(manifest, digest(manifest), quarantine)
    options = config['adapter']
    need(options['channels'] and set(options['channels']) <= {'wechat', 'alibaba'})
    need(set(config['channels']) == set(options['channels']), '两个渠道配置范围不一致，请核对受审配置')
    for channel, readers in config['channels'].items():
        need(readers['readers'] == list(WECHAT_MODELS if channel == 'wechat' else (ALIBABA_MODEL,)),
             '所选模型没有当前恢复契约，请使用已核验读者')
        routes = config.get('routes', {}).get(channel)
        need(routes is None or [route[0] for route in routes] == readers['readers'])
        need(routes is None or all(set(route) <= set(readers['readers']) | set(official_contract.MODELS)
                                  for route in routes), '路由模型没有受审技术契约，请核对配置')
    routed = {model for readers in config.get('routes', {}).values() for route in readers for model in route}
    official = official_contract.OfficialDeepSeek() if routed.intersection(official_contract.MODELS) else None
    official_options = config.get('official_deepseek')
    if official is not None:
        need(isinstance(official_options, dict) and official_options.get('bucket') in config['physical']['buckets'])
        need(all(config['physical']['models'][model]['bucket'] == official_options['bucket']
                 for model in routed.intersection(official_contract.MODELS)))
        need(config['physical']['buckets'][official_options['bucket']]['kind'] == 'prepaid')
        official_contract.checked_runtime()
    need(inventory_only or type(options['request_tokens']) is int and options['request_tokens'] > 0,
         '物理请求保守预留未核实，请先完成只读盘点')
    for fixed in ('/opt/imgw/wechat', '/opt/imgw/scripts', '/opt/imgw/scripts/icbu-conversation-analysis'):
        if fixed not in sys.path:
            sys.path.append(fixed)
    raw = checked_code(LOADER, options['loader_sha256'])
    namespace = {'__name__': '_kb_reviewed_live_loader', '__file__': str(LOADER)}
    exec(compile(raw, str(LOADER), 'exec'), namespace)
    live = namespace['load']()
    from api_groupbot import load_config
    from video_knowledge.assistant_policy import read_policy
    matrix = live.kb_model_runtime.create_matrix(read_policy(), allow_isolation=True)
    after = live.read_recovery_boundary(matrix)
    need(after == manifest['after'], '固定新增边界与原生产契约不一致，请停止并核对')
    need(digest(matrix.source_boundary()) == digest(quarantine), '原生产隔离清单与受审清单不一致')

    active = {'fd': None}

    @contextmanager
    def execution_session():
        need(active['fd'] is None, '不能重复进入知识处理运行窗口')
        fd = os.open(live.kb_profile.LOCK, os.O_RDWR | os.O_NOFOLLOW)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            active['fd'] = fd
            if 'alibaba' in config['channels']:
                with history.worker_lock(old.ANALYSIS_PATH):
                    yield
            else:
                yield
        finally:
            active['fd'] = None
            os.close(fd)

    @contextmanager
    def context(*, writable=False):
        # 与原 kb_live 使用同一 kb-build.lock，避免旧worker并行修改Catalog。
        owned = active['fd'] is None
        fd = os.open(live.kb_profile.LOCK, os.O_RDWR | os.O_NOFOLLOW) if owned else active['fd']
        try:
            if owned:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            source_config = load_config(Path(live.cpq_batch.CONFIG))
            policy = read_policy()
            with closing(read_only(live.cpq_batch.STATE)) as state:
                ctx = live.kb_context(state, source_config, policy, int(time.time()))
                scope = live.kb_profile.flag_scope()
                need(scope is not None, '原知识处理开关未授权，请核对现行范围')
                opener = (live.private_store.open_private_db(live.cpq_batch.SOURCE_CATALOG)
                          if writable else read_only(live.cpq_batch.SOURCE_CATALOG))
                with closing(opener) as db:
                    db.row_factory = sqlite3.Row
                    db.execute('PRAGMA synchronous=FULL')
                    allowed = set(live.kb_extract.scope_chatrooms(db, ctx, scope))
                    ctx = restrict_context(ctx, allowed)
                    yield db, state, ctx
        except BlockingIOError:
            raise ReviewError('原知识处理写者仍在运行，请先核对停机窗口') from None
        finally:
            if owned:
                os.close(fd)

    channels = {}
    if 'wechat' in options['channels']:
        from history_fence import HistoryFence
        fence = HistoryFence(options['approved_calls_path'], options['approved_calls_sha256'],
                             gate_db_path=gate_path)
        channels['wechat'] = WechatAdapter(live, matrix, context, quarantine,
            request_tokens=options['request_tokens'], settle_seconds=options['settle_seconds'],
            history_fence=fence, gate_path=gate_path, official=official,
            prepared_ttl=0 if inventory_only else 30)
    if 'alibaba' in options['channels']:
        ali_root = ALI_CORE.parent
        pins = {'analysis_core.py': ALI_CORE_SHA,
                'analysis_runtime.py': '7b9362e752a97d4a8153f9874615a63f50dc18997a39f8295f49516eb1c70e21',
                'analysis_multimodel.py': 'b9bede6f7bd7105f39e3f6fca223811965fc3a2e000713148bdb7a8c45be7abc',
                'analysis_quota_pool.py': 'beb98bf2de43de4e4599c3e8aaf3ec23bc3be80c354304a8cb3274fac22391ec',
                'quota_runtime.py': '5ab05f1ac860d1504259c2a2eab1de21593106c8cc09d1e48edbd39b11d299c7',
                'source_freeze.py': 'eb902edb84056c4fd47f887b6bb9ae6103cdf172c75dc9de1fe3bc9adcccc824'}
        for name, pin in pins.items():
            checked_code(ali_root / name, pin)
        import analysis_core as core
        import analysis_runtime as old
        import analysis_quota_pool as pool
        import quota_runtime as runtime
        import source_freeze
        from supplier_knowledge.icbu_quota_only_models_v1 import QwenFreeMatrix
        history = old.history_module()
        auth, cfg, policy_sha = old.authority(history)
        routing, policy, frozen_manifest, frozen, boundary = runtime.context(history, auth, policy_sha)
        if inventory_only:
            # 构造BGEClient会发health请求；空跑仅使用源码已固定的版本字符串。
            engine = SimpleNamespace(model_version='BAAI/bge-m3@5617a9f61b028005a4858fdac845db406aefb181')
        else:
            from hybrid_knowledge_engine import configured_engine
            engine = configured_engine()
        groups = lambda: old.source_groups(history, auth, engine.model_version)[0]
        base_matrix = QwenFreeMatrix(policy['matrix_policy'])
        ali_matrix = runtime.MatrixWithOriginalCredential(base_matrix, boundary)
        channels['alibaba'] = AlibabaAdapter(core, old, pool, ali_matrix, groups,
            auth, boundary, history, engine, quarantine,
            receipts_path=options['alibaba_receipts_path'], request_tokens=options['request_tokens'],
            frozen=frozen, require_new_source=source_freeze.require_new_source, official=official)
    return AdapterHub(channels, quarantine, execution_session=execution_session,
                      official=official, official_options=official_options), manifest, quarantine
