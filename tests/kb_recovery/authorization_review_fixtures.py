"""首次授权审核只用虚构副本；文件与 SQLite 访问限于 /tmp。"""
from contextlib import contextmanager
from copy import deepcopy
import builtins
import io
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
from types import SimpleNamespace
from urllib.parse import unquote, urlsplit

import prepare_install as p


def quarantine_fixture():
    return {'schema': 'kb-source-quarantine-v1', 'holds': [
        {'channel': 'wechat' if number < 3 else 'alibaba',
         'request_sha256': p.b.sha('fictional-request-' + str(number)),
         'prompt_sha256': p.b.sha('fictional-prompt-' + str(number)),
         'source_keys': [p.b.sha('fictional-held-source-' + str(number))],
         'source_sha256': [p.b.sha('fictional-held-original-' + str(number))],
         'content_sha256': [p.b.sha('fictional-held-body-' + str(number))]}
        for number in range(4)]}


def provenance_fixture(quarantine):
    return {'schema': 'kb-r5a-provenance-v1', 'quarantine': deepcopy(quarantine),
            'unknown_checklist': [
                {'channel': row['channel'], 'request_sha256': row['request_sha256'],
                 'prompt_sha256': row['prompt_sha256'], 'source_count': len(row['source_keys']),
                 'source_set_sha256': p.b.digest(row), 'state': '永久隔离'}
                for row in quarantine['holds']],
            'document_checks': [], 'alibaba_units': [], 'alibaba_excluded': []}


def stage_marker(root, name):
    directory = root / name
    old = directory / 'stage.json'
    if old.exists():
        old.unlink()
    p.write_json(old, {'input_sha256': p.b.sha('fictional-input-' + name),
                      'completed_at': 1000, 'outputs': p.file_pins(directory)})


def session_fixture(tmp_path, *, wechat_sources=2, wechat_units=2, alibaba_sources=1):
    root = p.private_dir(tmp_path, 'fictional-session')
    snapshot = p.private_dir(root, 'snapshot')
    files = {}
    for channel, names in p.SOURCES.items():
        directory = p.private_dir(snapshot, channel)
        for name in names:
            path = directory / name
            if name.endswith('.sqlite3'):
                p.write(path, b'')
                with sqlite3.connect(path) as db:
                    db.execute('CREATE TABLE fictional_fixture(value TEXT)')
                    db.execute('INSERT INTO fictional_fixture VALUES(?)', ('完全虚构，仅供隔离测试',))
            else:
                p.write_json(path, {'fictional': True})
            files[channel + '/' + name] = {
                'sha256': p.sha(p.raw_file(path)), 'started_at': 999, 'finished_at': 1000}
    p.write_json(snapshot / 'snapshots.json', {'schema': 'kb-install-snapshots-v1',
        'files': files, 'wal_samples': [{}, {}, {}], 'atomic_across_databases': False})
    stage_marker(root, 'snapshot')
    quarantine = quarantine_fixture()
    provenance = provenance_fixture(quarantine)
    inventory = p.private_dir(root, 'inventory')
    p.write_json(inventory / 'quarantine.json', quarantine)
    p.write_json(inventory / 'provenance.json', provenance)
    counts = {'wechat_sources': wechat_sources, 'wechat_units': wechat_units,
              'alibaba_sources': alibaba_sources, 'alibaba_units': alibaba_sources}
    p.write_json(inventory / 'summary.json', {'counts': counts, 'expected': counts, 'tolerance': 0})
    stage_marker(root, 'inventory')
    return SimpleNamespace(root=root, quarantine=quarantine, provenance=provenance,
                           files=files, counts=counts)


@contextmanager
def tmp_only(monkeypatch):
    """连读生产路径也拒绝；只在被测调用期间启用，pytest 自身不受影响。"""
    opened = []
    saved_open, saved_io_open, saved_os_open = builtins.open, io.open, os.open
    saved_connect = sqlite3.connect

    def check(path, *, uri=False, dir_fd=None):
        if isinstance(path, int):
            return
        if isinstance(path, bytes):
            path = os.fsdecode(path)
        if uri and str(path).startswith('file:'):
            path = unquote(urlsplit(str(path)).path)
        item = Path(path)
        if dir_fd is not None:
            item = Path(os.readlink('/proc/self/fd/' + str(dir_fd))) / item
        item = item.absolute()
        if '..' in item.parts or not item.is_relative_to('/tmp'):
            raise AssertionError('测试禁止读取任何真实路径')
        opened.append(str(item))

    def checked_open(path, *args, **kwargs):
        check(path)
        return saved_open(path, *args, **kwargs)

    def checked_io_open(path, *args, **kwargs):
        check(path)
        return saved_io_open(path, *args, **kwargs)

    def checked_os_open(path, flags, *args, **kwargs):
        check(path, dir_fd=kwargs.get('dir_fd'))
        return saved_os_open(path, flags, *args, **kwargs)

    def checked_connect(path, *args, **kwargs):
        check(path, uri=kwargs.get('uri', False))
        return saved_connect(path, *args, **kwargs)

    def blocked(*args, **kwargs):
        raise AssertionError('测试禁止真实网络、模型和子进程')

    with monkeypatch.context() as changes:
        changes.setattr(builtins, 'open', checked_open)
        changes.setattr(io, 'open', checked_io_open)
        changes.setattr(os, 'open', checked_os_open)
        changes.setattr(sqlite3, 'connect', checked_connect)
        changes.setattr(socket.socket, 'connect', blocked)
        changes.setattr(socket.socket, 'connect_ex', blocked)
        changes.setattr(socket, 'create_connection', blocked)
        changes.setattr(subprocess, 'Popen', blocked)
        yield opened
