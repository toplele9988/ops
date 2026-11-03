"""预付费路由仅用虚构资料、假官方服务和临时私有账本。"""
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import sys

import pytest

HERE = Path(__file__).resolve().parents[2] / 'deploy/kb'
sys.path.insert(0, str(HERE))
import backlog as b
import coordinator as c
import physical_gate as g
from test_backlog import entry, manifest, quarantine
from test_coordinator import FakeAdapter, ali, config


FREE_A, FREE_B = 'fictional-free-a', 'fictional-free-b'
PAID_A, PAID_B = 'fictional-official-pro', 'fictional-official-flash'


def settings():
    value = config()
    value['routes'] = {'wechat': [[FREE_A, PAID_A], [FREE_B, PAID_B]],
                       'alibaba': [[FREE_B, PAID_B]]}
    value['physical']['models'] = {
        model: {'bucket': bucket, 'frozen': False, 'cost_micros_per_1m_tokens': rate}
        for model, bucket, rate in [(FREE_A, 'free_a', None), (FREE_B, 'free_b', None),
                                    (PAID_A, 'official', 1000000), (PAID_B, 'official', 1000000)]}
    value['physical']['buckets'] = {
        bucket: {'kind': kind, 'verified': True, 'max_requests': 30,
                 'max_tokens': 10000, 'max_cost_micros': 10000 if kind == 'prepaid' else None}
        for bucket, kind in [('free_a', 'free_pool'), ('free_b', 'free_pool'), ('official', 'prepaid')]}
    return value


class ReceiptAdapter(FakeAdapter):
    """假服务返回原始收据；验收回调核对它已先进入独立物理账本。"""
    def __init__(self, items, path):
        super().__init__(items)
        self.path, self.outcomes = path, {}
        self.validated, self.applied = [], []

    def plan(self, item):
        names = [FREE_A, FREE_B] if item['channel'] == 'wechat' else [FREE_B]
        return [{'model': model, 'reservation': 100} for model in names]

    def request(self, item, model, reservation):
        self.requests.append((item['source_id'], model))
        outcome = self.outcomes.get(model, 'ok')
        if isinstance(outcome, BaseException):
            raise outcome
        return {'requested_model': model, 'status': outcome,
                'raw_body': json.dumps({'model': model, 'facts': [], 'usage': {
                    'prompt_tokens': 30, 'completion_tokens': 10, 'total_tokens': 40}})}

    def validate_response(self, item, model, receipt):
        key = g.digest({'item': item, 'model': model})
        with sqlite3.connect(self.path) as db:
            row = db.execute('SELECT response_json,response_sha256 FROM gate_calls WHERE request_key=?',
                             (key,)).fetchone()
        assert row is not None and row[0] is not None
        assert json.loads(row[0]) == receipt
        assert b.sha(row[0]) == row[1]
        self.validated.append((item['source_id'], model))
        if receipt['status'] != 'ok':
            raise g.ProviderRejected(receipt['status'], receipt,
                                     retry_after_seconds=60 if receipt['status'] == 'rate_limited' else None)
        return {'content': {'facts': []}, 'metadata': {
            'requested_model': model, 'actual_model': model,
            'usage': {'prompt_tokens': 30, 'completion_tokens': 10, 'total_tokens': 40}}}

    def apply_local(self, item, replies):
        names = [reply['metadata']['actual_model'] for reply in replies]
        assert len(names) == len(set(names))
        self.applied.append(tuple(names))
        return super().apply_local(item, replies)


class Harness:
    def __init__(self, tmp_path, items, value=None):
        self.directory = tmp_path / 'private-prepaid'
        self.directory.mkdir(mode=0o700)
        self.path, self.gate_path = self.directory / 'progress.sqlite3', self.directory / 'physical.sqlite3'
        self.manifest, self.config = manifest(items), value or settings()
        self.now = 10000
        self.adapter = ReceiptAdapter(items, self.gate_path)
        self.restart()

    def restart(self):
        self.scheduler = b.Scheduler(self.manifest, b.digest(self.manifest), quarantine(), path=self.path)
        self.gate = g.PhysicalGate(self.gate_path, self.config['physical'], clock=lambda: self.now)
        self.coordinator = c.Coordinator(self.scheduler, self.gate, self.adapter, self.config,
                                        clock=lambda: self.now)

    def tick(self, seconds=0):
        self.now += seconds
        return self.coordinator.run_round()


