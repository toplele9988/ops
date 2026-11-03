"""协调器演练全用虚构来源、假时钟和假请求，不连接模型或渠道。"""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys

import pytest

HERE = Path(__file__).resolve().parents[2] / 'deploy/kb'
sys.path.insert(0, str(HERE))
import backlog as b
import coordinator as c
from physical_gate import PhysicalGate, GateUnknown, GateStopped
from test_backlog import entry, manifest, quarantine


def config():
    return {'schema': 'kb-recovery-coordinator-v1', 'backlog_limit': 2, 'live_limit': 2,
            'max_consecutive_failures': 1, 'stop_on_unknown': True, 'auto_fallback': False,
            'poll_seconds': 5, 'physical': {'schema': 'kb-physical-gate-v1', 'interval_seconds': 30,
            'models': {model: {'bucket': bucket, 'frozen': False, 'cost_micros_per_1m_tokens': rate}
                       for model, bucket, rate in [('fictional-a', 'free', None),
                                                   ('fictional-b', 'free', None),
                                                   ('fictional-paid', 'paid', 1000000)]},
            'buckets': {bucket: {'kind': kind, 'verified': True, 'max_requests': count,
                                 'max_tokens': 10000, 'max_cost_micros': 10000}
                        for bucket, kind, count in [('free', 'free_pool', 20), ('paid', 'prepaid', 20)]}}}


class FakeAdapter:
    def __init__(self, items):
        self.sources = {i['source_id']: deepcopy(i) for i in items}
        self.lives, self.requests, self.locals = [], [], []
        self.fail_request = self.fail_local = False
        self.quarantine = quarantine()

    def preflight(self):
        return deepcopy(self.quarantine)

    def discover_live(self, after):
        return deepcopy(self.lives)

    def read_source(self, item):
        return deepcopy(self.sources[item['source_id']])

    def plan(self, item):
        models = ['fictional-a', 'fictional-b'] if item['channel'] == 'wechat' else ['fictional-paid']
        return [{'model': model, 'reservation': 100} for model in models]

    def request(self, item, model, reservation):
        self.requests.append((item['source_id'], model))
        if self.fail_request:
            raise TimeoutError('虚构超时')
        return {'content': {'facts': []}, 'metadata': {'actual_model': model}}

    def apply_local(self, item, replies):
        if self.fail_local:
            raise OSError('虚构落库失败')
        # Fake idempotent receipt also covers a commit before progress commit.
        if item['source_id'] not in self.locals:
            self.locals.append(item['source_id'])


class Harness:
    def __init__(self, tmp_path, items, *, settings=None):
        self.directory = tmp_path / 'coordinator-private'
        self.directory.mkdir(mode=0o700)
        self.path = self.directory / 'progress.sqlite3'
        self.gate_path = self.directory / 'physical.sqlite3'
        self.manifest = manifest(items)
        self.adapter = FakeAdapter(items)
        self.config = settings or config()
        self.now = 10000
        self.restart()

    def restart(self):
        self.scheduler = b.Scheduler(self.manifest, b.digest(self.manifest), quarantine(), path=self.path)
        self.gate = PhysicalGate(self.gate_path, self.config['physical'], clock=lambda: self.now)
        self.coordinator = c.Coordinator(self.scheduler, self.gate, self.adapter, self.config,
                                        clock=lambda: self.now)

    def tick(self, seconds=0):
        self.now += seconds
        return self.coordinator.run_round()


def ali(n, **kwargs):
    item = entry(n, **kwargs)
    item['channel'] = 'alibaba'
    return item


def test_live_priority_two_reader_spacing_survives_restart(tmp_path):
    h = Harness(tmp_path, [ali(1)])
    live = entry(2, at=1200)
    h.adapter.lives = [live]
    h.adapter.sources[live['source_id']] = live
    h.coordinator.discover()
    assert h.tick() == 0  # First reader saved; neither second reader nor backlog may bypass interval.
    assert h.adapter.requests == [(live['source_id'], 'fictional-a')]
    h.restart()
    assert h.tick(29) == 0
    assert h.tick(1) == 1
    assert h.adapter.requests == [(live['source_id'], 'fictional-a'), (live['source_id'], 'fictional-b')]
    assert h.tick(30) == 1
    assert h.adapter.requests[-1] == (ali(1)['source_id'], 'fictional-paid')
    assert h.tick(30) == 0
    assert len(h.adapter.requests) == 3


