#!/usr/bin/env python3
"""Read-only Knowledge Hub CLI. No AI runner, sends, network or history writes."""
import argparse
import json
from pathlib import Path
import sys

# Running from a checkout needs no install; production BGE remains a lazy optional import.
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))


def main(argv=None, *, hub=None):
    parser = argparse.ArgumentParser(description='查询统一知识中枢（只读）')
    parser.add_argument('query', help='问题，最多 500 字')
    parser.add_argument('--purpose', choices=('owner', 'product_facts'), default='owner')
    parser.add_argument('--top', type=int, default=8)
    parser.add_argument('--json', action='store_true', dest='as_json')
    args = parser.parse_args(argv)
    try:
        from console.kb import default_hub
        from console.kb.index import numpy_module
        from console.kb.search import validate
        validate(args.query, args.purpose, args.top)
        if numpy_module() is None:
            print('缺少 numpy：已退化为词法检索；混合检索请使用 /opt/imgw/venv/bin/python。', file=sys.stderr)
        service = hub if hub is not None else default_hub()
        result = service.search(args.query, args.purpose, args.top)
        if result['ready'] and result['mode'] == 'lexical':
            print('向量检索不可用，当前使用词法检索。', file=sys.stderr)
        result['company_brief'] = service.brief.read()
        if args.as_json:
            print(json.dumps(result, ensure_ascii=False))
        else:
            print('公司背景\n' + result['company_brief'])
            print(f"\n检索模式：{result['mode']}；命中：{len(result['items'])}")
            for row in result['items']:
                flags = (' · 保密' if row['confidential'] else '') + (' · 成本价敏感' if row['is_cost_price'] else '')
                print(f"\n[{row['rank']}] {row['title']} · {row['source_type_label']} · {row['date'] or '日期未知'}{flags}")
                print(row['snippet'])
            if not result['items']:
                print('知识库里没有找到相关资料')
        if not result['ready']:
            print('知识库尚未就绪。', file=sys.stderr)
            return 1
        return 0
    except ValueError:
        print('参数无效：问题须为 1 至 500 字，条数须为 1 至 20。', file=sys.stderr)
        return 2
    except ImportError:
        print('缺少运行依赖，请使用本机控制台 Python 环境。', file=sys.stderr)
        return 1
    except Exception:
        print('知识库暂时无法读取，请检查本机读取权限与运行环境。', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
