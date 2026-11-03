"""真实候选模块图、生产适配器、双重账本串联；只注入虚构来源和假runner。"""
from contextlib import closing
from copy import deepcopy
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'deploy/kb'))
import backlog as b
import coordinator as c
from history_fence import HistoryFence, file_digest
from physical_gate import PhysicalGate, GateUnknown
from test_coordinator import config
from test_production_adapter import wechat


def assemble(tmp_path, fixture):
    f, adapter = fixture
    directory = tmp_path / 'integrated-private'
    directory.mkdir(mode=0o700)
    approved = directory / 'approved.sqlite3'
    approved.touch(mode=0o600)
    with closing(sqlite3.connect(f.frozen.path.as_uri() + '?mode=ro', uri=True)) as source, \
            closing(sqlite3.connect(approved)) as target:
        source.backup(target)
    gate_path = directory / 'physical.sqlite3'
    adapter.history_fence = HistoryFence(approved, file_digest(approved), gate_path)
    adapter.gate_path = gate_path
    settings = config()
    settings['physical']['models'] = {model: {'bucket': 'free', 'frozen': False,
                                             'cost_micros_per_1m_tokens': None}
                                     for model in adapter.models}
    settings['physical']['buckets']['free']['max_tokens'] = 1000000
    manifest = b.make_manifest({'schema': 'kb-backlog-review-v1', 'after': 1000,
                               'snapshots': {'calls': file_digest(approved)}, 'entries': []}, adapter.quarantine)
    progress_path = directory / 'progress.sqlite3'
    now = [10000]
    f.matrix.now = lambda: now[0]

    def restart():
        scheduler = b.Scheduler(manifest, b.digest(manifest), adapter.quarantine, path=progress_path)
        gate = PhysicalGate(gate_path, settings['physical'], clock=lambda: now[0])
        return c.Coordinator(scheduler, gate, adapter, settings, clock=lambda: now[0])
    return f, adapter, restart, now, gate_path


def test_production_adapter_runtime_fence_and_gate_complete_without_repeat(tmp_path, wechat):
    f, adapter, restart, now, gate_path = assemble(tmp_path, wechat)
    f.add(1, 5000)
    coordinator = restart()
    coordinator.discover()
    assert coordinator.run_round() == 0
    assert len(f.requests) == 1
    coordinator = restart()
    coordinator.discover()  # Fence must accept only the first reader's exact gate response.
    now[0] += 29
    assert coordinator.run_round() == 0 and len(f.requests) == 1
    now[0] += 1
    assert coordinator.run_round() == 1 and len(f.requests) == 2
    coordinator = restart()
    coordinator.discover()
    now[0] += 30
    assert coordinator.run_round() == 0 and len(f.requests) == 2
    assert f.db.execute('SELECT status FROM kb_segment').fetchall()[0][0] == 'no_facts'
    with sqlite3.connect(f.frozen.path) as db:
        assert db.execute("SELECT count(*) FROM calls WHERE state='hold'").fetchone()[0] == 3
        assert db.execute("SELECT count(*) FROM calls WHERE state='complete'").fetchone()[0] == 2
    assert PhysicalGate.read_status(gate_path)['buckets']['free']['reserved_tokens'] == 131072


def test_runtime_new_hold_is_global_unknown_and_old_three_are_unchanged(tmp_path, wechat):
    f, adapter, restart, now, gate_path = assemble(tmp_path, wechat)
    f.add(1, 5000)
    def timeout(prompt, schema, model):
        f.requests.append((f.modules.kb_model_runtime.sha(prompt), model))
        raise f.modules.kb_model_runtime.ModelError('api_timeout')
    f.matrix.complete_callback = timeout
    coordinator = restart()
    coordinator.discover()
    with pytest.raises(GateUnknown):
        coordinator.run_round()
    coordinator = restart()
    now[0] += 100
    with pytest.raises(GateUnknown):
        coordinator.run_round()
    assert len(f.requests) == 1
    with sqlite3.connect(f.frozen.path) as db:
        for original in f.held_rows:
            assert db.execute('SELECT * FROM calls WHERE cache_key=? AND attempt=?', original[:2]).fetchone() == original
        assert db.execute("SELECT count(*) FROM calls WHERE state='hold'").fetchone()[0] == 4
    assert PhysicalGate.read_status(gate_path)['stop_reason'] == 'unknown'
