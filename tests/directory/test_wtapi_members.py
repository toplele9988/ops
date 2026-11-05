"""Fictional credentials and fake HTTP responses only."""
import io
import json
import sys
from types import SimpleNamespace

import pytest

from console.directory import wtapi_source as source
from console.directory.errors import RefreshFailed

GROUP = 'fiction-member-room@chatroom'
SECRET = 'fiction-members-only-secret'


@pytest.fixture
def env(tmp_path, monkeypatch):
    token = tmp_path / 'token'
    token.write_text(SECRET)
    token.chmod(0o600)
    config = tmp_path / 'config.json'
    config.write_text(json.dumps(dict(app_id='fiction-app', token_file='token')))
    e = SimpleNamespace(config=config, calls=[], payload={'ret': 200, 'data': {'memberList': []}}, error=None)
    class Response(io.BytesIO):
        headers = {}
        def getcode(self):
            return 200
        def geturl(self):
            return source._ORIGIN + '/finder/v2/api/group/getChatroomMemberList'
    class Opener:
        def open(self, request, timeout):
            e.calls.append((request, timeout))
            if e.error:
                raise e.error
            return Response(json.dumps(e.payload).encode())
    monkeypatch.setattr(source, 'build_opener', lambda *handlers: Opener())
    monkeypatch.setitem(sys.modules, 'wtapi_client', SimpleNamespace(
        _DeadlineHTTPSHandler=lambda deadline: object(),
        _read_before_deadline=lambda response, limit, deadline, timeout: response.read(limit + 1)))
    monkeypatch.setattr('socket.socket.connect', lambda *a: pytest.fail('network forbidden'))
    monkeypatch.setattr('socket.create_connection', lambda *a: pytest.fail('network forbidden'))
    return e


def test_members_capability_uses_only_fixed_read_endpoint_and_token_convention(env):
    before = env.config.read_bytes(), env.config.stat().st_mtime_ns
    client = source.build_readonly_members_client(env.config)
    assert {name for name in dir(client) if not name.startswith('__')} == {'get_chatroom_member_list'}
    assert not hasattr(client, '__dict__') and not hasattr(client, 'client')
    assert client.get_chatroom_member_list(GROUP) == env.payload
    request, timeout = env.calls[0]
    assert request.method == 'POST' and timeout == 20
    assert request.full_url == 'https://wx.chuapi.com/finder/v2/api/group/getChatroomMemberList'
    assert json.loads(request.data) == {'appId': 'fiction-app', 'chatroomId': GROUP}
    assert request.get_header('X-finder-token') == SECRET
    assert before == (env.config.read_bytes(), env.config.stat().st_mtime_ns)


@pytest.mark.parametrize('group', ['', 'fiction-peer', GROUP + '\n', 'bad space@chatroom', None, [], GROUP + '/other'])
def test_invalid_group_never_reaches_transport(env, group):
    with pytest.raises(RefreshFailed):
        source.build_readonly_members_client(env.config).get_chatroom_member_list(group)
    assert env.calls == []


@pytest.mark.parametrize('error', [TimeoutError(SECRET), RuntimeError(SECRET), ValueError(SECRET)])
def test_member_error_is_fixed_and_never_retried(env, error, capsys, caplog):
    env.error = error
    with pytest.raises((RefreshFailed, TimeoutError)) as caught:
        source.build_readonly_members_client(env.config).get_chatroom_member_list(GROUP)
    assert len(env.calls) == 1
    assert SECRET not in str(caught.value) + caplog.text + capsys.readouterr().out
    assert caught.value.__context__ is None and caught.value.__cause__ is None


def test_missing_deadline_helpers_is_unavailable_without_transport(env, monkeypatch):
    monkeypatch.setitem(sys.modules, 'wtapi_client', SimpleNamespace())
    with pytest.raises(RefreshFailed) as caught:
        source.build_readonly_members_client(env.config)
    assert caught.value.status_code == 503 and env.calls == []
