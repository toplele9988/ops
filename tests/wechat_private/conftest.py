"""Real candidate source, fictional data only. Production DBs and all networking are denied."""
import importlib.util
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
SOURCE = Path(os.environ.get('WECHAT_PRIVATE_BASELINE', '/opt/imgw/wechat'))
sys.dont_write_bytecode = True


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    sys.modules[name] = value
    spec.loader.exec_module(value)
    return value


@pytest.fixture(scope='session')
def candidate(tmp_path_factory):
    prepare = module('private_prepare', ROOT / 'deploy/wechat-private/prepare.py')
    dest = prepare.prepare(SOURCE, tmp_path_factory.mktemp('private-code'))
    # Unmodified imports remain read-only code references. No config/data files
    # are copied, and bytecode writing is disabled before importing any source.
    for source in SOURCE.rglob('*.py'):
        rel = source.relative_to(SOURCE)
        if any('.old' in p or p == 'tests' for p in rel.parts):
            continue
        target = dest / rel
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.symlink_to(source)
    sys.path.insert(0, str(dest))
    return dest


@pytest.fixture(autouse=True)
def offline(monkeypatch, tmp_path, candidate):
    def blocked(*args, **kwargs):
        raise AssertionError('private tests forbid network, provider, subprocess or send calls')
    monkeypatch.setattr(socket.socket, 'connect', blocked)
    monkeypatch.setattr(socket.socket, 'connect_ex', blocked)
    monkeypatch.setattr(socket, 'create_connection', blocked)
    monkeypatch.setattr(subprocess, 'run', blocked)
    monkeypatch.setattr(subprocess, 'Popen', blocked)
    original = sqlite3.connect

    def connect(path, *args, **kwargs):
        value = str(path)
        assert value.startswith((str(tmp_path) + '/', tmp_path.as_uri() + '/')), 'only this test temporary DB is allowed'
        return original(path, *args, **kwargs)
    monkeypatch.setattr(sqlite3, 'connect', connect)


@pytest.fixture
def env(tmp_path, monkeypatch, candidate):
    import api_groupbot
    import wechat_private
    from console.channels import wechat_private as projection
    manage = module('private_manage', ROOT / 'deploy/wechat-private/manage.py')
    path = tmp_path / 'state.sqlite3'
    path.touch()
    store = manage.State(path)
    store.db.executescript('''
    CREATE TABLE messages(app_id TEXT,message_id TEXT,group_id TEXT,sender_id TEXT,created_at REAL,
        payload TEXT,status TEXT,rule TEXT DEFAULT '',updated_at REAL,PRIMARY KEY(app_id,message_id));
    CREATE TABLE message_scopes(app_id TEXT,message_id TEXT,profile_id TEXT,scope_id TEXT,own_wxid TEXT,
        business_role TEXT,PRIMARY KEY(app_id,message_id));
    CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT);
    CREATE TABLE inbound_routes(app_id TEXT,message_id TEXT,route TEXT,PRIMARY KEY(app_id,message_id));
    CREATE TABLE media_completion_pending(app_id TEXT,message_id TEXT,own_wxid TEXT,group_id TEXT,sender_id TEXT,
        ordinal INTEGER,previous_descriptor_sha256 TEXT,current_descriptor_sha256 TEXT,source_payload_sha256 TEXT,
        status TEXT,created_at REAL,updated_at REAL,PRIMARY KEY(app_id,message_id,ordinal,current_descriptor_sha256));
    ''')
    wechat_private.initialize(store)
    store.paused = lambda: False
    archive, contacts = tmp_path / 'archive.sqlite3', tmp_path / 'contacts.sqlite3'
    original = projection.contact_role
    monkeypatch.setattr(wechat_private, 'contact_role', lambda app, own, peer, **kwargs:
                        original(app, own, peer, archive_path=archive, contact_path=contacts))
    workspace = tmp_path / 'workspace'
    workspace.mkdir(mode=0o700)
    token = tmp_path / 'fake-token'
    token.write_text('fictional-never-real')
    config = dict(app_id='fictional-app', assistant_profile_id='fictional-profile',
        state_file=str(path), receive_only_mode=True, send_enabled=False, collection_only_groups=True,
        base_url='https://wx.chuapi.com', token_file=str(token), active_after=1800000000,
        owner_control=dict(own_wxid='fictional-self',owner_wxid='fictional-operator',workspace=str(workspace)),
        company_knowledge=dict(enabled=True, company_id='fictional-company', company_name='虚构公司',active_after=1800000000))
    monkeypatch.setattr(api_groupbot, 'load_config', lambda path: config)
    yield type('Env', (), dict(path=path, store=store, config=config, now=1900000000,
                              archive=archive, contacts=contacts, workspace=workspace, root=tmp_path))()
    store.close()
