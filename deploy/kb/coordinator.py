"""独立恢复协调器：复用冻结清单和来源闸门，每名读者单独持久请求。

默认 CLI 只读状态。生产装配只通过已审查的 production_adapter，不能从配置
导入任意模块；原调用账本、未知隔离、熔断和水位均不清理。
"""
from __future__ import annotations

import argparse
from contextlib import closing, nullcontext
from copy import deepcopy
import json
import os
from pathlib import Path
import signal
import sqlite3
import time

import backlog as b
import source_gate
from physical_gate import PhysicalGate, GateDeferred, GateUnknown, QuotaExhausted

DEFAULT_CONFIG = '/etc/imgw/kb-recovery-coordinator.json'


def validate_config(config):
    b.need(isinstance(config, dict) and config.get('schema') == 'kb-recovery-coordinator-v1',
           '协调器配置版本不正确，请重新核对候选配置')
    b.need(type(config.get('backlog_limit')) is int and 0 < config['backlog_limit'] <= 2,
           '积压每批最多 2 个单元，请核对配置')
    b.need(type(config.get('live_limit')) is int and config['live_limit'] > 0)
    b.need(type(config.get('max_consecutive_failures')) is int and config['max_consecutive_failures'] == 1
           and config.get('stop_on_unknown') is True and config.get('auto_fallback') is False,
           '未知结果或本地失败必须停止，不能自动切换模型')
    b.need(type(config.get('poll_seconds')) in (int, float)
           and 1 <= config['poll_seconds'] <= 60)
    b.need(config.get('physical', {}).get('interval_seconds', 0) >= 30,
           '模型物理请求至少间隔 30 秒')
    routes = config.get('routes', {})
    b.need(isinstance(routes, dict) and set(routes) <= {'wechat', 'alibaba'})
    models, buckets = config['physical']['models'], config['physical']['buckets']
    for channel, readers in routes.items():
        b.need(isinstance(readers, list) and len(readers) == (2 if channel == 'wechat' else 1))
        seen = set()
        for route in readers:
            b.need(isinstance(route, list) and 1 <= len(route) <= 2
                   and all(isinstance(model, str) and model in models for model in route)
                   and not seen.intersection(route), '微信各读者可选模型不能相同，请核对独立复核配置')
            seen.update(route)
            if len(route) == 2:
                b.need(buckets[models[route[0]]['bucket']]['kind'] == 'free_pool'
                       and buckets[models[route[1]]['bucket']]['kind'] == 'prepaid'
                       and models[route[0]]['bucket'] != models[route[1]]['bucket'],
                       '路由只允许明确免费耗尽后续接独立预付费桶')
    return config


