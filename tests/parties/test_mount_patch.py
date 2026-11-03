"""Round-trip only in temp directories. Baseline is a public source snapshot."""
from hashlib import sha256
import importlib.util
from pathlib import Path
import subprocess
from types import SimpleNamespace

from starlette.responses import JSONResponse

ROOT = Path(__file__).resolve().parents[2]
PATCHES = ROOT / 'src/console/customers/patches'


def test_mount_patch_round_trip(tmp_path):
    target = tmp_path / 'console/v3_customers_api.py'
    target.parent.mkdir()
    (target.parent / 'customers').mkdir()
    baseline = (PATCHES / 'baseline/v3_customers_api.py').read_bytes()
    target.write_bytes(baseline)
    expected = (PATCHES / 'baseline.sha256').read_text().split()[0]
    assert sha256(baseline).hexdigest() == expected
    command = ['patch', '--batch', '--fuzz=0', '-p1', '-i', str(PATCHES / 'production-mount.patch')]
    subprocess.run([*command, '--dry-run'], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(command, cwd=tmp_path, check=True, capture_output=True)
    for line in (PATCHES / 'candidate.sha256').read_text().splitlines():
        digest, relative = line.split()
        assert sha256((tmp_path / relative).read_bytes()).hexdigest() == digest
    spec = importlib.util.spec_from_file_location('console.parties_mount_candidate', target)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    gate = SimpleNamespace(error_response=lambda r, code: JSONResponse({}, status_code=code))
    # Construction does not read any source. Every future handler still uses gates.
    routes = module.build(gate, inbox=SimpleNamespace(read=lambda: (_ for _ in ()).throw(AssertionError('no source read'))))
    assert len(routes) == 16
    assert len({(r.path, tuple(sorted(r.methods))) for r in routes}) == 16
    subprocess.run([*command, '-R', '--dry-run'], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run([*command, '-R'], cwd=tmp_path, check=True, capture_output=True)
    assert target.read_bytes() == baseline
    assert not (tmp_path / 'console/customers/operations.py').exists()
