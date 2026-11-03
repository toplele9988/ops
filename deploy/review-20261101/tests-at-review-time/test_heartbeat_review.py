from concurrent.futures import ThreadPoolExecutor
import sqlite3
import time

from conftest import ROOT, load


def test_log_retention_uses_index_and_concurrent_heartbeats_remain_atomic(tmp_path):
    fixture = load('r7c_heartbeat_fixture', ROOT / 'tests/icbu_v2/test_heartbeat_log.py')
    module = fixture.load()
    tmp_path.chmod(0o700)
    store = module.HeartbeatStore(tmp_path / 'heartbeat.sqlite3')
    now = 1800000000
    store.receive(fixture.beat(), now=now - 15 * 86400)
    with sqlite3.connect(store.path) as db:
        plan = db.execute('EXPLAIN QUERY PLAN DELETE FROM heartbeat_log WHERE received_at<?', (now - module.LOG_RETENTION_SECONDS,)).fetchall()
    assert any('heartbeat_log_received' in row[-1] and 'SEARCH' in row[-1] for row in plan)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda i: store.receive(fixture.beat(), now=now + i), range(20)))
    assert all(r['persisted'] for r in results)
    with sqlite3.connect(store.path) as db:
        assert db.execute('SELECT count(*) FROM heartbeat_log').fetchone()[0] == 20
        assert db.execute('SELECT count(*) FROM probe_state').fetchone()[0] == 1
        assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
