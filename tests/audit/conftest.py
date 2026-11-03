"""Synthetic-only tests. No socket or production SQLite access is allowed."""
from pathlib import Path
import socket
import sqlite3

import pytest


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError('network is forbidden in audit tests')
    monkeypatch.setattr(socket, 'create_connection', blocked)
    monkeypatch.setattr(socket.socket, 'connect', blocked)
    monkeypatch.setattr(socket.socket, 'connect_ex', blocked)
    connect = sqlite3.connect
    def isolated(database, *args, **kwargs):
        value = str(database)
        if any(root in value for root in ('/var/lib/imgw', '/opt/imgw', '/etc/imgw')):
            raise AssertionError('production database is forbidden in tests')
        return connect(database, *args, **kwargs)
    monkeypatch.setattr(sqlite3, 'connect', isolated)
