"""补处理仅用虚构来源、假请求和临时 SQLite；不连接真实渠道或模型。"""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sqlite3

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('backlog_under_test', ROOT / 'deploy/kb/backlog.py')
b = importlib.util.module_from_spec(spec)
spec.loader.exec_module(b)


def quarantine():
    return {'schema': 'kb-source-quarantine-v1', 'holds': [
        {'channel': 'wechat' if n < 3 else 'alibaba', 'request_sha256': b.sha('虚构请求' + str(n)),
         'source_keys': [b.source_key('虚构未知来源' + str(n))],
         'source_sha256': [b.sha('虚构未知原始来源' + str(n))],
         'content_sha256': [b.sha('虚构未知正文' + str(n))],
         'prompt_sha256': b.sha('虚构未知提示词' + str(n))} for n in range(4)]}


def entry(n, *, reason='never_requested', at=900):
    response = {'facts': ['虚构事实' + str(n)]}
    item = {'channel': 'wechat', 'source_id': '虚构资料' + str(n),
            'fingerprint': b.sha('虚构指纹' + str(n)), 'source_sha256': b.sha('虚构原始来源' + str(n)),
            'content_sha256': b.sha('虚构正文' + str(n)),
            'prompt_sha256': b.sha('虚构提示词' + str(n)), 'source_at': at, 'admitted_at': at,
            'reason': reason, 'checkpoints': [], 'completed_steps': []}
    if reason in ('complete', 'local_pending'):
        item['checkpoints'] = [{'cache_key': b.sha('虚构cache' + str(n)), 'model': 'fictional-model',
                                'response_sha256': b.digest(response),
                                'checkpoint_sha256': b.sha('虚构checkpoint' + str(n))}]
        item['completed_steps'] = ['response'] if reason == 'local_pending' else ['response', 'facts', 'index']
    if reason == 'time_unconfirmed':
        item['source_at'] = None
    return item


def manifest(items):
    return b.make_manifest({'schema': 'kb-backlog-review-v1', 'after': 1000,
                           'snapshots': {'catalog': b.sha('虚构快照')}, 'entries': items}, quarantine())


def policy(**updates):
    return {'models': ['fictional-model'], 'max_requests': 20, 'max_tokens': 20000,
            'request_tokens': 100, 'batch_size': 10, 'interval_seconds': 1,
            'max_consecutive_failures': 2, **updates}


class Harness:
    def __init__(self, tmp_path, items):
        self.manifest = manifest(items)
        directory = tmp_path / 'private'
        directory.mkdir(mode=0o700)
        self.path = directory / 'progress.sqlite3'
        self.sources = {i['source_id']: deepcopy(i) for i in items}
        self.requests = []
        self.local = []
        self.now = 10000
        self.scheduler = self.restart()

    def restart(self):
        return b.Scheduler(self.manifest, b.digest(self.manifest), quarantine(), path=self.path)

    def clock(self):
        self.now += 10
        return self.now

    def request(self, item, model, reservation):
        assert model == 'fictional-model' and reservation == 100
        self.requests.append(item['source_id'])
        return {'facts': ['虚构事实' + item['source_id']]}

    def load(self, item):
        number = item['source_id'].removeprefix('虚构资料')
        return [{'checkpoint_sha256': c['checkpoint_sha256'], 'response_sha256': c['response_sha256'],
                 'response': {'facts': ['虚构事实' + number]}} for c in item['checkpoints']]

    def apply(self, item, replies):
        self.local.append(item['source_id'])

    def run(self, **overrides):
        args = {'read_source': lambda item: deepcopy(self.sources[item['source_id']]),
                'load_checkpoint': self.load, 'apply_local': self.apply,
                'request': self.request, 'policy': policy(), 'backlog_limit': 2, 'clock': self.clock}
        args.update(overrides)
        return self.scheduler.run_batch(**args)


