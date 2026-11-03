"""Only fictional files and fake WTAPI clients; never import the live client."""
import hashlib
import importlib
import json
from pathlib import Path
import sys
import traceback
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from console.directory.errors import RefreshFailed
from console.directory import wtapi_source as source

SECRET = 'fiction-only-token-canary'
GROUP = 'fiction-room@chatroom'


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr('socket.socket.connect', Mock(side_effect=AssertionError('network forbidden')))
    monkeypatch.setattr('socket.create_connection', Mock(side_effect=AssertionError('network forbidden')))


@pytest.fixture
def setup(tmp_path, monkeypatch):
    token_path = tmp_path / 'token.txt'
    token_path.write_text(SECRET + '\n', encoding='utf8')
    token_path.chmod(0o600)
    config_path = tmp_path / 'api_config.json'
    config_path.write_text(json.dumps(dict(app_id='fiction-app', token_file=token_path.name,
        base_url='https://untrusted.invalid/' + SECRET, send_enabled=True,
        groups={'fiction-room@chatroom': {'group_name': '虚构供应商'}})), encoding='utf8')
    calls = []
    options = []
    payloads = {'contacts': {'ret': 200, 'data': {'friends': [], 'chatrooms': [GROUP], 'ghs': []}},
                'brief': {'ret': 200, 'data': [{'userName': GROUP, 'nickName': 'SUP 虚构', 'remark': '虚构备注'}]}}

    class FakeWTAPIClient:
        def __init__(self, **kwargs):
            options.append(kwargs)
            self.token = kwargs['token']

        def fetch_contacts_list(self, app_id):
            calls.append(('contacts', app_id))
            value = payloads['contacts']
            if isinstance(value, BaseException):
                raise value
            return value

        def get_brief_info(self, app_id, wxids):
            calls.append(('brief', app_id, wxids))
            value = payloads['brief']
            if isinstance(value, BaseException):
                raise value
            return value

        def forbidden(self, *args, **kwargs):
            pytest.fail('write/download/detail method must not be called')

        send_text = send_file = send_document = download_attachment = forbidden
        get_chatroom_info = get_member_name = bind_group_memory = forbidden
        manage_group = activate = register_saved_metadata = forbidden

    monkeypatch.setitem(sys.modules, 'wtapi_client', SimpleNamespace(WTAPIClient=FakeWTAPIClient))
    return SimpleNamespace(config=config_path, token=token_path, calls=calls,
                           options=options, payloads=payloads, factory=FakeWTAPIClient)


def snapshot(path):
    return hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns, path.stat().st_mode


def assert_safe(exception, caplog, capsys):
    captured = capsys.readouterr()
    exposed = '\n'.join((str(exception), repr(exception),
        ''.join(traceback.format_exception(exception)), caplog.text, captured.out, captured.err))
    assert SECRET not in exposed
    assert exception.__context__ is None
    assert exception.__cause__ is None


def test_builder_reads_existing_token_file_convention_without_changing_files(setup):
    before = snapshot(setup.config), snapshot(setup.token)
    client = source.build_readonly_refresh_client(setup.config)
    assert setup.calls == []
    assert setup.options == [{'token': SECRET, 'base_url': 'https://wx.chuapi.com', 'timeout': 20}]
    assert (snapshot(setup.config), snapshot(setup.token)) == before
    assert client.fetch_contacts_list() == setup.payloads['contacts']
    assert client.get_brief_info([GROUP]) == setup.payloads['brief']
    assert setup.calls == [('contacts', 'fiction-app'), ('brief', 'fiction-app', [GROUP])]


def test_absolute_token_file_is_resolved_without_reading_other_config_fields(setup):
    setup.config.write_text(json.dumps({'app_id': 'fiction-app', 'token_file': str(setup.token),
                                       'token': 'ignored-fiction-token', 'owner_control': SECRET}))
    source.build_readonly_refresh_client(setup.config)
    assert setup.options[0]['token'] == SECRET


