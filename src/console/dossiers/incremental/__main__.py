"""Explicit one-shot CLI. No scheduler, routing or service installation."""
import argparse
import json
from pathlib import Path
import sqlite3

from ..store import DB_PATH
from .sources import WECHAT_PATH
from .worker import Incremental, numeric_summary


def main(argv=None):
    parser = argparse.ArgumentParser(description='供应商档案每日增量（只复用已有解析文本）')
    sub = parser.add_subparsers(dest='command', required=True)
    run = sub.add_parser('run')
    run.add_argument('--once', action='store_true', required=True)
    run.add_argument('--dry-run', action='store_true')
    run.add_argument('--database', type=Path, default=DB_PATH)
    run.add_argument('--wechat', type=Path, default=WECHAT_PATH)
    run.add_argument('--report', type=Path)
    run.add_argument('--max-windows', type=int)
    run.add_argument('--daily-budget-tokens', type=int, default=300000)
    run.add_argument('--since-rowid', type=int, help='仅 dry-run：覆盖游标，回看指定 rowid 之后的消息')
    args = parser.parse_args(argv)
    if args.since_rowid is not None and not args.dry_run:
        parser.error('--since-rowid 仅可用于 --dry-run')
    if args.dry_run and args.report is None:
        parser.error('--dry-run 必须指定 --report')
    try:
        result = Incremental(args.database, args.wechat).run(dry_run=args.dry_run, report=args.report,
            max_windows=args.max_windows, daily_budget_tokens=args.daily_budget_tokens, since_rowid=args.since_rowid)
    except (OSError, ValueError, sqlite3.Error):
        print(json.dumps(dict(status='failed', failure_code='incremental_unavailable'), ensure_ascii=False))
        return 1
    print(json.dumps(numeric_summary(result), ensure_ascii=False))
    return 1 if result['failed_windows'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