def test_live_priority_and_backlog_resume_without_duplicate_or_omission(tmp_path):
    h = Harness(tmp_path, [entry(1), entry(2), entry(3), entry(4, reason='complete')])
    live = entry(5, at=1100)
    h.sources[live['source_id']] = live
    h.scheduler.admit_live([live], current_quarantine=quarantine())
    assert h.run(backlog_limit=1) == 2
    assert h.requests[0] == live['source_id']
    h.scheduler = h.restart()
    h.scheduler.admit_live([live], current_quarantine=quarantine())
    assert h.run(backlog_limit=1) == 1
    assert h.run(backlog_limit=1) == 1
    assert h.run(backlog_limit=1) == 0
    assert set(h.requests) == {entry(n)['source_id'] for n in (1, 2, 3, 5)}
    assert len(h.requests) == len(set(h.requests)) == 4
    assert h.scheduler.progress() == {('backlog', 'done'): 4, ('live', 'done'): 1}


def test_reliable_response_only_resumes_local_steps_without_budget(tmp_path):
    h = Harness(tmp_path, [entry(1, reason='local_pending'), entry(2, reason='complete'), entry(3)])
    assert h.run(policy=None, request=None) == 1
    assert h.requests == []
    assert h.local == [entry(1)['source_id']]
    assert h.scheduler.progress()[('backlog', 'pending')] == 1


@pytest.mark.parametrize('change', ['source', 'content', 'prompt'])
def test_unknown_evidence_cannot_reenter_with_changed_version_model_or_window(change):
    item = entry(1)
    frozen = quarantine()['holds'][0]
    if change == 'source':
        item['source_id'] = '虚构未知来源0'
    elif change == 'content':
        item['content_sha256'] = frozen['content_sha256'][0]
    else:
        item['prompt_sha256'] = frozen['prompt_sha256']
    with pytest.raises(b.ReviewError, match='未知请求隔离'):
        manifest([item])


def test_tampered_manifest_rejected_before_creating_progress(tmp_path):
    value = manifest([entry(1)])
    pinned = b.digest(value)
    value['items'][0]['fingerprint'] = 'f' * 64
    path = tmp_path / 'forbidden.sqlite3'
    with pytest.raises(b.ReviewError, match='校验失败'):
        b.Scheduler(value, pinned, quarantine(), path=path)
    assert not path.exists()


def test_source_and_checkpoint_changed_before_local_are_rejected(tmp_path):
    h = Harness(tmp_path, [entry(1, reason='local_pending')])
    h.sources[entry(1)['source_id']]['fingerprint'] = 'f' * 64
    with pytest.raises(b.ReviewError, match='来源或检查点已变化'):
        h.run()
    assert h.requests == h.local == []
    h.sources[entry(1)['source_id']] = entry(1, reason='local_pending')
    with pytest.raises(b.ReviewError, match='持久响应与清单不一致'):
        h.run(load_checkpoint=lambda item: [{'response': {}, 'response_sha256': 'a' * 64,
                                           'checkpoint_sha256': 'b' * 64}])
    assert h.requests == h.local == []


def test_timeout_stops_entire_queue_and_restart_never_retries(tmp_path):
    h = Harness(tmp_path, [entry(1), entry(2)])
    def timeout(item, model, reservation):
        h.requests.append(item['source_id'])
        raise TimeoutError('虚构超时')
    with pytest.raises(b.ReviewError, match='模型结果未知'):
        h.run(request=timeout)
    h.scheduler = h.restart()
    with pytest.raises(b.ReviewError, match='补处理已停止'):
        h.run()
    assert len(h.requests) == 1
    assert h.scheduler.progress().get(('backlog', 'unknown')) == 1


def test_process_crash_after_started_is_permanent_unknown(tmp_path):
    h = Harness(tmp_path, [entry(1)])
    with sqlite3.connect(h.path) as db:
        db.execute("UPDATE work SET state='provider_started'")
    h.scheduler = h.restart()
    with pytest.raises(b.ReviewError, match='补处理已停止'):
        h.run()
    assert h.requests == []


def test_local_failure_reuses_durable_response_after_restart(tmp_path):
    h = Harness(tmp_path, [entry(1)])
    def fail(item, replies):
        raise OSError('虚构本地失败')
    with pytest.raises(b.ReviewError, match='本地续做失败'):
        h.run(apply_local=fail)
    assert len(h.requests) == 1
    h.scheduler = h.restart()
    assert h.run() == 1
    assert len(h.requests) == 1


