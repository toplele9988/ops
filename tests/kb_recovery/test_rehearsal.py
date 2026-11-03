"""真实补丁在虚构来源、临时 SQLite、假 runner 下的完整续跑演练。"""
import json
import sqlite3

import pytest

from recovery_fixtures import live_fixture, load_modules


def checkpoint(fixture):
    with sqlite3.connect(fixture.frozen.path) as db:
        return db.execute("SELECT * FROM calls WHERE state='hold' ORDER BY cache_key").fetchall()


def test_preflight_watermark_natural_increment_once(tmp_path, monkeypatch):
    f = live_fixture(tmp_path, monkeypatch)
    f.add(1, 800)
    f.add(2, 900, admitted=1100)  # Cutover 后补入的历史证据仍排除。
    f.add(3, None, admitted=1100)
    with f.db:
        f.db.execute('INSERT INTO kb_live_state VALUES(?,?,?)', ('fictional-room', 900, 700))
    before = tuple(f.db.execute('SELECT * FROM kb_live_state').fetchone())
    assert len(f.matrix.preflight()) == 3
    for _ in range(4):
        assert f.tick() == []
    assert tuple(f.db.execute('SELECT * FROM kb_live_state').fetchone()) == before
    assert f.requests == []
    f.add(4, 1100)
    logs = f.tick()
    assert logs[0]['units'] == 1
    assert len(f.requests) == 2  # 同一单元的两个独立读者，各一次。
    assert tuple(f.db.execute('SELECT seen_at,processed_at FROM kb_live_state').fetchone()) == (1100, 1100)
    for _ in range(5):
        assert f.tick() == []
    assert len(f.requests) == 2
    assert checkpoint(f) == f.held_rows
    assert f.frozen.ledger['models']['fictional-model-3']['unknown_reserved_tokens'] == 5003
    assert f.db.execute('PRAGMA user_version').fetchone()[0] == 0
    f.db.close()


def test_equal_timestamp_arrival_and_append_do_not_replay(tmp_path, monkeypatch):
    f = live_fixture(tmp_path, monkeypatch)
    f.add(1, 1100)
    f.tick()
    first_requests = list(f.requests)
    f.add(2, 1100)  # 和已完成水位相同，也必须处理。
    f.tick()
    assert len(f.requests) == 4
    assert f.requests[:2] == first_requests
    assert len({prompt for prompt, _ in f.requests}) == 2
    f.add(3, 1150)
    f.tick()
    assert len(f.requests) == 6
    assert f.db.execute('SELECT count(*) FROM kb_segment').fetchone()[0] == 3
    assert checkpoint(f) == f.held_rows
    f.db.close()


def test_fact_keeps_fictional_source_and_time(tmp_path, monkeypatch):
    f = live_fixture(tmp_path, monkeypatch)
    f.facts.append({'msg': 1, 'type': 'spec', 'attr': 'power_w',
                    'product': '', 'model': '', 'quote': '200W'})
    f.add(1, 1100, text='虚构测试灯具 功率200W')
    assert f.tick()[0]['facts'] == 1
    row = f.db.execute('SELECT evidence_id,quoted_at,readers FROM kb_fact').fetchone()
    assert tuple(row[:2]) == ('fictional-evidence-1', 1100)
    assert len(row['readers'].split('+')) == 2
    assert f.tick() == []
    assert len(f.requests) == 2
    f.db.close()


def test_preflight_rejects_before_any_catalog_or_frontier_write(tmp_path, monkeypatch):
    f = live_fixture(tmp_path, monkeypatch)
    f.add(1, 1100)
    f.frozen.ledger['models']['fictional-model-3']['accounted_tokens'] += 1
    statements = []
    f.db.set_trace_callback(statements.append)
    with pytest.raises(f.modules.kb_live._TickFailure):
        f.tick()
    assert statements == []
    assert not f.modules.kb_live.RECOVERY_DB.exists()
    assert f.requests == []
    f.db.close()


@pytest.mark.parametrize('change', ['short_to_document', 'document_to_short', 'role', 'room', 'time'])
def test_mutated_evidence_never_enters_a_different_unit(tmp_path, monkeypatch, change):
    f = live_fixture(tmp_path, monkeypatch)
    text = '虚构长资料参数。' * 100 if change == 'document_to_short' else '虚构原资料参数'
    f.add(1, 1100, text=text)
    f.tick()
    before = list(f.requests)
    if change == 'short_to_document':
        f.evidence[0]['body_text'] = '虚构长资料参数。' * 100
    elif change == 'document_to_short':
        f.evidence[0]['body_text'] = '虚构短资料'
    elif change == 'role':
        monkeypatch.setattr(f.modules.kb_live, 'kb_context', lambda *a: {
            '_supplier_context': {}, '_learning': {'fictional-room': {}}})
    elif change == 'room':
        f.evidence[0]['chatroom_id'] = 'fictional-other-room'
    else:
        f.evidence[0]['source_created_at'] += 1
    with pytest.raises(f.modules.kb_live._TickFailure):
        f.tick()
    assert f.requests == before
    assert checkpoint(f) == f.held_rows
    f.db.close()


def test_persisted_epoch_prevents_cutover_rollback(tmp_path, monkeypatch):
    f = live_fixture(tmp_path, monkeypatch)
    assert f.tick() == []
    with pytest.raises(f.modules.kb_live._TickFailure):
        f.modules.kb_live.tick(f.db, None, f.modules.kb_live.LiveConfig(None, 0), {}, 10000,
                              matrix=f.matrix, limiter=f.limiter, recovery_after=900)
    assert f.requests == []
    f.db.close()


