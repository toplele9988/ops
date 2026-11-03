import sqlite3

import pytest

from console.wamedia import MediaStore, identity
from console.wamedia.scan import scan


def test_dedup_retry_and_private_storage(tmp_path):
    store = MediaStore(tmp_path / 'db', tmp_path / 'blobs')
    key = store.register('fictional', 'm1', 'image', 'x.png')
    assert store.register('fictional', 'm1', 'image', 'other.png') == key
    calls = []
    def fetch(session, chat_id, mid):
        calls.append((session, chat_id, mid))
        return b'fictional image bytes', 'image/png'
    assert store.retry('fictional', 'm1', fetch)['state'] == 'ready'
    store.retry('fictional', 'm1', fetch)
    assert len(calls) == 1
    assert store.read(key)[1] == b'fictional image bytes'
    assert (tmp_path / 'blobs').stat().st_mode & 0o777 == 0o700
    assert len(list((tmp_path / 'blobs').iterdir())) == 1
    other = store.register('fictional', 'm2', 'image')
    store.save(other, 'image/png', b'fictional image bytes')
    assert len(list((tmp_path / 'blobs').iterdir())) == 1


def test_reject_bad_type_and_size_and_retry_failure(tmp_path):
    store = MediaStore(tmp_path / 'db', tmp_path / 'blobs')
    key = store.register('fictional', 'm', 'file')
    with pytest.raises(ValueError):
        store.save(key, 'text/html', b'<script>')
    with pytest.raises(ValueError):
        store.save(key, 'text/plain', b'')
    with pytest.raises(ValueError):
        store.retry('fictional', 'm', lambda *_: (_ for _ in ()).throw(TimeoutError()))
    assert store.get(key)['state'] == 'failed'
    store.retry('fictional', 'm', lambda *_: (b'fictional', 'text/plain'))
    assert store.get(key)['state'] == 'ready'


def test_scan_readonly_and_idempotent(tmp_path):
    bridge = tmp_path / 'bridge.db'
    with sqlite3.connect(bridge) as db:
        db.execute('CREATE TABLE messages(session TEXT,message_id TEXT,chat_id TEXT,media_json TEXT,has_media INTEGER,received REAL)')
        db.execute('INSERT INTO messages VALUES(?,?,?,?,?,?)', ('fictional', 'm', '123@c.us', '{"mimetype":"audio/ogg"}', 1, 1))
    store = MediaStore(tmp_path / 'db', tmp_path / 'blobs')
    assert scan(bridge, store) == scan(bridge, store) == 1
    assert store.get(identity('fictional', 'm'))['kind'] == 'voice'
    with sqlite3.connect(store.db) as db:
        assert db.execute('SELECT count(*) FROM media').fetchone()[0] == 1


def test_blob_symlink_is_rejected(tmp_path):
    store = MediaStore(tmp_path / 'db', tmp_path / 'blobs')
    key = store.register('fictional', 'm', 'file')
    store.save(key, 'text/plain', b'fictional')
    blob = next(store.root.iterdir())
    blob.unlink()
    blob.symlink_to(tmp_path / 'other')
    assert store.read(key) is None


def test_database_symlink_and_hardlink_rejected(tmp_path):
    victim = tmp_path / 'victim'
    victim.write_bytes(b'fictional victim')
    symlink = tmp_path / 'link'
    symlink.symlink_to(victim)
    with pytest.raises(OSError):
        MediaStore(symlink, tmp_path / 'blobs').register('fictional', 'm', 'file')
    assert victim.read_bytes() == b'fictional victim'
    hardlink = tmp_path / 'hardlink'
    hardlink.hardlink_to(victim)
    with pytest.raises(OSError):
        MediaStore(hardlink, tmp_path / 'blobs').register('fictional', 'm', 'file')
    assert victim.read_bytes() == b'fictional victim'


def test_kind_and_size_rejection_visible(tmp_path, monkeypatch):
    store = MediaStore(tmp_path / 'db', tmp_path / 'blobs')
    key = store.register('fictional', 'm', 'image')
    with pytest.raises(ValueError):
        store.save(key, 'application/pdf', b'fictional')
    assert store.get(key)['state'] == 'failed'
    monkeypatch.setattr('console.wamedia.store.MAX_BYTES', 2)
    with pytest.raises(ValueError):
        store.save(key, 'image/png', b'oversized')
    assert store.get(key)['state'] == 'failed'


def test_nas_unmounted_buffers_without_creating_remote_path_then_recovers(tmp_path, monkeypatch):
    mount = tmp_path / 'mount'
    mount.mkdir(mode=0o700)
    root = mount / 'archive' / 'whatsapp'
    buffer = tmp_path / 'local-buffer'
    store = MediaStore(tmp_path / 'db', root, mount=mount, buffer_root=buffer)
    key = store.register('fictional', 'm', 'image')
    calls = []

    def fetch(*args):
        calls.append(args)
        return b'fictional image', 'image/png'

    monkeypatch.setattr('console.wamedia.store.os.path.ismount', lambda _: False)
    with pytest.raises(ValueError, match='本机缓冲'):
        store.retry('fictional', 'm', fetch)
    assert not root.exists()
    assert store.get(key)['state'] == 'failed'
    assert store.get(key)['buffered'] is True
    assert len(list(buffer.glob('*.blob'))) == 1
    assert len(calls) == 1

    root.mkdir(parents=True, mode=0o700)
    (root / '.imgw-archive-id').write_text('fictional marker')
    monkeypatch.setattr('console.wamedia.store.os.path.ismount', lambda p: p == mount)
    assert store.retry('fictional', 'm', fetch)['state'] == 'ready'
    assert store.get(key)['buffered'] is False
    assert len(calls) == 1
    assert store.read(key)[1] == b'fictional image'


def test_nas_missing_or_symlink_marker_never_writes_mount(tmp_path, monkeypatch):
    mount = tmp_path / 'mount'
    mount.mkdir(mode=0o700)
    root = mount / 'media'
    root.mkdir(mode=0o700)
    store = MediaStore(tmp_path / 'db', root, mount=mount, buffer_root=tmp_path / 'buffer')
    key = store.register('fictional', 'm', 'file')
    monkeypatch.setattr('console.wamedia.store.os.path.ismount', lambda p: p == mount)
    with pytest.raises(ValueError, match='本机缓冲'):
        store.save(key, 'text/plain', b'fictional')
    assert list(root.iterdir()) == []
    marker = root / '.imgw-archive-id'
    marker.symlink_to(tmp_path / 'fake-marker')
    with pytest.raises(ValueError, match='本机缓冲'):
        store.save(key, 'text/plain', b'fictional')
    assert list(root.iterdir()) == [marker]
    assert store.get(key)['state'] == 'failed'


def test_nas_rejects_buffer_inside_mount(tmp_path):
    mount = tmp_path / 'mount'
    with pytest.raises(ValueError, match='本机缓冲'):
        MediaStore(tmp_path / 'db', mount / 'media', mount=mount, buffer_root=mount / 'buffer')


def test_download_identifies_media_when_metadata_has_no_kind(tmp_path):
    store = MediaStore(tmp_path / 'db', tmp_path / 'blobs')
    key = store.register('fictional', 'm', 'file')
    store.retry('fictional', 'm', lambda *_: (b'fictional voice', 'audio/ogg'))
    assert store.get(key)['kind'] == 'voice'
