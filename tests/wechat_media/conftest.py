"""只用虚构文件与临时 SQLite；真实网络和生产库均禁止访问。"""
from contextlib import closing
import importlib.util
from pathlib import Path
import socket
import sqlite3

import pytest


ROOT = Path(__file__).resolve().parents[2]


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    connect = sqlite3.connect

    def local(database, *args, **kwargs):
        assert str(database).startswith((str(tmp_path), tmp_path.as_uri())), '测试只能连接本例临时库'
        return connect(database, *args, **kwargs)

    def forbidden(*args, **kwargs):
        raise AssertionError('测试禁止访问真实渠道或模型')

    monkeypatch.setattr(sqlite3, 'connect', local)
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    monkeypatch.setenv('OPS_MEDIA_KEY_PATH', str(tmp_path / 'fictional-media.key'))


@pytest.fixture
def scanner():
    return module('fictional_media_scanner', ROOT / 'deploy/wechat-media/private_media_register.py')


@pytest.fixture
def diagnostic():
    return module('fictional_media_diagnostic', ROOT / 'scripts/wechat_media_failures.py')


@pytest.fixture
def snapshot(tmp_path):
    root = tmp_path / 'private'
    root.mkdir(mode=0o700)
    root.chmod(0o700)
    for name in ('state.sqlite3', 'registry.sqlite3', 'catalog.sqlite3'):
        (root / name).touch(mode=0o600)
    with closing(sqlite3.connect(root / 'state.sqlite3')) as db, db:
        db.executescript('''
            CREATE TABLE video_card_jobs(state,error_code,task_id,source_sha256,source_bytes,
                filename,receipt_filename,created_at);
            CREATE TABLE company_note_assets(status,kind,format,archive_status,archive_bytes,name,text);
            CREATE TABLE receive_collection_jobs(state,result_status);
            CREATE TABLE receive_collection_results(job_id);
            CREATE TABLE vk_nas_sources(filename,source_bytes,observed_at,source_sha256);
        ''')
        db.executemany('INSERT INTO video_card_jobs VALUES(?,?,?,?,?,?,?,?)', [
            ('blocked', 'source_or_receipt_invalid', 101, 'a' * 64, 8, '虚构.mp4', '虚构.mp4', 100),
            ('download_unknown', 'download_outcome_unknown', None, None, None, '虚构未知.mp4', None, 100)])
        db.execute('INSERT INTO vk_nas_sources VALUES(?,?,?,?)', ('虚构.mp4', 8, 200, 'a' * 64))
        db.execute('INSERT INTO company_note_assets VALUES(?,?,?,?,?,?,?)',
                   ('extract_failed', 'image', 'unknown', 'ready', 8, '虚构隐藏名称', '虚构隐藏正文'))
        db.executemany('INSERT INTO receive_collection_jobs VALUES(?,?)', [
            ('complete', 'ready'), ('complete', 'download_failed'), ('RAW_ARCHIVED_NO_TEXT', 'waiting_cloud_budget')])
        db.execute('INSERT INTO receive_collection_results VALUES(?)', ('虚构隐藏标识',))
    with closing(sqlite3.connect(root / 'registry.sqlite3')) as db, db:
        db.executescript('CREATE TABLE assets(sha256,source_verified); CREATE TABLE observations(relative_path,sha256);')
        db.execute('INSERT INTO assets VALUES(?,0)', ('a' * 64,))
        db.execute('INSERT INTO observations VALUES(?,?)', ('虚构隐藏路径/附件.jpg', 'a' * 64))
    with closing(sqlite3.connect(root / 'catalog.sqlite3')) as db, db:
        db.executescript('''CREATE TABLE multimedia_assets(asset_id,owner,source_scope,source_sha256);
            CREATE TABLE knowledge_chunks(asset_id,owner,source_scope);''')
        db.execute('INSERT INTO multimedia_assets VALUES(?,?,?,?)', ('虚构资产', '虚构持有人', '虚构范围', 'a' * 64))
        db.execute('INSERT INTO knowledge_chunks VALUES(?,?,?)', ('虚构资产', '虚构持有人', '虚构范围'))
    return root
