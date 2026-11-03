import argparse
from contextlib import closing
import copy
import fcntl
import hashlib
import json
import os
import re
import sqlite3
import sys
import time
from types import SimpleNamespace
import urllib.parse

import pytest

from conftest import load_module, selected

PRIVATE = '虚构错误详情不应输出'


@pytest.mark.parametrize('error,code', [
    (ValueError('binding_changed'), 'binding_changed'),
    (ValueError('runtime_changed'), 'runtime_changed'),
    (ValueError('bge_unavailable'), 'bge_unavailable'),
    (ValueError('file_identity_changed'), 'file_identity_changed'),
    (ValueError(PRIVATE), 'index_unavailable'),
    (RuntimeError('binding_changed'), 'index_unavailable'),
    (sqlite3.OperationalError(PRIVATE), 'index_unavailable'),
    (ImportError(PRIVATE), 'index_unavailable'),
])
def test_raw_index_fixed_codes_one_attempt(candidate, monkeypatch, capsys, error, code):
    calls = []

    def scan():
        calls.append(1)
        raise error

    monkeypatch.setitem(sys.modules, 'history_runtime', SimpleNamespace(do_scan=scan))
    main = selected(candidate / 'console-waha-history-raw-v1/history_index_worker.py',
                    {'SAFE_ERRORS', 'main'}, {'json': json})['main']
    assert main() == 1
    output = capsys.readouterr()
    result = json.loads(output.out)
    assert result['error'] == 'index_unavailable' and result['error_code'] == code
    assert result['message'].startswith('历史索引未完成')
    assert len(calls) == 1 and not output.err and PRIVATE not in output.out


def test_raw_index_busy_does_not_claim_work_or_retry(candidate, monkeypatch, capsys):
    def busy():
        raise BlockingIOError
    monkeypatch.setitem(sys.modules, 'history_runtime', SimpleNamespace(do_scan=busy))
    main = selected(candidate / 'console-waha-history-raw-v1/history_index_worker.py',
                    {'SAFE_ERRORS', 'main'}, {'json': json})['main']
    assert main() == 0
    assert json.loads(capsys.readouterr().out) == {'ok': True, 'status': 'busy'}


@pytest.fixture
def names(candidate, tmp_path):
    root = tmp_path / 'fiction-private-history'
    root.mkdir(mode=0o700)
    target = root / 'chat-names.json'
    binding = dict(session='fiction-session', account_id='fiction-account', owner='fiction-owner', scope='fiction-scope')
    with closing(sqlite3.connect(root / 'history.sqlite3')) as db, db:
        db.execute('CREATE TABLE binding(session TEXT,account_id TEXT,owner TEXT,scope TEXT)')
        db.execute('INSERT INTO binding VALUES(?,?,?,?)', tuple(binding.values()))
    cache = load_module('r7a_fiction_names', candidate / 'waha-names-refresh-v1/wa_names.py')
    cache.write_initial(target, dict(schema=cache.SCHEMA, binding=list(binding.values()), captured_at=1,
        chats={'fiction-chat-1': '虚构原名称'}, coverage_guaranteed=False))
    state = SimpleNamespace(status='WORKING', chats=[{'id': 'fiction-chat-1', 'name': '虚构新名称'}],
                            requests=[], changed=False)
    config = {'session': binding['session']}

    def api(_, path):
        state.requests.append(path)
        if path.startswith('/api/sessions/'):
            return {'status': state.status, 'me': {'id': binding['account_id']}}
        if 'offset=0' in path:
            return state.chats
        return []

    def need(ok, code):
        if not ok:
            raise ValueError(code)

    # The production session gate is retained, with only its GET client replaced.
    session_fn = selected(candidate / 'waha-names-refresh-v1/private_history.py', {'_session'},
                          {'_need': need, 'urllib': urllib})['_session']
    entry = SimpleNamespace(secure_ancestors=lambda root: None,
        read_config=lambda: config, read_binding=lambda c: dict(binding), bound_reader=lambda c, b: api)
    env = dict(ROOT=root, TARGET=target, os=os, time=time, fcntl=fcntl, json=json, sqlite3=sqlite3,
        argparse=argparse, entry=entry, wa_names=cache,
        private_history=SimpleNamespace(_private_db=lambda root: None, DB_NAME='history.sqlite3', _session=session_fn),
        history=SimpleNamespace(list_chats=lambda c, get, offset, size: get(c, f'/api/fiction-session/chats?offset={offset}'),
                                chat_id=lambda item: item['id']),
        bridge=SimpleNamespace(CHAT=re.compile(r'fiction-chat-\d+')))
    namespace = selected(candidate / 'waha-names-refresh-v1/capture_names_worker.py',
                         {'acquire_with_deadline', 'run', 'SAFE_ERRORS', 'main'}, env)
    return SimpleNamespace(state=state, root=root, target=target, binding=binding, cache=cache,
                           namespace=namespace)


def test_names_recovered_session_refreshes_atomically_and_preserves_prior(names, capsys):
    old = names.target.read_bytes()
    assert names.namespace['main'](['--refresh']) == 0
    assert (names.root / 'chat-names.previous.json').read_bytes() == old
    result = json.loads(capsys.readouterr().out)
    assert result['status'] == 'complete' and result['no_messages_requested'] is True
    assert names.cache.read_names(names.target, tuple(names.binding.values())) == {'fiction-chat-1': '虚构新名称'}
    assert all('/messages' not in p for p in names.state.requests)
    assert names.namespace['main'](['--refresh']) == 0
    assert names.cache.read_names(names.target, tuple(names.binding.values())) == {'fiction-chat-1': '虚构新名称'}


