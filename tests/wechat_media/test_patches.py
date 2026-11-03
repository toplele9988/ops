"""补丁只在 /tmp 应用；假下载器验证未知结果永不自动重发。"""
import ast
import contextlib
import errno
import hashlib
from pathlib import Path, PureWindowsPath
import sqlite3
import subprocess
import sys
import threading
import time
from types import ModuleType, SimpleNamespace

import pytest

from conftest import ROOT


@pytest.mark.parametrize('name,patch', [
    ('private_media_register.py', 'private-media-errors.patch'),
    ('video_card_jobs.py', 'video-card-errors.patch')])
def test_patch_round_trip_is_exact_production_sha(tmp_path, name, patch):
    target = tmp_path / 'video_knowledge' / name
    target.parent.mkdir()
    candidate = (ROOT / 'deploy/wechat-media' / name).read_bytes()
    target.write_bytes(candidate)
    patch_path = ROOT / 'deploy/wechat-media' / patch
    result = subprocess.run(['patch', '--batch', '-R', '-p1', '-i', str(patch_path)],
                            cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    baseline = dict(line.split(None, 1)[::-1] for line in
                    (ROOT / 'deploy/wechat-media/baseline.sha256').read_text().splitlines())
    assert hashlib.sha256(target.read_bytes()).hexdigest() == baseline['video_knowledge/' + name]
    result = subprocess.run(['patch', '--batch', '-p1', '-i', str(patch_path)],
                            cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert target.read_bytes() == candidate


def scan(scanner, root, out):
    return scanner.run(root, out, mount_guard=lambda p: 'fictional-source-mount',
        output_guard=lambda p: 'fictional-output-mount', minimum_free_bytes=1,
        space_fn=lambda p: 10**12, output_device_fn=lambda p: -1)


@pytest.fixture
def files(tmp_path):
    root, out = tmp_path / 'source', tmp_path / 'output'
    root.mkdir(mode=0o700)
    (root / 'fictional.jpg').write_bytes(b'fictional opaque bytes')
    return root, out


def test_scanner_second_run_reuses_content_and_observation(scanner, files):
    root, out = files
    assert scan(scanner, root, out)['archived'] == 1
    second = scan(scanner, root, out)
    assert second['existing'] == 1 and second['rejected'] == 0
    with contextlib.closing(sqlite3.connect(out / 'media-registry.sqlite3')) as db:
        assert db.execute('SELECT COUNT(*) FROM assets').fetchone()[0] == 1
        assert db.execute('SELECT COUNT(*) FROM observations').fetchone()[0] == 1
    assert len(list(out.glob('*.blob'))) == 1


@pytest.mark.parametrize('number,reason', [(errno.EACCES, 'permission_denied'),
    (errno.EPERM, 'permission_denied'), (errno.ENOENT, 'not_found'),
    (errno.ESTALE, 'stale_handle'), (errno.EIO, 'io_error'), (errno.ELOOP, 'symlink'),
    (errno.EFBIG, 'file_too_large'), (errno.ENAMETOOLONG, 'name_too_long'),
    (errno.EILSEQ, 'name_encoding'), (errno.EDQUOT, 'quota'), (errno.ENOSPC, 'disk_full')])
def test_source_errors_keep_errno_without_private_paths(scanner, files, monkeypatch, number, reason):
    def fail(*args, **kwargs):
        raise OSError(number, 'fictional-private-message', '/fictional-private-name')
    monkeypatch.setattr(scanner, 'nofollow_source', fail)
    result = scan(scanner, *files)
    assert result['status'] == 'partial'
    assert result['errors'] == {'source_open_' + reason: 1}
    assert result['rejected'] == 1
    assert 'fictional-private' not in str(result)


def test_output_failure_is_not_reported_as_source_failure(scanner, files, monkeypatch):
    def fail(*args, **kwargs):
        raise OSError(errno.ENOSPC, 'fictional-private-output')
    monkeypatch.setattr(scanner.tempfile, 'mkstemp', fail)
    result = scan(scanner, *files)
    assert result['errors'] == {'output_create_disk_full': 1}
    assert result['archived'] == 0 and result['rejected'] == 1


@pytest.mark.parametrize('message,code', [('fictionalprivatevalue', 'source_validation_failed'),
    ('source_short_read', 'source_short_read')])
def test_validation_codes_need_allowlist_not_just_safe_characters(scanner, files, monkeypatch, message, code):
    def fail(*args, **kwargs):
        raise ValueError(message)
    monkeypatch.setattr(scanner, 'nofollow_source', fail)
    result = scan(scanner, *files)
    assert result['errors'] == {code: 1}
    assert 'fictionalprivatevalue' not in str(result)


def test_unknown_io_stage_cannot_leak_a_private_string(scanner):
    assert scanner.io_code(scanner.MediaIOError('fictionalprivatevalue', errno.EIO)) == 'unclassified_io_error'


def test_failed_publish_cleans_only_its_own_temporary(scanner, files, monkeypatch):
    def fail(*args, **kwargs):
        raise OSError(errno.EROFS, 'fictional-private-output')
    monkeypatch.setattr(scanner.os, 'link', fail)
    result = scan(scanner, *files)
    assert result['errors'] == {'output_publish_read_only': 1}
    assert not list(files[1].glob('.incoming-*'))
    assert not list(files[1].glob('*.blob'))


def test_symlink_and_oversize_are_still_rejected(scanner, files):
    root, out = files
    (root / 'link.jpg').symlink_to(root / 'fictional.jpg')
    with (root / 'oversize.mp4').open('wb') as handle:
        handle.truncate(scanner.MAX_BYTES + 1)
    result = scan(scanner, root, out)
    assert result['errors'] == {'source_symlink': 1, 'invalid_source_file': 1}
    assert result['archived'] == 1
    with pytest.raises(ValueError, match='unsafe_path'):
        scanner.nofollow_source(root, Path('../escape.jpg'))


def test_mount_change_stops_processing(scanner, files):
    count = 0
    def guard(path):
        nonlocal count
        count += 1
        return 'first' if count == 1 else 'changed'
    result = scanner.run(*files, mount_guard=guard, output_guard=lambda p: 'output',
        minimum_free_bytes=1, space_fn=lambda p: 10**12, output_device_fn=lambda p: -1)
    assert result['status'] == 'partial'
    assert result['errors'] == {'source_mount_changed': 1}
    assert result['archived'] == 0


@pytest.fixture
def lane(tmp_path):
    # Compile the candidate's real state machine without importing any provider,
    # policy reader, credentials loader or production application bootstrap.
    tree = ast.parse((ROOT / 'deploy/wechat-media/video_card_jobs.py').read_text())
    wanted = {'State', 'initialize', 'task_id', 'verify_detail', 'failure_code', 'tick'}
    nodes = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name in wanted
             or isinstance(n, ast.Assign) and any(isinstance(t, ast.Name)
                and t.id in ('DOWNLOAD_DIR', '_FAILURE_CODES') for t in n.targets)]
    def forbidden(*args, **kwargs):
        raise AssertionError('必须注入假下载器，不能访问真实渠道')
    env = dict(__name__='fictional_card_lane', contextlib=contextlib, sqlite3=sqlite3,
        threading=threading, time=time, PureWindowsPath=PureWindowsPath,
        call_tool=forbidden, silent=lambda config: True, source=lambda *a: ('fictional-object', 'fictional-nonce'),
        document=lambda value: value)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), '<虚构下载器状态机>', 'exec'), env)
    store = env['State'](tmp_path / 'cards.sqlite3')
    env['initialize'](store)
    with store.transaction() as db:
        db.execute('''INSERT INTO video_card_jobs(job_id,account_sha256,source_scope,object_id,person,
            target_message_id,target_callback_sha256,filename,state,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?)''', ('fictional-job', 'a' * 64, 'fictional-scope',
            '123', 'fictional-person', 'fictional-message', 'b' * 64, 'wxcard_fictional.mp4', 'queued', 10, 10))
    yield SimpleNamespace(env=env, store=store, config={'realtime_video_cards': True})
    store.close()


