"""Generate unit candidates in a temporary directory; never install or start them."""
from pathlib import Path
import subprocess

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / 'deploy/whatsapp/wamedia_service.sh'


def test_service_candidates_are_restricted_and_mount_is_soft(tmp_path):
    subprocess.run(['bash', '-n', str(SCRIPT)], check=True)
    output = tmp_path / 'candidates'
    command = ['bash', str(SCRIPT), str(output), '/opt/fictional-console']
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    unit = (output / 'imgw-wamedia.service').read_text()
    assert 'User=imgw\nGroup=imgw' in unit
    assert 'ProtectSystem=strict' in unit
    assert 'ReadOnlyPaths=-/var/lib/imgw/whatsapp' in unit
    assert [line for line in unit.splitlines() if line.startswith('ReadWritePaths=')] == [
        'ReadWritePaths=/var/lib/imgw/ops-app',
        'ReadWritePaths=-/mnt/nas229/edge/imgw-archive/whatsapp']
    assert 'Wants=' in unit and 'After=' in unit
    for hard in ('Requires=', 'RequiresMountsFor=', 'BindsTo=', 'ExecStartPre='):
        assert hard not in unit
    assert 'IMGW_WA_MEDIA_ROOT=/mnt/nas229/edge/imgw-archive/whatsapp' in unit
    assert 'IMGW_WA_MEDIA_MOUNT=/mnt/nas229/edge/imgw-archive' in unit
    assert '-m console.wamedia.ingest --bridge-db ' in unit
    assert '--once' not in unit
    dropin = (output / '70-wamedia.conf').read_text()
    assert 'IMGW_WA_MEDIA_DB=/var/lib/imgw/ops-app/whatsapp-media.sqlite3' in dropin
    assert 'sudo install' in result.stdout and '尚未安装或启动服务' in result.stdout
    before = unit, dropin
    again = subprocess.run(command, capture_output=True, text=True)
    assert again.returncode == 1 and '已存在' in again.stderr
    assert ((output / 'imgw-wamedia.service').read_text(), (output / '70-wamedia.conf').read_text()) == before


@pytest.mark.parametrize('code_root', ['/opt/../fictional', '/opt/fictional%h', '/opt/fictional\nExecStart=bad'])
def test_generator_rejects_unit_or_path_injection(tmp_path, code_root):
    output = tmp_path / 'candidates'
    result = subprocess.run(['bash', str(SCRIPT), str(output), code_root], capture_output=True, text=True)
    assert result.returncode == 2
    assert not output.exists()
