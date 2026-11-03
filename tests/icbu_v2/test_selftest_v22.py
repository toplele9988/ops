"""故障演练只允许显式临时目录，任何失败都不重发请求。"""
import io

import pytest

from test_selftest import TEST_TOKEN, selftest


def test_v22_cli_keeps_previous_sequences_and_adds_v22(monkeypatch):
    calls = []
    monkeypatch.setattr(selftest.sys, 'stdin', io.StringIO(TEST_TOKEN + '\n'))
    monkeypatch.setattr(selftest, 'Client', lambda *args: object())
    monkeypatch.setattr(selftest, 'run', lambda client: calls.append('v2'))
    monkeypatch.setattr(selftest, 'run_v21', lambda client: calls.append('v21'))
    monkeypatch.setattr(selftest, 'run_v22', lambda client: calls.append('v22'))
    assert selftest.main(['--url', 'http://127.0.0.1:18888', '--v21', '--v22']) == 0
    assert calls == ['v2', 'v21', 'v22']


@pytest.mark.parametrize('url,options', [
    ('http://127.0.0.1:8791', []), ('http://192.168.1.22:18888', ['--allow-lan']),
])
def test_v22_refuses_production_port_and_lan_before_reading_token(url, options, monkeypatch):
    class Unreadable:
        def readline(self, *args):
            pytest.fail('不应读取令牌')
    monkeypatch.setattr(selftest.sys, 'stdin', Unreadable())
    assert selftest.main(['--url', url, '--v22', *options]) == 1


@pytest.mark.parametrize('path', ['', '/var/lib/imgw/fictional', '/tmp/../var/fictional'])
def test_fault_directory_must_be_explicit_and_temporary(path, monkeypatch):
    monkeypatch.setenv('ICBU_V22_TEST_DIR', path)
    with pytest.raises(selftest.CheckFailed):
        with selftest.StorageFault():
            pytest.fail('不安全目录不能进入演练')


@pytest.mark.parametrize('unsafe', ['symlink', 'permissions'])
def test_fault_directory_rejects_symlinks_and_weak_permissions(tmp_path, monkeypatch, unsafe):
    directory = tmp_path / 'fault'
    if unsafe == 'symlink':
        directory.symlink_to(tmp_path, target_is_directory=True)
    else:
        directory.mkdir(mode=0o755)
        directory.chmod(0o755)
    monkeypatch.setenv('ICBU_V22_TEST_DIR', str(directory))
    with pytest.raises(selftest.CheckFailed):
        with selftest.StorageFault():
            pytest.fail('不安全目录不能进入演练')


def test_fault_switch_does_not_overwrite_or_remove_existing_marker(tmp_path, monkeypatch):
    tmp_path.chmod(0o700)
    marker = tmp_path / 'attachments-unavailable'
    marker.write_text('fictional other test')
    monkeypatch.setenv('ICBU_V22_TEST_DIR', str(tmp_path))
    with pytest.raises(selftest.CheckFailed):
        with selftest.StorageFault() as fault:
            fault.unavailable()
    assert marker.read_text() == 'fictional other test'


def test_failed_network_check_restores_own_marker_without_retry(tmp_path, monkeypatch):
    tmp_path.chmod(0o700)
    monkeypatch.setenv('ICBU_V22_TEST_DIR', str(tmp_path))
    calls = []
    class Client:
        def request(self, method, path, *args):
            calls.append((method, path))
            if len(calls) == 1:
                return 200, dict(ok=True, service='icbu-inbound', attachments_storage='ok')
            raise selftest.CheckFailed('虚构结果未知，没有自动重试')
    with pytest.raises(selftest.CheckFailed, match='没有自动重试'):
        selftest.run_v22(Client(), lambda value: None)
    assert len(calls) == 2 and not (tmp_path / 'attachments-unavailable').exists()
