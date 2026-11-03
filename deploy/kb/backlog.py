"""停机补处理的离线审阅清单和隔离调度器；没有生产来源适配器或默认模型入口。

本模块的新契约不是生产表结构。审阅方从只读备份重建稳定来源/提示词，
核对全部 checkpoint 和落库步骤后才能生成 review。CLI 只有 build/verify；
Scheduler 仅接受显式注入的来源核验、持久响应读取、本地续做和假请求函数。
真实适配器、预算审批及启动窗口未接入，因此不能直接恢复任何生产服务。
"""
from __future__ import annotations

import argparse
from contextlib import closing, contextmanager
from copy import deepcopy
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import stat
import tempfile
import time

HEX = re.compile(r'[0-9a-f]{64}\Z')
REASONS = {'complete', 'local_pending', 'never_requested', 'unknown', 'time_unconfirmed'}
STEPS = {'response', 'facts', 'index'}
ENTRY_KEYS = {'channel', 'source_id', 'fingerprint', 'source_sha256', 'content_sha256', 'prompt_sha256',
              'source_at', 'admitted_at', 'reason', 'checkpoints', 'completed_steps'}
DEFAULT_DB = '/var/lib/imgw/ops-app/kb-backlog-recovery.sqlite3'


class ReviewError(ValueError):
    pass


def need(ok, message='清单不完整，请重新核对来源和检查点'):
    if not ok:
        raise ReviewError(message)


def canon(value):
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    except (TypeError, ValueError):
        raise ReviewError('清单包含无效值，请重新核对审阅文件') from None


def sha(value):
    return hashlib.sha256(value if isinstance(value, bytes) else value.encode('utf-8')).hexdigest()


def digest(value):
    return sha(canon(value))


def fingerprint(metadata):
    """调用方须传入已核验的完整来源/授权元信息；不替代来源授权。"""
    need(isinstance(metadata, dict) and bool(metadata))
    return digest(metadata)


def response_digest(response):
    # 微信 checkpoint 是 canonical envelope；阿里 checkpoint 可为原始响应文本。
    return sha(response) if isinstance(response, str) else digest(response)


def source_key(source_id):
    need(isinstance(source_id, str) and 0 < len(source_id) <= 1024)
    return sha(source_id)


def is_sha(value):
    return isinstance(value, str) and bool(HEX.fullmatch(value))


def number(value):
    return type(value) in (float, int) and math.isfinite(value) and value > 0


def verify_quarantine(value):
    need(isinstance(value, dict) and set(value) == {'schema', 'holds'})
    need(value['schema'] == 'kb-source-quarantine-v1')
    holds = value['holds']
    need(isinstance(holds, list) and len(holds) == 4)
    need(all(isinstance(h, dict) for h in holds))
    need([h.get('channel') for h in holds].count('wechat') == 3)
    need([h.get('channel') for h in holds].count('alibaba') == 1)
    ids = set()
    for hold in holds:
        need(set(hold) == {'channel', 'request_sha256', 'source_keys', 'source_sha256', 'content_sha256', 'prompt_sha256'})
        need(is_sha(hold['request_sha256']) and is_sha(hold['prompt_sha256']))
        need(hold['request_sha256'] not in ids)
        ids.add(hold['request_sha256'])
        for key in ('source_keys', 'source_sha256', 'content_sha256'):
            values = hold[key]
            need(isinstance(values, list) and bool(values) and all(is_sha(v) for v in values))
            need(len(set(values)) == len(values))
    return value


def quarantine_sets(quarantine):
    verify_quarantine(quarantine)
    return ({v for h in quarantine['holds'] for v in h['source_keys']},
            {v for h in quarantine['holds'] for v in h['content_sha256']},
            {h['prompt_sha256'] for h in quarantine['holds']},
            {v for h in quarantine['holds'] for v in h['source_sha256']})


def assert_not_quarantined(items, quarantine):
    sources, contents, prompts, origins = quarantine_sets(quarantine)
    for item in items:
        need(source_key(item['source_id']) not in sources
             and item['content_sha256'] not in contents and item['prompt_sha256'] not in prompts
             and item['source_sha256'] not in origins,
             '资料命中未知请求隔离，请保留隔离并交人工核对')


