#!/usr/bin/env python3
"""Manual quotation extraction. Preview only unless --apply is supplied."""
import argparse
from dataclasses import replace
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from console.archive.store import ARCHIVE_PATH
from console.channels import shared_inbox
from console.extract.service import Extract
from console.inbox.store import INBOUND_PATH, WECHAT_PATH
from console.roles.ai import DEFAULT_RUNNER
from console.roles.common import Config
from console.roles.storage import RateLimited, run_lock


def run(service, *, apply=False, who='extract-cli', conversation_key=None, days=30, limit=20):
    """An injectable entry point; dry runs never reserve quota or invoke AI."""
    with run_lock(service.store):
        return service.scan(who, apply=apply, conversation_key=conversation_key, days=days, limit=limit)


def main(argv=None):
    parser = argparse.ArgumentParser(description='抽取供应商/货代价格线索；默认预览，不写库、不调用 AI')
    parser.add_argument('--archive', type=Path, default=ARCHIVE_PATH)
    parser.add_argument('--inbound', type=Path, help='本地阿里入站库；自定义档案时默认同目录 inbound.sqlite3')
    parser.add_argument('--wechat', type=Path, help='本地微信只读库；自定义档案时默认禁用')
    parser.add_argument('--conversation-key', help='只扫描指定会话')
    parser.add_argument('--days', type=int, default=30, help='回看天数，1–365，默认30')
    parser.add_argument('--limit', type=int, default=20, help='单次会话上限，1–100，默认20')
    parser.add_argument('--apply', action='store_true', help='保存待确认建议及岗位运行记录')
    parser.add_argument('--ai', action='store_true', help='显式启用官方模型，须同时 --apply')
    parser.add_argument('--who', default='extract-cli')
    parser.add_argument('--timezone', default='Asia/Shanghai')
    args = parser.parse_args(argv)
    if args.ai and not args.apply:
        parser.error('--ai 必须同时提供 --apply；预览始终不调用 AI')
    if not 1 <= args.days <= 365 or not 1 <= args.limit <= 100:
        parser.error('--days 须为1–365，--limit 须为1–100')
    try:
        default = args.archive.resolve() == ARCHIVE_PATH.resolve()
        inbox = shared_inbox(args.inbound or (INBOUND_PATH if default else args.archive.parent / 'inbound.sqlite3'),
            wechat_path=args.wechat or (WECHAT_PATH if default else args.archive.parent / 'disabled-wechat.sqlite3'),
            whatsapp_config_path=args.archive.parent / 'disabled-whatsapp.json',
            fetch=lambda *a, **kw: (_ for _ in ()).throw(RuntimeError('CLI 未启用网络渠道')))
        service = Extract(args.archive, inbox=inbox, runner=DEFAULT_RUNNER if args.ai else None,
                          config=replace(Config(), timezone=args.timezone))
        result = run(service, apply=args.apply, who=args.who, conversation_key=args.conversation_key,
                     days=args.days, limit=args.limit)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except RateLimited as error:
        print(json.dumps(dict(error=str(error), retry_after=error.retry_after, result=error.result), ensure_ascii=False))
        return 2
    except Exception:
        print('报价抽取员暂时无法运行，请核对档案路径和配置', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
