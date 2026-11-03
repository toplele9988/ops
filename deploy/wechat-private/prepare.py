#!/usr/bin/env python3
"""Verify the observed production source hashes and patch an isolated directory."""
import argparse
import hashlib
from pathlib import Path
import shutil
import subprocess
import tempfile

HERE = Path(__file__).resolve().parent
NEW = ('wechat_private.py', 'console/channels/wechat_private.py')


def manifest(name):
    result = {}
    for line in (HERE / name).read_text().splitlines():
        checksum, path = line.split('  ', 1)
        if Path(path).is_absolute() or '..' in Path(path).parts:
            raise ValueError('invalid_manifest')
        result[path] = checksum
    return result


def verify(root, name):
    for path, expected in manifest(name).items():
        source = root / path
        if source.is_symlink() or hashlib.sha256(source.read_bytes()).hexdigest() != expected:
            raise ValueError('baseline_or_candidate_hash_mismatch:' + path)


def prepare(source_root=Path('/opt/imgw/wechat'), destination=None):
    source_root = Path(source_root).resolve()
    verify(source_root, 'baseline.sha256')
    dest = Path(destination) if destination else Path(tempfile.mkdtemp(prefix='wechat-private-candidate-'))
    dest = dest.resolve()
    if dest == source_root or source_root in dest.parents:
        raise ValueError('candidate_must_be_isolated')
    dest.mkdir(parents=True, exist_ok=True)
    for path in manifest('baseline.sha256'):
        target = dest / path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_root / path, target)
    for path in NEW:
        if (source_root / path).exists():
            raise ValueError('new_module_already_exists:' + path)
    subprocess.run(['patch', '--batch', '--fuzz=0', '--forward', '-p1', '-i', str(HERE / 'production.patch')],
                   cwd=dest, check=True, capture_output=True)
    for path in NEW:
        target = dest / path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(HERE / ('wechat_private.py' if path == 'wechat_private.py' else 'wechat_private_adapter.py'), target)
    verify(dest, 'candidate.sha256')
    return dest


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--source-root', type=Path, default=Path('/opt/imgw/wechat'))
    p.add_argument('--destination', type=Path, required=True)
    a = p.parse_args()
    print(prepare(a.source_root, a.destination))