def validate_entry(item):
    need(isinstance(item, dict) and set(item) == ENTRY_KEYS)
    need(item['channel'] in ('wechat', 'alibaba'))
    source_key(item['source_id'])
    need(all(is_sha(item[k]) for k in ('fingerprint', 'source_sha256', 'content_sha256', 'prompt_sha256')))
    need(item['source_at'] is None or number(item['source_at']))
    need(number(item['admitted_at']) and isinstance(item['reason'], str) and item['reason'] in REASONS)
    need(isinstance(item['completed_steps'], list) and all(isinstance(s, str) for s in item['completed_steps'])
         and set(item['completed_steps']) <= STEPS)
    need(len(set(item['completed_steps'])) == len(item['completed_steps']))
    checkpoints = item['checkpoints']
    need(isinstance(checkpoints, list))
    keys = set()
    for checkpoint in checkpoints:
        need(isinstance(checkpoint, dict) and set(checkpoint) == {
            'cache_key', 'model', 'response_sha256', 'checkpoint_sha256'})
        need(is_sha(checkpoint['cache_key']) and is_sha(checkpoint['response_sha256'])
             and is_sha(checkpoint['checkpoint_sha256']))
        need(isinstance(checkpoint['model'], str) and 0 < len(checkpoint['model']) <= 128)
        need(checkpoint['cache_key'] not in keys)
        keys.add(checkpoint['cache_key'])
    if item['reason'] == 'complete':
        need(set(item['completed_steps']) == STEPS and bool(checkpoints))
    elif item['reason'] == 'local_pending':
        need(bool(checkpoints) and 'response' in item['completed_steps']
             and set(item['completed_steps']) != STEPS)
    elif item['reason'] == 'never_requested':
        need(not checkpoints and not item['completed_steps'])
    if item['source_at'] is None and item['reason'] != 'unknown':
        need(item['reason'] == 'time_unconfirmed')
    return item


def validate_review(review, quarantine):
    need(isinstance(review, dict) and set(review) == {'schema', 'after', 'snapshots', 'entries'})
    need(review['schema'] == 'kb-backlog-review-v1')
    need(type(review['after']) is int and 0 < review['after'] <= time.time())
    need(isinstance(review['snapshots'], dict) and bool(review['snapshots']))
    need(all(isinstance(k, str) and re.fullmatch(r'[a-z][a-z0-9_-]{0,60}', k) and is_sha(v)
             for k, v in review['snapshots'].items()))
    need(isinstance(review['entries'], list))
    seen = set()
    for item in review['entries']:
        validate_entry(item)
        key = source_key(item['source_id'])
        need(key not in seen, '同一资料出现多次，请先合并已完成检查点')
        seen.add(key)
        if item['reason'] not in ('unknown', 'time_unconfirmed'):
            assert_not_quarantined([item], quarantine)
        if item['reason'] == 'unknown':
            sources, contents, prompts, origins = quarantine_sets(quarantine)
            need(key in sources or item['content_sha256'] in contents or item['prompt_sha256'] in prompts
                 or item['source_sha256'] in origins,
                 '未知资料缺少隔离证据，请先补齐隔离清单')
    verify_quarantine(quarantine)
    return review


def make_manifest(review, quarantine):
    validate_review(review, quarantine)
    return {'schema': 'kb-backlog-manifest-v1', 'after': review['after'],
            'quarantine_sha256': digest(quarantine), 'review_sha256': digest(review),
            'snapshots': deepcopy(review['snapshots']),
            'items': deepcopy([i for i in review['entries'] if i['reason'] not in ('unknown', 'time_unconfirmed')]),
            'excluded': deepcopy([i for i in review['entries'] if i['reason'] in ('unknown', 'time_unconfirmed')])}


