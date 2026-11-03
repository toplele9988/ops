"""Hash-checked static publication and rollback helpers. No service operations."""
import hashlib
import os
from pathlib import Path
import shutil
import tempfile


def inventory(root):
    root = Path(root)
    if root.is_symlink():
        raise ValueError('unsafe_static_root')
    if not root.exists():
        return None
    result = {}
    for p in root.rglob('*'):
        if p.is_symlink():
            raise ValueError('unsafe_static_asset')
        if p.is_file():
            result[str(p.relative_to(root))] = hashlib.sha256(p.read_bytes()).hexdigest()
    return result


def publish(source, target, expected, uid, gid):
    source, target = Path(source), Path(target)
    if inventory(source) != expected:
        raise ValueError('static_build_hash_mismatch')
    if any(p.is_symlink() for p in (target, *target.parents)):
        raise ValueError('unsafe_static_target')
    target.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix='.dossiers-dist-', dir=target.parent))
    detached = None
    try:
        stage.chmod(0o750)
        os.chown(stage, uid, gid)
        for name in expected:
            p = stage / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes((source / name).read_bytes())
        for p in stage.rglob('*'):
            p.chmod(0o750 if p.is_dir() else 0o640)
            os.chown(p, uid, gid)
        if inventory(stage) != expected:
            raise ValueError('static_stage_hash_mismatch')
        if target.exists():
            detached = Path(tempfile.mkdtemp(prefix='.dossiers-previous-', dir=target.parent))
            detached.rmdir()
            os.replace(target, detached)
        try:
            os.replace(stage, target)
        except BaseException:
            if detached:
                os.replace(detached, target)
                detached = None
            raise
    finally:
        if stage.exists():
            shutil.rmtree(stage)
        if detached and detached.exists():
            shutil.rmtree(detached)
