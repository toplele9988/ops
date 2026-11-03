"""假原授权函数只读临时配置；不打开任何现场源码、配置或数据库。"""
from contextlib import closing, contextmanager
import builtins
import io
import json
from pathlib import Path
import sqlite3
import sys
from types import SimpleNamespace
from urllib.parse import unquote, urlsplit

import pytest

from recovery_fixtures import ROOT

if str(ROOT / 'deploy/kb') not in sys.path:
    sys.path.append(str(ROOT / 'deploy/kb'))
import authorization_runtime as runtime


@pytest.fixture
def only_tmp(monkeypatch, tmp_path):
    original_open, original_io, original_connect = builtins.open, io.open, sqlite3.connect
    opened = []

    def check(path):
        if isinstance(path, int):
            return
        if str(path).startswith('file:'):
            path = unquote(urlsplit(str(path)).path)
        need = Path(path).absolute()
        assert need.is_relative_to(tmp_path), '假授权禁止读取真实路径'
        opened.append(need)

    def guarded(path, *args, **kwargs):
        check(path)
        return original_open(path, *args, **kwargs)

    def guarded_io(path, *args, **kwargs):
        check(path)
        return original_io(path, *args, **kwargs)

    def connect(path, *args, **kwargs):
        check(path)
        return original_connect(path, *args, **kwargs)

    monkeypatch.setattr(builtins, 'open', guarded)
    monkeypatch.setattr(io, 'open', guarded_io)
    monkeypatch.setattr(sqlite3, 'connect', connect)
    yield opened


def fake_originals(tmp_path):
    config = tmp_path / 'config.json'
    config.write_text('{"fictional":true}')
    config.chmod(0o600)
    policy = tmp_path / 'policy.json'
    policy.write_text('{"fictional_policy":true}')
    policy.chmod(0o600)
    frozen = tmp_path / 'source-freeze.json'
    frozen.write_text('{}')
    frozen.chmod(0o600)
    called = []

    def load_config(path):
        called.append('config')
        return json.loads(path.read_text())

    def read_policy():
        called.append('policy')
        return json.loads(policy.read_text())

    def eligible(*args):
        called.append('eligible')
        return {'fake-room': {'registered_business_role': 'supplier'}}

    def context(state, config, policy, now):
        eligible(state, config, policy, now)
        return {'channels': {'fake-room': '虚构资料群'}, '_learning': {},
                '_supplier_context': {'channels': {'fake-room': '虚构资料群'}}}

    def authorized(db, ctx):
        called.append('authorized')
        yield {'evidence_id': 'FICTITIOUS-1', 'chatroom_id': 'fake-room', 'price_kind': 'not_price'}

    def verified(*args):
        return True

    namespace = {}
    exec('def scope_chatrooms(db, ctx, scope):\n'
         '    return sorted({r["chatroom_id"] for r in kb_authorized(db, ctx)})\n', namespace)

    class Rejected(ValueError):
        pass

    def source_record(raw, auth):
        called.append('source_record')
        if raw.get('deny'):
            raise Rejected(raw['deny'])
        return {'inbound_id': raw['id']}

    def collect_groups(records, ledger, **kwargs):
        called.append(('collect_groups', records, ledger, kwargs))
        return {'fake-key': {'source_version': 'fake-version', 'records': records}}, {}

    def boundary(*args):
        called.append('boundary')

    def require_new(*args):
        called.append('freeze')

    modules = SimpleNamespace(
        api=SimpleNamespace(load_config=load_config), policy=SimpleNamespace(read_policy=read_policy),
        history=SimpleNamespace(CONFIG=config, eligible=eligible, kb_context=context, kb_authorized=authorized),
        business=SimpleNamespace(verified_evidence=verified),
        extract=SimpleNamespace(scope_chatrooms=namespace['scope_chatrooms']),
        flag_scope=lambda: 'all',
        runtime=SimpleNamespace(authority=lambda history: ({'owner': '虚构负责人'}, {}, 'fake-sha'),
                                boundary=boundary, MAX_SOURCE_ROWS=500000),
        alibaba_history=SimpleNamespace(source_record=source_record, Rejected=Rejected, PARSER='fake-parser'),
        freeze=SimpleNamespace(checked=lambda *args: frozenset(), sha=lambda value: 'fake-sha',
                               require_new_source=require_new),
        core=SimpleNamespace(collect_groups=collect_groups), pins={}, called=called)
    return modules


def test_fake_original_functions_and_end_recheck(tmp_path, monkeypatch, only_tmp):
    modules = fake_originals(tmp_path)

    @contextmanager
    def loaded():
        yield modules

    monkeypatch.setattr(runtime, 'load_originals', loaded)
    with runtime.original_authorization(tmp_path, tmp_path, 1000) as auth:
        extract, ctx, rows = auth.wechat_rows(None, None)
        assert extract is modules.extract and len(rows) == 1
        assert ctx['channels'] == {'fake-room': '虚构资料群'}
        assert auth.alibaba_record({'id': 'FICTITIOUS-2'}) == {'inbound_id': 'FICTITIOUS-2'}
    assert modules.called.count('boundary') == 2
    assert modules.called.count('config') == 2
    assert modules.called.count('policy') == 2
    assert {'config.json', 'policy.json', 'source-freeze.json'} <= {path.name for path in only_tmp}


