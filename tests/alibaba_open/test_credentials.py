import json
import os
from pathlib import Path

import pytest

from console.alibaba_open.app_credentials import Credentials
from console.alibaba_open.errors import OpenError


def credentials(tmp_path, mode=0o640, content=None):
    path = tmp_path / 'credentials.json'
    path.write_text(json.dumps(content or dict(schema='alibaba-open-v1', app_key='fiction-key',
                                             app_secret='fiction-secret')))
    path.chmod(mode)
    return Credentials(path, expected_uid=os.getuid(), expected_gid=os.getgid())


def test_status_does_not_read_values(tmp_path, monkeypatch):
    config = credentials(tmp_path)
    monkeypatch.setattr(Path, 'open', lambda *a, **k: pytest.fail('status must only stat'))
    assert config.status()['configured']


def test_load_is_lazy_and_validates_schema(tmp_path):
    config = credentials(tmp_path)
    assert config.load() == dict(app_key='fiction-key', app_secret='fiction-secret')
    config.path.write_text('{"schema":"wrong","app_key":"SENTINEL_SECRET"}')
    with pytest.raises(OpenError) as error:
        config.load()
    assert 'SENTINEL' not in str(error.value)


@pytest.mark.parametrize('mode', [0o600, 0o644, 0o660, 0o777])
def test_permissions_rejected(tmp_path, mode):
    config = credentials(tmp_path, mode)
    assert not config.status()['configured']
    with pytest.raises(OpenError):
        config.load()


def test_missing_file_and_owner(tmp_path):
    config = Credentials(tmp_path / 'missing', expected_gid=os.getgid())
    assert not config.status()['configured']
    with pytest.raises(OpenError):
        config.load()
    config = credentials(tmp_path)
    config.expected_uid = os.getuid() + 1
    assert not config.status()['configured']


def test_symlink_and_hardlink_rejected(tmp_path):
    config = credentials(tmp_path)
    linked = tmp_path / 'linked'
    linked.symlink_to(config.path)
    assert not Credentials(linked, expected_uid=os.getuid(), expected_gid=os.getgid()).status()['configured']
    os.link(config.path, tmp_path / 'hardlink')
    assert not config.status()['configured']


def test_replaced_file_and_size_rejected(tmp_path, monkeypatch):
    config = credentials(tmp_path)
    real_open = os.open

    def replace(path, flags, **kwargs):
        if path == config.path.name:
            replacement = tmp_path / 'replacement'
            replacement.write_text('{}')
            replacement.chmod(0o640)
            replacement.replace(config.path)
        return real_open(path, flags, **kwargs)

    monkeypatch.setattr(os, 'open', replace)
    with pytest.raises(OpenError):
        config.load()
    config.path.write_text('x' * 8193)
    assert not config.status()['configured']


def test_symlink_parent_rejected_before_read(tmp_path):
    folder = tmp_path / 'real'
    folder.mkdir()
    config = credentials(folder)
    alias = tmp_path / 'alias'
    alias.symlink_to(folder, target_is_directory=True)
    config = Credentials(alias / config.path.name, expected_uid=os.getuid(), expected_gid=os.getgid())
    assert not config.status()['configured']
    with pytest.raises(OpenError):
        config.load()


def test_parent_replacement_cannot_redirect_load(tmp_path, monkeypatch):
    folder = tmp_path / 'real'
    folder.mkdir()
    config = credentials(folder)
    original_check = config._check

    def swap_parent(info):
        original_check(info)
        if folder.exists() and not folder.is_symlink():
            folder.rename(tmp_path / 'old')
            folder.symlink_to(tmp_path / 'old', target_is_directory=True)

    monkeypatch.setattr(config, '_check', swap_parent)
    with pytest.raises(OpenError):
        config.load()