def test_new_unknown_stops_both_channels_and_restart_never_resends(tmp_path):
    h = Harness(tmp_path, [ali(1), entry(2)])
    h.adapter.fail_request = True
    with pytest.raises(GateUnknown, match='结果未知'):
        h.tick()
    h.restart()
    with pytest.raises((b.ReviewError, GateUnknown)):
        h.tick(100)
    assert len(h.adapter.requests) == 1
    state = c.status(h.path, h.gate_path)
    assert state['queues']['backlog']['unknown'] == 1
    assert state['physical']['stop_reason'] == 'unknown'


def test_power_cut_before_response_is_unknown_and_reservation_retained(tmp_path):
    h = Harness(tmp_path, [ali(1)])
    # Simulate durable provider_started with no durable response, the exact crash boundary.
    original = h.gate._audit
    def crash_after_started(db, kind, payload):
        return original(db, kind, payload)
    h.gate._audit = crash_after_started
    assert h.tick() == 1
    with sqlite3.connect(h.gate_path) as db:
        db.execute("UPDATE gate_calls SET state='provider_started',response_json=NULL,response_sha256=NULL")
    h.restart()
    with pytest.raises(GateUnknown):
        h.tick(100)
    assert len(h.adapter.requests) == 1
    assert h.gate.status()['buckets']['paid']['reserved_tokens'] == 100


def test_crash_after_response_before_local_commit_reuses_all_readers(tmp_path):
    h = Harness(tmp_path, [entry(1)])
    assert h.tick() == 0
    original = h.adapter.apply_local
    def crash(item, replies):
        original(item, replies)
        raise KeyboardInterrupt('虚构断电，落库收据已提交但进度未提交')
    h.adapter.apply_local = crash
    with pytest.raises(KeyboardInterrupt):
        h.tick(30)
    h.adapter.apply_local = original
    h.restart()
    assert h.tick(30) == 1
    assert len(h.adapter.requests) == 2 and len(h.adapter.locals) == 1
    assert h.tick(30) == 0


def test_bucket_exhaustion_stops_only_that_bucket_without_fallback(tmp_path):
    settings = config()
    settings['physical']['buckets']['paid']['max_requests'] = 0
    h = Harness(tmp_path, [entry(1), ali(2)], settings=settings)
    h.tick()
    h.tick(30)
    assert {model for _, model in h.adapter.requests} == {'fictional-a', 'fictional-b'}
    assert h.gate.status()['bucket_stops'] == {'paid': 'quota_exhausted'}
    assert h.gate.status()['stop_reason'] is None


def test_one_local_failure_stops_all_and_keeps_responses(tmp_path):
    h = Harness(tmp_path, [ali(1), ali(2)])
    h.adapter.fail_local = True
    with pytest.raises(b.ReviewError, match='失败 1 次'):
        h.tick()
    h.restart()
    with pytest.raises((b.ReviewError, GateStopped)):
        h.tick(100)
    assert len(h.adapter.requests) == 1
    assert h.gate.status()['stop_reason'] == 'local_failure'


@pytest.mark.parametrize('hold', range(4))
def test_all_four_isolated_sources_rejected_before_admission(tmp_path, hold):
    h = Harness(tmp_path, [])
    live = entry(99, at=1200)
    live['source_sha256'] = quarantine()['holds'][hold]['source_sha256'][0]
    h.adapter.sources[live['source_id']] = live
    h.adapter.lives = [live]
    with pytest.raises(b.ReviewError, match='停止全队'):
        h.coordinator.discover()
    assert h.adapter.requests == [] and h.scheduler.progress() == {}


def test_status_is_readonly_even_with_unfinished_request(tmp_path):
    h = Harness(tmp_path, [ali(1)])
    h.tick()
    before = {p.name: p.read_bytes() for p in h.directory.iterdir()}
    result = c.status(h.path, h.gate_path)
    assert result['queues']['backlog']['done'] == 1
    assert result['last_success'] == 10000
    assert before == {p.name: p.read_bytes() for p in h.directory.iterdir()}
    assert '虚构资料' not in json.dumps(result, ensure_ascii=False)


