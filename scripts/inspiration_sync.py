#!/usr/bin/env python3
"""Offline import by default; model invocation requires an explicit --enrich."""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from console.inspiration.enrich import ai_task_runner, enrich_batch, gateway_runner
from console.inspiration.store import InspirationStore
from console.inspiration.sync import SourceSchemaError, sync_from_state


def _positive(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError('数量须为正整数')
    return number


def main(argv=None, runner=None):
    parser = argparse.ArgumentParser(description='同步并可选整理 AI 开发灵感卡片')
    parser.add_argument('--state-db', required=True, type=Path)
    parser.add_argument('--store', required=True, type=Path)
    parser.add_argument('--enrich', action='store_true', help='允许调用 ai-task 国产模型')
    parser.add_argument('--gateway', action='store_true', help='以 imgw 身份直接调用 ai-task 网关（定时任务用）')
    parser.add_argument('--limit', type=_positive, help='本次最多扫描的候选消息数及最多整理的卡片数')
    parser.add_argument('--batch-size', type=int, choices=range(1, 7), default=6)
    args = parser.parse_args(argv)
    if args.state_db.resolve() == args.store.resolve():
        parser.error('来源库与灵感库不能使用同一文件')
    try:
        store = InspirationStore(args.store)
        result = sync_from_state(args.state_db, store, args.limit)
        enriched, failed = 0, 0
        if args.enrich:
            # Snapshot the queue once. Failed cards are not picked up again in
            # this invocation, even if their cumulative error count is < 3.
            count = args.limit if args.limit is not None else max(1, store.counts()['relevance']['unknown'])
            pending = store.pending_enrichment(count)
            for offset in range(0, len(pending), args.batch_size):
                report = enrich_batch(pending[offset:offset + args.batch_size], runner or (gateway_runner if args.gateway else ai_task_runner))
                for item in report['results']:
                    store.save_enrichment(item['card_id'], item, report['model'])
                    enriched += 1
                for card_id in report['errors']:
                    store.record_enrich_error(card_id)
                    failed += 1
        print(f"扫描 {result['scanned']} 条候选消息，发现 {result['cards_found']} 张卡片，"
              f"新增 {result['new_cards']} 张，更新 {result['updated']} 张；"
              f"已整理 {enriched} 张，失败 {failed} 张。")
        return 1 if failed else 0
    except SourceSchemaError as exc:
        print(str(exc), file=sys.stderr)  # controlled messages contain schema names only
    except Exception:
        print('同步或保存失败，请检查数据库及文件权限。', file=sys.stderr)
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
