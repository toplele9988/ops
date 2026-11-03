"""Apply and reverse the candidate patch in a temporary tree, never a deployment."""
from pathlib import Path
import hashlib
import shutil
import subprocess


def test_candidate_patch_round_trip(tmp_path):
    root = Path(__file__).resolve().parents[2]
    bundle = root/'src/console/inbox/patches'
    manifest = bundle/'CANDIDATE.sha256'
    candidates = []
    for line in manifest.read_text().splitlines():
        digest, name = line.split('  ', 1)
        source = root/'src'/name
        assert hashlib.sha256(source.read_bytes()).hexdigest() == digest
        dest = tmp_path/name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, dest)
        candidates.append(name)
    patch = str(bundle/'production-mount.patch')
    def apply(*args):
        result = subprocess.run(['patch', '--batch', '-p1', '-d', str(tmp_path), '-i', patch, *args], capture_output=True, text=True)
        assert result.returncode == 0, result.stdout + result.stderr
    apply('--reverse', '--dry-run')
    apply('--reverse')
    for line in (bundle/'SOURCE_BASELINE.sha256').read_text().splitlines():
        digest, name = line.split('  ', 1)
        assert hashlib.sha256((tmp_path/name).read_bytes()).hexdigest() == digest
    apply('--dry-run')
    apply()
    for name in candidates:
        assert (tmp_path/name).read_bytes() == (root/'src'/name).read_bytes()