@pytest.mark.parametrize('status', ['FAILED', 'SCAN_QR_CODE', 'STOPPED', 'STARTING'])
def test_names_bad_session_preserves_cache_and_returns_binding_error(names, capsys, status):
    names.state.status = status
    before = names.target.read_bytes()
    assert names.namespace['main'](['--refresh']) == 1
    result = json.loads(capsys.readouterr().out)
    assert result['error_code'] == 'binding_mismatch' and result['type'] == 'ValueError'
    assert names.target.read_bytes() == before and not (names.root / 'chat-names.previous.json').exists()
    assert len(names.state.requests) == 1


def test_names_empty_after_relogin_keeps_existing_cache(names, capsys):
    names.state.chats = []
    before = names.target.read_bytes()
    assert names.namespace['main'](['--refresh']) == 1
    assert json.loads(capsys.readouterr().out)['error_code'] == 'source_empty_unverified'
    assert names.target.read_bytes() == before


def test_names_unknown_error_is_not_logged_or_retried(names, capsys):
    calls = []

    def fail(refresh=False):
        calls.append(refresh)
        raise ValueError(PRIVATE)

    names.namespace['main'].__globals__['run'] = fail
    assert names.namespace['main'](['--refresh']) == 1
    output = capsys.readouterr()
    assert json.loads(output.out)['error_code'] == 'refresh_unavailable'
    assert PRIVATE not in output.out + output.err and calls == [True]


def test_names_missing_cache_does_not_bootstrap_or_call_channel(names, capsys):
    names.target.unlink()
    assert names.namespace['main'](['--refresh']) == 1
    assert json.loads(capsys.readouterr().out)['error_code'] == 'cache_missing'
    assert not names.state.requests


def test_names_error_after_cache_replace_reports_uncertainty_without_retry(names, capsys):
    write = names.cache.write_refresh
    calls = []

    def late_failure(path, data):
        calls.append(1)
        write(path, data)
        raise RuntimeError(PRIVATE)

    names.cache.write_refresh = late_failure
    assert names.namespace['main'](['--refresh']) == 1
    result = json.loads(capsys.readouterr().out)
    assert '未确认完成' in result['message'] and calls == [1]
    assert names.cache.read_names(names.target, tuple(names.binding.values())) == {'fiction-chat-1': '虚构新名称'}
    assert (names.root / 'chat-names.previous.json').exists()


def test_daily_two_hold_guard_rejects_three_unknown_records_without_mutating_ledger(candidate):
    records = {f'fiction-model-{n}': dict(blocked_reason='usage_uncertain', in_flight=False,
        unknown_reserved_tokens=100+n, accounted_tokens=100+n) for n in range(3)}
    ledger = {'models': records}
    before = copy.deepcopy(ledger)
    matrix = SimpleNamespace(_read=lambda **kwargs: ledger)
    install = selected(candidate / 'wechat-kb-dual-live-v1/video_knowledge/quota_isolation.py',
                       {'install'}, {'copy': copy})['install']
    manifest = {'holds': [{'model': f'fiction-model-{n}'} for n in range(2)]}
    with pytest.raises(RuntimeError, match='^qwen_matrix_unavailable$'):
        install(matrix, manifest)
    assert ledger == before and not hasattr(matrix, '_kb_quarantine_installed')


@pytest.mark.parametrize('status', ['model_failed', 'model_usage_unknown', 'budget_blocked', 'calling'])
def test_daily_restart_never_retries_failed_or_unknown_period(candidate, tmp_path, status):
    with closing(sqlite3.connect(tmp_path / 'fiction-daily.sqlite3')) as db, db:
        db.execute('CREATE TABLE digest_runs(run_id TEXT,kind TEXT,period_id TEXT,owner_sha256 TEXT,policy_sha256 TEXT,status TEXT)')
        owner_sha = hashlib.sha256(b'fiction-owner').hexdigest()
        db.execute('INSERT INTO digest_runs VALUES(?,?,?,?,?,?)',
                   ('fiction-run', 'daily', '2033-05-18', owner_sha, 'fiction-policy-sha', status))
        def get(identity):
            state = db.execute('SELECT status FROM digest_runs WHERE run_id=?', (identity,)).fetchone()[0]
            return dict(status=state, run_id=identity, kind='daily', period_id='2033-05-18',
                        notification_status='not_ready', report_sha256=None, error_code='')
        def checkpoint(identity, new_status, **kwargs):
            db.execute('UPDATE digest_runs SET status=? WHERE run_id=?', (new_status, identity))
            return get(identity)
        state = SimpleNamespace(db=db, get=get, checkpoint=checkpoint)
        def forbidden(*a, **k):
            raise AssertionError('失败或未知日报不得重新调用模型或写归档')
        env = dict(hashlib=hashlib, sha=lambda value: 'fiction-policy-sha', write_archive=forbidden)
        code = selected(candidate / 'video-broker/20261012/knowledge_digest/daily_worker_candidate.py',
                        {'_run', '_view'}, env)
        result = code['_run']({'primary_owner': 'fiction-owner'}, {},
            {'kind': 'daily', 'period_id': '2033-05-18'}, state,
            boundary_check=forbidden, completion=forbidden)
        assert result['model_called'] is False and result['retry_permitted'] is False
        assert result['status'] == ('model_usage_unknown' if status == 'calling' else status)
