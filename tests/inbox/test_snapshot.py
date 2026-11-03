import ast
from contextlib import closing
from pathlib import Path
import shutil
import sqlite3
import pytest


def snapshot_function():
    source = Path(__file__).resolve().parents[2] / 'scripts/rehearsal_server.py'
    tree = ast.parse(source.read_text())
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'snapshot_copy')
    scope = dict(Path=Path, shutil=shutil, sqlite3=sqlite3, closing=closing)
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(source), 'exec'), scope)
    return scope['snapshot_copy']


def test_snapshot_copies_committed_wal_without_touching_source(tmp_path):
    copy = snapshot_function()
    source, target = tmp_path/'source.sqlite3', tmp_path/'copy.sqlite3'
    with closing(sqlite3.connect(source)) as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('CREATE TABLE fictional(value TEXT)')
        db.execute("INSERT INTO fictional VALUES('synthetic-only')")
        db.commit()
        files = [source, Path(str(source)+'-wal'), Path(str(source)+'-shm')]
        before = [(p.read_bytes(),p.stat().st_mtime_ns) for p in files]
        assert copy(source, target) == str(target)
        assert before == [(p.read_bytes(),p.stat().st_mtime_ns) for p in files]
        with closing(sqlite3.connect(target)) as copied:
            assert copied.execute('SELECT value FROM fictional').fetchone()[0] == 'synthetic-only'


def test_missing_snapshot_does_not_manufacture_empty_source(tmp_path):
    target = tmp_path/'copy.sqlite3'
    assert snapshot_function()(tmp_path/'missing.sqlite3',target) == str(target)
    assert not target.exists()
