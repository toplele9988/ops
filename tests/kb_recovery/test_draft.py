"""清单生成器只对隔离副本读数据，输出始终位于新的私有临时目录。"""
import importlib.util
import json
from pathlib import Path
import shutil
import sys

import pytest

from recovery_fixtures import ROOT, load_modules, synthetic_quarantine
from test_isolation import synthetic_holds


def tool():
    name = str(ROOT / 'deploy/kb')
    if name not in sys.path:
        sys.path.append(name)
    spec = importlib.util.spec_from_file_location('kb_release_draft', ROOT / 'deploy/kb/draft_release.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_draft_three_linked_manifests_without_mutating_source(tmp_path):
    modules = load_modules(tmp_path)
    f = synthetic_holds(modules, tmp_path)
    manifest = modules.kb_unknown_isolation.capture(f.db, f.ledger, f.matrix)
    f.db.close()
    for name, value in [('source-quarantine.json', synthetic_quarantine(modules, manifest)),
                        ('usage.json', f.ledger), ('quota-review.json', {
            'quota_baseline': f.matrix.quota_baseline, 'legacy_quarantine': []})]:
        path = f.path.parent / name
        path.write_text(json.dumps(value))
        path.chmod(0o600)
    before = {path.name: path.read_bytes() for path in f.path.parent.iterdir()}
    result = tool().draft(f.path.parent, 1000)
    try:
        isolation = json.loads((result / 'kb-two-unknown-manifest.json').read_text())
        boundary = json.loads((result / 'kb-live-recovery-v1.json').read_text())
        release = json.loads((result / 'kb-live-dual-v1.json').read_text())
        assert isolation['hold_count'] == 3
        assert len(release['modules']) == 7
        assert boundary['isolation_sha256'] == modules.kb_model_runtime.sha((result / 'kb-two-unknown-manifest.json').read_bytes())
        assert release['recovery_boundary_sha256'] == modules.kb_model_runtime.sha((result / 'kb-live-recovery-v1.json').read_bytes())
        assert release['source_quarantine_sha256'] == modules.kb_model_runtime.sha((result / 'kb-source-quarantine-v1.json').read_bytes())
        assert all(path.stat().st_mode & 0o777 == 0o600 for path in result.iterdir())
        assert before == {path.name: path.read_bytes() for path in f.path.parent.iterdir()}
    finally:
        shutil.rmtree(result)


def test_draft_rejects_non_temp_source():
    with pytest.raises(ValueError, match='只接受 /tmp'):
        tool().draft(Path('/var/lib/imgw'), 1000)


def test_draft_rejects_parent_traversal_before_opening():
    with pytest.raises(ValueError, match='只接受 /tmp'):
        tool().draft(Path('/tmp/../var/lib/imgw'), 1000)
