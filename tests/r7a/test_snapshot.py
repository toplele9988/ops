from contextlib import closing
import hashlib
import json
import os
import sqlite3

import pytest

from conftest import DIAGNOSE


@pytest.fixture
def snapshot(tmp_path):
    root = tmp_path / 'fiction-snapshot'
    root.mkdir(mode=0o700)
    schemas = {
        'wechat.sqlite3': '''CREATE TABLE messages(app_id TEXT,message_id TEXT,payload TEXT,created_at REAL,updated_at REAL,status TEXT);
            CREATE TABLE message_scopes(app_id TEXT,message_id TEXT,business_role TEXT);
            CREATE TABLE metadata(key TEXT,value TEXT);
            CREATE TABLE actions(app_id TEXT,message_id TEXT);
            CREATE TABLE analysis_archive(app_id TEXT,message_id TEXT);
            CREATE TABLE reply_archive(app_id TEXT,message_id TEXT);
            CREATE TABLE receive_collection_jobs(app_id TEXT,message_id TEXT);''',
        'digest.sqlite3': 'CREATE TABLE digest_runs(kind TEXT,status TEXT,error_code TEXT);',
        'history.sqlite3': 'CREATE TABLE messages(message_key TEXT);',
        'raw-index.sqlite3': 'CREATE TABLE wa_history_scan_state(state TEXT,reason TEXT);',
        'whatsapp.sqlite3': 'CREATE TABLE events(event_type TEXT,raw_json TEXT,received REAL);',
    }
    for name, schema in schemas.items():
        with closing(sqlite3.connect(root / name)) as db, db:
            db.executescript(schema)
        (root / name).chmod(0o600)
    with closing(sqlite3.connect(root / 'wechat.sqlite3')) as db, db:
        db.execute("INSERT INTO metadata VALUES('paused','true')")
        for n, role in enumerate(['learning', 'internal', 'unclassified', '虚构私有角色']):
            payload = dict(text='虚构正文不得输出', attachments=[{'name': '虚构附件不得输出'}] if n else [])
            db.execute('INSERT INTO messages VALUES(?,?,?,?,?,?)',
                       ('fiction-app', f'fiction-{n}', json.dumps(payload), 100+n, 200+n, 'pending'))
            db.execute('INSERT INTO message_scopes VALUES(?,?,?)', ('fiction-app', f'fiction-{n}', role))
        db.execute('INSERT INTO reply_archive VALUES(?,?)', ('fiction-app', 'fiction-3'))
    with closing(sqlite3.connect(root / 'digest.sqlite3')) as db, db:
        db.executemany('INSERT INTO digest_runs VALUES(?,?,?)', [
            ('daily', 'model_failed', 'qwen_matrix_unavailable'), ('daily', '虚构私有状态', '虚构私有错误')])
    with closing(sqlite3.connect(root / 'raw-index.sqlite3')) as db, db:
        db.executemany('INSERT INTO wa_history_scan_state VALUES(?,?)',
                       [('indexed', None), ('review', 'text_empty'), ('虚构私有状态', '虚构私有错误')])
    with closing(sqlite3.connect(root / 'whatsapp.sqlite3')) as db, db:
        db.execute('INSERT INTO events VALUES(?,?,?)', ('session.status', json.dumps(
            dict(payload=dict(status='WORKING'), me=dict(id='虚构账号不得输出'))), 100))
    return root


def fingerprint(root):
    return {p.name: (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns)
            for p in root.iterdir() if p.is_file()}


def test_aggregate_does_not_expose_content_identity_or_unknown_codes(snapshot):
    before = fingerprint(snapshot)
    result = DIAGNOSE.diagnose(snapshot)
    assert result['wechat']['pending'] == 4 and result['wechat']['paused'] is True
    assert result['wechat']['roles'] == dict(learning=1, internal=1, unclassified=1, unknown=1)
    assert result['wechat']['shapes'] == dict(text_only=1, attachments=3)
    assert result['wechat']['linked']['reply_archive'] == 1
    assert result['whatsapp_session'] == 'WORKING'
    assert '虚构' not in json.dumps(result, ensure_ascii=False)
    assert fingerprint(snapshot) == before


@pytest.mark.parametrize('value', ['/var/lib/imgw/wechat', '/tmp', '/tmp/../var/lib/imgw'])
def test_production_and_parent_traversal_are_rejected_before_open(value):
    with pytest.raises(ValueError):
        DIAGNOSE.diagnose(value)


@pytest.mark.parametrize('unsafe', ['root_mode', 'symlink_root', 'symlink_db', 'hardlink_db', 'public_db', 'wal', 'shm'])
def test_unsafe_snapshot_is_rejected(snapshot, tmp_path, unsafe):
    target = snapshot
    db = snapshot / 'wechat.sqlite3'
    if unsafe == 'root_mode':
        snapshot.chmod(0o755)
    elif unsafe == 'symlink_root':
        target = tmp_path / 'fiction-link'
        target.symlink_to(snapshot, target_is_directory=True)
    elif unsafe == 'symlink_db':
        original = tmp_path / 'fiction-external.sqlite3'
        db.rename(original)
        db.symlink_to(original)
    elif unsafe == 'hardlink_db':
        os.link(db, tmp_path / 'fiction-hardlink.sqlite3')
    elif unsafe == 'public_db':
        db.chmod(0o644)
    else:
        (snapshot / ('wechat.sqlite3-' + unsafe)).write_bytes(b'fiction')
    with pytest.raises(ValueError):
        DIAGNOSE.diagnose(target)


def test_readonly_files_are_supported(snapshot):
    for p in snapshot.iterdir():
        p.chmod(0o400)
    assert DIAGNOSE.diagnose(snapshot)['wechat']['pending'] == 4


def test_corrupt_payload_is_counted_without_echo(snapshot):
    with closing(sqlite3.connect(snapshot / 'wechat.sqlite3')) as db, db:
        db.execute('UPDATE messages SET payload=? WHERE message_id=?', ('虚构坏 JSON', 'fiction-0'))
    assert DIAGNOSE.diagnose(snapshot)['wechat']['malformed'] == 1


def test_missing_schema_cli_fails_with_chinese_message(snapshot, capsys):
    with closing(sqlite3.connect(snapshot / 'wechat.sqlite3')) as db, db:
        db.execute('DROP TABLE message_scopes')
    assert DIAGNOSE.main(['--snapshot', str(snapshot)]) == 1
    output = capsys.readouterr()
    assert '备份读取失败' in output.out and 'message_scopes' not in output.out + output.err
