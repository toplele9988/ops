"""Minimal read-only capability for the existing WTAPI metadata client.

The builder reads credentials only when explicitly called. Importing this
module never imports WTAPI, reads production configuration, or makes requests.
The returned object's entire public surface is the two metadata reads below;
it does not retain the full client as an accessible attribute.
"""
import importlib
import json
import os
from pathlib import Path
import re
import stat

from .errors import RefreshFailed
from .sources import CONFIG_PATH

_ORIGIN = 'https://wx.chuapi.com'
_CONFIG_ERROR = '通讯录刷新暂不可用：无法读取微信配置或缺少有效的 app_id、token_file'
_TOKEN_ERROR = '通讯录刷新暂不可用：无法读取有效的微信只读接口凭据'
_MODULE_ERROR = '通讯录刷新暂不可用：找不到只读微信客户端，请检查 sys.path 是否包含 /opt/imgw/wechat'
_CLIENT_ERROR = '通讯录刷新暂不可用：无法初始化只读微信客户端'
_CALL_ERROR = '只读微信接口未给出可确认结果；本次不重试'
_TIMEOUT_ERROR = '只读微信接口超时，结果不明；本次不重试'
_APP_ID = re.compile(r'[A-Za-z0-9_-]{1,128}\Z')
_GROUP_ID = re.compile(r'[A-Za-z0-9_-]{1,128}@chatroom\Z')


def _read_file(path, limit, *, private=False):
    # Match group_onboarding._client's token-file handling without its imports
    # or any chmod. Nonblocking open also avoids hanging on a misconfigured FIFO.
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_size > limit
                or private and stat.S_IMODE(info.st_mode) & 0o077):
            raise ValueError
        value = stream.read(limit + 1)
    if len(value) > limit:
        raise ValueError
    return value.decode('utf8')


def _credentials(config_path):
    # Project just the app ID and token-file locator; never keep other runtime
    # authority, group registration, or source configuration in this adapter.
    try:
        config_path = Path(config_path)
        config = json.loads(_read_file(config_path, 2 * 1024 * 1024))
        app_id, token_file = config.get('app_id'), config.get('token_file')
        valid = (isinstance(app_id, str) and _APP_ID.fullmatch(app_id)
                 and isinstance(token_file, str) and bool(token_file))
    except Exception:
        valid = False
    if not valid:
        # Raise outside the handler: even __context__ contains no source error.
        raise RefreshFailed(_CONFIG_ERROR, 503)
    del config
    try:
        token_path = Path(token_file).expanduser()
        if not token_path.is_absolute():
            token_path = config_path.parent / token_path
        token = _read_file(token_path, 8192, private=True).strip()
        valid = bool(token) and not any(character.isspace() for character in token)
    except Exception:
        valid = False
    if not valid:
        raise RefreshFailed(_TOKEN_ERROR, 503)
    return app_id, token


def _call_once(function, *args):
    try:
        return function(*args)
    except TimeoutError:
        timed_out = True
    except Exception:
        timed_out = False
    if timed_out:
        raise TimeoutError(_TIMEOUT_ERROR)
    raise RefreshFailed(_CALL_ERROR)


def _readonly_client(client, app_id):
    # Capture only the permitted bound methods, with no forwarding __getattr__,
    # __dict__, token/app attributes, or public handle to the original client.
    # This is a narrow application API, not a Python reflection sandbox.
    fetch, brief = client.fetch_contacts_list, client.get_brief_info
    if not callable(fetch) or not callable(brief):
        raise ValueError

    class ReadonlyRefreshClient:
        __slots__ = ()

        def fetch_contacts_list(self):
            return _call_once(fetch, app_id)

        def get_brief_info(self, wxids):
            batch = list(wxids) if isinstance(wxids, (list, tuple)) else []
            if (not 1 <= len(batch) <= 50
                    or any(not isinstance(identity, str) or not _GROUP_ID.fullmatch(identity)
                           for identity in batch)
                    or len(set(batch)) != len(batch)):
                raise RefreshFailed('群简要信息请求须包含 1 至 50 个不同的准确群 ID')
            return _call_once(brief, app_id, batch)

    return ReadonlyRefreshClient()


def build_readonly_refresh_client(config_path=CONFIG_PATH):
    """Create a two-method client using the existing token_file convention.

    Relative token paths are resolved beside api_config.json, as in the live
    launcher/group_onboarding helper. The fixed metadata origin and 20-second
    per-call ceiling match WTAPIClient's read-only management transport. There
    is deliberately no import or reuse of onboarding, sending, or discovery
    workers, and no inline-token fallback absent from those existing callers.
    """
    app_id, token = _credentials(config_path)
    try:
        client_type = importlib.import_module('wtapi_client').WTAPIClient
    except Exception:
        client_type = None
    if client_type is None:
        raise RefreshFailed(_MODULE_ERROR, 503)
    try:
        client = client_type(token=token, base_url=_ORIGIN, timeout=20)
        wrapper = _readonly_client(client, app_id)
    except Exception:
        wrapper = None
    if wrapper is None:
        raise RefreshFailed(_CLIENT_ERROR, 503)
    return wrapper
