"""Safety guard used by the r11 regression runner; source imports remain read-only."""
from pathlib import Path
import socket
import sqlite3
import shutil
import pytest


@pytest.fixture(autouse=True)
def r11_no_production_io(monkeypatch):
    connect = sqlite3.connect
    def local(path, *a, **kw):
        raw = str(path)
        allowed = raw == ':memory:' or raw.startswith(('/tmp/', 'file:///tmp/', 'file:/tmp/')) or '/private_out/' in raw
        assert allowed, 'r11 tests cannot open a production SQLite database'
        return connect(path, *a, **kw)
    def no_network(*a, **kw):
        raise AssertionError('r11 tests cannot access a network')
    copyfile = shutil.copyfile
    def safe_copy(source, target, *a, **kw):
        raw = str(source)
        assert not (raw.startswith('/var/lib/imgw/') and not raw.startswith(str(Path.cwd())) and '.sqlite' in raw), 'unit tests cannot copy production source databases'
        return copyfile(source, target, *a, **kw)
    monkeypatch.setattr(shutil, 'copyfile', safe_copy)
    monkeypatch.setattr(sqlite3, 'connect', local)
    monkeypatch.setattr(socket.socket, 'connect', no_network)
    monkeypatch.setattr(socket, 'create_connection', no_network)
