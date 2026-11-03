#!/usr/bin/env python3
"""Offline archive seed. Only numeric aggregate results are printed."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from console.archive.seed import CATALOG_PATH, REPORT_PATH, seed_archive, sync_packing_facts
from console.archive.store import ARCHIVE_PATH


def main(argv=None):
    parser = argparse.ArgumentParser(description='从只读目录与包装报告建立待确认档案')
    parser.add_argument('--catalog', type=Path, default=CATALOG_PATH)
    parser.add_argument('--packing-report', type=Path, default=REPORT_PATH)
    parser.add_argument('--archive', type=Path, default=ARCHIVE_PATH)
    parser.add_argument('--dry-run', action='store_true', help='只在内存中预演，不创建或修改档案文件')
    parser.add_argument('--sync-packing', action='store_true', help='仅刷新仍为抽取状态的已有包装字段')
    args = parser.parse_args(argv)
    try:
        result = (sync_packing_facts(args.packing_report, args.archive, dry_run=args.dry_run) if args.sync_packing
                  else seed_archive(args.catalog, args.packing_report, args.archive, dry_run=args.dry_run))
    except Exception:
        print('档案处理失败，请核对来源结构、参数与文件权限。', file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
