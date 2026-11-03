"""Fictional quota history only: temporary SQLite, no model or network calls."""
from concurrent.futures import ThreadPoolExecutor
import multiprocessing
from types import SimpleNamespace

import pytest

from console.archive.store import Store
from console.roles.storage import RateLimited, reserve


NOW = 1961568000.0


def quota(attempts=72):
    return SimpleNamespace(per_minute=attempts, per_hour=attempts)


def rows(store, table):
    with store.read() as db:
        return [tuple(row) for row in db.execute('SELECT * FROM ' + table + ' ORDER BY id')]


@pytest.mark.parametrize('version', [4, 7, 8])
def test_independent_bucket_preserves_old_schema_rows_and_writers(tmp_path, version):
    store = Store(tmp_path / 'fictional-quota.sqlite3', target_version=version)
    store.initialize()
    with store.write() as db:
        # Positional inserts model an old deployed writer with no bucket column.
        db.execute('INSERT INTO role_ai_reservations VALUES (1,?,3)', (NOW,))
        original_sql = db.execute("SELECT sql FROM sqlite_master WHERE name='role_ai_reservations'").fetchone()[0]
        versions = list(db.execute('SELECT * FROM schema_migrations ORDER BY version'))
    before = rows(store, 'role_ai_reservations')
    with pytest.raises(RateLimited):
        reserve(store, NOW, quota(3))
    reserve(store, NOW, quota(3), bucket='extract')
    assert rows(store, 'role_ai_reservations') == before
    assert rows(store, 'extract_ai_reservations') == [(1, NOW, 3)]
    with store.write() as db:
        assert db.execute("SELECT sql FROM sqlite_master WHERE name='role_ai_reservations'").fetchone()[0] == original_sql
        assert list(db.execute('SELECT * FROM schema_migrations ORDER BY version')) == versions
        db.execute('INSERT INTO role_ai_reservations VALUES (2,?,3)', (NOW,))
        assert not db.execute('PRAGMA foreign_key_check').fetchall()
    assert rows(store, 'extract_ai_reservations') == [(1, NOW, 3)]


def test_extract_twenty_four_calls_reserve_seventy_two_attempts(tmp_path):
    store = Store(tmp_path / 'fictional-quota.sqlite3', target_version=7)
    for _ in range(24):
        reserve(store, NOW, quota(), bucket='extract')
    assert len(rows(store, 'extract_ai_reservations')) == 24
    assert sum(row[2] for row in rows(store, 'extract_ai_reservations')) == 72
    # Another service instance sees the same durable quota, including failures.
    with pytest.raises(RateLimited) as blocked:
        reserve(Store(store.path, target_version=7), NOW, quota(), bucket='extract')
    assert blocked.value.retry_after == 3600
    assert '独立额度' in str(blocked.value) and '规则建议' not in str(blocked.value)
    # Exhausting extract does not consume any of the other roles' 60 attempts.
    for _ in range(20):
        reserve(store, NOW, quota(60))
    with pytest.raises(RateLimited):
        reserve(store, NOW, quota(60))
    assert sum(row[2] for row in rows(store, 'role_ai_reservations')) == 60


def test_shared_only_reservation_does_not_initialize_extract_bucket(tmp_path):
    store = Store(tmp_path / 'fictional-quota.sqlite3', target_version=4)
    reserve(store, NOW, quota())
    with store.read() as db:
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='extract_ai_reservations'").fetchone()


def test_bucket_pruning_and_rolling_boundaries_are_independent(tmp_path):
    store = Store(tmp_path / 'fictional-quota.sqlite3', target_version=7)
    reserve(store, NOW, quota(3))
    reserve(store, NOW, quota(3), bucket='extract')
    with pytest.raises(RateLimited) as blocked:
        reserve(store, NOW + 3599.2, quota(3), bucket='extract')
    assert blocked.value.retry_after == 1
    reserve(store, NOW + 3600, quota(3), bucket='extract')
    assert rows(store, 'role_ai_reservations') == [(1, NOW, 3)]
    assert len(rows(store, 'extract_ai_reservations')) == 1
    assert rows(store, 'extract_ai_reservations')[0][1:] == (NOW + 3600, 3)
    reserve(store, NOW + 3600, quota(3))
    assert len(rows(store, 'role_ai_reservations')) == 1


def test_extract_minute_limit_and_waits_are_still_enforced(tmp_path):
    store = Store(tmp_path / 'fictional-quota.sqlite3', target_version=7)
    config = SimpleNamespace(per_minute=3, per_hour=72)
    reserve(store, NOW, config, bucket='extract')
    with pytest.raises(RateLimited) as blocked:
        reserve(store, NOW + 12.1, config, bucket='extract')
    assert blocked.value.retry_after == 48
    reserve(store, NOW + 60, config, bucket='extract')
    assert len(rows(store, 'extract_ai_reservations')) == 2


@pytest.mark.parametrize('bucket', ['unknown', 'extract;DROP TABLE parties', '', None])
def test_unknown_bucket_is_rejected_before_creating_database(tmp_path, bucket):
    store = Store(tmp_path / 'fictional-quota.sqlite3', target_version=7)
    with pytest.raises(ValueError, match='额度桶'):
        reserve(store, NOW, quota(), bucket=bucket)
    assert not store.path.exists()


def attempt(path):
    try:
        reserve(Store(path, target_version=7), NOW, quota(9), bucket='extract')
        return True
    except RateLimited:
        return False


def test_concurrent_threads_share_atomic_extract_reservations(tmp_path):
    store = Store(tmp_path / 'fictional-quota.sqlite3', target_version=7)
    store.initialize()
    with ThreadPoolExecutor(max_workers=6) as pool:
        accepted = list(pool.map(attempt, [store.path] * 12))
    assert sum(accepted) == 3
    assert len(rows(store, 'extract_ai_reservations')) == 3
    assert rows(store, 'role_ai_reservations') == []


def process_attempt(path, queue):
    queue.put(attempt(path))


def test_concurrent_processes_initialize_one_bucket_and_obey_limit(tmp_path):
    store = Store(tmp_path / 'fictional-quota.sqlite3', target_version=7)
    store.initialize()
    context = multiprocessing.get_context('fork')
    queue = context.Queue()
    jobs = [context.Process(target=process_attempt, args=(store.path, queue)) for _ in range(6)]
    for job in jobs:
        job.start()
    for job in jobs:
        job.join(timeout=10)
        assert job.exitcode == 0
    assert sum(queue.get(timeout=2) for _ in jobs) == 3
    assert len(rows(store, 'extract_ai_reservations')) == 3
    assert rows(store, 'role_ai_reservations') == []
