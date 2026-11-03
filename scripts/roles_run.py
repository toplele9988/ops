#!/usr/bin/env python3
"""Manual/scheduler entry. Dry run by default, using local sources and no AI."""
import argparse
from dataclasses import replace
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from console.channels import shared_inbox
from console.archive.store import ARCHIVE_PATH
from console.inbox.store import INBOUND_PATH, WECHAT_PATH
from console.roles import Roles
from console.roles.ai import DEFAULT_RUNNER
from console.roles.common import Config
from console.roles.storage import RateLimited, run_lock


def run(service, role, *, apply=False, who='roles-cli'):
    """Inject a fake service/clock/inbox/runner without invoking a subprocess."""
    if role not in ('triage', 'patrol', 'brief'):
        raise ValueError('岗位须为 triage、patrol 或 brief')
    with run_lock(service.store):
        return getattr(service, 'scan_triage' if role == 'triage' else 'run_' + role)(who, apply=apply)


def main(argv=None):
    parser = argparse.ArgumentParser(description='运行内部岗位；默认仅预览，不写库、不调用 AI')
    parser.add_argument('--role', required=True, choices=('triage', 'patrol', 'brief'))
    parser.add_argument('--archive', type=Path, default=ARCHIVE_PATH, help='档案库或演练副本，默认沿用 archive 路径')
    parser.add_argument('--inbound', type=Path, help='本地阿里入站库；自定义档案时默认同目录 inbound.sqlite3')
    parser.add_argument('--wechat', type=Path, help='本地微信只读库；自定义档案时默认禁用')
    parser.add_argument('--apply', action='store_true', help='写入岗位产出/建议/提醒')
    parser.add_argument('--ai', action='store_true', help='显式启用官方模型（须同时 --apply）')
    parser.add_argument('--who', default='roles-cli', help='本机操作人，用于记录')
    parser.add_argument('--timezone', default='Asia/Shanghai')
    args = parser.parse_args(argv)
    if args.ai and not args.apply:
        parser.error('--ai 必须同时提供 --apply；预览始终不调用 AI')
    try:
        # No WhatsApp HTTP during CLI scans. Future timers may inject an inbox
        # with an explicitly configured read-only fetch into run().
        default = args.archive.resolve() == ARCHIVE_PATH.resolve()
        inbox = shared_inbox(args.inbound or (INBOUND_PATH if default else args.archive.parent / 'inbound.sqlite3'),
            wechat_path=args.wechat or (WECHAT_PATH if default else args.archive.parent / 'disabled-wechat.sqlite3'),
            whatsapp_config_path=args.archive.parent / 'disabled-whatsapp.json',
            fetch=lambda *a, **kw: (_ for _ in ()).throw(RuntimeError('CLI 未启用网络渠道')))
        service = Roles(args.archive, inbox=inbox, runner=DEFAULT_RUNNER if args.ai else None,
                        config=replace(Config(), timezone=args.timezone))
        result = run(service, args.role, apply=args.apply, who=args.who)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except RateLimited as error:
        print(json.dumps(dict(error=str(error), result=error.result), ensure_ascii=False))
        return 2
    except Exception:
        print('岗位暂时无法运行，请核对档案路径和配置', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