class Coordinator:
    """两个渠道共享一个 work 库及物理闸门；锁覆盖一轮，本地回调须幂等。"""

    def __init__(self, scheduler, gate, adapter, config, *, clock=time.time, stop=None):
        self.scheduler, self.gate, self.adapter = scheduler, gate, adapter
        self.config = deepcopy(validate_config(config))
        self.clock = clock
        self.stop = stop or (lambda: False)
        with scheduler.locked() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('CREATE TABLE IF NOT EXISTS reader_plans('
                       'source_key TEXT PRIMARY KEY,plan_json TEXT NOT NULL,plan_sha256 TEXT NOT NULL)')
            pin = b.digest(config)
            old = db.execute("SELECT value FROM epoch WHERE key='coordinator'").fetchone()
            b.need(old is None or old[0] == pin, '协调器策略已变化，请保留进度并重新审查')
            db.execute("INSERT OR IGNORE INTO epoch VALUES('coordinator',?)", (pin,))
            db.commit()

    def _guard(self, item=None):
        if self.config.get('release_sha256'):
            from verify_coordinator import verify_runtime
            verify_runtime(self.config['release_sha256'])
        current = self.adapter.preflight()
        source_gate.verify(current)
        b.need(b.digest(current) == b.digest(self.scheduler.quarantine),
               '隔离或原请求状态已变化，请立即停止并交总指挥核对')
        b.validate_manifest(self.scheduler.manifest, self.scheduler.expected, current)
        if item is not None:
            b.assert_not_quarantined([item], current)
            b.need(self.adapter.read_source(deepcopy(item)) == item,
                   '来源、权限或检查点已变化，请停止并重新核对')
        return current

    def _round_guard(self, item=None):
        try:
            return self._guard(item)
        except Exception:
            self.gate.stop_global('guard_failed')
            raise b.ReviewError('来源或运行状态核对未通过，已停止全队；请保留审计核查') from None

    def _round_guard_batch(self, items):
        # 适配器支持批量核验时，同一轮来源共用一次重建；否则逐条核验，语义不变。
        if not hasattr(self.adapter, 'read_sources'):
            for item in items:
                self._round_guard(item)
            return
        try:
            current = self._guard()
            if items:
                b.assert_not_quarantined(items, current)
                b.need(self.adapter.read_sources(deepcopy(items)) == items,
                       '来源、权限或检查点已变化，请停止并重新核对')
        except Exception:
            self.gate.stop_global('guard_failed')
            raise b.ReviewError('来源或运行状态核对未通过，已停止全队；请保留审计核查') from None

    def discover(self):
        try:
            return self._discover()
        except GateDeferred:
            raise
        except Exception:
            self._stop_uncertain()
            raise

    def _stop_uncertain(self):
        if self.gate.status()['stop_reason'] is None:
            self.gate.stop_global('guard_failed')

    def _discover(self):
        self.gate.assert_running()
        current = self._round_guard()
        items = self.adapter.discover_live(self.scheduler.manifest['after'])
        # Source checks precede admission, including rows already present in work.
        self._round_guard_batch(items)
        self.scheduler.admit_live(items, current_quarantine=current)

    def _plan(self, db, key, item):
        old = db.execute('SELECT * FROM reader_plans WHERE source_key=?', (key,)).fetchone()
        if old is not None:
            b.need(b.sha(old['plan_json']) == old['plan_sha256'],
                   '持久读者计划损坏，请保留检查点并重新审查')
            plan = json.loads(old['plan_json'])
        else:
            plan = self.adapter.plan(deepcopy(item))
        b.need(isinstance(plan, list) and len(plan) == (2 if item['channel'] == 'wechat' else 1),
               '读者数量不符合渠道契约，请核对稳定单元')
        b.need(all(isinstance(r, dict) and set(r) == {'model', 'reservation'}
                   and isinstance(r['model'], str) and type(r['reservation']) is int
                   and r['reservation'] > 0 for r in plan))
        b.need(len({r['model'] for r in plan}) == len(plan),
               '微信需要两名不同模型读者，请核对配置')
        routes = self.config.get('routes', {}).get(item['channel'])
        b.need(routes is None or [r['model'] for r in plan] == [route[0] for route in routes],
               '持久读者计划与受审路由不一致，请保留检查点核对')
        raw = b.canon(plan)
        b.need(old is None or (old['plan_json'] == raw and old['plan_sha256'] == b.sha(raw)),
               '读者或保守预留已变化，请保留检查点并重新审查')
        db.execute('INSERT OR IGNORE INTO reader_plans VALUES(?,?,?)', (key, raw, b.sha(raw)))
        db.commit()
        return plan

    def _checkpoint(self, item):
        replies = self.adapter.load_checkpoint(deepcopy(item))
        b.need(isinstance(replies, list) and len(replies) == len(item['checkpoints']))
        for reply, bound in zip(replies, item['checkpoints']):
            b.need(set(reply) == {'response', 'response_sha256', 'checkpoint_sha256'}
                   and reply['checkpoint_sha256'] == bound['checkpoint_sha256']
                   and reply['response_sha256'] == bound['response_sha256']
                   and b.response_digest(reply['response']) == bound['response_sha256'],
                   '持久响应与冻结清单不一致，请停止并核对')
        return replies

    def _call_reader(self, item, reader, position, runner):
        routes = self.config.get('routes', {}).get(item['channel'])
        route = routes[position] if routes else [reader['model']]
        receipt = (self.gate.fallback_receipt(route[0]) if len(route) == 2
                   and not self.gate.has_checkpoint(item, route[0]) else None)
        model = route[1] if receipt else route[0]
        try:
            return self.gate.call(item, model, reader['reservation'], runner, self._request_guard,
                check_cached=lambda checked, chosen: bool(self._guard(checked)),
                validate_response=getattr(self.adapter, 'validate_response', None), fallback_from=receipt)
        except QuotaExhausted:
            # 只有已持久的明确免费耗尽回执能够关闭原请求并准入新请求。
            # 本地预算、认证、余额、限流、模型漂移以及未知均没有这项权利。
            if len(route) == 2 and model == route[0]:
                receipt = self.gate.fallback_receipt(model)
                if receipt:
                    return self.gate.call(item, route[1], reader['reservation'], runner, self._request_guard,
                        check_cached=lambda checked, chosen: bool(self._guard(checked)),
                        validate_response=getattr(self.adapter, 'validate_response', None), fallback_from=receipt)
            raise

    def run_round(self):
        try:
            return self._run_round()
        except GateDeferred:
            raise
        except Exception:
            self._stop_uncertain()
            raise

    def _run_round(self):
        self.gate.assert_running()
        self._round_guard()  # No work or provider activity before each fresh safety check.
        if hasattr(self.adapter, 'refresh_balance'):
            self.adapter.refresh_balance(self.gate)
        s = self.scheduler
        completed = 0
        used = {'live': 0, 'backlog': 0}
        limits = {'live': self.config['live_limit'], 'backlog': self.config['backlog_limit']}
        with s.locked() as db:
            s._audit_events(db)
            b.need(db.execute("SELECT value FROM epoch WHERE key='stop'").fetchone() is None,
                   '补处理已停止，请保留账本并交总指挥核对')
            self.gate.assert_running()
            if hasattr(self.adapter, 'channel_status'):
                db.execute("INSERT OR REPLACE INTO epoch VALUES('channel_status',?)",
                           (b.canon(self.adapter.channel_status()),))
                db.commit()
            rows = db.execute("SELECT * FROM work WHERE state IN ('pending','local_pending')").fetchall()
            rows.sort(key=lambda r: (r['lane'] != 'live', r['source_key']))
            for row in rows:
                if self.stop():
                    break
                lane, key = row['lane'], row['source_key']
                if used[lane] >= limits[lane]:
                    continue
                item = s._restore_item(row)
                self._round_guard(item)
                if hasattr(self.adapter, 'check_channel'):
                    try:
                        self.adapter.check_channel(deepcopy(item))
                    except GateDeferred:
                        continue
                used[lane] += 1
                replies = None
                if row['response_json'] is not None:
                    b.need(b.sha(row['response_json']) == row['response_sha256'],
                           '本地响应检查点损坏，请停止并核对')
                    replies = json.loads(row['response_json'])
                elif item['reason'] == 'local_pending':
                    replies = self._checkpoint(item)
                else:
                    try:
                        plan = self._plan(db, key, item)
                    except GateDeferred:
                        used[lane] -= 1
                        continue
                    replies = []
                    physical_started = 0
                    def tracked_request(checked, model, reservation):
                        nonlocal physical_started
                        physical_started += 1
                        return self.adapter.request(checked, model, reservation)
                    try:
                        for position, reader in enumerate(plan):
                            if self.stop():
                                return completed
                            reply = self._call_reader(item, reader, position, tracked_request)
                            replies.append(reply)
                    except QuotaExhausted:
                        if not physical_started:
                            used[lane] -= 1
                        continue
                    except GateDeferred:
                        continue
                    except GateUnknown:
                        db.execute("UPDATE work SET state='unknown' WHERE source_key=?", (key,))
                        db.execute("INSERT OR REPLACE INTO epoch VALUES('stop','unknown')")
                        s._append_audit(db, 'unknown_stop', {'source_key': key})
                        db.commit()
                        raise
                    raw = b.canon(replies)
                    db.execute("UPDATE work SET state='local_pending',response_json=?,response_sha256=? "
                               'WHERE source_key=?', (raw, b.sha(raw), key))
                    db.commit()
                self._round_guard(item)
                if self.stop():
                    return completed
                try:
                    self.adapter.apply_local(deepcopy(item), deepcopy(replies))
                except Exception:
                    # Keep original response bytes and append a stop receipt before exiting.
                    current = db.execute('SELECT * FROM work WHERE source_key=?', (key,)).fetchone()
                    db.execute("INSERT OR REPLACE INTO epoch VALUES('stop','local_failure')")
                    s._append_audit(db, 'local_failure_stop', s._local_binding(current))
                    db.commit()
                    self.gate.stop_local()
                    raise b.ReviewError('本地处理失败 1 次，已停止全队；请保留检查点核对落库') from None
                db.execute('BEGIN IMMEDIATE')
                db.execute("UPDATE work SET state='done' WHERE source_key=?", (key,))
                db.execute("INSERT OR REPLACE INTO usage VALUES('last_success',?)", (self.clock(),))
                s._append_audit(db, 'unit_done', {'source_key': key, 'lane': lane})
                db.commit()
                completed += 1
        return completed

    def _request_guard(self, item, model):
        self._guard(item)
        if hasattr(self.adapter, 'check_request'):
            self.adapter.check_request(deepcopy(item), model)
        return True


