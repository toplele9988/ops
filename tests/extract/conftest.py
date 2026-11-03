"""All extraction fixtures are invented; network and real model jobs are blocked."""
import socket
import subprocess

import pytest


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError('extract tests must not contact networks or real AI')
    monkeypatch.setattr(socket.socket, 'connect', blocked)
    monkeypatch.setattr(socket, 'create_connection', blocked)
    original = subprocess.Popen
    def guarded(command, *args, **kwargs):
        if 'ai-task' in str(command):
            blocked()
        return original(command, *args, **kwargs)
    monkeypatch.setattr(subprocess, 'Popen', guarded)