@pytest.mark.parametrize('error', [ValueError('private configuration detail'), TypeError('fictional'),
                                 sqlite3.OperationalError('fictional'), ValueError({'fictional': True})])
def test_swallowed_authorization_exception_is_sticky(error, only_tmp):
    def swallowed():
        try:
            raise error
        except Exception:
            return False

    with pytest.raises(RuntimeError, match='^AUTH_WECHAT_EXCEPTION$'):
        with runtime.sticky_exceptions([swallowed]):
            assert swallowed() is False


def test_known_authorization_denial_remains_closed(only_tmp):
    def closed():
        try:
            raise ValueError('business_history_current_binding_required')
        except ValueError:
            return None

    with runtime.sticky_exceptions([closed]):
        assert closed() is None


def test_user_writable_authorization_module_is_rejected_before_import(tmp_path, only_tmp):
    candidate = tmp_path / 'fictional_authorization.py'
    candidate.write_text('raise AssertionError("虚构未受审源码不能执行")\n')
    with pytest.raises(RuntimeError, match='^AUTH_SOURCE_PATH$'):
        runtime.code(candidate, runtime.hashlib.sha256(candidate.read_bytes()).hexdigest())


def test_config_bad_mode_stops_before_original_chmod(tmp_path, only_tmp):
    modules = fake_originals(tmp_path)
    modules.history.CONFIG.chmod(0o640)
    with pytest.raises(RuntimeError, match='^AUTH_CONFIG_MODE$'):
        runtime.OriginalAuthorization(modules, tmp_path, tmp_path, 1000)
    assert modules.called == []
    assert modules.history.CONFIG.stat().st_mode & 0o777 == 0o640


def test_missing_config_and_import_failure_do_not_escape(tmp_path, monkeypatch, only_tmp):
    modules = fake_originals(tmp_path)
    modules.history.CONFIG.unlink()

    @contextmanager
    def loaded():
        yield modules

    monkeypatch.setattr(runtime, 'load_originals', loaded)
    with pytest.raises(RuntimeError, match='^AUTH_RUNTIME_FAILED$'):
        with runtime.original_authorization(tmp_path, tmp_path, 1000):
            pytest.fail('缺配置仍生成授权')

    @contextmanager
    def broken():
        raise ImportError('虚构配置细节不可外传')
        yield

    monkeypatch.setattr(runtime, 'load_originals', broken)
    with pytest.raises(RuntimeError, match='^AUTH_RUNTIME_FAILED$'):
        with runtime.original_authorization(tmp_path, tmp_path, 1000):
            pytest.fail('导入失败仍生成授权')


def test_authorization_change_during_review_stops(tmp_path, monkeypatch, only_tmp):
    modules = fake_originals(tmp_path)

    @contextmanager
    def loaded():
        yield modules

    monkeypatch.setattr(runtime, 'load_originals', loaded)
    with pytest.raises(RuntimeError, match='^AUTH_CONFIGURATION_CHANGED$'):
        with runtime.original_authorization(tmp_path, tmp_path, 1000):
            modules.history.CONFIG.write_text('{"fictional":false}')


def test_alibaba_normal_exclusion_and_unknown_error(tmp_path, only_tmp):
    modules = fake_originals(tmp_path)
    auth = runtime.OriginalAuthorization(modules, tmp_path, tmp_path, 1000)
    assert auth.alibaba_record({'id': 'FAKE', 'deny': 'account_not_registered'}) is None
    with pytest.raises(RuntimeError, match='^AUTH_ALIBABA_EXCEPTION$'):
        auth.alibaba_record({'id': 'FAKE', 'deny': 'private unknown source detail'})


def test_alibaba_snapshot_reconcile_and_original_freeze(tmp_path, only_tmp):
    modules = fake_originals(tmp_path)
    auth = runtime.OriginalAuthorization(modules, tmp_path, tmp_path, 1000)

    class Database:
        def __init__(self, rows):
            self.rows = rows

        def execute(self, sql):
            return self.rows

    inbound = Database([{'id': 'FAKE-1'}, {'id': 'FAKE-2', 'deny': 'account_not_registered'}])
    ledger = Database([{'inbound_id': 'FAKE-1', 'status': 'indexed'},
                       {'inbound_id': 'FAKE-2', 'status': 'indexed'}])
    assert list(auth.alibaba_groups(inbound, ledger)) == ['fake-key']
    call = next(call for call in modules.called if isinstance(call, tuple))
    assert call[1] == [{'inbound_id': 'FAKE-1'}]
    assert call[2] == [{'inbound_id': 'FAKE-1', 'status': 'indexed'}]
    assert call[3]['owner'] == '虚构负责人' and call[3]['parser'] == 'fake-parser'
    assert modules.called[-1] == 'freeze'

    def frozen(*args):
        raise ValueError('old_source_version_model_calls_frozen')

    modules.freeze.require_new_source = frozen
    assert auth.alibaba_groups(inbound, ledger) == {}

    with pytest.raises(RuntimeError, match='^AUTH_ALIBABA_RECONCILE$'):
        auth.alibaba_groups(inbound, Database([{'inbound_id': 'UNSEEN', 'status': 'indexed'}]))


