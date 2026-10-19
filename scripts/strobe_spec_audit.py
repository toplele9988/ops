#!/usr/bin/env python3
"""Print a read-only Markdown audit of the actual accepted strobe catalog."""
from pathlib import Path
import argparse
import os
import sys

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from console.strobe.repository import audit_catalog


def _cell(value):
    return str(value).replace('|', '\\|').replace('\r', ' ').replace('\n', ' ')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', default=os.environ.get('XL_DB_PATH'))
    parser.add_argument('--work-db', default=os.environ.get('CPQ_WORK_DB', '/var/lib/imgw/console/cpq_work.sqlite3'))
    args = parser.parse_args(argv)
    if not args.db:
        parser.error('--db or XL_DB_PATH is required')
    rows = audit_catalog(args.db, args.work_db)
    print('| 型号 | 完整度 | 缺失字段 | 冲突字段 | 图片数（可对客/总数） | 有效报价数 | 航空箱方案来源 |')
    print('|---|---:|---|---|---:|---:|---|')
    for row in rows:
        cells = [row['model'], f"{row['score']:.0%}", ', '.join(row['missing']) or '—',
                 ', '.join(row['conflicts']) or '—', f"{row['safe_images']}/{row['total_images']}",
                 row['valid_offers'], row['flight_case_source']]
        print('| ' + ' | '.join(_cell(value) for value in cells) + ' |')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