def test_watermarks_never_move_back_and_equal_timestamp_late_source_is_not_lost(tmp_path):
    h = Harness(tmp_path, [])
    first = entry(1, at=1200)
    h.sources[first['source_id']] = first
    h.scheduler.admit_live([first], current_quarantine=quarantine())
    h.run()
    h.scheduler = h.restart()
    late = entry(2, at=1200)
    h.sources[late['source_id']] = late
    h.scheduler.admit_live([late], current_quarantine=quarantine())
    assert h.run() == 1
    with sqlite3.connect(h.path) as db:
        assert db.execute("SELECT value FROM usage WHERE key='watermark_live'").fetchone() == (1200.0,)
    altered = deepcopy(h.manifest)
    altered['after'] = 999
    with pytest.raises(b.ReviewError, match='恢复边界或冻结清单已变化'):
        b.Scheduler(altered, b.digest(altered), quarantine(), path=h.path)


def test_completed_source_cannot_change_prompt_to_become_new_request(tmp_path):
    h = Harness(tmp_path, [entry(1, reason='complete', at=1100)])
    changed = entry(1, at=1100)
    changed['prompt_sha256'] = b.sha('虚构新版本新模型')
    with pytest.raises(b.ReviewError, match='指纹或处理计划已变化'):
        h.scheduler.admit_live([changed], current_quarantine=quarantine())
    assert h.requests == []


def test_explicit_budget_and_batch_limit_are_required_and_persisted(tmp_path):
    h = Harness(tmp_path, [entry(1), entry(2), entry(3)])
    assert h.run(policy=None) == 0
    assert h.run(policy=policy(max_requests=1)) == 1
    h.scheduler = h.restart()
    assert h.run(policy=policy(max_requests=1)) == 0
    assert len(h.requests) == 1
    with pytest.raises(b.ReviewError, match='预算策略已变化'):
        h.run(policy=policy(max_requests=5))
    with pytest.raises(b.ReviewError):
        h.run(policy=policy(max_tokens=None))


def test_quarantine_changes_stop_before_any_request(tmp_path):
    h = Harness(tmp_path, [entry(1)])
    changed = quarantine()
    changed['holds'][0]['source_keys'].append(b.sha('虚构新未知'))
    with pytest.raises(b.ReviewError, match='隔离清单已变化'):
        h.run(guard=lambda: changed)
    assert h.requests == []


def test_unknown_and_missing_time_are_frozen_exclusions():
    unknown = entry(1, reason='unknown')
    unknown['source_id'] = '虚构未知来源0'
    value = manifest([unknown, entry(2, reason='time_unconfirmed')])
    assert value['items'] == []
    assert len(value['excluded']) == 2
    assert b.validate_manifest(value, b.digest(value), quarantine()) == value


def test_private_review_rejects_links_and_non_temporary_paths(tmp_path):
    directory = tmp_path / 'private'
    directory.mkdir(mode=0o700)
    file = directory / 'review.json'
    file.write_text('{}')
    file.chmod(0o600)
    link = directory / 'linked.json'
    link.symlink_to(file)
    with pytest.raises(b.ReviewError, match='符号链接'):
        b.read_private(link)
    with pytest.raises(b.ReviewError, match='只接受 /tmp'):
        b.read_private('/var/lib/imgw/never-open.json')


def test_restart_restores_pending_live_plan_without_readmitting(tmp_path):
    h = Harness(tmp_path, [])
    lives = [entry(2, at=1100), entry(3, at=1200)]
    h.sources.update({i['source_id']: i for i in lives})
    h.scheduler.admit_live(lives, current_quarantine=quarantine())
    assert h.run(policy=policy(batch_size=1)) == 1
    h.scheduler = h.restart()
    # The same approved batch policy and original immutable plan survive restart.
    assert h.run(policy=policy(batch_size=1)) == 1
    assert len(h.requests) == 2 and set(h.requests) == {i['source_id'] for i in lives}
    assert h.run(policy=policy(batch_size=1)) == 0
    assert len(set(h.requests)) == 2