def validate_manifest(manifest, expected_sha256, quarantine):
    need(is_sha(expected_sha256) and digest(manifest) == expected_sha256,
         '冻结清单校验失败，请停止并重新审查原始清单')
    need(isinstance(manifest, dict) and set(manifest) == {
        'schema', 'after', 'quarantine_sha256', 'review_sha256', 'snapshots', 'items', 'excluded'})
    need(manifest['schema'] == 'kb-backlog-manifest-v1'
         and manifest['quarantine_sha256'] == digest(verify_quarantine(quarantine)))
    need(is_sha(manifest['review_sha256']))
    need(isinstance(manifest['items'], list) and isinstance(manifest['excluded'], list))
    need(all(isinstance(i, dict) for i in manifest['items'] + manifest['excluded']))
    need(all(i.get('reason') in ('complete', 'local_pending', 'never_requested') for i in manifest['items']))
    need(all(i.get('reason') in ('unknown', 'time_unconfirmed') for i in manifest['excluded']))
    # Partition order may differ from the review, so its original digest remains
    # an audit pin; each source/checkpoint is independently revalidated here.
    review = {'schema': 'kb-backlog-review-v1', 'after': manifest['after'],
              'snapshots': manifest['snapshots'], 'entries': manifest['items'] + manifest['excluded']}
    validate_review(review, quarantine)
    return manifest


def private_path(path, *, temporary=False, exists=True):
    path = Path(path).absolute()
    need('..' not in path.parts, '路径包含上级目录，请使用明确的私有文件路径')
    need(not temporary or path.is_relative_to('/tmp'), '只接受 /tmp 中的私有审阅副本')
    need(not any(p.is_symlink() for p in (path, *path.parents)), '路径含符号链接，请使用私有普通文件')
    parent = path.parent.stat()
    need(parent.st_uid == os.geteuid() and stat.S_IMODE(parent.st_mode) == 0o700,
         '父目录必须由当前用户持有且权限为 0700')
    if exists or path.exists():
        info = path.lstat()
        need(stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid()
             and stat.S_IMODE(info.st_mode) == 0o600 and info.st_nlink == 1,
             '文件必须为当前用户持有的 0600 普通文件且无硬链接')
    return path


def read_private(path):
    path = private_path(path, temporary=True)
    need(path.stat().st_size <= 32 * 1024 * 1024, '审阅文件过大，请分批审阅')
    return json.loads(path.read_text())


def validate_policy(policy):
    need(isinstance(policy, dict) and set(policy) == {'models', 'max_requests', 'max_tokens',
         'request_tokens', 'batch_size', 'interval_seconds', 'max_consecutive_failures'},
         '新增模型预算尚未批准，请先填写明确额度与批次')
    need(isinstance(policy['models'], list) and bool(policy['models'])
         and all(isinstance(m, str) and bool(m) for m in policy['models']))
    for key in ('max_requests', 'max_tokens', 'request_tokens', 'batch_size', 'max_consecutive_failures'):
        need(type(policy[key]) is int and policy[key] > 0, '模型额度或批次无效，请重新核对')
    need(type(policy['interval_seconds']) in (int, float) and math.isfinite(policy['interval_seconds'])
         and policy['interval_seconds'] >= 1, '请求间隔无效，请至少间隔 1 秒')
    need(policy['request_tokens'] <= policy['max_tokens'])
    return policy


