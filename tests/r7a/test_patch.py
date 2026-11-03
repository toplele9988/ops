import hashlib
import json
from pathlib import Path
import shutil

import pytest

from conftest import ROOT, VERIFY


def test_all_candidates_match_reviewed_digests(candidate):
    manifest = json.loads((ROOT / 'deploy/r7a/baseline.json').read_text())
    for name, expected in manifest['candidate_sha256'].items():
        assert hashlib.sha256((candidate / name).read_bytes()).hexdigest() == expected


def test_changed_source_is_rejected(tmp_path):
    source = tmp_path / 'fiction-source'
    path = source / 'wechat/api_groupbot.py'
    path.parent.mkdir(parents=True)
    path.write_text('raise RuntimeError("虚构变动基线")\n')
    with pytest.raises(ValueError, match='源码基线已变化'):
        VERIFY.prepare(source)
    assert path.read_text().startswith('raise RuntimeError')


def test_symlink_source_is_rejected(tmp_path):
    source = tmp_path / 'fiction-source'
    source.mkdir()
    (source / 'wechat').symlink_to('/opt/imgw/wechat', target_is_directory=True)
    with pytest.raises(ValueError, match='符号链接'):
        VERIFY.prepare(source)


@pytest.mark.parametrize('value', ['/etc/fiction', '../fiction', 'fiction/../../escape'])
def test_patch_names_cannot_escape(value):
    with pytest.raises(ValueError):
        VERIFY.relative(value)
