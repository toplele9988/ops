"""三条虚构未知结果的冻结清单与拒绝条件。"""
from copy import deepcopy
import os
import sqlite3
from types import SimpleNamespace

import pytest

from recovery_fixtures import load_modules


def synthetic_holds(modules, tmp_path):
    root = tmp_path / 'private-ledger'
    root.mkdir(mode=0o700)
    path = root / 'calls.sqlite3'
    with path.open('xb'):
        pass
    path.chmod(0o600)
    db = sqlite3.connect(path)
    db.executescript('''CREATE TABLE calls(
      cache_key TEXT NOT NULL,attempt INTEGER NOT NULL,prompt_sha256 TEXT NOT NULL,
      model TEXT NOT NULL,state TEXT NOT NULL,error_code TEXT,day INTEGER NOT NULL,
      reserved_tokens INTEGER NOT NULL,response_json TEXT,response_sha256 TEXT,
      created_at REAL NOT NULL,updated_at REAL NOT NULL,
      PRIMARY KEY(cache_key,attempt));
      CREATE TABLE scope(key TEXT PRIMARY KEY,value TEXT NOT NULL);''')
    models = {}
    for number in range(1, 4):
        model = 'fictional-model-' + str(number)
        reserved = 5000 + number
        db.execute('INSERT INTO calls VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                   (modules.kb_unknown_isolation.digest(['key', number]), 1,
                    modules.kb_unknown_isolation.digest(['prompt', number]),
                    model, 'hold', 'api_timeout', 100, reserved,
                    None, None, 100.0, 101.0))
        models[model] = {'blocked_reason': 'usage_uncertain', 'in_flight': False,
                         'unknown_reserved_tokens': reserved, 'accounted_tokens': 20}
    models['fictional-exhausted'] = {
        'blocked_reason': 'free_tier_exhausted', 'in_flight': False,
        'unknown_reserved_tokens': 6000, 'accounted_tokens': 1000000}
    models['fictional-ready'] = {'blocked_reason': '', 'in_flight': False,
                                 'unknown_reserved_tokens': 0, 'accounted_tokens': 0}
    db.execute("INSERT INTO scope VALUES('hold','api_timeout')")
    db.commit()
    ledger = {'models': models}
    matrix = SimpleNamespace(legacy_quarantine=(), quota_baseline={name: 0 for name in models})
    matrix._read = lambda locked=False: deepcopy(ledger)
    checked = []
    matrix._quota_ready = lambda value: checked.append(value)
    return SimpleNamespace(db=db, path=path, ledger=ledger, matrix=matrix, checked=checked)


@pytest.fixture
def frozen(tmp_path):
    modules = load_modules(tmp_path)
    fixture = synthetic_holds(modules, tmp_path)
    fixture.modules = modules
    fixture.manifest = modules.kb_unknown_isolation.capture(
        fixture.db, fixture.ledger, fixture.matrix)
    yield fixture
    fixture.db.close()


def test_exact_three_preserve_audit_and_reserved_tokens(frozen):
    isolation = frozen.modules.kb_unknown_isolation
    before = frozen.db.execute('SELECT * FROM calls ORDER BY cache_key').fetchall()
    original = deepcopy(frozen.ledger)
    manifest = frozen.manifest
    assert manifest['schema'] == 'wechat-kb-unknown-v2'
    assert manifest['hold_count'] == len(manifest['holds']) == 3
    assert all(row['state'] == 'quarantine' for row in manifest['holds'])
    assert len(isolation.assess(frozen.db, frozen.ledger, frozen.matrix, manifest)) == 3
    assert len(isolation.inspect_file(frozen.path, frozen.ledger, frozen.matrix, manifest)) == 3
    frozen.modules.quota_isolation.install(frozen.matrix, manifest)
    frozen.matrix._quota_ready(deepcopy(frozen.ledger))
    assert set(frozen.checked[0]['models']) == {'fictional-ready', 'fictional-exhausted'}
    assert frozen.db.execute('SELECT * FROM calls ORDER BY cache_key').fetchall() == before
    assert frozen.ledger == original


