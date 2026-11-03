"""All identities, text and sources in this suite are fictional and offline."""
import pytest


@pytest.fixture(autouse=True)
def offline(tmp_path, monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError('测试禁止连接网络')
    monkeypatch.setattr('socket.socket.connect', denied)
    monkeypatch.setenv('OPS_MEDIA_KEY_PATH', str(tmp_path / 'media.key'))
