"""四条未知请求按来源永久隔离；换窗口、版本、模型仍拒绝。"""
import json
import sqlite3

import pytest

from recovery_fixtures import live_fixture, load_modules, synthetic_quarantine
from test_isolation import synthetic_holds


@pytest.mark.parametrize('channel', ['wechat', 'alibaba'])
@pytest.mark.parametrize('match', ['source', 'content', 'original'])
def test_increment_unknown_reuse_rejected_before_state_write(tmp_path, monkeypatch, channel, match):
    f = live_fixture(tmp_path, monkeypatch)
    f.add(1, 1100)
    row = f.evidence[-1]
    source = 'fictional-held-source-1' if channel == 'wechat' else 'fictional-ali-source'
    content = '虚构未知资料1' if channel == 'wechat' else '虚构阿里未知资料'
    if match == 'source':
        row['evidence_id'] = source
    elif match == 'content':
        row['body_text'] = content
    else:
        # Alias ID, parser output and prompt all changed; original bytes did not.
        original = 'fictional-held-original-1' if channel == 'wechat' else 'fictional-ali-original'
        row['source_sha256'] = f.modules.kb_model_runtime.sha(original)
    with pytest.raises(f.modules.kb_live._TickFailure):
        f.tick()
    assert f.requests == []
    assert f.db.execute('SELECT count(*) FROM kb_live_state').fetchone()[0] == 0
    assert not f.modules.kb_live.RECOVERY_DB.exists()
    f.db.close()


@pytest.mark.parametrize('cache', [False, True])
def test_quarantine_manifest_pin_checked_before_cached_or_new_request(tmp_path, monkeypatch, cache):
    f = live_fixture(tmp_path, monkeypatch)
    f.add(1, 1100)
    f.tick()
    before = list(f.requests)
    path = f.modules.kb_model_runtime.SOURCE_QUARANTINE
    value = json.loads(path.read_text())
    value['holds'][0]['source_keys'][0] = '0' * 64
    path.write_text(json.dumps(value))
    unit = f.modules.kb_extract.units(f.db, {'_learning': {},
        '_kb_live_ids': frozenset([f.evidence[0]['evidence_id']]),
        '_kb_live_rows': {f.evidence[0]['evidence_id']: f.evidence[0]}}, ['fictional-room'])[0]
    prompt = f.modules.kb_extract.PROMPT + unit['text'] if cache else '虚构新请求'
    with pytest.raises(f.modules.kb_model_runtime.ModelError):
        f.matrix.completion(prompt, f.modules.kb_extract.SCHEMA, provider='dashscope')
    assert f.requests == before
    f.db.close()


def test_quarantine_binds_exact_original_hold_rows(tmp_path):
    m = load_modules(tmp_path)
    held = synthetic_holds(m, tmp_path)
    manifest = m.kb_unknown_isolation.capture(held.db, held.ledger, held.matrix)
    gate = m.kb_source_gate
    value = synthetic_quarantine(m, manifest)
    gate.bind_wechat(value, manifest)
    value['holds'][0]['request_sha256'] = '0' * 64
    with pytest.raises(ValueError):
        gate.bind_wechat(value, manifest)
    held.db.close()


def test_gate_rechecks_after_admission_before_reader(tmp_path, monkeypatch):
    f = live_fixture(tmp_path, monkeypatch)
    f.add(1, 1100)
    original = f.matrix.check_sources
    seen = []
    def checked(rows, prompt_sha256=None):
        seen.append(prompt_sha256)
        if prompt_sha256 is not None:
            raise f.modules.kb_model_runtime.ModelError('kb_model_hold')
        return original(rows, prompt_sha256)
    monkeypatch.setattr(f.matrix, 'check_sources', checked)
    with pytest.raises(f.modules.kb_live._TickFailure):
        f.tick()
    assert seen[0] is None and any(p is not None for p in seen)
    assert f.requests == []
    with sqlite3.connect(f.frozen.path) as db:
        assert db.execute("SELECT count(*) FROM calls WHERE state='hold'").fetchone()[0] == 3
    f.db.close()


def test_isolated_non_live_extraction_cannot_skip_source_gate(tmp_path, monkeypatch):
    f = live_fixture(tmp_path, monkeypatch)
    f.add(1, 1100)
    f.evidence[0]['evidence_id'] = 'fictional-held-source-1'
    initialized = []
    monkeypatch.setattr(f.modules.kb_extract.kb_store, 'initialize', lambda db: initialized.append(True))
    with pytest.raises(f.modules.kb_model_runtime.ModelError, match='kb_model_hold'):
        f.modules.kb_extract.run_units(f.db, {'_learning': {}}, 10000,
                                     ['fictional-room'], matrix=f.matrix)
    assert f.requests == initialized == []
    f.db.close()


def test_replacing_reviewed_quarantine_cannot_rebase_persisted_epoch(tmp_path, monkeypatch):
    f = live_fixture(tmp_path, monkeypatch)
    assert f.tick() == []
    runtime = f.modules.kb_model_runtime
    path = runtime.SOURCE_QUARANTINE
    value = json.loads(path.read_text())
    value['holds'][0]['source_keys'] = [runtime.sha('fictional-shrunken-source-list')]
    path.write_text(json.dumps(value))
    f.matrix.source_quarantine_sha256 = runtime.sha(path.read_bytes())
    with pytest.raises(f.modules.kb_live._TickFailure):
        f.tick()
    assert f.requests == []
    f.db.close()
