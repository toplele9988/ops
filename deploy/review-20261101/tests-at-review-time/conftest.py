"""审查只在临时源码、虚构数据库和动态回环端口执行。"""
import hashlib
import importlib.util
from pathlib import Path
import shutil
import socket
import sqlite3
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]


def load(name, path, *, package=False):
    options = {'submodule_search_locations': [str(path.parent)]} if package else {}
    spec = importlib.util.spec_from_file_location(name, path, **options)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope='session')
def candidate(tmp_path_factory):
    root = tmp_path_factory.mktemp('review-candidate')
    for line in (ROOT / 'deploy/review-20261101/baseline.sha256').read_text().splitlines():
        digest, rel = line.split('  ', 1)
        assert hashlib.sha256((ROOT / rel).read_bytes()).hexdigest() == digest, rel
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / rel, target)
    patch = (ROOT / 'deploy/review-20261101/fixes.patch').read_bytes()
    result = subprocess.run(['patch', '--batch', '--fuzz=0', '-p1', '-d', str(root)],
                            input=patch, capture_output=True)
    assert result.returncode == 0, result.stdout.decode()
    assert b'offset' not in result.stdout and b'fuzz' not in result.stdout
    return root


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv('OPS_MEDIA_KEY_PATH', str(tmp_path / 'media.key'))
    original_connect = sqlite3.connect

    def database(path, *args, **kwargs):
        assert str(path) == ':memory:' or str(path).startswith(('/tmp/', 'file:///tmp/', '/proc/self/fd/')), '禁止访问真实数据库'
        return original_connect(path, *args, **kwargs)

    connect, bind = socket.socket.connect, socket.socket.bind

    def local_connect(sock, address):
        assert isinstance(address, tuple) and address[0] == '127.0.0.1' and address[1] != 8791, '禁止真实渠道连接'
        return connect(sock, address)

    def local_bind(sock, address):
        assert address == ('127.0.0.1', 0), '只能使用动态回环端口'
        return bind(sock, address)

    monkeypatch.setattr(sqlite3, 'connect', database)
    monkeypatch.setattr(socket.socket, 'connect', local_connect)
    monkeypatch.setattr(socket.socket, 'bind', local_bind)
    monkeypatch.setattr(socket.socket, 'connect_ex', lambda *a: pytest.fail('禁止其他网络连接'))


def receiver(root, monkeypatch, suffix):
    helper = load('r7c_receiver_helper', ROOT / 'tests/icbu_v2/receiver_fixtures.py')
    attachments = load('r7c_attachments_' + suffix, root / 'deploy/icbu/v2.2/icbu_attachments.py')
    monkeypatch.setitem(sys.modules, 'icbu_attachments', attachments)
    module = helper.load_receiver(root / 'deploy/icbu/v2.2/icbu_inbound_receiver.py', 'r7c_receiver_' + suffix)
    return module, attachments, helper


def package(root, family, suffix):
    return load('r7c_' + family + '_' + suffix, root / ('src/console/' + family + '/__init__.py'), package=True)
