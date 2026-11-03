"""The four original bundles are superseded by the production-based r11 bundle."""
from pathlib import Path
import hashlib
import subprocess


def test_candidate_patch_round_trip(tmp_path):
    root = Path(__file__).resolve().parents[2]
    bundle = root / 'patches/ops-r11'
    manifest = (bundle / 'candidate.sha256').read_text().splitlines()
    for line in manifest:
        digest, name = line.split()
        source = bundle / 'v3.candidate.py' if name == 'console/v3.py' else root / 'src' / name
        assert hashlib.sha256(source.read_bytes()).hexdigest() == digest
        dest = tmp_path / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(source.read_bytes())
    def apply(*args):
        result = subprocess.run(['patch', '--batch', '--fuzz=0', '-p1', '-d', str(tmp_path), '-i', str(bundle / 'production.patch'), *args], capture_output=True, text=True)
        assert result.returncode == 0, result.stdout + result.stderr
    def verify(manifest):
        for line in (bundle / manifest).read_text().splitlines():
            digest, name = line.split()
            assert hashlib.sha256((tmp_path / name).read_bytes()).hexdigest() == digest
    apply('-R', '--dry-run'); apply('-R'); verify('baseline.sha256')
    for name in (bundle / 'absent.list').read_text().splitlines():
        assert not (tmp_path / name).exists()
    apply('--dry-run'); apply(); verify('candidate.sha256')