def tick(lane, rpc=None, stamp=100):
    kwargs = {'now': lambda: stamp}
    if rpc is not None:
        kwargs['rpc'] = rpc
    return lane.env['tick'](lane.store, lane.config, {'catalog': 'fictional-catalog'}, **kwargs)


@pytest.mark.parametrize('message,code', [('card_source_changed', 'card_source_changed'),
    ('assistant_person_unauthorized', 'assistant_person_unauthorized'),
    ('fictional-private-payload', 'source_validation_failed')])
def test_rejected_preflight_has_no_download_and_no_false_unknown(lane, message, code):
    def fail(*args):
        raise ValueError(message)
    lane.env['source'] = fail
    result = tick(lane)
    assert result['status'] == 'blocked' and result['error_code'] == code
    assert not result['api_send_attempted']


@pytest.mark.parametrize('failure', ['timeout', 'missing_task_id', 'invalid_response'])
def test_submission_uncertainty_is_frozen_after_one_rpc(lane, failure):
    calls = []
    def rpc(name, arguments):
        calls.append((name, arguments))
        if failure == 'timeout':
            raise TimeoutError('fictional-private-network-detail')
        if failure == 'invalid_response':
            raise ValueError('fictional-private-response')
        return {'id': 'not-an-integer'}
    result = tick(lane, rpc)
    assert result['status'] == 'download_unknown'
    assert result['error_code'] == 'download_outcome_unknown'
    assert tick(lane, rpc, 200)['status'] == 'idle'
    assert len(calls) == 1
    assert calls[0][0] == 'download_wxchannels_video'
    assert calls[0][1]['existing_action'] == 'error'