def test_exhausted_provider_budget_still_completes_local_work_in_other_lane(tmp_path):
    h = Harness(tmp_path, [entry(1, reason='local_pending')])
    live = entry(2, at=1100)
    h.sources[live['source_id']] = live
    h.scheduler.admit_live([live], current_quarantine=quarantine())
    # Persist a prior fully consumed request/token budget. The new live item must
    # remain pending while already checkpointed backlog continues without a call.
    with sqlite3.connect(h.path) as db:
        db.execute("INSERT INTO usage VALUES('requests',1)")
        db.execute("INSERT INTO usage VALUES('tokens',100)")
    assert h.run(policy=policy(max_requests=1, max_tokens=100)) == 1
    assert h.local == [entry(1)['source_id']] and h.requests == []
    assert h.scheduler.progress() == {('backlog', 'done'): 1, ('live', 'pending'): 1}


def test_response_checkpoint_is_processed_before_unrequested_in_same_lane(tmp_path):
    h = Harness(tmp_path, [entry(1), entry(2, reason='local_pending')])
    assert h.run(policy=policy(batch_size=1), backlog_limit=1) == 1
    assert h.local == [entry(2)['source_id']] and h.requests == []


def test_malformed_unknown_hold_rejected_with_review_error():
    value = quarantine()
    value['holds'][0] = None
    with pytest.raises(b.ReviewError):
        b.verify_quarantine(value)


def test_raw_response_checkpoint_keeps_original_bytes_for_local_replay(tmp_path):
    item = entry(1, reason='local_pending')
    raw = '{ "facts": ["虚构原始响应"] }\n'
    item['checkpoints'][0]['response_sha256'] = b.sha(raw)
    h = Harness(tmp_path, [item])
    checkpoint = item['checkpoints'][0]
    assert h.run(policy=None, request=None, load_checkpoint=lambda i: [{
        'response': raw, 'response_sha256': checkpoint['response_sha256'],
        'checkpoint_sha256': checkpoint['checkpoint_sha256']}]) == 1
    assert h.requests == []


def test_parent_directory_traversal_cannot_escape_temporary_review_root():
    with pytest.raises(b.ReviewError, match='路径包含上级目录'):
        b.read_private('/tmp/../var/lib/imgw/never-open.json')


def test_continuous_live_arrivals_leave_reserved_backlog_slots_each_round(tmp_path):
    h = Harness(tmp_path, [entry(1), entry(2), entry(3)])
    for round_number in range(3):
        lives = [entry(10 + round_number * 2 + n, at=1100 + round_number) for n in range(2)]
        h.sources.update({item['source_id']: item for item in lives})
        h.scheduler.admit_live(lives, current_quarantine=quarantine())
        start = len(h.requests)
        assert h.run(policy=policy(batch_size=1), backlog_limit=1) == 2
        batch = h.requests[start:]
        assert h.scheduler.items[b.source_key(batch[0])]['source_at'] > 1000
        assert h.scheduler.items[b.source_key(batch[1])]['source_at'] < 1000
        assert h.scheduler.progress()[('backlog', 'done')] == round_number + 1
    assert h.scheduler.progress() == {('backlog', 'done'): 3, ('live', 'done'): 3, ('live', 'pending'): 3}
    assert len(h.requests) == len(set(h.requests)) == 6


def test_changed_alias_body_and_prompt_still_match_original_source_quarantine():
    item = entry(88)
    item['source_id'] = '虚构另一解析器产生的新编号'
    item['content_sha256'] = b.sha('虚构改写后的解析正文')
    item['prompt_sha256'] = b.sha('虚构新模型新窗口提示词')
    item['source_sha256'] = quarantine()['holds'][0]['source_sha256'][0]
    with pytest.raises(b.ReviewError, match='未知请求隔离'):
        manifest([item])


def local_failure_harness(tmp_path):
    h = Harness(tmp_path, [entry(1)])
    def fail(item, replies):
        raise OSError('虚构本地提交失败')
    with pytest.raises(b.ReviewError, match='本地续做失败'):
        h.run(policy=policy(max_consecutive_failures=1), apply_local=fail)
    return h