@pytest.mark.parametrize('sql', [
    "UPDATE calls SET updated_at=updated_at+1 WHERE model='fictional-model-3'",
    "UPDATE calls SET reserved_tokens=reserved_tokens+1 WHERE model='fictional-model-3'",
    "UPDATE calls SET state='provider_started' WHERE model='fictional-model-3'",
    "UPDATE calls SET state='complete' WHERE model='fictional-model-3'",
    "UPDATE calls SET error_code='api_transport_failed' WHERE model='fictional-model-3'",
    "UPDATE calls SET response_json='{}' WHERE model='fictional-model-3'",
    "UPDATE calls SET response_sha256='not-empty' WHERE model='fictional-model-3'",
    "UPDATE scope SET value='unknown_provider_outcome' WHERE key='hold'",
    "INSERT INTO scope VALUES('new_scope','not-reviewed')",
    "INSERT INTO calls SELECT cache_key,attempt+1,prompt_sha256,model,state,error_code,day,"
    "reserved_tokens,response_json,response_sha256,created_at,updated_at FROM calls "
    "WHERE model='fictional-model-3'",
])
def test_changed_or_new_unknown_fails_closed(frozen, sql):
    frozen.db.execute(sql)
    frozen.db.commit()
    with pytest.raises(frozen.modules.kb_unknown_isolation.IsolationError):
        frozen.modules.kb_unknown_isolation.assess(
            frozen.db, frozen.ledger, frozen.matrix, frozen.manifest)


@pytest.mark.parametrize('mutation', ['tokens', 'record', 'uncertainty', 'in_flight'])
def test_quota_mutation_is_never_ignored(frozen, mutation):
    frozen.modules.quota_isolation.install(frozen.matrix, frozen.manifest)
    changed = deepcopy(frozen.ledger)
    record = changed['models']['fictional-model-3']
    if mutation == 'tokens':
        record['unknown_reserved_tokens'] += 1
    elif mutation == 'record':
        record['accounted_tokens'] += 1
    elif mutation == 'uncertainty':
        changed['models']['fictional-ready']['blocked_reason'] = 'usage_uncertain'
    else:
        record['in_flight'] = True
    with pytest.raises(RuntimeError, match='qwen_matrix_unavailable'):
        frozen.matrix._quota_ready(changed)
    with pytest.raises(frozen.modules.kb_unknown_isolation.IsolationError):
        frozen.modules.kb_unknown_isolation.assess(
            frozen.db, changed, frozen.matrix, frozen.manifest)


def test_manifest_cannot_silently_drop_or_release_a_hold(frozen):
    isolation = frozen.modules.kb_unknown_isolation
    for manifest in [
        {**frozen.manifest, 'hold_count': 2},
        {**frozen.manifest, 'holds': frozen.manifest['holds'][:2]},
        {**frozen.manifest, 'schema': 'wechat-kb-two-unknown-v1'},
        {**frozen.manifest, 'holds': [
            {**row, 'state': 'retry'} for row in frozen.manifest['holds']]},
    ]:
        with pytest.raises(isolation.IsolationError):
            isolation.assess(frozen.db, frozen.ledger, frozen.matrix, manifest)
        with pytest.raises(RuntimeError, match='qwen_matrix_unavailable'):
            frozen.modules.quota_isolation.install(frozen.matrix, manifest)


def test_held_prompt_blocked_and_partial_live_room_allowed(frozen):
    isolation = frozen.modules.kb_unknown_isolation
    held = isolation.assess(frozen.db, frozen.ledger, frozen.matrix, frozen.manifest)
    assert isolation.confirm_live_units(held, []) == held
    assert isolation.confirm_live_units(held, [next(iter(held))]) == held
    assert isolation.confirm_units(held, list(held)) == held
    for prompt in held:
        with pytest.raises(isolation.IsolationError):
            isolation.allow_other_prompt(
                frozen.db, frozen.ledger, frozen.matrix, prompt, frozen.manifest)
    assert isolation.allow_other_prompt(
        frozen.db, frozen.ledger, frozen.matrix, isolation.digest('虚构新资料'), frozen.manifest)
    with pytest.raises(isolation.IsolationError):
        isolation.confirm_units(held, list(held)[:2])


@pytest.mark.parametrize('unsafe', ['permissions', 'parent_permissions', 'symlink', 'hardlink'])
def test_ledger_path_controls_remain_strict(frozen, tmp_path, unsafe):
    path = frozen.path
    if unsafe == 'permissions':
        path.chmod(0o644)
    elif unsafe == 'parent_permissions':
        path.parent.chmod(0o755)
    elif unsafe == 'symlink':
        link = path.parent / 'alias.sqlite3'
        link.symlink_to(path)
        path = link
    else:
        os.link(path, path.parent / 'alias.sqlite3')
    with pytest.raises(frozen.modules.kb_unknown_isolation.IsolationError):
        frozen.modules.kb_unknown_isolation.inspect_file(
            path, frozen.ledger, frozen.matrix, frozen.manifest)