def test_busy_room_settles_each_evidence(tmp_path, monkeypatch):
    f = live_fixture(tmp_path, monkeypatch)
    f.add(1, 1100)
    f.add(2, 1999)
    logs = f.modules.kb_live.tick(
        f.db, None, f.modules.kb_live.LiveConfig(None, 8), {}, 2000,
        matrix=f.matrix, limiter=f.limiter, recovery_after=1000)
    assert logs[0]['units'] == 1
    assert f.db.execute('SELECT processed_at FROM kb_live_state').fetchone()[0] == 1100
    assert len(f.requests) == 2
    f.db.close()


def test_catalog_failure_reuses_complete_model_checkpoints(tmp_path, monkeypatch):
    f = live_fixture(tmp_path, monkeypatch)
    f.add(1, 1100)
    original = f.modules.kb_extract._record
    def fail(*args, **kwargs):
        raise sqlite3.OperationalError('虚构本地写入失败')
    monkeypatch.setattr(f.modules.kb_extract, '_record', fail)
    with pytest.raises(f.modules.kb_live._TickFailure):
        f.tick()
    assert len(f.requests) == 2
    assert f.db.execute('SELECT processed_at FROM kb_live_state').fetchone()[0] == 0
    monkeypatch.setattr(f.modules.kb_extract, '_record', original)
    f.limiter._events.clear()
    f.tick()
    assert len(f.requests) == 2
    assert f.db.execute('SELECT processed_at FROM kb_live_state').fetchone()[0] == 1100
    assert checkpoint(f) == f.held_rows
    f.db.close()


def test_old_quarantine_is_terminal_and_not_more(tmp_path, monkeypatch):
    f = live_fixture(tmp_path, monkeypatch)
    f.add(1, 800)
    unit = f.modules.kb_extract.units(f.db, {'_learning': {}}, ['fictional-room'])[0]
    prompt_hash = f.modules.kb_model_runtime.sha(f.modules.kb_extract.PROMPT + unit['text'])
    f.matrix.quarantine_preflight = lambda prompts: frozenset([prompt_hash])
    result = f.modules.kb_extract.run_units(f.db, {'_learning': {},
        '_kb_live_rows': {row['evidence_id']: row for row in f.evidence}}, 10000,
                                           ['fictional-room'], matrix=f.matrix)
    assert result['quarantined'] == 1
    assert result['more'] is False
    assert result['units'] == 0
    assert f.requests == []
    f.db.close()


def test_failed_content_does_not_advance_or_retry(tmp_path, monkeypatch):
    f = live_fixture(tmp_path, monkeypatch)
    f.add(1, 1100)
    f.facts.extend([{'msg': 1} for _ in range(201)])
    assert f.tick()[0]['units'] == 1
    assert f.db.execute('SELECT processed_at FROM kb_live_state').fetchone()[0] == 0
    assert f.db.execute('SELECT status FROM kb_segment').fetchone()[0] == 'failed'
    requests = list(f.requests)
    assert f.tick() == []
    assert f.requests == requests
    f.db.close()


def test_failed_room_stays_behind_after_later_success(tmp_path, monkeypatch):
    f = live_fixture(tmp_path, monkeypatch)
    f.add(1, 1100)
    f.facts.extend([{'msg': 1} for _ in range(201)])
    f.tick()
    f.facts.clear()
    f.add(2, 1200)
    assert f.tick()[0]['units'] == 1
    assert f.db.execute('SELECT processed_at FROM kb_live_state').fetchone()[0] == 0
    assert f.tick() == []
    assert len(f.requests) == 3
    f.db.close()


def test_changed_row_between_admission_and_units_fails_before_request(tmp_path, monkeypatch):
    f = live_fixture(tmp_path, monkeypatch)
    f.add(1, 1100)
    def changed(db, ctx):
        return [{**row, 'body_text': '虚构资料被改写'} for row in f.evidence]
    monkeypatch.setattr(f.modules.kb_extract, 'kb_authorized', changed)
    with pytest.raises(f.modules.kb_live._TickFailure):
        f.tick()
    assert f.requests == []
    f.db.close()


def test_real_round_with_all_io_redirected_to_fictional_files(tmp_path, monkeypatch):
    import sys
    from types import ModuleType
    f = live_fixture(tmp_path, monkeypatch)
    f.add(1, 1100)
    live = f.modules.kb_live
    state_path = tmp_path / 'fictional-state.sqlite3'
    with sqlite3.connect(state_path):
        pass
    catalog_path = tmp_path / 'fictional-catalog.sqlite3'
    catalog_path.chmod(0o600)
    fake_groupbot = ModuleType('api_groupbot')
    fake_groupbot.load_config = lambda path: None
    monkeypatch.setitem(sys.modules, 'api_groupbot', fake_groupbot)
    monkeypatch.setattr(live, 'read_policy', lambda: {})
    monkeypatch.setattr(live.cpq_batch, 'STATE', str(state_path))
    monkeypatch.setattr(live.cpq_batch, 'SOURCE_CATALOG', str(catalog_path))
    monkeypatch.setattr(live.cpq_batch, 'CONFIG', str(tmp_path / 'unused-fictional-config.json'))
    monkeypatch.setattr(live.kb_profile, 'LOCK', tmp_path / 'fictional-build.lock')
    monkeypatch.setattr(live.kb_model_runtime, 'create_matrix', lambda *a, **kw: f.matrix)
    monkeypatch.setattr(live, 'read_recovery_boundary', lambda matrix: 1000)
    monkeypatch.setattr(live.time, 'time', lambda: 10000)
    assert live._round(settle_seconds=0, limiter=f.limiter)[0]['units'] == 1
    assert live._round(settle_seconds=0, limiter=f.limiter) == []
    assert len(f.requests) == 2
    assert checkpoint(f) == f.held_rows
    f.db.close()
