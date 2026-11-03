#!/usr/bin/env python3
"""Run one explicit order read; never installs a schedule or retries a request."""
import argparse
import json

from console.alibaba_open.service import AlibabaOpen


class Parser(argparse.ArgumentParser):
    def format_help(self):
        return super().format_help().replace('usage:', '用法：', 1)

    def error(self, message):
        self.exit(2, '命令参数无效，请运行 --help 查看用法\n')


def main():
    parser = Parser(description='读取一次阿里订单；默认只查看状态，不连接外部接口',
                    usage='阿里订单单次读取 [选项]', add_help=False)
    parser._optionals.title = '选项'
    parser.add_argument('-h', '--help', action='help', help='显示用法后退出')
    parser.add_argument('--archive', metavar='路径', help='既有案子档案库路径')
    parser.add_argument('--db', metavar='路径', help='阿里接入独立数据库路径')
    parser.add_argument('--key', metavar='路径', help='授权加密密钥文件路径')
    parser.add_argument('--credentials', metavar='路径', help='应用凭据文件路径')
    parser.add_argument('--poll', action='store_true', help='明确执行一次订单读取')
    parser.add_argument('--enable-network', action='store_true', help='由总指挥核对协议后允许外部请求')
    args = parser.parse_args()
    options = dict(db_path=args.db, key_path=args.key, credentials_path=args.credentials,
                   network_enabled=args.enable_network)
    if args.archive:
        options['archive_path'] = args.archive
    try:
        service = AlibabaOpen(**options)
        result = service.write('poll', {'confirm': '1'}, 'alibaba-open-cli') if args.poll else service.read('status')
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception:
        # CLI errors must also never print paths, request parameters or token values.
        print('阿里订单读取未完成，请在控制台查看状态和下一步提示；系统不会自动重试')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
