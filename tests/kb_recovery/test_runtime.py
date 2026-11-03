"""真实调用账本适配器只接假 runner，未知结果不得再次出站。"""
from copy import deepcopy
import json
import os
import sqlite3
from types import SimpleNamespace

import pytest

from recovery_fixtures import load_modules
from test_isolation import synthetic_holds


@pytest.fixture
def runtime_fixture(tmp_path):
    modules = load_modules(tmp_path)
    frozen = synthetic_holds(modules, tmp_path)
    runtime = modules.kb_model_runtime
    prompts = ['虚构旧请求' + str(number) for number in range(1, 4)]
    for number, prompt in enumerate(prompts, 1):
        frozen.db.execute('UPDATE calls SET prompt_sha256=? WHERE model=?',
                          (runtime.sha(prompt), 'fictional-model-' + str(number)))
    frozen.db.commit()
    manifest = modules.kb_unknown_isolation.capture(frozen.db, frozen.ledger, frozen.matrix)
    original_holds = frozen.db.execute('SELECT * FROM calls ORDER BY cache_key').fetchall()
    original_ledger = deepcopy(frozen.ledger)
    frozen.db.close()
    modules.quota_isolation.install(frozen.matrix, manifest)
    frozen.matrix.select_model_readonly = lambda prompt, schema, **kw: {
        'model': kw.get('preferred_model') or 'fictional-ready', 'reservation_tokens': 8000}
    requests = []

    def complete(prompt, schema, model):
        requests.append((runtime.sha(prompt), model))
        return response(model)

    policy = {'root': str(frozen.path.parent), 'model_codes': tuple(frozen.ledger['models']),
              'schema': 'wechat-kb-model-runtime-v1', 'calls_per_day': 1000000,
              'tokens_per_day': 1000000000}
    matrix = runtime.Matrix(policy, frozen.matrix, complete=complete, now=lambda: 10000,
                            isolation_enabled=True, manifest=manifest)
    result = SimpleNamespace(modules=modules, runtime=runtime, frozen=frozen, matrix=matrix,
                             requests=requests, prompts=prompts, manifest=manifest,
                             original_holds=original_holds, original_ledger=original_ledger)
    assert len(matrix.preflight()) == 3
    yield result
    with sqlite3.connect(frozen.path) as db:
        assert db.execute("SELECT * FROM calls WHERE model LIKE 'fictional-model-%' "
                          'ORDER BY cache_key').fetchall() == original_holds
    assert frozen.ledger == original_ledger


def response(model):
    return {'content': json.dumps({'facts': []}), 'metadata': {
        'requested_model': model, 'actual_model': model,
        'usage': {'prompt_tokens': 10, 'completion_tokens': 2, 'total_tokens': 12}}}


def cache_key(fixture, prompt, preferred=None):
    return fixture.runtime.sha(fixture.runtime.canon({
        'prompt': prompt, 'schema': {}, 'preferred_model': preferred,
        'version': fixture.matrix.policy['schema'], 'output': 4096}))


def insert_checkpoint(fixture, prompt, state, *, preferred=None):
    runtime = fixture.runtime
    raw = runtime.canon(response('fictional-ready')) if state == 'complete' else None
    with sqlite3.connect(fixture.frozen.path) as db:
        db.execute('INSERT INTO calls VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                   (cache_key(fixture, prompt, preferred), 1, runtime.sha(prompt),
                    'fictional-ready', state, None if state != 'hold' else 'api_timeout',
                    0, 8000, raw, runtime.sha(raw) if raw is not None else None, 9999, 10000))


@pytest.mark.parametrize('number', [0, 1, 2])
@pytest.mark.parametrize('preferred', [None, 'fictional-ready'])
def test_held_prompt_blocked_even_with_other_reader_complete(runtime_fixture, number, preferred):
    f = runtime_fixture
    prompt = f.prompts[number]
    insert_checkpoint(f, prompt, 'complete', preferred=preferred)
    with pytest.raises(f.runtime.ModelError, match='kb_model_hold'):
        f.matrix.completion(prompt, {}, provider='dashscope', preferred_model=preferred)
    assert f.requests == []


