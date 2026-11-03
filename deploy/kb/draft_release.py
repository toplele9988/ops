"""只从 /tmp 私有账本副本生成待审清单，不发布、不启动、不调用模型。"""
from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sqlite3
import stat
import tempfile
from types import SimpleNamespace

from verify_patch import prepare
from source_gate import bind_wechat


def private_snapshot(path):
    path = Path(path)
    if '..' in path.parts:
        raise ValueError('只接受 /tmp 中无路径穿越的隔离副本')
    path = path.absolute()
    if not path.is_relative_to('/tmp') or any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('只接受 /tmp 中无符号链接的隔离副本')
    info = path.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise ValueError('副本目录必须由当前用户持有且权限为 0700')
    for name in ('calls.sqlite3', 'usage.json', 'quota-review.json', 'source-quarantine.json'):
        file = path / name
        info = file.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o600):
            raise ValueError('副本文件必须为当前用户持有的 0600 普通文件且无硬链接')
    return path


def draft(snapshot, after):
    import time
    if type(after) is not int or after <= 0 or after > time.time():
        raise ValueError('切换时间不正确，请填写已审阅的过去时间戳')
    snapshot = private_snapshot(snapshot)
    code = prepare()
    try:
        spec = importlib.util.spec_from_file_location('kb_isolation_draft', code / 'video_knowledge/kb_unknown_isolation.py')
        isolation = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(isolation)
        usage = json.loads((snapshot / 'usage.json').read_text())
        review = json.loads((snapshot / 'quota-review.json').read_text())
        quarantine = json.loads((snapshot / 'source-quarantine.json').read_text())
        # Reviewer supplies the actual readonly matrix baseline/quarantine view;
        # this tool never imports a provider client or credential loader.
        matrix = SimpleNamespace(quota_baseline=review['quota_baseline'],
                                 legacy_quarantine=review['legacy_quarantine'])
        with closing(sqlite3.connect((snapshot / 'calls.sqlite3').as_uri() + '?mode=ro', uri=True)) as calls:
            calls.execute('PRAGMA query_only=ON')
            calls.execute('BEGIN')
            try:
                manifest = isolation.capture(calls, usage, matrix)
            except isolation.IsolationError:
                raise ValueError('三条未知记录与额度证据不匹配，请重新核对副本') from None
        bind_wechat(quarantine, manifest)
        output = Path(tempfile.mkdtemp(prefix='kb-recovery-release-', dir='/tmp'))
        def write(name, value):
            raw = (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + '\n').encode()
            fd = os.open(output / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, 'wb') as stream:
                stream.write(raw)
            return hashlib.sha256(raw).hexdigest()
        isolation_sha = write('kb-two-unknown-manifest.json', manifest)
        quarantine_sha = write('kb-source-quarantine-v1.json', quarantine)
        boundary_sha = write('kb-live-recovery-v1.json', {
            'schema': 'wechat-kb-live-recovery-v1', 'after': after,
            'isolation_sha256': isolation_sha})
        write('kb-live-dual-v1.json', {
            'schema': 'kb-live-dual-v1', 'recovery_boundary_sha256': boundary_sha,
            'source_quarantine_sha256': quarantine_sha,
            'modules': {path.stem: hashlib.sha256(path.read_bytes()).hexdigest()
                        for path in sorted((code / 'video_knowledge').glob('*.py'))}})
        return output
    finally:
        shutil.rmtree(code)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot', required=True, type=Path, help='/tmp 中的私有副本目录')
    parser.add_argument('--after', required=True, type=int, help='老板确认的自然增量起点，UTC Unix 秒')
    args = parser.parse_args()
    output = draft(args.snapshot, args.after)
    print(json.dumps({'结果': '已生成待审清单，未发布', '隔离清单目录': str(output), 'hold数量': 3}, ensure_ascii=False))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error):
        raise SystemExit('无法生成清单，请核对私有副本、源码基线和三条未知记录') from None
