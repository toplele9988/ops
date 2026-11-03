"""核对 R5-A 交付与真实源码 SHA；只读检查或在 /tmp 验证 R3-C 补丁。"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import stat

from verify_patch import prepare, safe_name

HERE = Path(__file__).resolve().parent


def checked(path, expected):
    path = Path(path)
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('源码路径含符号链接，请重新核对基线')
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError('源码类型不符，请重新核对基线')
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError('源码摘要已变化，请停止并重新审查候选')
    return raw


def verify_runtime(expected=None):
    path = HERE / 'coordinator-baseline.json'
    raw = path.read_bytes()
    if expected is not None and hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError('协调器发布清单摘要不匹配，请保留进度并核对')
    baseline = json.loads(raw)
    for name, pin in baseline['runtime_sha256'].items():
        checked(HERE / safe_name(name), pin)
    if expected is not None:
        for path, pin in {**baseline['production_source_sha256'],
                          **baseline['installed_overlay_sha256']}.items():
            checked(path, pin)
    return baseline


def verify(*, installed=False):
    baseline = verify_runtime()
    for path, pin in baseline['production_source_sha256'].items():
        checked(path, pin)
    if installed:
        for path, pin in baseline['installed_overlay_sha256'].items():
            checked(path, pin)
    else:
        candidate = prepare()
        try:
            for path, pin in baseline['installed_overlay_sha256'].items():
                relative = Path(path).relative_to(baseline['overlay_base'])
                checked(candidate / relative, pin)
        finally:
            shutil.rmtree(candidate)
    return {'结果': '协调器源码与补丁摘要核对通过，未请求模型、未部署',
            '运行文件数': len(baseline['runtime_sha256']),
            '生产参考文件数': len(baseline['production_source_sha256'])}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--installed', action='store_true', help='只读核对已安装补丁的源码摘要')
    args = parser.parse_args()
    print(json.dumps(verify(installed=args.installed), ensure_ascii=False))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError):
        raise SystemExit('候选基线核对未通过，请保留原账本并重新审查源码；未部署') from None