class Scheduler:
    """串行协调器。callbacks 必须自行保证本地落库幂等，且不能隐藏模型调用。

    read_source(item) 返回与清单相同的来源/检查点元信息；load_checkpoint(item)
    返回逐个 {checkpoint_sha256,response_sha256,response}；apply_local(item,replies)
    只允许本地步骤。request(item,model,reservation) 仅由显式预算准入，返回
    JSON 响应。断线或进程死于 request 之后，provider_started 永久转 unknown；
    不会因重启、换窗口、换版本、换模型重发。每轮先处理最多 policy.batch_size
    个新增，再处理最多 backlog_limit 个积压；两路共享预算与限速，首次请求将
    两个批次上限共同冻结。生产适配器仍待审查。
    """
    def __init__(self, manifest, expected_sha256, quarantine, *, path=None):
        validate_manifest(manifest, expected_sha256, quarantine)
        self.manifest = deepcopy(manifest)
        self.expected = expected_sha256
        self.quarantine = deepcopy(quarantine)
        self.path = private_path(path or os.environ.get('OPS_KB_BACKLOG_DB', DEFAULT_DB), exists=False)
        self.items = {source_key(i['source_id']): deepcopy(i) for i in manifest['items']}
        with self.locked() as db:
            db.executescript('''BEGIN IMMEDIATE;
                CREATE TABLE IF NOT EXISTS epoch(key TEXT PRIMARY KEY,value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS work(source_key TEXT PRIMARY KEY,item_sha256 TEXT NOT NULL,item_json TEXT NOT NULL,
                    lane TEXT NOT NULL,state TEXT NOT NULL,response_json TEXT,response_sha256 TEXT);
                CREATE TABLE IF NOT EXISTS usage(key TEXT PRIMARY KEY,value REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS audit(seq INTEGER PRIMARY KEY,kind TEXT NOT NULL,
                    payload_json TEXT NOT NULL,payload_sha256 TEXT NOT NULL,
                    previous_sha256 TEXT NOT NULL,event_sha256 TEXT NOT NULL);''')
            for key, value in [('manifest', expected_sha256), ('after', str(manifest['after'])),
                               ('quarantine', digest(quarantine))]:
                row = db.execute('SELECT value FROM epoch WHERE key=?', (key,)).fetchone()
                need(row is None or row[0] == value, '恢复边界或冻结清单已变化，请保留旧进度并重新审查')
                db.execute('INSERT OR IGNORE INTO epoch VALUES(?,?)', (key, value))
            for key, item in self.items.items():
                row = db.execute('SELECT item_sha256 FROM work WHERE source_key=?', (key,)).fetchone()
                need(row is None or row[0] == digest(item), '资料指纹已变化，请停止并核对')
                db.execute('INSERT OR IGNORE INTO work VALUES(?,?,?,?,?,NULL,NULL)',
                           (key, digest(item), canon(item), 'backlog', 'done' if item['reason'] == 'complete' else 'pending'))
            self._audit_events(db)
            for stored in db.execute('SELECT * FROM work'):
                self._restore_item(stored)
            db.execute("UPDATE work SET state='unknown' WHERE state='provider_started'")
            if db.execute("SELECT 1 FROM work WHERE state='unknown'").fetchone():
                db.execute("INSERT OR REPLACE INTO epoch VALUES('stop','unknown')")
            db.commit()

    def _restore_item(self, row):
        item = validate_entry(json.loads(row['item_json']))
        key = source_key(item['source_id'])
        need(key == row['source_key'] and digest(item) == row['item_sha256'],
             '持久资料计划校验失败，请保留进度并重新核对')
        need(key not in self.items or digest(self.items[key]) == digest(item),
             '资料计划与冻结清单不一致，请停止并核对')
        self.items[key] = item
        return item

    def _audit_events(self, db):
        events = []
        previous = '0' * 64
        for expected_seq, row in enumerate(db.execute('SELECT * FROM audit ORDER BY seq'), 1):
            payload = json.loads(row['payload_json'])
            bound = {'seq': expected_seq, 'kind': row['kind'],
                     'payload_sha256': digest(payload), 'previous_sha256': previous}
            need(row['seq'] == expected_seq and canon(payload) == row['payload_json']
                 and row['payload_sha256'] == bound['payload_sha256']
                 and row['previous_sha256'] == previous and row['event_sha256'] == digest(bound),
                 '补处理审计校验失败，请保留账本并重新核对')
            previous = row['event_sha256']
            events.append({'kind': row['kind'], 'payload': payload, 'event_sha256': previous})
        tail = db.execute("SELECT value FROM epoch WHERE key='audit_tail'").fetchone()
        need((not events and tail is None) or (bool(events) and tail is not None and tail[0] == previous),
             '补处理审计链不完整，请保留账本并重新核对')
        return events

    def _append_audit(self, db, kind, payload):
        events = self._audit_events(db)
        previous = events[-1]['event_sha256'] if events else '0' * 64
        seq = len(events) + 1
        event_sha = digest({'seq': seq, 'kind': kind, 'payload_sha256': digest(payload),
                            'previous_sha256': previous})
        db.execute('INSERT INTO audit VALUES(?,?,?,?,?,?)',
                   (seq, kind, canon(payload), digest(payload), previous, event_sha))
        db.execute("INSERT OR REPLACE INTO epoch VALUES('audit_tail',?)", (event_sha,))

    def _local_binding(self, row):
        item = self._restore_item(row)
        need(row['state'] in ('pending', 'local_pending')
             and (row['response_json'] is not None or item['reason'] == 'local_pending'),
             '待恢复资料没有可靠响应，请保留隔离并重新核对')
        if row['response_json'] is not None:
            need(sha(row['response_json']) == row['response_sha256'],
                 '本地响应检查点损坏，请停止并核对')
        return {'manifest_sha256': self.expected, 'quarantine_sha256': digest(self.quarantine),
                'source_key': row['source_key'], 'item_sha256': row['item_sha256'],
                'fingerprint': item['fingerprint'], 'source_sha256': item['source_sha256'],
                'content_sha256': item['content_sha256'], 'prompt_sha256': item['prompt_sha256'],
                'checkpoints_sha256': digest(item['checkpoints']),
                'completed_steps_sha256': digest(item['completed_steps']),
                'response_sha256': row['response_sha256']}

    def resume_local(self, expected_manifest_sha, proof_sha256, verify_local):
        """只恢复经核对的本地失败；未知请求、预算和付费准入一律不解除。

        verify_local(context) 必须重新核对真实来源授权、当前隔离清单、原响应
        checkpoint 及本地幂等落库收据，成功返回该收据的 proof_sha256。它只能
        做本地只读核对，不能请求模型。本方法没有 CLI 或默认核对器。
        """
        need(expected_manifest_sha == self.expected and is_sha(proof_sha256),
             '恢复清单或核对收据不正确，请重新审查')
        validate_manifest(self.manifest, expected_manifest_sha, self.quarantine)
        need(callable(verify_local), '缺少本地核对方法，请先核对落库收据')
        with self.locked() as db:
            events = self._audit_events(db)
            stop = db.execute("SELECT value FROM epoch WHERE key='stop'").fetchone()
            need(stop is not None and stop[0] == 'local_failure',
                 '当前停止原因不能本地恢复；未知请求必须永久隔离')
            need(not db.execute("SELECT 1 FROM work WHERE state IN ('unknown','provider_started')").fetchone(),
                 '存在未知请求，不能解除停止或重新发送')
            failures = [event for event in events if event['kind'] == 'local_failure_stop']
            need(bool(failures), '缺少原本地失败审计，请保留停止并重新核对')
            failure = failures[-1]
            row = db.execute('SELECT * FROM work WHERE source_key=?',
                             (failure['payload']['source_key'],)).fetchone()
            need(row is not None, '待恢复资料已变化，请保留停止并重新核对')
            bound = self._local_binding(row)
            need(bound == failure['payload'], '来源或检查点与停止审计不一致，请保留停止并重新核对')
            context = {'failure_event_sha256': failure['event_sha256'],
                       'failure': bound, 'proof_sha256': proof_sha256}
            try:
                verified = verify_local(deepcopy(context))
            except Exception:
                raise ReviewError('本地核对未通过，请保留停止并检查落库收据') from None
            need(isinstance(verified, str) and verified == proof_sha256,
                 '本地核对收据不匹配，请保留停止并重新核对')
            db.execute('BEGIN IMMEDIATE')
            current = db.execute('SELECT * FROM work WHERE source_key=?', (bound['source_key'],)).fetchone()
            need(current is not None and self._local_binding(current) == bound,
                 '核对期间资料发生变化，请保留停止并重新审查')
            self._append_audit(db, 'local_failure_resumed', context)
            changed = db.execute("DELETE FROM epoch WHERE key='stop' AND value='local_failure'")
            need(changed.rowcount == 1, '停止原因已变化，请保留停止并重新核对')
            db.execute("INSERT OR REPLACE INTO usage VALUES('failures',0)")
            db.commit()

    @contextmanager
    def locked(self):
        lock = private_path(str(self.path) + '.lock', exists=False)
        fd = os.open(lock, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if not self.path.exists():
                new = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_WRONLY, 0o600)
                os.close(new)
            private_path(self.path)
            with closing(sqlite3.connect(self.path, timeout=2)) as db:
                db.row_factory = sqlite3.Row
                db.execute('PRAGMA synchronous=FULL')
                yield db
        except BlockingIOError:
            raise ReviewError('已有补处理写者，请等待该批次结束') from None
        finally:
            os.close(fd)

    def admit_live(self, items, *, current_quarantine):
        need(digest(current_quarantine) == digest(self.quarantine), '隔离清单已变化，请立即停止')
        validated = deepcopy(items)
        for item in validated:
            validate_entry(item)
            need(item['reason'] in ('never_requested', 'local_pending', 'complete'))
            need(item['source_at'] is not None and item['source_at'] > self.manifest['after']
                 and item['admitted_at'] > self.manifest['after'], '新增资料没有通过固定恢复边界')
        assert_not_quarantined(validated, current_quarantine)
        with self.locked() as db:
            db.execute('BEGIN IMMEDIATE')
            for item in validated:
                key = source_key(item['source_id'])
                row = db.execute('SELECT item_sha256 FROM work WHERE source_key=?', (key,)).fetchone()
                need(row is None or row[0] == digest(item), '同一资料指纹或处理计划已变化，请核对后再继续')
                db.execute('INSERT OR IGNORE INTO work VALUES(?,?,?,?,?,NULL,NULL)',
                           (key, digest(item), canon(item), 'live', 'done' if item['reason'] == 'complete' else 'pending'))
            db.commit()
        self.items.update({source_key(i['source_id']): i for i in validated})

    def progress(self):
        with self.locked() as db:
            return {(r['lane'], r['state']): r['n'] for r in db.execute(
                'SELECT lane,state,count(*) n FROM work GROUP BY lane,state')}

    def run_batch(self, *, read_source, load_checkpoint, apply_local, request=None,
                  policy=None, backlog_limit=1, guard=None, clock=time.time):
        need(type(backlog_limit) is int and backlog_limit > 0)
        if policy is not None:
            validate_policy(policy)
        # Live work runs first, with separate backlog slots reserved each round.
        # Both lanes still share the approved call/token budget and rate limit.
        live_limit = policy['batch_size'] if policy else backlog_limit
        done = 0
        live_done = 0
        backlog_done = 0
        policy_pin = digest({'policy': policy, 'backlog_limit': backlog_limit}) if policy else None
        with self.locked() as db:
            self._audit_events(db)
            def preflight(item):
                validate_manifest(self.manifest, self.expected, self.quarantine)
                current = guard() if guard else self.quarantine
                need(digest(current) == digest(self.quarantine), '隔离清单已变化，请立即停止')
                assert_not_quarantined([item], current)
                need(read_source(deepcopy(item)) == item, '来源或检查点已变化，请停止并重新核对')

            stop = db.execute("SELECT value FROM epoch WHERE key='stop'").fetchone()
            need(stop is None, '补处理已停止，请保留账本和未知请求，交人工核对')
            candidates = db.execute("SELECT * FROM work WHERE state IN ('pending','local_pending')").fetchall()
            for stored in candidates:
                self._restore_item(stored)
            candidates.sort(key=lambda r: (r['lane'] != 'live',
                            r['response_json'] is None and self.items[r['source_key']]['reason'] != 'local_pending',
                            r['source_key']))
            for row in candidates:
                if row['lane'] == 'live' and live_done >= live_limit:
                    continue
                if row['lane'] == 'backlog' and backlog_done >= backlog_limit:
                    continue
                key = row['source_key']
                item = self.items[key]
                preflight(item)
                replies = None
                if row['response_json'] is not None:
                    need(sha(row['response_json']) == row['response_sha256'], '本地响应检查点损坏，请停止并核对')
                    replies = json.loads(row['response_json'])
                elif item['reason'] == 'local_pending':
                    replies = load_checkpoint(deepcopy(item))
                    need(isinstance(replies, list) and len(replies) == len(item['checkpoints']))
                    for reply, bound in zip(replies, item['checkpoints']):
                        need(set(reply) == {'response', 'response_sha256', 'checkpoint_sha256'})
                        need(reply['checkpoint_sha256'] == bound['checkpoint_sha256']
                             and reply['response_sha256'] == bound['response_sha256']
                             and response_digest(reply['response']) == bound['response_sha256'],
                             '持久响应与清单不一致，请停止并核对')
                else:
                    if request is None or policy is None:
                        continue
                    def amount(name):
                        value = db.execute('SELECT value FROM usage WHERE key=?', (name,)).fetchone()
                        return value[0] if value else 0
                    now = clock()
                    if (amount('requests') >= policy['max_requests']
                            or amount('tokens') + policy['request_tokens'] > policy['max_tokens']
                            or (amount('last_request') and now - amount('last_request') < policy['interval_seconds'])):
                        continue
                    pinned = db.execute("SELECT value FROM epoch WHERE key='policy'").fetchone()
                    need(pinned is None or pinned[0] == policy_pin, '模型或预算策略已变化，请重新审查')
                    db.execute('BEGIN IMMEDIATE')
                    db.execute("INSERT OR IGNORE INTO epoch VALUES('policy',?)", (policy_pin,))
                    db.execute("UPDATE work SET state='provider_started' WHERE source_key=?", (key,))
                    for name, value in [('requests', amount('requests') + 1),
                                        ('tokens', amount('tokens') + policy['request_tokens']), ('last_request', now)]:
                        db.execute('INSERT OR REPLACE INTO usage VALUES(?,?)', (name, value))
                    db.commit()
                    try:
                        replies = [request(deepcopy(item), policy['models'][0], policy['request_tokens'])]
                        raw = canon(replies)
                    except BaseException:
                        db.execute("UPDATE work SET state='unknown' WHERE source_key=?", (key,))
                        db.execute("INSERT OR REPLACE INTO epoch VALUES('stop','unknown')")
                        db.commit()
                        raise ReviewError('模型结果未知，已永久停止重发；请保留预留和审计') from None
                    db.execute('BEGIN IMMEDIATE')
                    db.execute("UPDATE work SET state='local_pending',response_json=?,response_sha256=? "
                               'WHERE source_key=?', (raw, sha(raw), key))
                    db.commit()
                preflight(item)
                try:
                    apply_local(deepcopy(item), deepcopy(replies))
                except Exception:
                    failed = db.execute("SELECT value FROM usage WHERE key='failures'").fetchone()
                    failures = int(failed[0] if failed else 0) + 1
                    db.execute("INSERT OR REPLACE INTO usage VALUES('failures',?)", (failures,))
                    if failures >= (policy['max_consecutive_failures'] if policy else 1):
                        db.execute("INSERT OR REPLACE INTO epoch VALUES('stop','local_failure')")
                        failed_row = db.execute('SELECT * FROM work WHERE source_key=?', (key,)).fetchone()
                        self._append_audit(db, 'local_failure_stop', self._local_binding(failed_row))
                    db.commit()
                    raise ReviewError('本地续做失败，已保留响应检查点；请检查落库后再恢复') from None
                db.execute('BEGIN IMMEDIATE')
                db.execute("UPDATE work SET state='done' WHERE source_key=?", (key,))
                db.execute("INSERT OR REPLACE INTO usage VALUES('failures',0)")
                # This watermark is observation only; all pending items still use
                # their source keys, so timestamp ties never hide late arrivals.
                mark = 'watermark_' + row['lane']
                old = db.execute('SELECT value FROM usage WHERE key=?', (mark,)).fetchone()
                db.execute('INSERT OR REPLACE INTO usage VALUES(?,?)',
                           (mark, max(old[0] if old else 0, item['admitted_at'])))
                db.commit()
                done += 1
                backlog_done += row['lane'] == 'backlog'
                live_done += row['lane'] == 'live'
        return done


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('build', 'verify'))
    parser.add_argument('--input', type=Path, required=True, help='/tmp 私有审阅或冻结清单')
    parser.add_argument('--quarantine', type=Path, required=True, help='/tmp 私有未知隔离清单')
    parser.add_argument('--sha256', help='审阅者独立记录的冻结清单 SHA-256')
    args = parser.parse_args()
    value = read_private(args.input)
    quarantine = read_private(args.quarantine)
    if args.command == 'build':
        manifest = make_manifest(value, quarantine)
        output = Path(tempfile.mkdtemp(prefix='kb-backlog-review-')) / 'manifest.json'
        fd = os.open(output, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'w') as stream:
            stream.write(canon(manifest))
        print(canon({'结果': '已生成待审冻结清单，未启动处理', '清单': str(output),
                     'sha256': digest(manifest), '可续做或待审批': len(manifest['items']),
                     '隔离或时间待确认': len(manifest['excluded'])}))
    else:
        validate_manifest(value, args.sha256, quarantine)
        print(canon({'结果': '冻结清单校验通过，未请求模型'}))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, KeyError, TypeError, sqlite3.Error):
        raise SystemExit('清单校验失败，请核对私有副本、固定边界和未知隔离；未启动处理') from None
