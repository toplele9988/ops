"""Incrementally import candidate messages through a strictly read-only connection."""
import math
from pathlib import Path
import sqlite3

from .cards import parse_finder_card

WATERMARK_KEY = 'state_watermark_v1'


class SourceSchemaError(ValueError):
    """The source cannot supply the fields needed for a safe incremental sync."""


def sync_from_state(state_db_path, store, limit=None):
    if limit is not None and (type(limit) is not int or limit < 1):
        raise ValueError('同步数量须为正整数')
    source_path = Path(state_db_path).resolve()
    if source_path == store.path.resolve():
        raise ValueError('来源库与灵感库不能使用同一文件')
    result = dict(scanned=0, cards_found=0, new_cards=0, updated=0)
    try:
        source = sqlite3.connect(source_path.as_uri() + '?mode=ro', uri=True)
    except sqlite3.Error:
        raise SourceSchemaError('无法只读打开来源消息库，请检查文件及权限') from None
    try:
        source.execute('PRAGMA query_only=ON')
        columns = {row[1] for row in source.execute('PRAGMA table_info(messages)')}
        if not columns:
            raise SourceSchemaError('来源消息库缺少 messages 表')
        missing = {'app_id', 'message_id', 'created_at', 'payload'} - columns
        if missing:
            raise SourceSchemaError('来源 messages 表缺少字段：' + '、'.join(sorted(missing)))
        # The two string fields break timestamp ties, including different apps.
        watermark = store.get_meta(WATERMARK_KEY, [-1, '', ''])
        query = '''SELECT created_at,message_id,app_id,payload FROM messages
            WHERE (payload LIKE '%finderFeed%' OR payload LIKE '%finder_description%')
            AND (created_at,message_id,app_id) > (?,?,?)
            ORDER BY created_at,message_id,app_id'''
        parameters = list(watermark)
        if limit is not None:
            query += ' LIMIT ?'
            parameters.append(limit)
        for seen_at, message_id, app_id, payload in source.execute(query, parameters):
            if not isinstance(seen_at, (int, float)) or not math.isfinite(seen_at):
                raise SourceSchemaError('来源 messages 的 created_at 必须是有效秒级时间')
            if not isinstance(message_id, str) or not isinstance(app_id, str):
                raise SourceSchemaError('来源 messages 的消息标识和应用标识必须是文本')
            result['scanned'] += 1
            cards = parse_finder_card(payload)
            result['cards_found'] += len(cards)
            for card in cards:
                is_new = store.upsert_card(card, message_id, seen_at)
                result['new_cards' if is_new else 'updated'] += 1
            store.set_meta(WATERMARK_KEY, [seen_at, message_id, app_id])
    except sqlite3.Error:
        raise SourceSchemaError('读取来源消息库失败，请检查表结构和数据库完整性') from None
    finally:
        source.close()
    return result
