"""只核对源码并在临时目录应用补丁，不提供生产安装入口。"""
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


def relative(name):
    path = Path(name)
    if not path.parts or path.is_absolute() or any(x in ('.', '..') for x in path.parts):
        raise ValueError('文件名不安全，请重新核对补丁清单')
    return path


def prepare(source=Path('/opt/imgw')):
    source = Path(source).absolute()
    manifest = json.loads((HERE / 'baseline.json').read_text(encoding='utf-8'))
    target = Path(tempfile.mkdtemp(prefix='r7a-code-', dir='/tmp'))
    target.chmod(0o700)
    try:
        for name, expected in manifest['source_sha256'].items():
            path = source / relative(name)
            if any(p.is_symlink() for p in (path, *path.parents)):
                raise ValueError('源码路径含符号链接，请核对基线')
            info = path.stat()
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError('源码类型不符合要求，请核对基线')
            raw = path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != expected:
                raise ValueError('源码基线已变化，请重新审查补丁')
            dest = target / relative(name)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(raw)
        for name, expected in manifest['patch_sha256'].items():
            path = HERE / relative(name)
            if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                raise ValueError('补丁摘要不一致，请重新审查补丁')
            for flags in (['--dry-run'], []):
                result = subprocess.run(['patch', '--batch', '--fuzz=0', '-p1', *flags,
                                         '-i', str(path)], cwd=target, capture_output=True)
                if result.returncode:
                    raise ValueError('补丁不能完整应用，请核对基线')
        for name, expected in manifest['candidate_sha256'].items():
            raw = (target / relative(name)).read_bytes()
            if hashlib.sha256(raw).hexdigest() != expected:
                raise ValueError('候选源码摘要不一致，请重新核对补丁')
            compile(raw, name, 'exec')
        return target
    except BaseException:
        shutil.rmtree(target)
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=Path('/opt/imgw'), help='只读源码根目录')
    args = parser.parse_args(argv)
    try:
        target = prepare(args.source)
    except (OSError, ValueError):
        print('基线或补丁核对失败，请重新审查源码与清单')
        return 1
    print(json.dumps({'结果': '基线、补丁与语法核对通过', '候选目录': str(target)}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