@pytest.mark.parametrize('key,value', [('backlog_limit', 3), ('max_consecutive_failures', 2),
                                      ('auto_fallback', True), ('stop_on_unknown', False)])
def test_authorized_policy_cannot_be_relaxed(key, value):
    value_config = config()
    value_config[key] = value
    with pytest.raises(b.ReviewError):
        c.validate_config(value_config)


def test_manifest_completed_never_calls_or_republishes(tmp_path):
    h = Harness(tmp_path, [ali(1, reason='complete')])
    assert h.tick() == 0
    h.restart()
    assert h.tick(60) == 0
    assert h.adapter.requests == h.adapter.locals == []


def test_two_exhausted_sources_do_not_starve_other_account(tmp_path):
    free = entry(99)
    paid = [ali(n) for n in range(20) if b.source_key(ali(n)['source_id']) < b.source_key(free['source_id'])][:2]
    assert len(paid) == 2
    settings = config()
    settings['physical']['buckets']['paid']['max_requests'] = 0
    h = Harness(tmp_path, paid + [free], settings=settings)
    h.tick()
    assert h.adapter.requests == [(free['source_id'], 'fictional-a')]
    assert h.tick(30) == 1
    assert h.adapter.locals == [free['source_id']]


def test_corrupt_response_stops_persistently_without_resending(tmp_path):
    h = Harness(tmp_path, [ali(1)])
    original = h.adapter.apply_local
    h.adapter.apply_local = lambda item, replies: (_ for _ in ()).throw(KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt):
        h.tick()
    h.adapter.apply_local = original
    with sqlite3.connect(h.path) as db:
        db.execute("UPDATE work SET response_sha256=?", ('0' * 64,))
    with pytest.raises(b.ReviewError, match='检查点损坏'):
        h.tick(60)
    assert h.gate.status()['stop_reason'] == 'guard_failed'
    h.restart()
    with pytest.raises(GateStopped):
        h.tick(60)
    assert len(h.adapter.requests) == 1


def test_top_level_status_reports_unknown_before_work_can_be_updated(tmp_path):
    h = Harness(tmp_path, [ali(1)])
    h.gate.stop_global('unknown')
    result = c.status(h.path, h.gate_path)
    assert result['stop_reason'] == 'unknown'
    assert result['queues']['backlog']['pending'] == 1


def test_cli_json_status_contains_no_source_or_response(tmp_path, capsys):
    h = Harness(tmp_path, [ali(1)])
    h.tick()
    assert c.main(['status', '--progress', str(h.path), '--physical-db', str(h.gate_path), '--json']) == 0
    output = capsys.readouterr().out
    assert json.loads(output)['queues']['backlog']['done'] == 1
    assert '虚构资料' not in output and 'content' not in output


def test_saved_readers_finish_locally_after_model_exhaustion(tmp_path):
    h = Harness(tmp_path, [entry(1)])
    assert h.tick() == 0
    original = h.gate.call
    def interrupted(*args, **kwargs):
        result = original(*args, **kwargs)
        if args[1] == 'fictional-b':
            raise KeyboardInterrupt('虚构断电：两响应已保存，work尚未保存')
        return result
    h.gate.call = interrupted
    with pytest.raises(KeyboardInterrupt):
        h.tick(30)
    h.restart()
    # A fresh plan would reject exhausted models; the already frozen reader plan
    # and two durable responses require no new model admission.
    from physical_gate import QuotaExhausted
    h.adapter.plan = lambda item: (_ for _ in ()).throw(QuotaExhausted('虚构额度已用完'))
    h.adapter.check_request = lambda item, model: (_ for _ in ()).throw(QuotaExhausted('虚构额度已用完'))
    assert h.tick(30) == 1
    assert len(h.adapter.requests) == 2 and len(h.adapter.locals) == 1


def test_stop_after_first_reader_saves_response_and_starts_no_second_reader(tmp_path):
    h = Harness(tmp_path, [entry(1), ali(2)])
    stopped = [False]
    original = h.adapter.request
    def request(item, model, reservation):
        response = original(item, model, reservation)
        stopped[0] = True
        return response
    h.adapter.request = request
    h.coordinator.stop = lambda: stopped[0]
    assert h.tick() == 0
    assert len(h.adapter.requests) == 1
    assert h.gate.status()['stop_reason'] is None