def test_deeper_optional_grant_cannot_swallow_authorization_error(tmp_path, only_tmp):
    modules = fake_originals(tmp_path)

    def owner_pairs():
        try:
            raise OSError('虚构配置不可读，不能透传')
        except OSError:
            return []

    def authority(history):
        owner_pairs()
        return {'owner': '虚构负责人'}, {}, 'fake-sha'

    modules.grant = SimpleNamespace(owner_pairs=owner_pairs)
    modules.runtime.authority = authority
    with pytest.raises(RuntimeError, match='^AUTH_WECHAT_EXCEPTION$'):
        runtime.OriginalAuthorization(modules, tmp_path, tmp_path, 1000)


def test_fixed_authorization_binding_uses_immutable_and_rechecks(tmp_path, monkeypatch, only_tmp):
    path = tmp_path / 'binding.sqlite3'
    path.write_bytes(b'fictional authorization metadata')
    path.chmod(0o600)
    monkeypatch.setattr(runtime, 'AUTH_BINDING_PATH', path)
    calls = []
    authorizers = []
    marker = SimpleNamespace(set_authorizer=authorizers.append)

    def native(database, **kwargs):
        calls.append((database, kwargs))
        return marker

    monkeypatch.setattr(sqlite3, 'connect', native)
    with runtime.authorization_connections():
        assert sqlite3.connect('file:' + str(path) + '?mode=ro', uri=True, timeout=3) is marker
    assert calls == [('file:' + str(path) + '?mode=ro&immutable=1', {'uri': True, 'timeout': 3})]
    assert sqlite3.connect is native
    authorize = authorizers[0]
    assert authorize(sqlite3.SQLITE_SELECT, None, None, None, None) == sqlite3.SQLITE_OK
    assert authorize(sqlite3.SQLITE_READ, 'binding', 'owner', None, None) == sqlite3.SQLITE_OK
    assert authorize(sqlite3.SQLITE_READ, 'messages', 'content', None, None) == sqlite3.SQLITE_DENY
    assert authorize(sqlite3.SQLITE_UPDATE, 'binding', 'owner', None, None) == sqlite3.SQLITE_DENY

    with pytest.raises(RuntimeError, match='^AUTH_BINDING_CHANGED$'):
        with runtime.authorization_connections():
            sqlite3.connect('file:' + str(path) + '?mode=ro', uri=True, timeout=3)
            path.write_bytes(b'fictional authorization changed')


def test_binding_wal_frames_block_before_native_connect(tmp_path, monkeypatch, only_tmp):
    path = tmp_path / 'binding.sqlite3'
    path.write_bytes(b'fictional authorization metadata')
    Path(str(path) + '-wal').write_bytes(b'x' * 33)
    monkeypatch.setattr(runtime, 'AUTH_BINDING_PATH', path)
    called = []
    monkeypatch.setattr(sqlite3, 'connect', lambda *args, **kwargs: called.append(True))
    with pytest.raises(RuntimeError, match='^AUTH_BINDING_NOT_QUIESCENT$'):
        with runtime.authorization_connections():
            sqlite3.connect('file:' + str(path) + '?mode=ro', uri=True, timeout=3)
    assert called == []


def test_binding_connection_cannot_read_messages_or_write(tmp_path, monkeypatch, only_tmp):
    path = tmp_path / 'binding.sqlite3'
    with closing(sqlite3.connect(path)) as db:
        db.execute('CREATE TABLE binding(session TEXT,account_id TEXT,owner TEXT,scope TEXT)')
        db.execute('INSERT INTO binding VALUES(?,?,?,?)', ('business', 'fake-account', 'fake-owner', 'fake-scope'))
        db.execute('CREATE TABLE messages(content TEXT)')
        db.execute('INSERT INTO messages VALUES(?)', ('虚构正文，不用于授权',))
        db.commit()
    path.chmod(0o600)
    monkeypatch.setattr(runtime, 'AUTH_BINDING_PATH', path)
    with runtime.authorization_connections(), closing(sqlite3.connect(
            'file:' + str(path) + '?mode=ro', uri=True, timeout=3)) as db:
        assert db.execute('SELECT session,account_id,owner,scope FROM binding').fetchone() == (
            'business', 'fake-account', 'fake-owner', 'fake-scope')
        with pytest.raises(sqlite3.DatabaseError):
            db.execute('SELECT content FROM messages').fetchall()
        with pytest.raises(sqlite3.DatabaseError):
            db.execute('INSERT INTO binding VALUES(?,?,?,?)', ('x', 'x', 'x', 'x'))
