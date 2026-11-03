"""Mount checks use virtual mount topology and fictional bytes only."""
from pathlib import Path
import os

import pytest

from console.wamedia import MediaStore
from console.wamedia.store import _nearest_mount


def topology(tmp_path, monkeypatch):
    outer = tmp_path / 'autofs'
    inner = outer / 'edge' / 'archive'
    root = inner / 'whatsapp'
    root.mkdir(parents=True, mode=0o700)
    (root / '.imgw-archive-id').write_text('fictional marker')
    mounts = {outer, inner}
    monkeypatch.delenv('IMGW_WA_MEDIA_MOUNT', raising=False)
    monkeypatch.setattr('console.wamedia.store.os.path.ismount', lambda p: Path(p) in mounts)
    real_stat = Path.stat
    def differing_outer_device(path, **kwargs):
        info = real_stat(path, **kwargs)
        if path == outer:
            values = list(info)
            values[2] += 1
            return os.stat_result(values)
        return info
    monkeypatch.setattr(Path, 'stat', differing_outer_device)
    return outer, inner, root, mounts


def test_nearest_real_mount_is_nested_cifs_not_outer_autofs(tmp_path, monkeypatch):
    _, inner, root, _ = topology(tmp_path, monkeypatch)
    assert _nearest_mount(root) == inner
    store = MediaStore(tmp_path / 'db', root, mount=_nearest_mount(root), buffer_root=tmp_path / 'buffer')
    assert store.mount == inner
    key = store.register('fixture', 'img', 'image')
    store.save(key, 'image/png', b'fictional image')
    assert store.read(key)[1] == b'fictional image'
    virtual_outer = Path('/mnt/fictional-r5b')
    virtual_inner = virtual_outer / 'edge' / 'archive'
    monkeypatch.setattr('console.wamedia.store.os.path.ismount', lambda p: p in (virtual_outer, virtual_inner))
    inferred = MediaStore(tmp_path / 'other-db', virtual_inner / 'whatsapp', buffer_root=tmp_path / 'other-buffer')
    assert inferred.mount == virtual_inner


def test_disappearing_inner_mount_stays_pinned_and_never_writes_fallback_disk(tmp_path, monkeypatch):
    _, inner, root, mounts = topology(tmp_path, monkeypatch)
    store = MediaStore(tmp_path / 'db', root, mount=_nearest_mount(root), buffer_root=tmp_path / 'buffer')
    key = store.register('fixture', 'img', 'image')
    mounts.remove(inner)
    with pytest.raises(ValueError, match='本机缓冲'):
        store.save(key, 'image/png', b'fictional image')
    assert store.mount == inner
    assert list(root.iterdir()) == [root / '.imgw-archive-id']
    assert store.read(key) is None
    assert store.get(key)['buffered']
    assert len(list(store.buffer_root.glob('*.blob'))) == 1
    mounts.add(inner)
    assert store.retry('fixture', 'img', lambda *_: pytest.fail('应复用本机缓冲'))['state'] == 'ready'


def test_explicit_outer_mount_with_different_device_fails_closed(tmp_path, monkeypatch):
    outer, _, root, _ = topology(tmp_path, monkeypatch)
    store = MediaStore(tmp_path / 'db', root, mount=outer, buffer_root=tmp_path / 'buffer')
    key = store.register('fixture', 'img', 'image')
    with pytest.raises(ValueError, match='本机缓冲'):
        store.save(key, 'image/png', b'fictional image')
    assert not list(root.glob('*.blob'))
    assert store.get(key)['state'] == 'failed'


def test_mnt_without_any_mount_never_becomes_local_storage(tmp_path, monkeypatch):
    monkeypatch.delenv('IMGW_WA_MEDIA_MOUNT', raising=False)
    monkeypatch.setattr('console.wamedia.store.os.path.ismount', lambda _: False)
    root = Path('/mnt/fictional-r5b/edge/archive/whatsapp')
    resolve, mkdir, exists = Path.resolve, Path.mkdir, Path.exists
    monkeypatch.setattr(Path, 'resolve', lambda p, **kw: p if p.is_relative_to('/mnt') else resolve(p, **kw))
    monkeypatch.setattr(Path, 'exists', lambda p: False if p.is_relative_to('/mnt') else exists(p))
    def local_only(p, *args, **kw):
        assert not p.is_relative_to('/mnt'), '测试不得在真实挂载路径创建目录'
        return mkdir(p, *args, **kw)
    monkeypatch.setattr(Path, 'mkdir', local_only)
    store = MediaStore(tmp_path / 'db', root, buffer_root=tmp_path / 'buffer')
    assert store.mount == Path('/mnt/fictional-r5b')
    key = store.register('fixture', 'img', 'image')
    with pytest.raises(ValueError, match='本机缓冲'):
        store.save(key, 'image/png', b'fictional image')
    assert not root.exists()
    assert len(list(store.buffer_root.glob('*.blob'))) == 1


def test_local_root_on_separate_filesystem_keeps_local_storage_rules(tmp_path, monkeypatch):
    monkeypatch.delenv('IMGW_WA_MEDIA_MOUNT', raising=False)
    monkeypatch.setattr('console.wamedia.store.os.path.ismount', lambda p: p == tmp_path)
    store = MediaStore(tmp_path / 'db', tmp_path / 'blobs', buffer_root=tmp_path / 'buffer')
    assert store.mount is None
    key = store.register('fixture', 'img', 'image')
    store.save(key, 'image/png', b'fictional image')
    assert store.read(key)[1] == b'fictional image'
