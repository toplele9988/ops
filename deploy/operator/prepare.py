#!/usr/bin/env python3
"""Verify exact observed source hashes and assemble an isolated review candidate."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

HERE = Path(__file__).resolve().parent


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def manifest(name):
    result = {}
    for line in (HERE/name).read_text().splitlines():
        checksum, name = line.split('  ',1)
        if Path(name).is_absolute() or '..' in Path(name).parts:
            raise ValueError('invalid_manifest')
        result[name] = checksum
    return result


def locate(name, backend, web):
    return web/name[4:] if name.startswith('web/') else backend/name


def verify(backend, web, name='baseline.sha256'):
    for relative, expected in manifest(name).items():
        path=locate(relative,backend,web)
        if path.is_symlink() or digest(path)!=expected:
            raise ValueError('hash_mismatch:'+relative)


def wechat_state(backend):
    """Both reviewed preconditions accepted; operator does not modify these files."""
    for label in ('baseline','candidate'):
        expected=manifest('wechat-private-'+label+'.sha256')
        if all((backend/name).is_file() and not (backend/name).is_symlink() and digest(backend/name)==value for name,value in expected.items()):
            return label
    raise ValueError('wechat_private_prerequisite_hash_mismatch')


def prepare(backend,web,destination):
    backend,web,destination=Path(backend).resolve(),Path(web).resolve(),Path(destination).resolve()
    if destination==backend or backend in destination.parents or destination==web or web in destination.parents:
        raise ValueError('candidate_must_be_isolated')
    if destination.exists() and any(destination.iterdir()):
        raise ValueError('candidate_must_be_empty')
    verify(backend,web)
    prerequisite=wechat_state(backend)
    destination.mkdir(parents=True,exist_ok=True)
    # Complete importable source snapshot for route checks, including packages
    # partially changed by the private-chat prerequisite. Only manifest files
    # are ever installed. No databases, configurations or static dist are copied.
    for path in (backend/'console').rglob('*.py'):
        relative=path.relative_to(backend)
        if any('.old-' in part or '.bak' in part or part=='__pycache__' for part in relative.parts):
            continue
        target=destination/relative;target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(path,target)
    shutil.copytree(web/'src',destination/'web/src',dirs_exist_ok=True)
    for name in ('index.html','package.json','tsconfig.json','vite.config.ts'):
        shutil.copyfile(web/name,destination/'web'/name)
    for name in manifest('baseline.sha256'):
        target=destination/name;target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(locate(name,backend,web),target)
    # No changes to the private-chat package files. Copy them only for isolated
    # route/import checks against either reviewed prerequisite.
    for name in manifest('wechat-private-'+prerequisite+'.sha256'):
        target=destination/name;target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(backend/name,target)
    subprocess.run(['patch','--batch','--fuzz=0','--forward','-p1','-i',str(HERE/'production.patch')],cwd=destination,check=True,capture_output=True)
    payload=HERE/'payload'
    for name in manifest('payload.sha256'):
        if (locate(name,backend,web)).exists():
            raise ValueError('new_file_collision:'+name)
        target=destination/name;target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(payload/name,target)
    verify(destination,destination/'web','candidate.sha256')
    verify(destination,destination/'web','payload.sha256')
    return prerequisite


if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--source-root',type=Path,default=Path('/opt/imgw/wechat'))
    p.add_argument('--web-root',type=Path,default=Path('/srv/imgw-dev/ops/web'))
    p.add_argument('--destination',type=Path,required=True)
    a=p.parse_args()
    print(json.dumps(dict(wechat_private=prepare(a.source_root,a.web_root,a.destination),candidate=str(a.destination))))
