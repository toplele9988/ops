from pathlib import Path
import sqlite3
import time

import pytest

from console.overview import safety
from console.pipeline.readers import READERS
from conftest import NOW, channel, execute, read, stage


def test_every_sqlite_connection_readonly_no_body_columns_and_no_file_changes(healthy, monkeypatch):
    root = healthy['inbound'].parent
    before = {p.name: (p.stat().st_mtime_ns, p.read_bytes()) for p in root.iterdir()}
    original = sqlite3.connect
    connections = []
    denied = []
    forbidden_columns = {'content', 'sender_id', 'group_id', 'response_json', 'result_json',
                         'draft', 'search_result', 'original', 'plan_json', 'asset_id'}
    forbidden_actions = {sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE,
                         sqlite3.SQLITE_CREATE_TABLE, sqlite3.SQLITE_DROP_TABLE, sqlite3.SQLITE_ATTACH}

    def connect(database, *args, **kwargs):
        assert 'mode=ro' in database and kwargs.get('uri') is True
        db = original(database, *args, **kwargs)
        connections.append(db)
        def authorise(action, arg1, arg2, *_):
            if action in forbidden_actions or (action == sqlite3.SQLITE_READ and arg2 in forbidden_columns):
                denied.append((action, arg1, arg2))
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK
        db.set_authorizer(authorise)
        return db

    monkeypatch.setattr(sqlite3, 'connect', connect)
    data = read(healthy)
    assert all(item['state'] == 'ok' for item in data['channels']), denied
    assert data['model_budget']['deepseek_official']['balance_micros'] > 0
    after = {p.name: (p.stat().st_mtime_ns, p.read_bytes()) for p in root.iterdir()}
    assert before == after
    for db in connections:
        with pytest.raises(sqlite3.ProgrammingError):
            db.execute('SELECT 1')


def test_one_query_timeout_does_not_affect_other_sources(healthy, monkeypatch):
    def expensive(db, now):
        return db.execute('''WITH RECURSIVE x(n) AS (VALUES(1) UNION ALL SELECT n+1 FROM x WHERE n<100000000)
                             SELECT SUM(n) FROM x''').fetchone()
    monkeypatch.setitem(READERS, 'wechat_callbacks', ('wechat', expensive))
    start = time.monotonic()
    data = read(healthy, config={'query_timeout_seconds': .01})
    assert time.monotonic() - start < 2
    assert stage(data, 'wechat', 'collecting')['state'] == 'unavailable'
    assert channel(data, 'whatsapp')['state'] == 'ok'


def test_db_size_limit_is_per_source(healthy, monkeypatch):
    monkeypatch.setattr(safety, 'MAX_DB_BYTES', 128 * 1024)
    with healthy['calls'].open('ab') as stream:
        stream.truncate(129 * 1024)
    data = read(healthy)
    assert stage(data, 'wechat', 'knowledge')['state'] == 'unavailable'
    assert channel(data, 'whatsapp')['state'] == 'ok'


@pytest.mark.parametrize('kind', ['large', 'duplicate', 'nonfinite', 'fifo', 'symlink'])
def test_json_source_protections(healthy, kind):
    path = healthy['cpq']
    if kind == 'large':
        path.write_text(' ' * (safety.MAX_JSON_BYTES + 1))
    elif kind == 'duplicate':
        path.write_text('{"models":{},"models":{}}')
    elif kind == 'nonfinite':
        path.write_text('{"models":{},"version":NaN}')
    elif kind == 'fifo':
        import os
        path.unlink()
        os.mkfifo(path)
    else:
        path.unlink()
        path.symlink_to(healthy['wechat_usage'])
    start = time.monotonic()
    data = read(healthy)
    assert time.monotonic() - start < 2
    assert all(item['remaining_tokens'] is None for item in data['model_budget']['free_pools'])
    assert channel(data, 'wechat')['state'] == 'ok'


def test_database_symlink_and_missing_shm_are_unknown(healthy):
    path = healthy['calls']
    path.unlink()
    path.symlink_to(healthy['analysis'])
    assert stage(read(healthy), 'wechat', 'knowledge')['state'] == 'unavailable'
    Path(str(healthy['whatsapp']) + '-wal').write_bytes(b'fictional-wal')
    assert stage(read(healthy), 'whatsapp', 'connected')['state'] == 'unavailable'


def test_missing_column_is_unknown_not_empty(healthy):
    execute(healthy['analysis'], 'ALTER TABLE windows RENAME COLUMN updated_at TO fake_removed')
    item = stage(read(healthy), 'alibaba', 'knowledge')
    assert item['state'] == 'unavailable' and item['total'] is None


def test_history_result_size_limit(healthy, monkeypatch):
    import console.overview.readers as readers
    monkeypatch.setattr(readers, 'MAX_MONTHS', 0)
    data = read(healthy)
    assert data['history']['alibaba']['messages'] is None
    assert data['history']['alibaba']['completeness'] == 'unproven'
    assert channel(data, 'wechat')['state'] == 'ok'
