"""python -m console.names report: read-only numeric coverage report."""
import argparse
import json

from .report import report
from .resolver import NamesResolver
from .sources import STATE_PATH


def main():
    parser = argparse.ArgumentParser(description='只读核对各渠道会话名字，仅输出数字和原因计数')
    parser.add_argument('command', choices=['report'])
    parser.add_argument('--wechat-state', default=str(STATE_PATH))
    parser.add_argument('--directory-cache')
    parser.add_argument('--members-cache')
    parser.add_argument('--dossiers')
    parser.add_argument('--contacts')
    parser.add_argument('--alibaba-inbound')
    parser.add_argument('--whatsapp-config')
    args = parser.parse_args()
    resolver = NamesResolver(args.wechat_state, directory_path=args.directory_cache,
                             members_path=args.members_cache, dossiers_path=args.dossiers, contacts_path=args.contacts)
    result = report(state_path=args.wechat_state, resolver=resolver,
                    alibaba_path=args.alibaba_inbound, whatsapp_config=args.whatsapp_config)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if all(value['统计完整'] for value in result.values()) else 1


if __name__ == '__main__':
    raise SystemExit(main())