def test_free_rejection_receipt_switches_only_after_saved_known_refusal(tmp_path):
    source = ali(1)
    h = Harness(tmp_path, [source])
    h.adapter.outcomes[FREE_B] = 'free_tier_exhausted'
    assert h.tick() == 0
    assert h.adapter.requests == [(source['source_id'], FREE_B)]
    receipt_key = h.gate.fallback_receipt(FREE_B)
    assert receipt_key == g.digest({'item': source, 'model': FREE_B})
    h.restart()
    assert h.tick(29) == 0
    assert h.tick(1) == 1
    assert h.adapter.requests == [(source['source_id'], FREE_B), (source['source_id'], PAID_B)]
    assert h.adapter.applied == [(PAID_B,)]
    assert h.tick(60) == 0
    assert len(h.adapter.requests) == 2
    assert h.gate.status()['stop_reason'] is None


def test_two_readers_remain_different_through_both_free_to_official_routes(tmp_path):
    source = entry(2)
    h = Harness(tmp_path, [source])
    h.adapter.outcomes.update({FREE_A: 'free_tier_exhausted', FREE_B: 'free_tier_exhausted'})
    for step in range(4):
        h.tick(0 if step == 0 else 30)
    assert h.adapter.requests == [(source['source_id'], model) for model in [FREE_A, PAID_A, FREE_B, PAID_B]]
    assert h.adapter.applied == [(PAID_A, PAID_B)]
    h.restart()
    h.tick(60)
    assert len(h.adapter.requests) == 4


def test_local_quota_exhaustion_does_not_authorize_paid_route(tmp_path):
    value = settings()
    value['physical']['buckets']['free_b']['max_tokens'] = 0
    h = Harness(tmp_path, [ali(3)], value)
    h.tick()
    h.restart()
    h.tick(100)
    assert h.adapter.requests == []
    assert h.gate.fallback_receipt(FREE_B) is None


def test_existing_unknown_prevents_free_or_official_requests(tmp_path):
    h = Harness(tmp_path, [ali(4)])
    h.gate.stop_global('unknown')
    h.restart()
    with pytest.raises(g.GateUnknown):
        h.tick(60)
    assert h.adapter.requests == []
    with pytest.raises(g.GateUnknown):
        h.gate.fallback_receipt(FREE_B)


def test_timeout_is_unknown_and_never_switches_channel_after_restart(tmp_path):
    source = ali(5)
    h = Harness(tmp_path, [source])
    h.adapter.outcomes[FREE_B] = TimeoutError('虚构官方网络超时')
    with pytest.raises(g.GateUnknown):
        h.tick()
    h.restart()
    with pytest.raises(g.GateUnknown):
        h.tick(100)
    assert h.adapter.requests == [(source['source_id'], FREE_B)]
    assert h.gate.status()['stop_reason'] == 'unknown'
    with pytest.raises(g.GateUnknown):
        h.gate.fallback_receipt(FREE_B)


@pytest.mark.parametrize('reason', ['insufficient_balance', 'authentication_failed', 'rate_limited', 'model_drift'])
def test_known_official_refusal_stops_just_bucket_and_never_retries(tmp_path, reason):
    source = ali(6)
    h = Harness(tmp_path, [source])
    h.adapter.outcomes[FREE_B] = 'free_tier_exhausted'
    h.tick()
    h.adapter.outcomes[PAID_B] = reason
    h.tick(30)
    h.restart()
    h.tick(120)
    assert h.adapter.requests == [(source['source_id'], FREE_B), (source['source_id'], PAID_B)]
    state = h.gate.status()
    assert state['stop_reason'] is None
    assert state['bucket_stops']['official'] == reason
    assert h.adapter.locals == []
    if reason == 'rate_limited':
        with sqlite3.connect(h.gate_path) as db:
            waits = db.execute("SELECT retry_after_seconds FROM gate_rejections WHERE code='rate_limited'").fetchall()
        assert waits == [(60,)]


