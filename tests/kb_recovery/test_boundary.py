"""发布清单、固定切换时间与补丁基线的拒绝条件。"""
import json

import pytest

from recovery_fixtures import load_modules


@pytest.mark.parametrize('change', ['valid', 'unreviewed_hash', 'isolation', 'boolean_time', 'future_time'])
def test_boundary_requires_release_pin_and_isolation_binding(tmp_path, monkeypatch, change):
    from types import SimpleNamespace
    m = load_modules(tmp_path)
    value = {'schema': 'wechat-kb-live-recovery-v1', 'after': 1000, 'isolation_sha256': 'a' * 64}
    if change == 'isolation':
        value['isolation_sha256'] = 'b' * 64
    elif change == 'boolean_time':
        value['after'] = True
    elif change == 'future_time':
        value['after'] = 9999999999999
    raw = json.dumps(value).encode()
    monkeypatch.setattr(m.kb_model_runtime, 'restricted', lambda path, **kw: raw)
    monkeypatch.setattr(m.kb_live, 'RECOVERY_BOUNDARY_SHA256',
                        '0' * 64 if change == 'unreviewed_hash' else m.kb_model_runtime.sha(raw))
    matrix = SimpleNamespace(manifest_sha256='a' * 64, source_boundary=lambda: None)
    if change == 'valid':
        assert m.kb_live.read_recovery_boundary(matrix) == 1000
        assert matrix.recovery_boundary_sha256 == m.kb_model_runtime.sha(raw)
    else:
        with pytest.raises(m.kb_model_runtime.ModelError):
            m.kb_live.read_recovery_boundary(matrix)
