"""安装清单仅生成和静态核对；不执行其中的 root 命令。"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shlex
import stat

import pytest


def tool():
    path = Path(__file__).resolve().parents[2] / 'deploy/kb/prepare_install_root.py'
    spec = importlib.util.spec_from_file_location('kb_prepare_root', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def bundle(tmp_path):
    tmp_path.chmod(0o700)
    source = tmp_path / "虚构配置 ' $(不执行) .json"
    source.write_text('{"虚构配置":true}')
    source.chmod(0o600)
    pin = hashlib.sha256(source.read_bytes()).hexdigest()
    files = [dict(source=str(source), target='/etc/imgw/kb-recovery-coordinator.json',
                  sha256=pin, owner='root', mode='0640')]
    output = tmp_path / 'commands'
    output.mkdir(mode=0o700)
    return tool(), files, output, pin


def payload(path):
    command = path.read_text().splitlines()[4]
    words = shlex.split(command)
    assert words[:2] == ['/opt/imgw/venv/bin/python', '-']
    return json.loads(words[2])


def test_generates_private_quoted_commands_without_execution(bundle, monkeypatch):
    module, files, directory, pin = bundle
    import subprocess
    monkeypatch.setattr(subprocess, 'run', lambda *a, **kw: pytest.fail('生成阶段不能执行命令'))
    module.generate_commands(files, directory, pin)
    assert sorted(path.name for path in directory.iterdir()) == ['install-root.sh', 'rollback-root.sh']
    for name, action in [('install-root.sh', 'install'), ('rollback-root.sh', 'rollback')]:
        path = directory / name
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        spec = payload(path)
        assert spec['action'] == action
        assert spec['files'] == files
        assert spec['config_sha'] == pin
        assert spec['backup'] == '/var/backups/imgw-kb-prepare-commands'
    compile(module.ROOT_PROGRAM, '<虚构待审root程序>', 'exec')


def test_repeating_same_generation_preserves_bytes(bundle):
    module, files, directory, pin = bundle
    module.generate_commands(files, directory, pin)
    before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in directory.iterdir()}
    module.generate_commands(files, directory, pin)
    assert before == {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in directory.iterdir()}


def test_root_script_has_stop_first_and_never_start_or_enable(bundle):
    module, files, directory, pin = bundle
    module.generate_commands(files, directory, pin)
    raw = (directory / 'rollback-root.sh').read_text()
    assert "['systemctl', 'stop', service]" in raw
    assert "['systemctl', 'daemon-reload']" in raw
    assert "'--installed'" in raw
    assert "['systemctl', 'start'" not in raw
    assert "['systemctl', 'enable'" not in raw
    assert "if record['ledger']:\n            continue" in raw
    assert '状态目录已有内容，禁止覆盖旧账本' in raw
    assert "open('/dev/tty', 'r') as terminal" in raw
    assert '只保全新状态目录' in raw and '无在途请求且原库已保全' in raw
    assert raw.index("subprocess.run(['systemctl', 'stop', service]") < raw.index("open('/dev/tty', 'r') as terminal")


@pytest.mark.parametrize('field,value', [
    ('target', '/etc/imgw/../../etc/passwd'), ('target', '/opt/imgw/unrelated.py'),
    ('target', '/var/lib/imgw/ops-app/kb-recovery-coordinator/progress.sqlite3'),
    ('owner', 'imgw'), ('mode', '0666'), ('sha256', '0' * 64),
])
def test_rejects_unreviewed_target_or_permissions(bundle, field, value):
    module, files, directory, pin = bundle
    files[0][field] = value
    with pytest.raises(ValueError):
        module.generate_commands(files, directory, pin)
    assert not list(directory.iterdir())


def test_rejects_duplicate_targets(bundle):
    module, files, directory, pin = bundle
    with pytest.raises(ValueError, match='重复'):
        module.generate_commands(files * 2, directory, pin)


def test_rejects_unbound_config_sha(bundle):
    module, files, directory, pin = bundle
    with pytest.raises(ValueError, match='配置 SHA'):
        module.generate_commands(files, directory, '0' * 64)


@pytest.mark.parametrize('link', ['symbolic', 'hard'])
def test_rejects_linked_sources(bundle, link):
    module, files, directory, pin = bundle
    source = Path(files[0]['source'])
    alias = source.parent / 'alias.json'
    if link == 'symbolic':
        alias.symlink_to(source)
    else:
        os.link(source, alias)
    files[0]['source'] = str(alias)
    with pytest.raises(ValueError):
        module.generate_commands(files, directory, pin)


def test_rejects_public_source_or_output_directory(bundle):
    module, files, directory, pin = bundle
    Path(files[0]['source']).chmod(0o644)
    with pytest.raises(ValueError, match='0600'):
        module.generate_commands(files, directory, pin)
    Path(files[0]['source']).chmod(0o600)
    directory.chmod(0o755)
    with pytest.raises(ValueError, match='0700'):
        module.generate_commands(files, directory, pin)


def test_rejects_changed_existing_command(bundle):
    module, files, directory, pin = bundle
    module.generate_commands(files, directory, pin)
    (directory / 'install-root.sh').write_text('虚构的已变更清单')
    with pytest.raises(ValueError, match='已有命令清单变化'):
        module.generate_commands(files, directory, pin)


def test_approved_calls_owner_and_mode_are_fixed(bundle):
    module, files, directory, pin = bundle
    extra = dict(files[0], target=module.STATE + '/approved-calls.sqlite3', owner='imgw', mode='0600')
    module.generate_commands(files + [extra], directory, pin)
    assert payload(directory / 'install-root.sh')['files'][1] == extra


def test_root_precheck_pins_original_and_candidate_overlay_and_production_sources(bundle):
    module, files, directory, pin = bundle
    module.generate_commands(files, directory, pin, valid_until=2000000000)
    spec = payload(directory / 'install-root.sh')
    release = json.loads((module.HERE / 'coordinator-baseline.json').read_text())
    baseline = json.loads((module.HERE / 'baseline.json').read_text())
    assert spec['production_source_sha256'] == release['production_source_sha256']
    for name, expected in baseline['sha256'].items():
        target = str(Path(baseline['base']) / name)
        assert expected in spec['overlay_before'][target]
        assert release['installed_overlay_sha256'][target] in spec['overlay_before'][target]
        assert target not in spec['overlay_allow_absent']
    assert spec['overlay_allow_absent'] == [
        '/opt/imgw/wechat-kb-dual-live-v1/video_knowledge/kb_source_gate.py']
    assert spec['valid_until'] == 2000000000
    program = module.ROOT_PROGRAM
    assert program.index("time.time() <= spec['valid_until']") < program.index('backup.mkdir(mode=0o700)')
    assert program.index("spec['production_source_sha256'].items()") < program.index('backup.mkdir(mode=0o700)')
    assert "spec['valid_until'] is not None" in program


@pytest.mark.parametrize('invalid', [0, -1, float('inf'), float('nan'), '2000000000', True])
def test_rejects_invalid_observation_deadline(bundle, invalid):
    module, files, directory, pin = bundle
    with pytest.raises(ValueError, match='有效期'):
        module.generate_commands(files, directory, pin, valid_until=invalid)