@pytest.mark.parametrize('state', ['download_unknown', 'blocked', 'submitting', 'indexed'])
def test_historical_states_never_reenter_download(lane, state):
    with lane.store.transaction() as db:
        db.execute('UPDATE video_card_jobs SET state=?', (state,))
    assert tick(lane)['status'] == 'idle'


def test_polling_retry_is_read_only(lane):
    with lane.store.transaction() as db:
        db.execute("UPDATE video_card_jobs SET state='downloading',task_id=321")
    calls = []
    def rpc(name, arguments):
        calls.append(name)
        raise TimeoutError('fictional-private-detail')
    assert tick(lane, rpc)['status'] == 'downloading'
    assert tick(lane, rpc, 200)['status'] == 'downloading'
    assert calls == ['get_download_task_detail', 'get_download_task_detail']


def test_receipt_filename_escape_is_blocked_without_download(lane):
    with lane.store.transaction() as db:
        db.execute("UPDATE video_card_jobs SET state='downloading',task_id=321")
    calls = []
    def rpc(name, arguments):
        calls.append(name)
        return dict(id=321, content_id='wxchannels:123', status=5, files=[dict(
            name='../escape.mp4', file_path=r'Z:\WeChatdownloads\..\escape.mp4',
            download_dir=r'Z:\WeChatdownloads', status='finished', size=20, downloaded=20)])
    result = tick(lane, rpc)
    assert result['status'] == 'blocked'
    assert result['error_code'] == 'download_file_mismatch'
    assert calls == ['get_download_task_detail']


def test_catalog_rule_is_recorded_without_releasing_source(lane, monkeypatch):
    with lane.store.transaction() as db:
        db.execute("UPDATE video_card_jobs SET state='archive_verified',source_sha256=?", ('c' * 64,))
    class Reader:
        def __init__(self, path):
            raise ValueError('catalog_schema_version_unsupported')
    package = ModuleType('retrieval')
    package.__path__ = []
    child = ModuleType('retrieval.catalog_reader')
    child.CatalogReader = Reader
    monkeypatch.setitem(sys.modules, 'retrieval', package)
    monkeypatch.setitem(sys.modules, 'retrieval.catalog_reader', child)
    lane.env['authority'] = lambda *args, **kwargs: [('fictional-owner', 'fictional-scope')]
    result = tick(lane)
    assert result['status'] == 'blocked'
    assert result['error_code'] == 'catalog_schema_version_unsupported'
    assert tick(lane, stamp=200)['status'] == 'idle'