def test_complete_checkpoint_reused_without_second_runner_request(runtime_fixture):
    f = runtime_fixture
    prompt = '虚构自然增量资料'
    first = f.matrix.completion(prompt, {}, provider='dashscope')
    assert len(f.requests) == 1
    second = f.matrix.completion(prompt, {}, provider='dashscope')
    assert first == second == response('fictional-ready')
    assert len(f.requests) == 1
    with sqlite3.connect(f.frozen.path) as db:
        assert db.execute('SELECT state,attempt FROM calls WHERE cache_key=?',
                          (cache_key(f, prompt),)).fetchall() == [('complete', 1)]


@pytest.mark.parametrize('state', ['provider_started', 'hold'])
def test_new_unknown_preflight_blocks_all_requests(runtime_fixture, state):
    f = runtime_fixture
    insert_checkpoint(f, '虚构新增未知结果', state)
    with pytest.raises(f.runtime.ModelError, match='kb_model_hold'):
        f.matrix.preflight()
    with pytest.raises(f.runtime.ModelError, match='kb_model_hold'):
        f.matrix.completion('虚构另一条新资料', {}, provider='dashscope')
    assert f.requests == []


def test_timeout_records_fourth_hold_and_never_retries(runtime_fixture):
    f = runtime_fixture
    prompt = '虚构结果未知的新请求'

    def timeout(prompt, schema, model):
        f.requests.append((f.runtime.sha(prompt), model))
        raise f.runtime.ModelError('api_timeout')

    f.matrix.complete_callback = timeout
    with pytest.raises(f.runtime.ModelError, match='api_timeout'):
        f.matrix.completion(prompt, {}, provider='dashscope')
    assert len(f.requests) == 1
    for _ in range(3):
        with pytest.raises(f.runtime.ModelError, match='kb_model_hold'):
            f.matrix.completion(prompt, {}, provider='dashscope')
        with pytest.raises(f.runtime.ModelError, match='kb_model_hold'):
            f.matrix.completion('虚构别的新资料', {}, provider='dashscope')
    assert len(f.requests) == 1
    with sqlite3.connect(f.frozen.path) as db:
        assert db.execute("SELECT count(*) FROM calls WHERE state='hold'").fetchone()[0] == 4
        assert db.execute('SELECT state,error_code,attempt FROM calls WHERE cache_key=?',
                          (cache_key(f, prompt),)).fetchall() == [('hold', 'api_timeout', 1)]
    with pytest.raises(f.runtime.ModelError, match='kb_model_hold'):
        f.matrix.preflight()


@pytest.mark.parametrize('reuse_cached_prompt', [False, True])
def test_boundary_pin_rechecked_before_each_completion(runtime_fixture, monkeypatch, tmp_path,
                                                       reuse_cached_prompt):
    f = runtime_fixture
    path = tmp_path / 'reviewed-boundary.json'
    path.write_text('{"虚构审阅版本":1}')
    path.chmod(0o600)
    original_reader = f.runtime.restricted
    reads = []

    def fixture_reader(requested, *, owner, **kwargs):
        # Root ownership is simulated only for this allowlisted synthetic file.
        # All regular mode/link checks still run against its actual test owner.
        assert requested == path and owner == 0
        reads.append(requested)
        return original_reader(requested, owner=os.geteuid(), **kwargs)

    monkeypatch.setattr(f.runtime, 'RECOVERY_BOUNDARY', path)
    monkeypatch.setattr(f.runtime, 'restricted', fixture_reader)
    f.matrix.recovery_boundary_sha256 = f.runtime.sha(path.read_bytes())
    prompt = '虚构边界测试请求'
    f.matrix.completion(prompt, {}, provider='dashscope')
    assert len(f.requests) == 1
    assert len(reads) >= 2
    path.write_text('{"虚构审阅版本":2}')
    with pytest.raises(f.runtime.ModelError, match='kb_model_dependency_changed'):
        f.matrix.completion(prompt if reuse_cached_prompt else '虚构下一条请求', {}, provider='dashscope')
    assert len(f.requests) == 1
