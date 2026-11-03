#!/usr/bin/env python3
"""Hash-checked, additive staging. Does not import an application or any data."""
import argparse
import hashlib
from pathlib import Path
import shutil
import subprocess
import tempfile

HERE = Path(__file__).resolve().parent


def manifest(name):
    result = {}
    for line in (HERE / name).read_text().splitlines():
        sha, name = line.split('  ', 1)
        if Path(name).is_absolute() or '..' in Path(name).parts:
            raise ValueError('invalid_manifest')
        result[name] = sha
    return result


def checksum(path):
    path = Path(path)
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() and not path.is_symlink() else None


def matches(root, name):
    return all(checksum(Path(root) / name) == sha for name, sha in manifest(name).items())


def variant(root):
    for name in ('production', 'original'):
        if matches(root, name + '.candidate.sha256'):
            return name, True
        if matches(root, name + '.baseline.sha256'):
            return name, False
    raise ValueError('baseline_drift_requires_review_and_rebuild')


def prepare(root=Path('/opt/imgw/wechat'), destination=None):
    root = Path(root).resolve(strict=True)
    kind, installed = variant(root)
    dest = Path(destination) if destination else Path(tempfile.mkdtemp(prefix='dossiers-candidate-'))
    dest = dest.resolve()
    if dest == root or root in dest.parents or (dest.exists() and any(dest.iterdir())):
        raise ValueError('destination_must_be_empty_and_isolated')
    dest.mkdir(parents=True, exist_ok=True)
    absent = (HERE / 'absent.list').read_text().splitlines()
    if not installed and any((root / name).exists() for name in absent):
        raise ValueError('new_module_collision')
    names = manifest(kind + ('.candidate.sha256' if installed else '.baseline.sha256'))
    for name in names:
        target = dest / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / name, target)
    if not installed:
        subprocess.run(['patch', '--batch', '--fuzz=0', '--forward', '-p1', '-i', str(HERE / (kind + '.patch'))], cwd=dest, check=True, capture_output=True)
    if not matches(dest, kind + '.candidate.sha256'):
        raise ValueError('candidate_hash_mismatch')
    # Unmodified Python imports are read-only references. Never stage private data.
    for base in (root, Path('/opt/imgw/wechat')):
        for source in base.rglob('*.py'):
            rel = source.relative_to(base)
            if any('.old' in part or '.bak' in part or part == 'tests' for part in rel.parts):
                continue
            target = dest / rel
            if not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                target.symlink_to(source)
    return dest, kind, installed


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--source-root', type=Path, default=Path('/opt/imgw/wechat'))
    p.add_argument('--destination', type=Path, required=True)
    a = p.parse_args()
    _, kind, installed = prepare(a.source_root, a.destination)
    print('staged', kind, 'already_installed=' + str(installed))
