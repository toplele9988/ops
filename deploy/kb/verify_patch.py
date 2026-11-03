"""核对源码基线，在全新临时目录 dry-run 并生成候选；不安装生产文件。"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile

HERE = Path(__file__).resolve().parent


def safe_name(name):
    path = Path(name)
    if path.is_absolute() or '..' in path.parts or not path.parts:
        raise ValueError('清单文件名不安全，请重新审查清单')
    return path


def prepare(source=None):
    baseline = json.loads((HERE / 'baseline.json').read_text())
    source = Path(source or baseline['base']).absolute()
    # Destination is always created here; no caller-controlled deployment target.
    target = Path(tempfile.mkdtemp(prefix='kb-recovery-code-', dir='/tmp'))
    try:
        for name, expected in baseline['sha256'].items():
            safe_name(name)
            path = source / name
            if any(p.is_symlink() for p in (path, *path.parents)):
                raise ValueError('源码路径包含符号链接，请核对源码目录')
            info = path.stat()
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError('源码文件类型不符合预期，请核对基线')
            raw = path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != expected:
                raise ValueError('源码基线已变化，请重新审查补丁')
            dest = target / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(raw)
        for name in baseline['patches']:
            safe_name(name)
            path = HERE / name
            if hashlib.sha256(path.read_bytes()).hexdigest() != baseline['patch_sha256'][name]:
                raise ValueError('补丁已变化，请重新审查补丁与基线清单')
            for flags in (['--dry-run'], []):
                result = subprocess.run(
                    ['patch', '--batch', '--fuzz=0', '-p1', *flags, '-i', str(path)],
                    cwd=target, capture_output=True, text=True, check=False)
                if result.returncode:
                    raise ValueError('补丁未能完整应用，请核对基线')
        for name, expected in baseline.get('new_files', {}).items():
            safe_name(name)
            if hashlib.sha256((target / name).read_bytes()).hexdigest() != expected:
                raise ValueError('新增模块与审阅摘要不一致，请重新核对补丁')
        for path in target.rglob('*.py'):
            compile(path.read_bytes(), str(path), 'exec')
        return target
    except BaseException:
        shutil.rmtree(target)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, help='只读源码基线目录')
    args = parser.parse_args()
    target = prepare(args.source)
    print(json.dumps({'结果': '基线与补丁 dry-run 通过', '隔离候选目录': str(target)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