def status(progress_path, gate_path):
    """只读，不建库、不拿写锁、不将 provider_started 改成可重试状态。"""
    output = {'schema': 'kb-coordinator-status-v1', 'queues': {}, 'last_success': None,
              'stop_reason': None, 'physical': PhysicalGate.read_status(gate_path)}
    path = b.private_path(progress_path)
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as db:
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        b.Scheduler._audit_events(None, db)
        for lane in ('live', 'backlog'):
            output['queues'][lane] = dict.fromkeys(('pending', 'local_pending', 'done', 'unknown'), 0)
        for row in db.execute('SELECT lane,state,count(*) n FROM work GROUP BY lane,state'):
            state = 'unknown' if row['state'] == 'provider_started' else row['state']
            b.need(row['lane'] in output['queues'] and state in output['queues'][row['lane']])
            output['queues'][row['lane']][state] += row['n']
        row = db.execute("SELECT value FROM usage WHERE key='last_success'").fetchone()
        output['last_success'] = row['value'] if row else None
        stop = db.execute("SELECT value FROM epoch WHERE key='stop'").fetchone()
        output['stop_reason'] = stop['value'] if stop else None
        channels = db.execute("SELECT value FROM epoch WHERE key='channel_status'").fetchone()
        output['channels'] = json.loads(channels['value']) if channels else {}
    physical_stop = output['physical']['stop_reason']
    if physical_stop == 'unknown' or output['stop_reason'] is None:
        output['stop_reason'] = physical_stop
    return output