def test_module_import_is_lazy_and_has_no_configuration_or_client_side_effects(monkeypatch):
    forbidden = Mock(side_effect=AssertionError('import must not read credentials'))
    monkeypatch.setattr(source.os, 'open', forbidden)
    monkeypatch.setitem(sys.modules, 'wtapi_client', None)
    importlib.reload(source)
    forbidden.assert_not_called()


def test_type_and_attribute_whitelist_exposes_only_two_metadata_reads(setup):
    client = source.build_readonly_refresh_client(setup.config)
    assert {name for name in dir(client) if not name.startswith('__')} == {
        'fetch_contacts_list', 'get_brief_info'}
    assert type(client).__slots__ == ()
    for name in ('__dict__', 'client', '_client', 'token', '_token', 'app_id',
                 'send_text', 'send_file', 'send_document', 'download_attachment',
                 'get_chatroom_info', 'get_member_name', 'bind_group_memory',
                 'manage_group', 'activate', 'register_saved_metadata'):
        assert not hasattr(client, name)
    with pytest.raises(AttributeError):
        client.client = setup.factory
    assert SECRET not in repr(client)


@pytest.mark.parametrize('ids', [[], ['fiction-person'], [GROUP, GROUP], [None],
    'fiction-room@chatroom', {'id': GROUP}, ['bad space@chatroom'], ['a\n@chatroom'],
    ['a' * 129 + '@chatroom'], ['a@chatroom\n'], [f'fiction-{i}@chatroom' for i in range(51)]])
def test_brief_rejects_invalid_or_oversized_requests_before_transport(setup, ids):
    client = source.build_readonly_refresh_client(setup.config)
    with pytest.raises(RefreshFailed):
        client.get_brief_info(ids)
    assert setup.calls == []


def test_exact_fifty_group_batch_is_supported_once(setup):
    ids = tuple(f'fiction-{i}@chatroom' for i in range(50))
    client = source.build_readonly_refresh_client(setup.config)
    client.get_brief_info(ids)
    assert setup.calls == [('brief', 'fiction-app', list(ids))]


@pytest.mark.parametrize('config', [None, [], {}, {'app_id': 'fiction-app'},
    {'app_id': 'fiction-app', 'token': SECRET}, {'app_id': 'fiction-app', 'token_file': ''},
    {'app_id': 'fiction-app', 'token_file': 3}, {'app_id': 'bad app', 'token_file': 'token.txt'},
    {'app_id': None, 'token_file': 'token.txt'}])
def test_missing_or_invalid_config_is_fixed_chinese_503(setup, config, caplog, capsys):
    setup.config.write_text(json.dumps(config))
    with pytest.raises(RefreshFailed) as caught:
        source.build_readonly_refresh_client(setup.config)
    assert caught.value.status_code == 503
    assert str(caught.value) == source._CONFIG_ERROR
    assert setup.options == []
    assert_safe(caught.value, caplog, capsys)


@pytest.mark.parametrize('damage', ['missing', 'bad-json', 'bad-encoding', 'oversize', 'symlink', 'directory'])
def test_configuration_read_errors_do_not_echo_source_content(setup, damage, caplog, capsys):
    if damage == 'missing':
        setup.config.unlink()
    elif damage == 'bad-json':
        setup.config.write_text('{"' + SECRET)
    elif damage == 'bad-encoding':
        setup.config.write_bytes(SECRET.encode() + b'\xff')
    elif damage == 'oversize':
        setup.config.write_bytes(b' ' * (2 * 1024 * 1024 + 1))
    else:
        setup.config.unlink()
        if damage == 'symlink':
            setup.config.symlink_to(setup.token)
        else:
            setup.config.mkdir()
    with pytest.raises(RefreshFailed) as caught:
        source.build_readonly_refresh_client(setup.config)
    assert caught.value.status_code == 503
    assert str(caught.value) == source._CONFIG_ERROR
    assert_safe(caught.value, caplog, capsys)


