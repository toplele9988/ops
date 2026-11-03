"""Fictional bridge archives and fake transports only; no real services or media."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
from urllib.error import HTTPError

import pytest

from console.wamedia import MediaStore, identity
from console.wamedia.bridge import BridgeArchive
from console.wamedia.ingest import IngestWorker, MAX_ATTEMPTS


@pytest.fixture
def archived(tmp_path, monkeypatch):
    import socket
    def forbidden(*args, **kwargs):
        raise AssertionError('测试不得连接真实服务')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    bridge = tmp_path / 'bridge.sqlite3'
    root = tmp_path / 'bridge-media'
    root.mkdir(mode=0o700)
    with sqlite3.connect(bridge) as db:
        db.executescript('''CREATE TABLE messages(message_key TEXT PRIMARY KEY,session TEXT NOT NULL,
            account_id TEXT NOT NULL,chat_id TEXT NOT NULL,message_id TEXT NOT NULL,direction TEXT NOT NULL,
            content TEXT NOT NULL,sent_at REAL,received REAL NOT NULL,raw_event TEXT NOT NULL,
            has_media INTEGER NOT NULL,media_json TEXT NOT NULL,source_sha TEXT NOT NULL,
            owner TEXT NOT NULL,scope TEXT NOT NULL,UNIQUE(session,account_id,message_id,source_sha));
            CREATE TABLE media_jobs(message_key TEXT PRIMARY KEY,status TEXT NOT NULL,
            metadata_json TEXT NOT NULL,updated REAL NOT NULL);''')
    calls, ticks = [], [1000]
    def unavailable(*args):
        calls.append(args)
        raise HTTPError('http://127.0.0.1:5000/fictional', 500, 'private fake detail', {}, None)
    store = MediaStore(tmp_path / 'media.sqlite3', tmp_path / 'blobs')
    worker = IngestWorker(store, tmp_path / 'queue.sqlite3', unavailable, clock=lambda: ticks[0],
                          bridge_db=bridge, bridge_media=root)
    return bridge, root, store, worker, calls, ticks


def seed(archived, mid='img', body=b'fictional image', mime='image/png', *, status='archived', metadata=None):
    bridge, root, *_ = archived
    digest = hashlib.sha256(body).hexdigest()
    (root / digest).write_bytes(body)
    metadata = metadata if metadata is not None else dict(bytes=len(body), mimetype=mime, sha256=digest, text_status='opaque')
    with sqlite3.connect(bridge) as db:
        db.execute('INSERT INTO messages VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                   ('key-' + mid, 'fixture', '100001@c.us', '200002@c.us', mid, 'inbound', '',
                    1, 1, '{}', 1, json.dumps(dict(mimetype=mime)), 'fixture-sha', 'fixture', 'fixture'))
        db.execute('INSERT INTO media_jobs VALUES(?,?,?,?)', ('key-' + mid, status, json.dumps(metadata), 1))
    return digest


def test_archive_wins_over_waha_500_and_is_idempotent_readonly(archived):
    bridge, root, store, worker, calls, _ = archived
    digest = seed(archived)
    before = bridge.read_bytes(), (root / digest).read_bytes()
    assert worker.scan(bridge) == 1
    assert worker.run_one()
    key = identity('fixture', 'img')
    assert store.read(key)[1] == b'fictional image'
    assert store.get(key)['error'] == ''
    assert calls == []
    restarted = IngestWorker(store, worker.path, worker.fetch, bridge_db=bridge, bridge_media=root)
    assert restarted.scan(bridge) == 0
    assert not restarted.run_one()
    assert len(list(store.root.glob('*.blob'))) == 1
    with sqlite3.connect(store.db) as db:
        assert db.execute('SELECT count(*) FROM media').fetchone()[0] == 1
    assert (bridge.read_bytes(), (root / digest).read_bytes()) == before
    with BridgeArchive(bridge, root).connect() as db:
        assert db.execute('PRAGMA query_only').fetchone()[0] == 1
        with pytest.raises(sqlite3.OperationalError, match='readonly'):
            db.execute("UPDATE media_jobs SET status='deferred'")


def test_readonly_archive_sees_committed_wal_before_checkpoint(archived):
    from contextlib import closing
    bridge, _, store, worker, calls, _ = archived
    seed(archived, status='deferred')
    with closing(sqlite3.connect(bridge)) as writer:
        assert writer.execute('PRAGMA journal_mode=WAL').fetchone()[0] == 'wal'
        writer.execute("UPDATE media_jobs SET status='archived',updated=2")
        writer.commit()
        assert Path(str(bridge) + '-wal').exists()
        worker.scan(bridge)
        assert worker.run_one()
        assert store.read(identity('fixture', 'img'))[1] == b'fictional image'
    assert calls == []


@pytest.mark.parametrize('case,code', [
    ('length', 'archive_size'), ('hash', 'archive_hash'), ('missing', 'archive_missing'),
    ('mime', 'media_type'), ('traversal', 'archive_metadata'), ('uppercase', 'archive_metadata'),
    ('boolean', 'archive_metadata'), ('oversized', 'archive_metadata'), ('bad-json', 'archive_metadata'),
    ('symlink', 'archive_path'), ('parent-symlink', 'archive_path'), ('hardlink', 'archive_path'),
    ('directory', 'archive_path'), ('fifo', 'archive_path'),
])
def test_invalid_archive_refused_with_safe_reason_and_no_fallback(archived, case, code, tmp_path):
    bridge, root, store, worker, calls, _ = archived
    digest = seed(archived)
    path = root / digest
    metadata = dict(bytes=len(b'fictional image'), mimetype='image/png', sha256=digest)
    if case == 'length':
        path.write_bytes(b'wrong length')
    elif case == 'hash':
        path.write_bytes(b'x' * metadata['bytes'])
    elif case == 'missing':
        path.unlink()
    elif case == 'mime':
        metadata['mimetype'] = 'text/html'
    elif case == 'traversal':
        metadata['sha256'] = '../' + digest
    elif case == 'uppercase':
        metadata['sha256'] = digest.upper()
    elif case == 'boolean':
        metadata['bytes'] = True
    elif case == 'oversized':
        metadata['bytes'] = 50 * 1024 * 1024 + 1
    elif case in ('symlink', 'hardlink'):
        target = tmp_path / 'fictional-target'
        path.rename(target)
        if case == 'symlink':
            path.symlink_to(target)
        else:
            path.hardlink_to(target)
    elif case == 'parent-symlink':
        actual = root.with_name('actual-media')
        root.rename(actual)
        root.symlink_to(actual, target_is_directory=True)
    elif case == 'directory':
        path.unlink()
        path.mkdir()
    elif case == 'fifo':
        path.unlink()
        os.mkfifo(path)
    raw = 'not-json' if case == 'bad-json' else json.dumps(metadata)
    with sqlite3.connect(bridge) as db:
        db.execute('UPDATE media_jobs SET metadata_json=?', (raw,))
    worker.scan(bridge)
    assert worker.run_one()
    row = store.get(identity('fixture', 'img'))
    assert row['state'] == 'pending'
    assert row['error_code'] == code
    assert row['error'] and 'private fake detail' not in row['error'] and str(tmp_path) not in row['error']
    assert store.read(row['id']) is None
    assert not store.root.exists()
    assert calls == []


def test_retries_are_due_bounded_and_late_archive_revives_automatically(archived):
    bridge, root, store, worker, calls, ticks = archived
    digest = seed(archived, status='pending_media_url')
    worker.scan(bridge)
    for attempt in range(MAX_ATTEMPTS):
        assert worker.run_one()
        row = store.get(identity('fixture', 'img'))
        assert row['state'] == ('pending' if attempt + 1 < MAX_ATTEMPTS else 'failed')
        assert not worker.run_one()
        ticks[0] += 61
    assert len(calls) == MAX_ATTEMPTS
    assert not worker.run_one()
    with sqlite3.connect(bridge) as db:
        db.execute("UPDATE media_jobs SET status='archived',updated=2")
    assert worker.scan(bridge) == 0
    assert worker.run_one()
    assert store.get(identity('fixture', 'img'))['sha256'] == digest
    assert store.read(identity('fixture', 'img'))[1] == b'fictional image'
    assert not worker.run_one()
    assert len(calls) == MAX_ATTEMPTS


def test_archive_arrival_bypasses_old_retry_deadline(archived):
    bridge, _, store, worker, calls, _ = archived
    seed(archived, status='deferred')
    worker.scan(bridge)
    assert worker.run_one()
    assert not worker.run_one()
    with sqlite3.connect(bridge) as db:
        db.execute("UPDATE media_jobs SET status='archived',updated=2")
    assert worker.run_one()
    assert store.get(identity('fixture', 'img'))['state'] == 'ready'
    assert len(calls) == 1


@pytest.mark.parametrize('state', ['failed', 'ready'])
def test_old_queue_schema_and_failed_or_fallback_ready_record_use_archive(archived, state):
    bridge, _, store, worker, calls, _ = archived
    digest = seed(archived)
    key = store.register('fixture', 'img', 'image', chat_id='200002@c.us')
    store.save(key, 'image/png', b'old fictional fallback')
    with sqlite3.connect(store.db) as db:
        db.execute('UPDATE media SET state=?', (state,))
        db.execute('DROP TABLE media_errors')
    with sqlite3.connect(worker.path) as db:
        db.execute('CREATE TABLE jobs(id TEXT PRIMARY KEY,session TEXT NOT NULL,message_id TEXT NOT NULL,'
                   'attempts INTEGER NOT NULL DEFAULT 0,next_at REAL NOT NULL DEFAULT 0,'
                   'lease_until REAL NOT NULL DEFAULT 0,done INTEGER NOT NULL DEFAULT 0)')
        db.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,?)', (key, 'fixture', 'img', 4, 999999, 0, int(state == 'ready')))
    assert worker.run_one()
    assert store.get(key)['state'] == 'ready'
    assert store.get(key)['sha256'] == digest
    with sqlite3.connect(worker.path) as db:
        assert db.execute('SELECT attempts,done FROM jobs').fetchone() == (1, 1)
    assert calls == []


def test_media_schema_keeps_legacy_positional_insert_compatible(archived):
    _, _, store, worker, *_ = archived
    key = worker.enqueue('fixture', 'img', '200002@c.us', {'mimetype': 'image/png'})
    store.fail(key, 'archive_missing')
    legacy = identity('fixture', 'legacy')
    with sqlite3.connect(store.db) as db:
        assert len(db.execute('PRAGMA table_info(media)').fetchall()) == 11
        db.execute('INSERT OR IGNORE INTO media VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                   (legacy, 'fixture', 'legacy', '200002@c.us', 'image', '', 'pending', None, None, None, 1))
    assert store.get(legacy)['state'] == 'pending'
    assert store.get(legacy)['error'] == ''


def test_invalid_archived_record_stops_after_four_attempts_until_repaired(archived):
    bridge, root, store, worker, calls, ticks = archived
    digest = seed(archived)
    body = (root / digest).read_bytes()
    (root / digest).unlink()
    worker.scan(bridge)
    for _ in range(MAX_ATTEMPTS):
        assert worker.run_one()
        ticks[0] += 61
    assert not worker.run_one()
    assert store.get(identity('fixture', 'img'))['state'] == 'failed'
    (root / digest).write_bytes(body)
    assert worker.reset_failed() == 1
    assert worker.run_one()
    assert store.get(identity('fixture', 'img'))['state'] == 'ready'
    assert calls == []


def test_archive_lookup_is_bound_to_session(archived):
    bridge, root, *_ = archived
    seed(archived)
    source = BridgeArchive(bridge, root)
    assert source.load('other-fixture', 'img') is None
    assert source.load('fixture', "img' OR 1=1 --") is None


def test_nas_outage_only_uses_explicit_buffer_then_recovers(archived, tmp_path, monkeypatch):
    bridge, root, _, worker, calls, ticks = archived
    seed(archived)
    mount = tmp_path / 'mount'
    mount.mkdir(mode=0o700)
    nas = mount / 'whatsapp'
    online = [False]
    monkeypatch.setattr('console.wamedia.store.os.path.ismount', lambda p: online[0] and p == mount)
    store = MediaStore(tmp_path / 'nas-media.db', nas, mount=mount, buffer_root=tmp_path / 'buffer')
    worker.store = store
    worker.scan(bridge)
    assert worker.run_one()
    key = identity('fixture', 'img')
    assert store.get(key)['state'] == 'pending'
    assert store.get(key)['error_code'] == 'mount_unavailable'
    assert store.get(key)['buffered']
    assert not nas.exists()
    assert calls == []
    nas.mkdir(mode=0o700)
    (nas / '.imgw-archive-id').write_text('fictional marker')
    online[0] = True
    ticks[0] += 61
    assert worker.run_one()
    assert store.read(key)[1] == b'fictional image'
    assert calls == []


def test_exhausted_job_does_not_block_another_archive(archived):
    bridge, _, store, worker, calls, ticks = archived
    seed(archived, status='deferred')
    worker.scan(bridge)
    for _ in range(MAX_ATTEMPTS):
        assert worker.run_one()
        ticks[0] += 61
    seed(archived, 'other', b'other fictional image')
    worker.scan(bridge)
    assert worker.run_one()
    assert store.get(identity('fixture', 'img'))['state'] == 'failed'
    assert store.get(identity('fixture', 'other'))['state'] == 'ready'
    assert len(calls) == MAX_ATTEMPTS


def test_concurrent_archive_refresh_does_not_reset_an_active_lease(archived, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    bridge, root, store, one, calls, ticks = archived
    seed(archived)
    one.scan(bridge)
    two = IngestWorker(store, one.path, one.fetch, clock=lambda: ticks[0], bridge_db=bridge, bridge_media=root)
    entered, release = threading.Event(), threading.Event()
    save = store.save
    def waiting_save(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return save(*args, **kwargs)
    monkeypatch.setattr(store, 'save', waiting_save)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(one.run_one)
        assert entered.wait(5)
        assert two.run_one() is False
        release.set()
        assert first.result(timeout=5) is True
    with sqlite3.connect(one.path) as db:
        assert db.execute('SELECT attempts,done FROM jobs').fetchone() == (1, 1)
    assert calls == []


def test_once_subprocess_is_repeatable_and_needs_no_waha_config(archived, tmp_path):
    bridge, root, *_ = archived
    seed(archived)
    seed(archived, 'video', b'fictional video', 'video/mp4')
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1',
               IMGW_WA_MEDIA_DB=str(tmp_path / 'cli-media.db'), IMGW_WA_MEDIA_ROOT=str(tmp_path / 'cli-blobs'),
               IMGW_WA_MEDIA_BUFFER=str(tmp_path / 'cli-buffer'), IMGW_WA_MEDIA_MOUNT='',
               IMGW_WA_MEDIA_QUEUE=str(tmp_path / 'cli-queue.db'), OPS_MEDIA_KEY_PATH=str(tmp_path / 'media.key'))
    command = ['/opt/imgw/venv/bin/python', '-m', 'console.wamedia.ingest', '--bridge-db', str(bridge),
               '--bridge-media', str(root), '--config', str(tmp_path / 'absent-config'), '--once']
    for _ in range(2):
        result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=15)
        assert result.returncode == 0, result.stdout + result.stderr
        assert '本轮附件队列已处理' in result.stdout
    with sqlite3.connect(env['IMGW_WA_MEDIA_DB']) as db:
        assert db.execute("SELECT count(*) FROM media WHERE state='ready'").fetchone()[0] == 2
    assert len(list(Path(env['IMGW_WA_MEDIA_ROOT']).glob('*.blob'))) == 2


def test_once_read_failure_exits_without_sleep_or_private_detail(tmp_path, capsys, monkeypatch):
    from console.wamedia.ingest import main
    monkeypatch.setattr('console.wamedia.store.MediaStore', lambda: MediaStore(tmp_path / 'media.db', tmp_path / 'blobs'))
    result = main(['--bridge-db', str(tmp_path / 'absent-bridge'), '--queue', str(tmp_path / 'queue'), '--once'])
    assert result == 1
    output = capsys.readouterr().out
    assert '附件采集暂时不可用' in output
    assert str(tmp_path) not in output
