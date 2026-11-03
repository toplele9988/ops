"""Small SQLite catalog. Source ingestion never overwrites human/AI decisions."""
import json
import math
import os
from pathlib import Path
import sqlite3
import time
from contextlib import contextmanager

RELEVANCES = ('ai_dev', 'business', 'industry', 'other', 'unknown')
STATUSES = ('new', 'evaluating', 'trying', 'adopted', 'ignored')
CONFIDENCES = ('high', 'medium', 'low')
KINDS = ('skill', 'project', 'tool', 'plugin', 'method')

_SCHEMA = '''
CREATE TABLE IF NOT EXISTS cards (
    card_id TEXT PRIMARY KEY,
    author TEXT NOT NULL, text TEXT NOT NULL, tags_json TEXT NOT NULL,
    urls_json TEXT NOT NULL, first_seen REAL NOT NULL, last_seen REAL NOT NULL,
    times_seen INTEGER NOT NULL DEFAULT 1, source_message_ids_json TEXT NOT NULL,
    relevance TEXT NOT NULL DEFAULT 'unknown'
        CHECK(relevance IN ('ai_dev','business','industry','other','unknown')),
    summary TEXT NOT NULL DEFAULT '', items_json TEXT NOT NULL DEFAULT '[]',
    usefulness TEXT NOT NULL DEFAULT '', how_to_use TEXT NOT NULL DEFAULT '',
    confidence TEXT NOT NULL DEFAULT 'low' CHECK(confidence IN ('high','medium','low')),
    enriched_at REAL, enrich_model TEXT NOT NULL DEFAULT '',
    enrich_error_count INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'new'
        CHECK(status IN ('new','evaluating','trying','adopted','ignored')),
    note TEXT NOT NULL DEFAULT '', updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS cards_seen ON cards(last_seen DESC, card_id);
CREATE INDEX IF NOT EXISTS cards_pending ON cards(relevance, enrich_error_count);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
'''


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def _row(row):
    if row is None:
        return None
    result = dict(row)
    for field in ('tags', 'urls', 'items', 'source_message_ids'):
        result[field] = json.loads(result.pop(field + '_json'))
    result['enriched'] = result['enriched_at'] is not None
    return result


def validate_filters(q='', relevance=None, status=None, page=1, page_size=20,
                     order='last_seen'):
    if not isinstance(q, str) or len(q) > 500:
        raise ValueError('搜索词不能超过500字')
    if relevance not in (None, '', *RELEVANCES):
        raise ValueError('分类参数无效')
    if status not in (None, '', *STATUSES):
        raise ValueError('状态参数无效')
    if type(page) is not int or page < 1 or page > 1_000_000:
        raise ValueError('页码须为1至1000000的整数')
    if type(page_size) is not int or not 1 <= page_size <= 100:
        raise ValueError('每页数量须为1至100的整数')
    if order not in ('last_seen', 'first_seen', 'updated_at'):
        raise ValueError('排序参数无效')