@pytest.mark.parametrize('damage', ['missing', 'empty', 'whitespace', 'permissions', 'oversize',
                                   'bad-encoding', 'symlink', 'directory', 'fifo'])
def test_token_file_errors_are_fixed_and_never_chmod_or_echo_secrets(setup, damage, caplog, capsys):
    if damage == 'missing':
        setup.token.unlink()
    elif damage == 'empty':
        setup.token.write_text(' \n')
    elif damage == 'whitespace':
        setup.token.write_text(SECRET + ' invalid')
    elif damage == 'permissions':
        setup.token.chmod(0o644)
    elif damage == 'oversize':
        setup.token.write_bytes(b'a' * 8193)
    elif damage == 'bad-encoding':
        setup.token.write_bytes(SECRET.encode() + b'\xff')
    else:
        setup.token.unlink()
        if damage == 'symlink':
            setup.token.symlink_to(setup.config)
        elif damage == 'directory':
            setup.token.mkdir()
        else:
            source.os.mkfifo(setup.token, 0o600)
    with pytest.raises(RefreshFailed) as caught:
        source.build_readonly_refresh_client(setup.config)
    assert caught.value.status_code == 503
    assert str(caught.value) == source._TOKEN_ERROR
    if damage == 'permissions':
        assert setup.token.stat().st_mode & 0o777 == 0o644
    assert_safe(caught.value, caplog, capsys)


def test_missing_wtapi_module_explains_sys_path_without_leaking_secrets(setup, monkeypatch, caplog, capsys):
    monkeypatch.setitem(sys.modules, 'wtapi_client', None)
    with pytest.raises(RefreshFailed) as caught:
        source.build_readonly_refresh_client(setup.config)
    assert caught.value.status_code == 503
    assert '/opt/imgw/wechat' in str(caught.value)
    assert 'sys.path' in str(caught.value)
    assert_safe(caught.value, caplog, capsys)


@pytest.mark.parametrize('factory', [Mock(side_effect=ValueError(SECRET)), lambda **kwargs: object()])
def test_constructor_or_client_contract_failures_have_no_original_exception_chain(
        setup, monkeypatch, factory, caplog, capsys):
    monkeypatch.setitem(sys.modules, 'wtapi_client', SimpleNamespace(WTAPIClient=factory))
    with pytest.raises(RefreshFailed) as caught:
        source.build_readonly_refresh_client(setup.config)
    assert caught.value.status_code == 503
    assert str(caught.value) == source._CLIENT_ERROR
    assert_safe(caught.value, caplog, capsys)


@pytest.mark.parametrize('method', ['contacts', 'brief'])
@pytest.mark.parametrize('error', [RuntimeError(SECRET), ValueError(SECRET), OSError(SECRET), TimeoutError(SECRET)])
def test_call_errors_are_sanitized_and_never_retried(setup, method, error, caplog, capsys):
    setup.payloads[method] = error
    client = source.build_readonly_refresh_client(setup.config)
    expected = TimeoutError if isinstance(error, TimeoutError) else RefreshFailed
    with pytest.raises(expected) as caught:
        if method == 'contacts':
            client.fetch_contacts_list()
        else:
            client.get_brief_info([GROUP])
    assert len(setup.calls) == 1
    assert_safe(caught.value, caplog, capsys)


@pytest.mark.parametrize('payload', [None, {}, {'ret': 200}, {'ret': 200, 'data': []}, {'ret': 401, 'error': SECRET}])
def test_transport_leaves_unknown_or_missing_fields_for_refresh_validation_without_retry(setup, payload):
    setup.payloads['contacts'] = payload
    client = source.build_readonly_refresh_client(setup.config)
    assert client.fetch_contacts_list() is payload
    assert len(setup.calls) == 1
