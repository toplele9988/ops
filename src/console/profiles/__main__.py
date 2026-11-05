"""One-shot entry point. Detailed output only goes to an explicitly chosen private report."""
import argparse
import json
from pathlib import Path
import sqlite3

from .schema import CHANNELS
from .worker import Worker, numeric_summary


def main(argv=None):
    parser = argparse.ArgumentParser(description='读取完整会话，整理往来对象与进展')
    sub = parser.add_subparsers(dest='command', required=True)
    run = sub.add_parser('run')
    run.add_argument('--once', action='store_true', required=True)
    run.add_argument('--dry-run', action='store_true')
    run.add_argument('--report', type=Path)
    run.add_argument('--max-conversations', type=int, default=20)
    run.add_argument('--max-windows', type=int, default=30, help='每次每个会话最多处理的窗口数')
    run.add_argument('--daily-budget-tokens', type=int, default=1000000)
    run.add_argument('--channel', choices=CHANNELS)
    run.add_argument('--only', action='append', default=[], help='会话标识；可重复指定')
    args = parser.parse_args(argv)
    if args.dry_run and args.report is None:
        parser.error('--dry-run 必须指定仓库外的 --report')
    try:
        result = Worker().run(dry_run=args.dry_run, report=args.report,
                              max_conversations=args.max_conversations, max_windows=args.max_windows,
                              daily_budget_tokens=args.daily_budget_tokens, channel=args.channel, only=args.only)
    except (OSError, ValueError, sqlite3.Error):
        print(json.dumps(dict(status='failed', error_code='profiles_unavailable')))
        return 1
    print(json.dumps(numeric_summary(result), ensure_ascii=False))
    return int(bool(result['failed_conversations'] or result['missing'] or not result['source_ready']))


if __name__ == '__main__':
    raise SystemExit(main())