def test_accounts_keep_separate_reservations_when_one_route_switches(tmp_path):
    source = entry(7)
    h = Harness(tmp_path, [source])
    h.adapter.outcomes[FREE_A] = 'free_tier_exhausted'
    for step in range(3):
        h.tick(0 if step == 0 else 30)
    assert h.adapter.applied == [(PAID_A, FREE_B)]
    state = h.gate.status()['buckets']
    assert state['free_a']['reserved_tokens'] == 100
    assert state['free_b']['reserved_tokens'] == 100
    assert state['official']['reserved_tokens'] == 100
    assert state['official']['reserved_cost_micros'] == 100
    assert state['free_a']['reserved_cost_micros'] == state['free_b']['reserved_cost_micros'] == 0


def test_crash_after_official_receipt_saved_never_reissues_request(tmp_path):
    source = ali(8)
    h = Harness(tmp_path, [source])
    h.adapter.outcomes[FREE_B] = 'free_tier_exhausted'
    h.tick()
    original = h.adapter.apply_local
    h.adapter.apply_local = lambda *args: (_ for _ in ()).throw(KeyboardInterrupt('虚构断电'))
    with pytest.raises(KeyboardInterrupt):
        h.tick(30)
    h.adapter.apply_local = original
    h.restart()
    assert h.tick(30) == 1
    assert h.adapter.requests == [(source['source_id'], FREE_B), (source['source_id'], PAID_B)]
    assert h.adapter.locals == [source['source_id']]


def test_saved_primary_response_wins_over_later_bucket_exhaustion(tmp_path):
    source, later = entry(9), entry(10)
    h = Harness(tmp_path, [source])
    assert h.tick() == 0
    h.now += 30
    h.adapter.outcomes[FREE_A] = 'free_tier_exhausted'
    with pytest.raises(g.ProviderRejected):
        h.gate.call(later, FREE_A, 100, h.adapter.request, lambda *args: True,
                    validate_response=h.adapter.validate_response)
    h.restart()
    assert h.tick(30) == 1
    assert h.adapter.requests == [(source['source_id'], FREE_A),
                                  (later['source_id'], FREE_A),
                                  (source['source_id'], FREE_B)]
    assert h.adapter.applied == [(FREE_A, FREE_B)]


def test_paid_successor_requires_a_real_saved_free_refusal(tmp_path):
    source = ali(11)
    h = Harness(tmp_path, [source])
    with pytest.raises(g.GateError, match='免费额度拒绝收据'):
        h.gate.call(source, PAID_B, 100, h.adapter.request, lambda *args: True,
                    validate_response=h.adapter.validate_response, fallback_from='0' * 64)
    assert h.adapter.requests == []


def test_rejection_table_cannot_relabel_auth_failure_as_free_exhaustion(tmp_path):
    source = ali(12)
    h = Harness(tmp_path, [source])
    h.adapter.outcomes[FREE_B] = 'authentication_failed'
    h.tick()
    with sqlite3.connect(h.gate_path) as db:
        db.execute("UPDATE gate_rejections SET code='free_tier_exhausted'")
        db.execute("UPDATE gate_calls SET state='quota_rejected'")
    with pytest.raises(g.GateError):
        h.gate.fallback_receipt(FREE_B)
    assert h.adapter.requests == [(source['source_id'], FREE_B)]


def test_every_round_refreshes_balance_before_work(tmp_path):
    h = Harness(tmp_path, [])
    checks = []
    h.adapter.refresh_balance = lambda gate: checks.append(gate is h.gate)
    assert h.tick() == 0
    h.restart()
    assert h.tick(30) == 0
    assert checks == [True, True]


def test_overlap_between_wechat_reader_routes_is_rejected():
    value = settings()
    value['routes']['wechat'][1][1] = PAID_A
    with pytest.raises(b.ReviewError):
        c.validate_config(value)
