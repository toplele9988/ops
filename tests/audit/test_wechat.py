"""All source rows and credentials are invented, and network calls are blocked."""
from contextlib import closing, contextmanager
import hashlib
import json
import socket
import sqlite3
from types import SimpleNamespace

import pytest

from console.audit import wechat
from console.audit.common import failure_reason, read_db
from console.directory import WechatDirectory
from console.directory.sources import read_role_metadata

NOW = 2_000_000_000.0
DAY = 86400
SECRET = 'fictional-key-cookie-secret-never-return'
GROUP = 'fictional-private-group@chatroom'


def sql(path, query, args=()):
    with closing(sqlite3.connect(path)) as db, db:
        db.execute(query, args)


@pytest.fixture
def wx(tmp_path, monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError('Tests must not access the network')
    monkeypatch.setattr(socket.socket, 'connect', blocked)
    monkeypatch.setattr(socket, 'create_connection', blocked)
    state, discovery, config = (tmp_path / name for name in ('wechat.sqlite3', 'discovery.sqlite3', 'config.json'))
    with closing(sqlite3.connect(state)) as db, db:
        db.executescript('''
            CREATE TABLE messages(app_id TEXT,message_id TEXT,group_id TEXT,created_at REAL,payload TEXT);
            CREATE TABLE message_scopes(app_id TEXT,message_id TEXT,scope_id TEXT,business_role TEXT);
            CREATE TABLE onboarding_candidates(group_id TEXT,group_name TEXT);
            CREATE TABLE company_media(app_id TEXT,message_id TEXT,ordinal INTEGER,name TEXT,status TEXT,
                text TEXT,archive_status TEXT,archive_sha256 TEXT,archive_bytes INTEGER);
            CREATE TABLE receive_collection_jobs(job_id TEXT,state TEXT,result_status TEXT,
                updated_at REAL,claimed_at REAL,not_before REAL);
            CREATE TABLE received_callback_archive(received_at REAL,payload_json TEXT);
        ''')
    with closing(sqlite3.connect(discovery)) as db, db:
        db.executescript('''
            CREATE TABLE identities(identity TEXT,kind TEXT,first_seen REAL,last_seen REAL);
            CREATE TABLE snapshot_runs(run_id TEXT,state TEXT,started_at REAL,finished_at REAL,error TEXT);
        ''')
        db.execute('INSERT INTO snapshot_runs VALUES(?,?,?,?,?)', ('fictional-run', 'observed_unverified', NOW - 100, NOW - 90, SECRET))
    config.write_text(json.dumps(dict(groups={}, token=SECRET, Cookie=SECRET, api_key=SECRET)))
    ctx = SimpleNamespace(now=NOW, wechat_path=state, discovery_path=discovery, wechat_config_path=config,
                          directory=None, memo={})
    return ctx


def configure(ctx, *, role='supplier', name='虚构供应商', enabled=True, group=GROUP):
    data = json.loads(ctx.wechat_config_path.read_text())
    data['groups'][group] = dict(enabled=enabled, business_role=role, group_name=name, token=SECRET,
                                 auto_collection_source='fictional')
    ctx.wechat_config_path.write_text(json.dumps(data, ensure_ascii=False))


def message(ctx, *, at=NOW - 10, role='supplier', mid='fictional-message', group=GROUP, app='fictional-app'):
    sql(ctx.wechat_path, 'INSERT INTO messages VALUES(?,?,?,?,?)', (app, mid, group, at, SECRET))
    if role is not None:
        sql(ctx.wechat_path, 'INSERT INTO message_scopes VALUES(?,?,?,?)', (app, mid, group, role))


def job(ctx, *, state='processing', status='', claimed=NOW - 1000, due=NOW, updated=NOW - 100):
    sql(ctx.wechat_path, 'INSERT INTO receive_collection_jobs VALUES(?,?,?,?,?,?)',
        ('fictional-job-' + state, state, status, updated, claimed, due))


def callback_rows(ctx, recent, previous=70):
    with closing(sqlite3.connect(ctx.wechat_path)) as db, db:
        db.executemany('INSERT INTO received_callback_archive VALUES(?,?)',
                       [(NOW - 2 * DAY, SECRET)] * previous + [(NOW - 10, SECRET)] * recent)


def trigger(ctx, name):
    if name == 'not_enrolled':
        sql(ctx.discovery_path, 'INSERT INTO identities VALUES(?,?,?,?)', (GROUP, 'group', NOW - DAY, NOW))
    elif name == 'silent_groups':
        configure(ctx)
    elif name == 'role_conflicts':
        configure(ctx, role='customer', name='SUP88 虚构供货')
    elif name == 'unclassified_groups':
        configure(ctx, role='unclassified', name='虚构未定用途')
    elif name == 'media_archive':
        sql(ctx.wechat_path, 'INSERT INTO company_media VALUES(?,?,?,?,?,?,?,?,?)',
            ('fictional-app', 'fictional-message', 0, SECRET, 'pending', SECRET, '', '', 0))
    elif name == 'jobs_raw_no_text':
        job(ctx, state='RAW_ARCHIVED_NO_TEXT', status='waiting_cloud_budget')
    elif name == 'jobs_failed':
        job(ctx, state='complete', status='download_failed')
    elif name == 'jobs_stuck':
        job(ctx)
    elif name == 'callback_drop':
        callback_rows(ctx, 2)
    elif name == 'discovery_stale':
        sql(ctx.discovery_path, 'UPDATE snapshot_runs SET started_at=?,finished_at=?', (NOW - 2 * DAY - 5, NOW - 2 * DAY))


@pytest.mark.parametrize('key,title,check', wechat.CHECKS, ids=[x[0] for x in wechat.CHECKS])
def test_every_check_triggers(wx, key, title, check):
    trigger(wx, key.split('.', 1)[1])
    result = check(wx)
    assert result['key'] == key
    assert result['level'] != 'ok'
    assert result['count'] == 1
    assert result['meaning'] and result['remedy']


@pytest.mark.parametrize('key,title,check', wechat.CHECKS, ids=[x[0] for x in wechat.CHECKS])
def test_every_check_can_be_clear(wx, key, title, check):
    result = check(wx)
    assert result['level'] == 'ok'
    assert result['count'] == 0


@pytest.mark.parametrize('key,title,check', wechat.CHECKS, ids=[x[0] for x in wechat.CHECKS])
def test_every_check_reports_missing_source_to_boundary(wx, key, title, check):
    wx.wechat_path.unlink()
    wx.discovery_path.unlink()
    wx.wechat_config_path.unlink()
    with pytest.raises((OSError, ValueError, sqlite3.Error)):
        check(wx)


def test_directory_service_reused_without_loading_inbox(wx, monkeypatch):
    configure(wx)
    message(wx)
    directory = WechatDirectory(discovery_path=wx.discovery_path, config_path=wx.wechat_config_path,
                                state_path=wx.wechat_path, clock=lambda: NOW)
    def forbidden(*args, **kwargs):
        raise AssertionError('Audit must not load message bodies or generate media tokens')
    monkeypatch.setattr(directory.adapter, 'list_conversations', forbidden)
    wx.directory = directory
    assert wechat.check_silent_groups(wx)['count'] == 0
    assert wechat.check_unclassified_groups(wx)['count'] == 0
    assert not directory.cache_path.exists()


@pytest.mark.parametrize('age,count', [(7 * DAY, 0), (7 * DAY + 1, 1), (-1, 0)])
def test_seven_day_boundary_and_future_times(wx, age, count):
    configure(wx)
    message(wx, at=NOW - age)
    assert wechat.check_silent_groups(wx)['count'] == count


def test_disabled_enrollment_not_claimed_to_be_silent(wx):
    configure(wx, enabled=False)
    assert wechat.check_silent_groups(wx)['count'] == 0


def test_directory_role_majority_counts_only_distinct_scopes_and_app(wx):
    message(wx, role='supplier', mid='same', app='first')
    message(wx, role='customer', mid='same', app='second')
    # Duplicate scopes and unscoped messages must not alter a tied majority.
    sql(wx.wechat_path, 'INSERT INTO message_scopes VALUES(?,?,?,?)', ('first', 'same', 'extra', 'supplier'))
    for i in range(6):
        message(wx, role=None, mid='unscoped-' + str(i))
    roles = read_role_metadata(wx.wechat_path)
    assert roles[GROUP]['counterpart_role'] == 'customer'
    wx.memo.clear()
    assert wechat.check_unclassified_groups(wx)['count'] == 0


def test_explicit_unclassified_votes_not_overwritten_by_smaller_role(wx):
    message(wx, role='unclassified', mid='one')
    message(wx, role='unclassified', mid='two')
    message(wx, role='supplier', mid='three')
    assert read_role_metadata(wx.wechat_path)[GROUP]['counterpart_role'] == 'unclassified'


@pytest.mark.parametrize('status,digest,size,expected', [
    ('ready', 'a' * 64, 10, 0), ('ready', 'A' * 64, 10, 1), ('ready', 'a' * 64, 0, 1),
    ('pending', 'a' * 64, 10, 1), ('ready', 'a' * 63, 10, 1), (None, None, None, 1),
])
def test_media_archive_requires_valid_metadata(wx, status, digest, size, expected):
    sql(wx.wechat_path, 'INSERT INTO company_media VALUES(?,?,?,?,?,?,?,?,?)',
        ('fictional-app', 'fictional-message', 0, SECRET, 'ready', SECRET, status, digest, size))
    result = wechat.check_media_archive(wx)
    assert result['count'] == expected
    assert result['archive_rate'] == 1 - expected


@pytest.mark.parametrize('state,status,expected', [
    ('complete', 'ready', 0), ('complete', 'empty', 0), ('complete', 'download_failed', 1),
    ('interrupted', 'failed_interrupted', 1), ('source_changed', '', 1),
    ('retry_wait', 'download_failed', 1), ('RAW_ARCHIVED_NO_TEXT', 'extract_failed', 0),
])
def test_failures_use_result_status_and_preserve_archived_no_text(wx, state, status, expected):
    job(wx, state=state, status=status)
    assert wechat.check_jobs_failed(wx)['count'] == expected


@pytest.mark.parametrize('state,status,age,expected', [
    ('processing', '', 600, 0), ('processing', '', 601, 1),
    ('retry_wait', 'download_failed', 3600, 0), ('retry_wait', 'download_failed', 3601, 1),
    ('RAW_ARCHIVED_NO_TEXT', 'waiting_cloud_budget', 3601, 1),
    ('RAW_ARCHIVED_NO_TEXT', 'empty', 3601, 0), ('saved_original_needs_review', '', 3601, 1),
])
def test_stuck_job_boundaries(wx, state, status, age, expected):
    job(wx, state=state, status=status, claimed=NOW - age, due=NOW - age, updated=NOW - age)
    assert wechat.check_jobs_stuck(wx)['count'] == expected


@pytest.mark.parametrize('recent,previous,expected', [(2, 70, 1), (3, 70, 0), (4, 70, 0), (0, 0, 0)])
def test_callback_threshold_and_zero_baseline(wx, recent, previous, expected):
    callback_rows(wx, recent, previous)
    sql(wx.wechat_path, 'INSERT INTO received_callback_archive VALUES(?,?)', (NOW + 1, SECRET))
    sql(wx.wechat_path, 'INSERT INTO received_callback_archive VALUES(?,?)', (NOW - 8 * DAY - 1, SECRET))
    result = wechat.check_callback_drop(wx)
    assert result['count'] == expected
    assert result['recent_24h'] == recent
    assert result['previous_7d'] == previous


@pytest.mark.parametrize('age,expected', [(DAY, 0), (DAY + 1, 1), (-1, 1)])
def test_snapshot_exact_day_boundary_and_future(wx, age, expected):
    sql(wx.discovery_path, 'UPDATE snapshot_runs SET started_at=?,finished_at=?', (NOW - age - 1, NOW - age))
    assert wechat.check_discovery_stale(wx)['count'] == expected


def test_partial_or_unknown_snapshots_do_not_reset_success(wx):
    trigger(wx, 'discovery_stale')
    for state in ('partial_source', 'unknown', 'started', 'abandoned_read'):
        sql(wx.discovery_path, 'INSERT INTO snapshot_runs VALUES(?,?,?,?,?)', (state, state, NOW - 10, NOW, SECRET))
    assert wechat.check_discovery_stale(wx)['last_success_at'] == NOW - 2 * DAY


def test_metadata_only_output_is_redacted_limited_and_read_only(wx):
    for i in range(9):
        identity = GROUP.replace('group', 'group-' + str(i))
        sql(wx.discovery_path, 'INSERT INTO identities VALUES(?,?,?,?)', (identity, 'group', NOW - DAY, NOW))
    for key, _, _ in wechat.CHECKS:
        if not key.endswith('not_enrolled'):
            trigger(wx, key.split('.', 1)[1])
    paths = (wx.wechat_path, wx.discovery_path, wx.wechat_config_path)
    before = [(hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns) for p in paths]
    output = [check(wx) for _, _, check in wechat.CHECKS]
    serialized = json.dumps(output, ensure_ascii=False)
    assert SECRET not in serialized and GROUP not in serialized
    assert 'fictional-message' not in serialized and 'fictional-job' not in serialized
    assert all(len(item['samples']) <= 5 for item in output)
    assert len(output[0]['samples']) == 5
    assert before == [(hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns) for p in paths]


def test_one_broken_table_does_not_affect_independent_check(wx):
    sql(wx.wechat_path, 'DROP TABLE company_media')
    with pytest.raises(sqlite3.Error):
        wechat.check_media_archive(wx)
    assert wechat.check_callback_drop(wx)['level'] == 'ok'
    assert wechat.check_discovery_stale(wx)['level'] == 'ok'


def test_untrusted_timestamp_never_escapes_as_text(wx):
    job(wx, state='interrupted', updated=SECRET)
    result = wechat.check_jobs_failed(wx)
    assert result['samples'][0]['updated_at'] is None
    assert SECRET not in json.dumps(result)


def test_directory_metadata_does_not_read_message_bodies(wx):
    configure(wx)
    message(wx)
    accessed = set()

    @contextmanager
    def metadata_only(path):
        with read_db(path) as db:
            def authorizer(action, table, column, *unused):
                if action == sqlite3.SQLITE_READ:
                    accessed.add((table, column))
                    if column in ('payload', 'payload_json', 'text', 'original'):
                        return sqlite3.SQLITE_DENY
                return sqlite3.SQLITE_OK
            db.set_authorizer(authorizer)
            yield db

    directory = WechatDirectory(discovery_path=wx.discovery_path, config_path=wx.wechat_config_path,
                                state_path=wx.wechat_path, clock=lambda: NOW)
    result = directory.audit_snapshot(NOW, database=metadata_only)
    assert all(result['sources'].values())
    assert result['items'][0]['role'] == 'supplier'
    assert ('messages', 'created_at') in accessed
    assert all(column not in ('payload', 'payload_json', 'text', 'original') for _, column in accessed)


def test_primary_key_fast_path_preserves_directory_counts_and_role_ties(wx):
    with closing(sqlite3.connect(wx.wechat_path)) as db, db:
        db.executescript('''DROP TABLE messages; DROP TABLE message_scopes;
            CREATE TABLE messages(app_id TEXT NOT NULL,message_id TEXT NOT NULL,group_id TEXT NOT NULL,
                created_at REAL,payload TEXT,PRIMARY KEY(app_id,message_id));
            CREATE TABLE message_scopes(app_id TEXT NOT NULL,message_id TEXT NOT NULL,scope_id TEXT,
                business_role TEXT,PRIMARY KEY(app_id,message_id));''')
    configure(wx)
    message(wx, role='customer', mid='customer-message', at=NOW - 7 * DAY)
    message(wx, role='supplier', mid='supplier-message', at=NOW + 1)
    directory = WechatDirectory(discovery_path=wx.discovery_path, config_path=wx.wechat_config_path,
                                state_path=wx.wechat_path, clock=lambda: NOW)
    row = directory.audit_snapshot(NOW, database=read_db)['items'][0]
    assert row['n_messages'] == 2
    assert row['collect_state'] == 'collecting'
    assert row['counterpart_role'] == 'customer'


@pytest.mark.parametrize('source_error,kind,expected', [
    (FileNotFoundError(SECRET), 'missing', FileNotFoundError),
    (PermissionError(SECRET), 'permission', PermissionError),
    (TimeoutError(SECRET), 'timeout', TimeoutError),
    (sqlite3.OperationalError('interrupted'), 'timeout', TimeoutError),
    (sqlite3.DatabaseError(SECRET), 'database', sqlite3.DatabaseError),
    (ValueError(SECRET), 'format', ValueError),
    (OSError(SECRET), 'unavailable', OSError),
    (RuntimeError(SECRET), 'unavailable', OSError),
])
def test_directory_audit_preserves_safe_failure_type_only(wx, source_error, kind, expected):
    def unreadable(path):
        raise source_error
    directory = WechatDirectory(discovery_path=wx.discovery_path, config_path=wx.wechat_config_path,
        state_path=wx.wechat_path, clock=lambda: NOW, discovery_reader=unreadable)
    snapshot = directory.audit_snapshot(NOW, database=read_db)
    assert snapshot['source_errors'] == {'discovery': kind}
    assert SECRET not in json.dumps(snapshot)
    wx.directory = directory
    with pytest.raises(expected) as caught:
        wechat.check_not_enrolled(wx)
    assert SECRET not in str(caught.value)
    assert SECRET not in failure_reason(caught.value)
    assert 'source_errors' not in directory.list()
    assert 'source_errors' not in directory.summary()


def test_missing_directory_file_is_not_misreported_as_invalid_schema(wx):
    wx.discovery_path.unlink()
    with pytest.raises(FileNotFoundError) as caught:
        wechat.check_not_enrolled(wx)
    assert failure_reason(caught.value) == '来源文件不存在'
    assert wx.memo['wechat.directory']['source_errors']['discovery'] == 'missing'


def test_directory_timeout_gets_specific_safe_reason(wx):
    def timeout(path, now):
        raise TimeoutError(SECRET)
    wx.directory = WechatDirectory(discovery_path=wx.discovery_path, config_path=wx.wechat_config_path,
        state_path=wx.wechat_path, clock=lambda: NOW, state_reader=timeout)
    with pytest.raises(TimeoutError) as caught:
        wechat.check_silent_groups(wx)
    assert failure_reason(caught.value) == '统计超出本项时间预算，请稍后重试'
    # Independent discovery/config check still succeeds despite the bad state source.
    assert wechat.check_not_enrolled(wx)['level'] == 'ok'