def test_explicit_local_resume_archives_stop_and_never_requests_model_again(tmp_path):
    h = local_failure_harness(tmp_path)
    assert len(h.requests) == 1
    h.scheduler = h.restart()
    with sqlite3.connect(h.path) as db:
        original_audit = db.execute('SELECT * FROM audit').fetchall()
        assert len(original_audit) == 1
    with pytest.raises(b.ReviewError, match='补处理已停止'):
        h.run(policy=policy(max_consecutive_failures=1))
    proof = b.sha('虚构已核对的落库幂等性收据')
    inspected = []
    def verified(context):
        assert context['failure']['manifest_sha256'] == b.digest(h.manifest)
        assert context['failure']['quarantine_sha256'] == b.digest(quarantine())
        assert context['failure']['source_key'] == b.source_key(entry(1)['source_id'])
        assert b.is_sha(context['failure']['response_sha256'])
        inspected.append(context)
        return proof
    h.scheduler.resume_local(b.digest(h.manifest), proof, verified)
    assert len(inspected) == 1
    assert h.run(policy=policy(max_consecutive_failures=1)) == 1
    assert len(h.requests) == 1
    with sqlite3.connect(h.path) as db:
        audits = db.execute('SELECT * FROM audit ORDER BY seq').fetchall()
        assert audits[:1] == original_audit
        assert [r[1] for r in audits] == ['local_failure_stop', 'local_failure_resumed']
        assert db.execute("SELECT value FROM epoch WHERE key='stop'").fetchone() is None
    h.scheduler = h.restart()
    assert h.run(policy=policy(max_consecutive_failures=1)) == 0


def test_unknown_stop_cannot_be_resumed_even_with_local_receipt(tmp_path):
    h = Harness(tmp_path, [entry(1)])
    def timeout(item, model, reservation):
        h.requests.append(item['source_id'])
        raise TimeoutError('虚构结果未知')
    with pytest.raises(b.ReviewError, match='模型结果未知'):
        h.run(request=timeout)
    checked = []
    proof = b.sha('虚构本地收据')
    with pytest.raises(b.ReviewError, match='未知请求必须永久隔离'):
        h.scheduler.resume_local(b.digest(h.manifest), proof,
                                 lambda context: checked.append(context) or proof)
    assert checked == []
    h.scheduler = h.restart()
    with pytest.raises(b.ReviewError, match='补处理已停止'):
        h.run()
    assert len(h.requests) == 1


@pytest.mark.parametrize('wrong', ['manifest', 'proof', 'checkpoint'])
def test_local_resume_rejects_wrong_manifest_proof_or_changed_checkpoint(tmp_path, wrong):
    h = local_failure_harness(tmp_path)
    actual_proof = b.sha('虚构真实核对收据')
    expected = b.digest(h.manifest)
    supplied_proof = actual_proof
    if wrong == 'manifest':
        expected = '0' * 64
    elif wrong == 'proof':
        supplied_proof = b.sha('虚构另一张收据')
    else:
        with sqlite3.connect(h.path) as db:
            db.execute("UPDATE work SET response_json='{}'")
    with pytest.raises(b.ReviewError):
        h.scheduler.resume_local(expected, supplied_proof, lambda context: actual_proof)
    with sqlite3.connect(h.path) as db:
        assert db.execute("SELECT value FROM epoch WHERE key='stop'").fetchone() == ('local_failure',)
        assert db.execute('SELECT count(*) FROM audit').fetchone() == (1,)
    assert len(h.requests) == 1


def test_resume_audit_tampering_is_detected_and_cannot_clear_stop(tmp_path):
    h = local_failure_harness(tmp_path)
    with sqlite3.connect(h.path) as db:
        db.execute("UPDATE audit SET payload_json='{}'")
    proof = b.sha('虚构审查收据')
    with pytest.raises(b.ReviewError, match='审计校验失败'):
        h.scheduler.resume_local(b.digest(h.manifest), proof, lambda context: proof)
    with sqlite3.connect(h.path) as db:
        assert db.execute("SELECT value FROM epoch WHERE key='stop'").fetchone() == ('local_failure',)
