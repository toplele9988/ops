#!/usr/bin/env python3
"""只读扫描知识目录库，生成供应商包装核对报告与追问草稿（不发送）。"""
from pathlib import Path
import argparse
import sys


if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from console.supplier_data.catalog_reader import CatalogError
from console.supplier_data.report import write_report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--catalog', required=True, help='知识目录 SQLite 库路径（只读）')
    parser.add_argument('--out-dir', required=True, help='报告输出目录，建议 private_out/')
    args = parser.parse_args(argv)
    try:
        report = write_report(args.catalog, args.out_dir)
    except (CatalogError, ValueError, OSError) as exc:
        parser.error(str(exc))
    summary = report['summary']
    print(f'资料条目 {summary["total"]}；可直接报价（资料齐全）{summary["ddp_ready"]}；'
          f'可估算（须向供应商确认装箱数）{summary["ddp_estimable"]}；'
          f'必需资料有缺项 {summary["with_missing"]}；冲突 {summary["with_conflict"]}'
          f'（疑似多型号 {summary["by_conflict_reason"]["multi_model_suspected"]}，'
          f'数值不一致 {summary["by_conflict_reason"]["value_mismatch"]}）')
    print('已生成 packing_report.json、packing_report.html。抽取结果须人工核对，非已确认规格。')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