def read_config(path, expected):
    # Configuration is reviewed separately; no keys or runner code belong in it.
    path = Path(path).absolute()
    b.need('..' not in path.parts and not any(p.is_symlink() for p in (path, *path.parents)))
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as stream:
        import stat
        info = os.fstat(stream.fileno())
        b.need(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size <= 262144
               and info.st_uid == 0 and not info.st_mode & 0o022,
               '运行配置必须由 root 持有且不可被工作进程改写')
        raw = stream.read()
    b.need(b.is_sha(expected) and b.sha(raw) == expected, '配置摘要不匹配，请核对审阅版本')
    config = validate_config(json.loads(raw))
    b.need(b.is_sha(config.get('release_sha256')), '缺少协调器发布摘要，请先核对完整交付文件')
    from verify_coordinator import verify_runtime
    verify_runtime(config['release_sha256'])
    return config


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', nargs='?', default='status', choices=('status', 'inventory', 'once', 'serve'))
    parser.add_argument('--config', default=DEFAULT_CONFIG)
    parser.add_argument('--config-sha256')
    parser.add_argument('--progress', default=os.environ.get('OPS_KB_BACKLOG_DB', b.DEFAULT_DB))
    parser.add_argument('--physical-db', default=os.environ.get('OPS_KB_PHYSICAL_DB',
                        '/var/lib/imgw/ops-app/kb-physical-recovery.sqlite3'))
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args(argv)
    if args.command == 'status':
        result = status(args.progress, args.physical_db)
    else:
        config = read_config(args.config, args.config_sha256)
        from production_adapter import build_adapter
        adapter, manifest, quarantine = build_adapter(config, inventory_only=args.command == 'inventory',
                                                       gate_path=args.physical_db)
        if args.command == 'inventory':
            result = adapter.inventory(manifest, quarantine)
        else:
            session = adapter.execution_session() if hasattr(adapter, 'execution_session') else nullcontext()
            with session:
                adapter.preflight()
                gate = PhysicalGate(args.physical_db, config['physical'])
                gate.assert_running()
                scheduler = b.Scheduler(manifest, config['manifest_sha256'], quarantine, path=args.progress)
                coordinator = Coordinator(scheduler, gate, adapter, config)
                stopping = False
                coordinator.stop = lambda: stopping
                def stop(signum, frame):
                    nonlocal stopping
                    stopping = True
                signal.signal(signal.SIGTERM, stop)
                signal.signal(signal.SIGINT, stop)
                while not stopping:
                    coordinator.discover()
                    coordinator.run_round()
                    if args.command == 'once':
                        break
                    time.sleep(config['poll_seconds'])
            result = status(args.progress, args.physical_db)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, allow_nan=False))
    else:
        if 'queues' in result:
            print('知识库补处理状态（只读）')
            for lane, label in (('live', '新增'), ('backlog', '积压')):
                states = result['queues'][lane]
                print(f"{label}：待处理 {states['pending']}，待本地续做 {states['local_pending']}，"
                      f"完成 {states['done']}，结果未知 {states['unknown']}")
            reasons = {'unknown': '存在结果未知请求；保留隔离和预留，禁止重发，交总指挥核对',
                       'local_failure': '本地处理失败；保留响应，先核对落库和索引',
                       'guard_failed': '来源或运行状态核对失败；保留审计并重新核查'}
            print('全队状态：' + (reasons.get(result['stop_reason'], '已停止；请保留账本核对')
                  if result['stop_reason'] else '未记录全队停止，请继续核对各渠道和账户额度'))
            print('最近本地成功时间：' + (time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime(result['last_success']))
                  if result['last_success'] else '暂无成功记录'))
            for name, bucket in result['physical']['buckets'].items():
                print(f"账户 {name}：保守累计预留 {bucket['reserved_tokens']} token；"
                      + ('已停止该账户请求，请核对额度' if bucket['stop_reason'] else '未记录账户停止'))
        else:
            print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, sqlite3.Error, KeyError, TypeError):
        raise SystemExit('协调器已停止；请核对配置摘要、来源权限、原熔断与私有账本，保留全部审计') from None
