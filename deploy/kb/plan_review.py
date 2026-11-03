"""按受审只读配置生成私有积压审阅草案；不请求模型、不冻结、不启动服务。"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sqlite3
import tempfile

from backlog import canon, digest, validate_review
from coordinator import read_config
from production_adapter import build_adapter


def write_review(adapter, manifest, quarantine):
    review, excluded = adapter.plan_review(manifest['after'], manifest['snapshots'])
    validate_review(review, quarantine)
    output = Path(tempfile.mkdtemp(prefix='kb-authorized-plan-', dir='/tmp'))
    for name, value in (('review.json', review), ('quarantine.json', quarantine)):
        fd = os.open(output / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'w') as stream:
            stream.write(canon(value))
    return {'结果': '已生成私有待审草案，未请求模型；请审阅后另行冻结',
            '私有目录': str(output), 'review_sha256': digest(review),
            '候选单元数': len(review['entries']), '排除候选数': excluded,
            '模型请求': 0, '生产写入': 0}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', default='/etc/imgw/kb-recovery-coordinator.json')
    parser.add_argument('--config-sha256', required=True)
    args = parser.parse_args(argv)
    config = read_config(args.config, args.config_sha256)
    adapter, manifest, quarantine = build_adapter(config, inventory_only=True)
    result = write_review(adapter, manifest, quarantine)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ValueError, KeyError, TypeError, OSError, RuntimeError, sqlite3.Error):
        raise SystemExit('审阅草案生成已停止，请核对原授权、受审旧账本和未知隔离；未请求模型') from None
