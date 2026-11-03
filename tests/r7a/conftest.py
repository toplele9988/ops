"""只运行摘出的源码和虚构服务；不导入生产运行入口。"""
import ast
import importlib.util
from pathlib import Path
import shutil
import socket
import sqlite3
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


VERIFY = load_module('r7a_verify', ROOT / 'deploy/r7a/verify_patch.py')
DIAGNOSE = load_module('r7a_diagnose', ROOT / 'deploy/r7a/diagnose_snapshot.py')


def selected(path, names, env=None, *, class_name=None):
    """Execute selected definitions only, so configuration loaders never import."""
    source = ast.parse(Path(path).read_text(encoding='utf-8'))
    nodes = source.body
    if class_name:
        nodes = next(n for n in nodes if isinstance(n, ast.ClassDef) and n.name == class_name).body
    picked = []
    for node in nodes:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in names:
            picked.append(node)
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id in names for t in node.targets):
            picked.append(node)
    assert len(picked) == len(names)
    future = ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0)
    module = ast.fix_missing_locations(ast.Module(body=[future, *picked], type_ignores=[]))
    namespace = dict(env or {})
    exec(compile(module, str(path), 'exec'), namespace)
    return namespace


@pytest.fixture(scope='session')
def candidate():
    path = VERIFY.prepare()
    try:
        yield path
    finally:
        shutil.rmtree(path)


@pytest.fixture(autouse=True)
def offline(monkeypatch, tmp_path):
    def forbidden(*args, **kwargs):
        raise AssertionError('本套件禁止真实网络、模型或渠道调用')
    for name in ('connect', 'connect_ex'):
        monkeypatch.setattr(socket.socket, name, forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    original_connect = sqlite3.connect

    def local_database(database, *args, **kwargs):
        value = str(database)
        assert value.startswith((str(tmp_path) + '/', tmp_path.as_uri() + '/')), '测试只允许本例的临时库'
        return original_connect(database, *args, **kwargs)

    monkeypatch.setattr(sqlite3, 'connect', local_database)
    run = subprocess.run

    def patch_only(command, *args, **kwargs):
        assert (isinstance(command, list) and command[:3] == ['patch', '--batch', '--fuzz=0']
                and str(kwargs.get('cwd', '')).startswith('/tmp/r7a-code-')), '测试禁止启动模型、服务或外部脚本'
        return run(command, *args, **kwargs)

    monkeypatch.setattr(subprocess, 'run', patch_only)
    popen = subprocess.Popen

    def patch_process(command, *args, **kwargs):
        assert (isinstance(command, list) and command[:3] == ['patch', '--batch', '--fuzz=0']
                and str(kwargs.get('cwd', '')).startswith('/tmp/r7a-code-')), '测试禁止启动外部进程'
        return popen(command, *args, **kwargs)

    monkeypatch.setattr(subprocess, 'Popen', patch_process)
    for name in ('call', 'check_call', 'check_output'):
        monkeypatch.setattr(subprocess, name, forbidden)