class InspirationStore:
    def __init__(self, path, *, read_only=False):
        self.path = Path(path)
        self.read_only = read_only
        if read_only:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        # O_EXCL prevents truncation; restore process umask even on failure.
        previous = os.umask(0o077)
        try:
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                pass
            else:
                os.close(fd)
        finally:
            os.umask(previous)
        os.chmod(self.path, 0o600)
        with self._connection() as connection:
            connection.execute('PRAGMA journal_mode=WAL')
            connection.executescript(_SCHEMA)

    @contextmanager
    def _connection(self):
        if self.read_only:
            connection = sqlite3.connect(self.path.resolve().as_uri() + '?mode=ro', uri=True)
            connection.execute('PRAGMA query_only=ON')
        else:
            connection = sqlite3.connect(str(self.path))
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def upsert_card(self, card, message_id, seen_at):
        """Return True for a new card; otherwise count this source occurrence."""
        seen_at = float(seen_at)
        if not math.isfinite(seen_at):
            raise ValueError('消息时间无效')
        with self._connection() as connection:
            connection.execute('BEGIN IMMEDIATE')
            old = connection.execute('SELECT source_message_ids_json FROM cards WHERE card_id=?',
                                     (card['card_id'],)).fetchone()
            if old is None:
                connection.execute('''INSERT INTO cards
                    (card_id,author,text,tags_json,urls_json,first_seen,last_seen,
                     source_message_ids_json,updated_at) VALUES (?,?,?,?,?,?,?,?,?)''',
                    (card['card_id'], card.get('author', ''), card.get('text', ''),
                     _json(card.get('tags', [])), _json(card.get('urls', [])),
                     seen_at, seen_at, _json([str(message_id)]), time.time()))
                return True
            ids = json.loads(old['source_message_ids_json'])
            if str(message_id) not in ids:
                ids.append(str(message_id))
            connection.execute('''UPDATE cards SET last_seen=MAX(last_seen,?),
                times_seen=times_seen+1, source_message_ids_json=?, updated_at=? WHERE card_id=?''',
                (seen_at, _json(ids[-20:]), time.time(), card['card_id']))
            return False

    def list_cards(self, q='', relevance=None, status=None, page=1, page_size=20,
                   order='last_seen'):
        validate_filters(q, relevance, status, page, page_size, order)
        conditions, values = [], []
        for column, value in (('relevance', relevance), ('status', status)):
            if value:
                conditions.append(column + '=?')
                values.append(value)
        if q:
            # All LIKE metacharacters, including our escape character, are literal.
            pattern = '%' + q.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
            columns = ('author', 'text', 'summary')
            clauses = [f"{c} LIKE ? ESCAPE '\\'" for c in columns]
            clauses.extend(["EXISTS (SELECT 1 FROM json_each(cards.tags_json) "
                            "WHERE value LIKE ? ESCAPE '\\')",
                            "EXISTS (SELECT 1 FROM json_each(cards.items_json) "
                            "WHERE json_extract(value,'$.name') LIKE ? ESCAPE '\\')"])
            conditions.append('(' + ' OR '.join(clauses) + ')')
            values.extend([pattern] * len(clauses))
        where = ' WHERE ' + ' AND '.join(conditions) if conditions else ''
        with self._connection() as connection:
            connection.execute('BEGIN')
            total = connection.execute('SELECT COUNT(*) FROM cards' + where, values).fetchone()[0]
            rows = connection.execute('SELECT * FROM cards' + where +
                f' ORDER BY {order} DESC, card_id ASC LIMIT ? OFFSET ?',
                [*values, page_size, (page - 1) * page_size]).fetchall()
        return [_row(row) for row in rows], total

    def get_card(self, card_id):
        with self._connection() as connection:
            return _row(connection.execute('SELECT * FROM cards WHERE card_id=?', (card_id,)).fetchone())

    def set_status(self, card_id, status, note=''):
        if status not in STATUSES:
            raise ValueError('状态无效')
        if not isinstance(note, str) or len(note) > 500:
            raise ValueError('备注不能超过500字')
        with self._connection() as connection:
            changed = connection.execute('UPDATE cards SET status=?,note=?,updated_at=? WHERE card_id=?',
                                         (status, note, time.time(), card_id)).rowcount
        return bool(changed)

    def pending_enrichment(self, limit=100):
        if type(limit) is not int or limit < 1:
            raise ValueError('整理数量须为正整数')
        with self._connection() as connection:
            rows = connection.execute('''SELECT * FROM cards WHERE relevance='unknown'
                AND enrich_error_count<3 ORDER BY first_seen,card_id LIMIT ?''', (limit,)).fetchall()
        return [_row(row) for row in rows]

    def save_enrichment(self, card_id, result, model):
        from .enrich import validate_enrichment
        card = self.get_card(card_id)
        if card is None:
            return False
        safe = validate_enrichment(card, result)
        now = time.time()
        with self._connection() as connection:
            changed = connection.execute('''UPDATE cards SET relevance=?,summary=?,items_json=?,
                usefulness=?,how_to_use=?,confidence=?,enriched_at=?,enrich_model=?,updated_at=?
                WHERE card_id=?''', (safe['relevance'], safe['summary'], _json(safe['items']),
                safe['usefulness'], safe['how_to_use'], safe['confidence'], now,
                str(model)[:200], now, card_id)).rowcount
        return bool(changed)

    def record_enrich_error(self, card_id):
        with self._connection() as connection:
            connection.execute('''UPDATE cards SET enrich_error_count=enrich_error_count+1,
                updated_at=? WHERE card_id=?''', (time.time(), card_id))

    def counts(self):
        result = {'total': 0, 'relevance': dict.fromkeys(RELEVANCES, 0),
                  'status': dict.fromkeys(STATUSES, 0)}
        with self._connection() as connection:
            for row in connection.execute('SELECT relevance,status,COUNT(*) AS n FROM cards GROUP BY relevance,status'):
                result['total'] += row['n']
                result['relevance'][row['relevance']] += row['n']
                result['status'][row['status']] += row['n']
        return result

    def get_meta(self, key, default=None):
        with self._connection() as connection:
            row = connection.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
        return json.loads(row['value']) if row else default

    def set_meta(self, key, value):
        with self._connection() as connection:
            connection.execute('INSERT INTO meta(key,value) VALUES (?,?) '
                'ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key, _json(value)))
