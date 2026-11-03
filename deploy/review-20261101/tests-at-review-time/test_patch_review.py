import hashlib
from pathlib import Path
import shutil
import subprocess

from conftest import ROOT


def test_review_patch_reverses_to_every_reviewed_baseline(candidate, tmp_path):
    stage = tmp_path / 'reverse'
    shutil.copytree(candidate, stage)
    patch = (ROOT / 'deploy/review-20261101/fixes.patch').read_bytes()
    for options in (['--dry-run'], []):
        result = subprocess.run(['patch', '--batch', '--fuzz=0', '-R', '-p1', '-d', str(stage), *options],
                                input=patch, capture_output=True)
        assert result.returncode == 0 and b'offset' not in result.stdout and b'fuzz' not in result.stdout
    for line in (ROOT / 'deploy/review-20261101/baseline.sha256').read_text().splitlines():
        digest, rel = line.split('  ', 1)
        assert hashlib.sha256((stage / rel).read_bytes()).hexdigest() == digest


def test_supplied_v22_patch_matches_supplied_v21_and_v22_files(tmp_path):
    for name in ('icbu_inbound_receiver.py', 'icbu_attachments.py'):
        shutil.copyfile(ROOT / 'deploy/icbu' / name, tmp_path / name)
    result = subprocess.run(['patch', '--batch', '--fuzz=0', '-p1', '-d', str(tmp_path)],
        input=(ROOT / 'deploy/icbu/receiver-v2.2.patch').read_bytes(), capture_output=True)
    assert result.returncode == 0 and b'offset' not in result.stdout
    for name in ('icbu_inbound_receiver.py', 'icbu_attachments.py'):
        assert (tmp_path / name).read_bytes() == (ROOT / 'deploy/icbu/v2.2' / name).read_bytes()


def test_shell_candidates_parse_without_running_installation(candidate):
    for name in ('wamedia_install.sh', 'nas_imgw_archive_mount.sh', 'icbu_attachments_nas.sh'):
        subprocess.run(['bash', '-n', str(candidate / 'scripts' / name)], check=True)
