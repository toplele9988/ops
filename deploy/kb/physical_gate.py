"""跨渠道物理请求闸门；额度分账、响应检查点和未知停止均独立持久化。

这里的表和配置是恢复协调器的新契约，不是生产业务表。runner 必须只发起
一次物理请求；check 必须重新核对原授权、hold、熔断及上游额度。没有默认
runner，不加载凭据，不根据另一个账户的余额切换模型。
"""
from __future__ import annotations

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
import time


class GateError(ValueError):
    pass


class GateDeferred(GateError):
    """可以证明本次没有发出请求；仅等待或停该额度桶。"""


class RateLimited(GateDeferred):
    def __init__(self, wait_seconds):
        self.wait_seconds = wait_seconds
        super().__init__('距上次物理请求不足规定间隔，请等待后再检查')


class QuotaExhausted(GateDeferred):
    pass


class ProviderRejected(QuotaExhausted):
    """已收到明确响应；保留收据并停该桶，只有免费耗尽允许配置内续路由。"""
    def __init__(self, code, receipt, retry_after_seconds=None):
        need(code in ('free_tier_exhausted', 'insufficient_balance', 'authentication_failed',
                      'rate_limited', 'model_drift', 'invalid_response'))
        self.code, self.receipt = code, deepcopy(receipt)
        self.retry_after_seconds = retry_after_seconds
        super().__init__('模型请求已有明确拒绝回执，已停止该额度桶；保留收据后核对原因')


class GateStopped(GateError):
    pass


class GateUnknown(GateStopped):
    pass


def need(ok, message='物理请求配置或账本不完整，请保留记录并重新核对'):
    if not ok:
        raise GateError(message)


def canon(value):
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    except (TypeError, ValueError):
        raise GateError('响应或配置不能可靠持久化，请保留账本并核对') from None


def digest(value):
    return hashlib.sha256(canon(value).encode()).hexdigest()


def valid_sha(value):
    return isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value) is not None


def private_path(path, *, exists=True):
    path = Path(path).absolute()
    need('..' not in path.parts and not any(p.is_symlink() for p in (path, *path.parents)),
         '账本路径含上级目录或符号链接，请使用私有普通文件')
    parent = path.parent.stat()
    need(parent.st_uid == os.geteuid() and stat.S_IMODE(parent.st_mode) == 0o700,
         '账本父目录必须由当前用户持有且权限为 0700')
    if exists or path.exists():
        info = path.lstat()
        need(stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid()
             and stat.S_IMODE(info.st_mode) == 0o600 and info.st_nlink == 1,
             '账本必须为当前用户持有的 0600 普通文件且无硬链接')
    return path


def validate_config(config):
    need(isinstance(config, dict) and set(config) == {'schema', 'interval_seconds', 'models', 'buckets'}
         and config['schema'] == 'kb-physical-gate-v1')
    need(type(config['interval_seconds']) in (int, float) and math.isfinite(config['interval_seconds'])
         and config['interval_seconds'] >= 30, '物理请求间隔不得小于 30 秒')
    need(isinstance(config['models'], dict) and bool(config['models'])
         and isinstance(config['buckets'], dict) and bool(config['buckets']))
    for name, bucket in config['buckets'].items():
        need(isinstance(name, str) and re.fullmatch('[a-z][a-z0-9_-]{0,60}', name))
        need(isinstance(bucket, dict) and set(bucket) == {
            'kind', 'verified', 'max_requests', 'max_tokens', 'max_cost_micros'})
        need(bucket['kind'] in ('prepaid', 'free_pool', 'subscription') and type(bucket['verified']) is bool)
        for field in ('max_requests', 'max_tokens', 'max_cost_micros'):
            need(bucket[field] is None or type(bucket[field]) is int and bucket[field] >= 0)
    for name, model in config['models'].items():
        need(isinstance(name, str) and 0 < len(name) <= 128)
        need(isinstance(model, dict) and set(model) == {'bucket', 'frozen', 'cost_micros_per_1m_tokens'}
             and model['bucket'] in config['buckets'] and type(model['frozen']) is bool)
        rate = model['cost_micros_per_1m_tokens']
        need(rate is None or type(rate) is int and rate >= 0)
    return config


