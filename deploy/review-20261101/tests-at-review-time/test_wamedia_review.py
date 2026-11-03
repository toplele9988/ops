from contextlib import contextmanager
import importlib
from pathlib import Path
import sqlite3
import threading

import pytest

from conftest import ROOT, package


def modules(root, name):
    pkg = package(root, 'wamedia', name)
    return (importlib.import_module(pkg.__name__ + '.store'),
            importlib.import_module(pkg.__name__ + '.ingest'))


def test_replaced_nas_root_and_sentinel_are_not_accepted(candidate, monkeypatch, tmp_path):
    for source, suffix in ((ROOT, 'before'), (candidate, 'after')):
        module, _ = modules(source, suffix)
        mount = tmp_path / suffix / 'nas'
        root = mount / 'whatsapp'
        root.mkdir(parents=True, mode=0o700)
        (mount / '.imgw-archive-id').write_text('fictional marker')
        monkeypatch.setattr(module.os.path, 'ismount', lambda p: Path(p) == mount)
        store = module.MediaStore(tmp_path / suffix / 'media.sqlite3', root,
                                  mount=mount, buffer_root=tmp_path / suffix / 'buffer')
        key = store.register('fictional-session', 'fictional-message', 'file')
        store.save(key, 'text/plain', b'fictional first file')
        original = mount / 'original'
        root.rename(original)
        root.mkdir(mode=0o700)
        if suffix == 'before':
            store.save(key, 'text/plain', b'fictional replacement file', replace=True)
            assert list(root.glob('*.blob'))
        else:
            with pytest.raises(module.MediaError):
                store.save(key, 'text/plain', b'fictional replacement file', replace=True)
            assert not list(root.glob('*.blob')) and store.read(key) is None
            root.rmdir()
            original.rename(root)
            store.retry('fictional-session', 'fictional-message', lambda *a: pytest.fail('应使用已校验的缓冲'))
            assert store.read(key)[1] == b'fictional replacement file'


def test_expired_worker_cannot_overwrite_new_worker_result(candidate, tmp_path):
    @contextmanager
    def fake_source():
        yield object()

    class Archive:
        connect = staticmethod(fake_source)
        available = staticmethod(lambda _: False)
        load = staticmethod(lambda *_: None)

    for source, suffix in ((ROOT, 'before_lease'), (candidate, 'after_lease')):
        module, ingest = modules(source, suffix)
        root = tmp_path / suffix
        store = module.MediaStore(root / 'media.sqlite3', root / 'blobs')
        now = [1000]
        entered, release = threading.Event(), threading.Event()
        def slow(*args):
            entered.set()
            assert release.wait(3)
            raise ValueError('虚构慢任务失败')
        first = ingest.IngestWorker(store, root / 'queue.sqlite3', slow, clock=lambda: now[0])
        second = ingest.IngestWorker(store, root / 'queue.sqlite3', lambda *_: (b'fictional newer bytes', 'text/plain'),
                                     clock=lambda: now[0])
        first.archive = second.archive = Archive()
        key = first.enqueue('fictional-session', 'fictional-message', 'fictional@c.us', {'mimetype': 'text/plain'})
        thread = threading.Thread(target=first.run_one)
        thread.start()
        try:
            assert entered.wait(2)
            now[0] += 121
            worked = second.run_one()
            if suffix == 'before_lease':
                assert worked and store.get(key)['state'] == 'ready'
            else:
                assert worked is False
        finally:
            release.set()
            thread.join(3)
        assert not thread.is_alive()
        if suffix == 'before_lease':
            assert store.get(key)['state'] != 'ready'
            with sqlite3.connect(root / 'queue.sqlite3') as db:
                assert db.execute('SELECT done FROM jobs').fetchone()[0] == 0
        else:
            now[0] += 61
            assert second.run_one()
            assert store.read(key)[1] == b'fictional newer bytes'
