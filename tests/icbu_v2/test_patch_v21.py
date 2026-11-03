"""补丁正反应用只在临时源码副本中进行，不依赖生产目录。"""
import hashlib
from pathlib import Path
import shutil
import subprocess


DEPLOY = Path(__file__).resolve().parents[2] / 'deploy' / 'icbu'
BASELINES = {
    'icbu_inbound_receiver.py': 'd2bc8fbb487ee1c4cf8ee3f13af31438dce3484773b44de84a8f48028234fe1b',
    'icbu_attachments.py': 'b76a97da8f478c6d5244c5a2cba4371cbf8c676136e5f2929afad8c9985efb37',
}


def test_patch_reconstructs_exact_v2_baselines_and_candidates(tmp_path):
    patch = (DEPLOY / 'receiver-v2.1.patch').read_bytes()
    for name in BASELINES:
        shutil.copyfile(DEPLOY / name, tmp_path / name)

    def apply(*options):
        result = subprocess.run(['patch', '--batch', '--fuzz=0', '-p1', *options],
                                cwd=tmp_path, input=patch, capture_output=True, timeout=10)
        assert result.returncode == 0, (result.stdout + result.stderr).decode('utf-8')
        assert 'offset' not in result.stdout.decode('utf-8')

    apply('-R')
    for name, expected in BASELINES.items():
        assert hashlib.sha256((tmp_path / name).read_bytes()).hexdigest() == expected
    apply('--dry-run')
    apply()
    for name in BASELINES:
        assert (tmp_path / name).read_bytes() == (DEPLOY / name).read_bytes()
    apply('--dry-run', '-R')