class PhysicalGate:
    def __init__(self, path, config, *, clock=time.time):
        self.config = deepcopy(validate_config(config))
        self.clock = clock
        self.path = private_path(path, exists=False)
        with self.locked() as db:
            db.executescript('''BEGIN IMMEDIATE;
                CREATE TABLE IF NOT EXISTS gate_epoch(key TEXT PRIMARY KEY,value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS gate_calls(request_key TEXT PRIMARY KEY,binding_sha256 TEXT NOT NULL,
                    bucket TEXT NOT NULL,model TEXT NOT NULL,state TEXT NOT NULL,tokens INTEGER NOT NULL,
                    cost_micros INTEGER NOT NULL,started_at REAL NOT NULL,completed_at REAL,
                    response_json TEXT,response_sha256 TEXT);
                CREATE TABLE IF NOT EXISTS gate_stops(bucket TEXT PRIMARY KEY,reason TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS gate_rejections(request_key TEXT PRIMARY KEY,code TEXT NOT NULL,
                    receipt_json TEXT NOT NULL,receipt_sha256 TEXT NOT NULL,retry_after_seconds REAL);
                CREATE TABLE IF NOT EXISTS gate_routes(request_key TEXT PRIMARY KEY,receipt_key TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS gate_balances(bucket TEXT PRIMARY KEY,balance_json TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS gate_audit(seq INTEGER PRIMARY KEY,kind TEXT NOT NULL,
                    payload_json TEXT NOT NULL,previous_sha256 TEXT NOT NULL,event_sha256 TEXT NOT NULL);''')
            pin = digest(self.config)
            prior = db.execute("SELECT value FROM gate_epoch WHERE key='config'").fetchone()
            need(prior is None or prior[0] == pin, '请求策略已变化，请保留旧账本并重新审查额度迁移')
            db.execute("INSERT OR IGNORE INTO gate_epoch VALUES('config',?)", (pin,))
            self._verify_audit(db)
            interrupted = db.execute("SELECT request_key FROM gate_calls WHERE state='provider_started'").fetchall()
            if interrupted:
                db.execute("UPDATE gate_calls SET state='unknown' WHERE state='provider_started'")
                self._stop(db, '', 'unknown')
                self._audit(db, 'interrupted_unknown', {'request_keys': [r[0] for r in interrupted]})
            if db.execute("SELECT 1 FROM gate_calls WHERE state='unknown'").fetchone():
                self._stop(db, '', 'unknown')
            db.commit()

    @contextmanager
    def locked(self):
        lock = private_path(str(self.path) + '.lock', exists=False)
        fd = os.open(lock, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise GateDeferred('已有物理请求写者，请等待当前请求结束') from None
            if not self.path.exists():
                new = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_WRONLY, 0o600)
                os.close(new)
            private_path(self.path)
            with closing(sqlite3.connect(self.path, timeout=2)) as db:
                db.row_factory = sqlite3.Row
                db.execute('PRAGMA synchronous=FULL')
                yield db
        finally:
            os.close(fd)

    @staticmethod
    def _verify_audit(db):
        previous = '0' * 64
        for seq, row in enumerate(db.execute('SELECT * FROM gate_audit ORDER BY seq'), 1):
            payload = json.loads(row['payload_json'])
            expected = digest({'seq': seq, 'kind': row['kind'], 'payload': payload, 'previous': previous})
            need(row['seq'] == seq and canon(payload) == row['payload_json']
                 and row['previous_sha256'] == previous and row['event_sha256'] == expected,
                 '物理请求审计损坏，请停止并保留账本')
            previous = expected
        tail = db.execute("SELECT value FROM gate_epoch WHERE key='audit_tail'").fetchone()
        need((previous == '0' * 64 and tail is None) or tail is not None and tail[0] == previous,
             '物理请求审计链不完整，请停止并保留账本')
        return previous

    def _audit(self, db, kind, payload):
        previous = self._verify_audit(db)
        seq = db.execute('SELECT count(*) FROM gate_audit').fetchone()[0] + 1
        event = digest({'seq': seq, 'kind': kind, 'payload': payload, 'previous': previous})
        db.execute('INSERT INTO gate_audit VALUES(?,?,?,?,?)', (seq, kind, canon(payload), previous, event))
        db.execute("INSERT OR REPLACE INTO gate_epoch VALUES('audit_tail',?)", (event,))

    def _stop(self, db, bucket, reason):
        # Unknown has permanent priority over every other stop reason.
        old = db.execute('SELECT reason FROM gate_stops WHERE bucket=?', (bucket,)).fetchone()
        if old is None or reason == 'unknown':
            db.execute('INSERT OR REPLACE INTO gate_stops VALUES(?,?)', (bucket, reason))

    def stop_local(self):
        with self.locked() as db:
            db.execute('BEGIN IMMEDIATE')
            self._stop(db, '', 'local_failure')
            self._audit(db, 'local_failure_stop', {})
            db.commit()

    def stop_global(self, reason='guard_failed'):
        need(reason in ('guard_failed', 'local_failure', 'unknown'))
        with self.locked() as db:
            db.execute('BEGIN IMMEDIATE')
            self._stop(db, '', reason)
            self._audit(db, 'global_stop', {'reason': reason})
            db.commit()

    def _global_check(self, db):
        row = db.execute("SELECT reason FROM gate_stops WHERE bucket=''").fetchone()
        if row is not None:
            if row[0] == 'unknown':
                raise GateUnknown('存在结果未知请求，已停止全队；保留隔离和预留，禁止重发')
            raise GateStopped('物理请求已停止，请保留账本并核对停止原因')
        if db.execute("SELECT 1 FROM gate_calls WHERE state IN ('unknown','provider_started')").fetchone():
            raise GateUnknown('存在未确认请求，禁止继续或重新发送')

    def assert_running(self):
        with self.locked() as db:
            self._verify_audit(db)
            self._global_check(db)

    def record_balance_receipt(self, bucket, receipt):
        with self.locked() as db:
            self._global_check(db)
            db.execute('BEGIN IMMEDIATE')
            self._audit(db, 'balance_receipt', {'bucket': bucket, 'receipt': receipt})
            db.commit()

    def record_balance(self, bucket, balance):
        need(bucket in self.config['buckets'] and self.config['buckets'][bucket]['kind'] == 'prepaid')
        need(isinstance(balance, dict) and type(balance.get('total_micros')) is int
             and balance['total_micros'] >= 0 and type(balance.get('available')) is bool
             and type(balance.get('observed_at')) in (int, float)
             and math.isfinite(balance['observed_at']))
        with self.locked() as db:
            self._global_check(db)
            db.execute('BEGIN IMMEDIATE')
            db.execute('INSERT OR REPLACE INTO gate_balances VALUES(?,?)', (bucket, canon(balance)))
            self._audit(db, 'balance_checked', {'bucket': bucket, 'balance': balance})
            if not balance['available']:
                self._stop(db, bucket, 'insufficient_balance')
            db.commit()

    def stop_bucket(self, bucket, error):
        need(bucket in self.config['buckets'] and isinstance(error, ProviderRejected))
        with self.locked() as db:
            db.execute('BEGIN IMMEDIATE')
            self._stop(db, bucket, error.code)
            self._audit(db, 'balance_rejected', {'bucket': bucket, 'code': error.code,
                                               'receipt': error.receipt,
                                               'retry_after_seconds': error.retry_after_seconds})
            db.commit()

    def _fallback_receipt(self, db, model):
        self._global_check(db)
        need(model in self.config['models'])
        bucket = self.config['models'][model]['bucket']
        if self.config['buckets'][bucket]['kind'] != 'free_pool':
            return None
        for row in db.execute('SELECT r.*,c.model,c.bucket,c.state FROM gate_rejections r '
                              'JOIN gate_calls c USING(request_key) WHERE c.bucket=? '
                              "AND r.code='free_tier_exhausted' ORDER BY c.started_at", (bucket,)):
            need(row['state'] == 'quota_rejected' and digest(json.loads(row['receipt_json'])) == row['receipt_sha256'])
            events = [json.loads(event[0]) for event in db.execute(
                "SELECT payload_json FROM gate_audit WHERE kind='quota_rejected'")]
            matching = [event for event in events if event.get('request_key') == row['request_key']]
            need(len(matching) == 1 and matching[0].get('bucket') == row['bucket']
                 and matching[0].get('code') == row['code']
                 and matching[0].get('receipt_sha256') == row['receipt_sha256'],
                 '免费额度拒绝与原审计不一致，请停止并保留账本')
            # 免费额度按模型分桶；不能用另一模型的拒绝证明本模型额度耗尽。
            if row['model'] == model:
                return row['request_key']
        return None

    def fallback_receipt(self, model):
        with self.locked() as db:
            self._verify_audit(db)
            return self._fallback_receipt(db, model)

    def has_checkpoint(self, item, model):
        with self.locked() as db:
            self._verify_audit(db)
            self._global_check(db)
            row = db.execute('SELECT state FROM gate_calls WHERE request_key=?',
                             (digest({'item': item, 'model': model}),)).fetchone()
            return row is not None and row['state'] == 'complete'

    def _reject(self, db, request_key, bucket, error):
        raw = canon(error.receipt)
        db.execute('BEGIN IMMEDIATE')
        db.execute('INSERT OR IGNORE INTO gate_rejections VALUES(?,?,?,?,?)',
                   (request_key, error.code, raw, digest(error.receipt), error.retry_after_seconds))
        db.execute('UPDATE gate_calls SET state=?,completed_at=? WHERE request_key=?',
                   ('quota_rejected' if error.code == 'free_tier_exhausted' else 'provider_rejected',
                    self.clock(), request_key))
        self._stop(db, bucket, error.code)
        self._audit(db, 'quota_rejected' if error.code == 'free_tier_exhausted' else 'provider_rejected',
                    {'request_key': request_key, 'bucket': bucket, 'code': error.code,
                     'receipt_sha256': digest(error.receipt), 'retry_after_seconds': error.retry_after_seconds})
        db.commit()

    def _allocation(self, model, reservation):
        need(model in self.config['models'], '模型不在已核验名单，请先核对技术契约')
        need(type(reservation) is int and reservation > 0, '保守 token 预留无效，请先核对请求大小')
        spec = self.config['models'][model]
        if spec['frozen']:
            raise QuotaExhausted('模型已冻结，禁止新请求；保留原隔离与审计')
        bucket = self.config['buckets'][spec['bucket']]
        if (not bucket['verified'] or bucket['max_tokens'] is None or bucket['max_requests'] is None
                or bucket['kind'] == 'prepaid' and (
                    bucket['max_cost_micros'] is None or spec['cost_micros_per_1m_tokens'] is None
                    or spec['cost_micros_per_1m_tokens'] <= 0)):
            raise QuotaExhausted('该账户额度或价格尚未核实，已停止该账户请求')
        # A prepaid reservation uses a reviewed worst-case rate for every token;
        # no price, exchange rate or billing unit is inferred here.
        cost = ((reservation * spec['cost_micros_per_1m_tokens'] + 999999) // 1000000
                if bucket['kind'] == 'prepaid' else 0)
        return spec['bucket'], bucket, cost

    def _admit(self, db, model, reservation):
        self._global_check(db)
        bucket_name, bucket, cost = self._allocation(model, reservation)
        if db.execute('SELECT 1 FROM gate_stops WHERE bucket=?', (bucket_name,)).fetchone():
            raise QuotaExhausted('该账户已停止请求，请核对额度；系统不会自动换模型')
        live = db.execute('SELECT balance_json FROM gate_balances WHERE bucket=?', (bucket_name,)).fetchone()
        if live is not None:
            balance = json.loads(live[0])
            pending = db.execute('SELECT coalesce(sum(cost_micros),0) FROM gate_calls '
                                 'WHERE bucket=? AND started_at>=?', (bucket_name, balance['observed_at'])).fetchone()[0]
            if not balance['available'] or cost + pending > balance['total_micros']:
                raise QuotaExhausted('官方预付费余额不足以保守预留，已停止该桶；请核对余额')
        totals = db.execute('SELECT count(*),coalesce(sum(tokens),0),coalesce(sum(cost_micros),0) '
                            'FROM gate_calls WHERE bucket=?', (bucket_name,)).fetchone()
        if (totals[0] + 1 > bucket['max_requests'] or totals[1] + reservation > bucket['max_tokens']
                or bucket['kind'] == 'prepaid' and totals[2] + cost > bucket['max_cost_micros']):
            raise QuotaExhausted('该账户额度已用完，已停止该账户请求；系统不会自动换模型')
        # Wait from the last reliable finish when present. This conservative
        # rule also covers time spent between the durable marker and runner.
        row = db.execute('SELECT max(coalesce(completed_at,started_at)) FROM gate_calls').fetchone()
        now = self.clock()
        need(type(now) in (int, float) and math.isfinite(now) and now > 0)
        if row[0] is not None and now - row[0] < self.config['interval_seconds']:
            raise RateLimited(self.config['interval_seconds'] - (now - row[0]))
        return bucket_name, cost, now

    def ready(self, model, reservation):
        with self.locked() as db:
            self._verify_audit(db)
            try:
                self._admit(db, model, reservation)
            except GateDeferred:
                return False
        return True

    def call(self, item, model, reservation, runner, check, *, request_key=None, check_cached=None,
             validate_response=None, fallback_from=None):
        need(callable(runner) and callable(check), '缺少请求函数或原来源检查，请停止')
        need(check_cached is None or callable(check_cached))
        need(isinstance(item, dict) and item.get('channel') in ('wechat', 'alibaba')
             and isinstance(item.get('source_id'), str) and valid_sha(item.get('prompt_sha256')))
        bound = digest({'item': item, 'model': model})
        request_key = request_key or bound
        need(valid_sha(request_key), '物理请求标识无效，请重新核对稳定单元')
        with self.locked() as db:
            self._verify_audit(db)
            self._global_check(db)
            if fallback_from is not None:
                parent = db.execute('SELECT model FROM gate_calls WHERE request_key=?', (fallback_from,)).fetchone()
                need(parent is not None and self._fallback_receipt(db, parent['model']) == fallback_from
                     and self.config['buckets'][self.config['models'][model]['bucket']]['kind'] == 'prepaid',
                     '缺少明确免费额度拒绝收据，禁止切换付费通道')
            prior = db.execute('SELECT * FROM gate_calls WHERE request_key=?', (request_key,)).fetchone()
            if prior is not None:
                need(prior['binding_sha256'] == bound and prior['tokens'] == reservation,
                     '物理请求标识对应的来源或预留已变化，请停止')
            # Checks run even for checkpoint reuse. A cached answer cannot widen
            # current authorization or bypass a newly discovered source alias.
            # Quota admission belongs only to new requests: a model whose last
            # response used its balance can still supply that saved response.
            try:
                validator = check_cached if prior is not None and check_cached is not None else check
                need(validator(deepcopy(item), model) is True, '原来源或额度检查未通过，请停止并核对')
            except QuotaExhausted:
                if model in self.config['models'] and not self.config['models'][model]['frozen']:
                    db.execute('BEGIN IMMEDIATE')
                    self._stop(db, self.config['models'][model]['bucket'], 'quota_exhausted')
                    self._audit(db, 'bucket_stopped', {'bucket': self.config['models'][model]['bucket']})
                    db.commit()
                raise
            except GateDeferred:
                # A channel with an audited circuit still open has not sent a
                # request. It must not prevent another admitted channel running.
                raise
            except BaseException:
                db.execute('BEGIN IMMEDIATE')
                self._stop(db, '', 'guard_failed')
                self._audit(db, 'guard_failed', {'request_key': request_key})
                db.commit()
                raise GateStopped('原授权、隔离、熔断或额度检查未通过，已停止全队') from None
            if prior is not None:
                if prior['state'] in ('quota_rejected', 'provider_rejected'):
                    raise QuotaExhausted('该请求已被额度拒绝，禁止自动重发或切换模型')
                need(prior['state'] == 'complete' and prior['response_json'] is not None
                     and hashlib.sha256(prior['response_json'].encode()).hexdigest() == prior['response_sha256'],
                     '持久响应不完整，请保留账本并停止')
                saves = [json.loads(event[0]) for event in db.execute(
                    "SELECT payload_json FROM gate_audit WHERE kind='response_saved'")]
                saves = [event for event in saves if event.get('request_key') == request_key]
                need(len(saves) == 1 and saves[0].get('response_sha256') == prior['response_sha256'],
                     '持久响应与原保存审计不一致，请停止并保留检查点')
                response = json.loads(prior['response_json'])
                try:
                    return validate_response(deepcopy(item), model, response) if validate_response else response
                except ProviderRejected as error:
                    self._reject(db, request_key, prior['bucket'], error)
                    raise
            try:
                bucket, cost, now = self._admit(db, model, reservation)
            except QuotaExhausted:
                # A frozen model does not close its whole shared account.
                if model in self.config['models'] and not self.config['models'][model]['frozen']:
                    db.execute('BEGIN IMMEDIATE')
                    self._stop(db, self.config['models'][model]['bucket'], 'quota_exhausted')
                    self._audit(db, 'bucket_stopped', {'bucket': self.config['models'][model]['bucket']})
                    db.commit()
                raise
            db.execute('BEGIN IMMEDIATE')
            if fallback_from is not None:
                db.execute('INSERT OR IGNORE INTO gate_routes VALUES(?,?)', (request_key, fallback_from))
                self._audit(db, 'route_selected', {'request_key': request_key, 'receipt_key': fallback_from,
                                                  'model': model, 'binding_sha256': bound})
            db.execute('INSERT INTO gate_calls VALUES(?,?,?,?,?,?,?,?,NULL,NULL,NULL)',
                       (request_key, bound, bucket, model, 'provider_started', reservation, cost, now))
            self._audit(db, 'provider_started', {'request_key': request_key, 'bucket': bucket,
                                                'model': model, 'tokens': reservation, 'cost_micros': cost})
            db.commit()
            try:
                response = runner(deepcopy(item), model, reservation)
                need(isinstance(response, dict), '模型响应不完整，请保留请求记录并停止')
                raw = canon(response)
                completed_at = self.clock()
                need(type(completed_at) in (int, float) and math.isfinite(completed_at) and completed_at >= now)
                db.execute('BEGIN IMMEDIATE')
                db.execute("UPDATE gate_calls SET state='complete',completed_at=?,response_json=?,response_sha256=? "
                           'WHERE request_key=?', (completed_at, raw, hashlib.sha256(raw.encode()).hexdigest(), request_key))
                self._audit(db, 'response_saved', {'request_key': request_key,
                                                  'response_sha256': hashlib.sha256(raw.encode()).hexdigest()})
                db.commit()
            except ProviderRejected as error:
                db.rollback()
                # 上游已经给出拒绝，和未收到响应的超时严格分开。
                self._reject(db, request_key, bucket, error)
                raise
            except QuotaExhausted:
                db.rollback()
                db.execute('BEGIN IMMEDIATE')
                rejected_at = self.clock()
                need(type(rejected_at) in (int, float) and math.isfinite(rejected_at) and rejected_at >= now)
                db.execute("UPDATE gate_calls SET state='quota_rejected',completed_at=? WHERE request_key=?",
                           (rejected_at, request_key))
                self._stop(db, bucket, 'quota_exhausted')
                self._audit(db, 'quota_rejected', {'request_key': request_key, 'bucket': bucket})
                db.commit()
                raise
            except BaseException:
                db.rollback()
                db.execute('BEGIN IMMEDIATE')
                db.execute("UPDATE gate_calls SET state='unknown' WHERE request_key=?", (request_key,))
                self._stop(db, '', 'unknown')
                self._audit(db, 'unknown_stop', {'request_key': request_key})
                db.commit()
                raise GateUnknown('模型结果未知，已停止全队；保留预留和审计，禁止重发') from None
            try:
                return validate_response(deepcopy(item), model, response) if validate_response else response
            except ProviderRejected as error:
                self._reject(db, request_key, bucket, error)
                raise

    def status(self):
        return self.read_status(self.path)

    @staticmethod
    def read_status(path):
        """真正只读：不创建库、锁文件或目录，也不执行恢复写入。"""
        path = private_path(path)
        with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as db:
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA query_only=ON')
            db.execute('BEGIN')
            PhysicalGate._verify_audit(db)
            rows = db.execute('SELECT bucket,state,count(*) n,sum(tokens) tokens,sum(cost_micros) cost '
                              'FROM gate_calls GROUP BY bucket,state').fetchall()
            buckets = {}
            for row in rows:
                bucket = buckets.setdefault(row['bucket'], {'states': {}, 'reserved_tokens': 0, 'reserved_cost_micros': 0})
                bucket['states'][row['state']] = row['n']
                bucket['reserved_tokens'] += row['tokens']
                bucket['reserved_cost_micros'] += row['cost']
            stop = {row['bucket']: row['reason'] for row in db.execute('SELECT * FROM gate_stops')}
            for name, value in buckets.items():
                value['stop_reason'] = stop.get(name)
            uncertain = any(row['state'] in ('provider_started', 'unknown') for row in rows)
            return {'schema': 'kb-physical-status-v1', 'buckets': buckets,
                    'last_success_at': db.execute("SELECT max(completed_at) FROM gate_calls WHERE state='complete'").fetchone()[0],
                    'last_request_at': db.execute('SELECT max(started_at) FROM gate_calls').fetchone()[0],
                    'stop_reason': 'unknown' if uncertain else stop.get(''), 'bucket_stops': {k: v for k, v in stop.items() if k}}
