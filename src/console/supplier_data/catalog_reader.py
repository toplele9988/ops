"""Read the explicit catalog path using both SQLite read-only protections."""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import sqlite3

from .labels import product_label


class CatalogError(ValueError):
    """An unavailable catalog or incompatible catalog schema."""


REQUIRED_COLUMNS = {
    'equipment_specs': ('equipment_id', 'supplier', 'fixture_type', 'wattage',
                        'exw_price', 'currency', 'asset_id'),
    'knowledge_chunks': ('chunk_id', 'equipment_id', 'text'),
    'multimedia_assets': ('asset_id', 'filename', 'source_type'),
}
OPTIONAL_COLUMNS = ('model', 'name', 'product_name', 'description')


@contextmanager
def open_catalog(db_path):
    """Expose a query-only, mode=ro connection; close it even on failure."""
    path = Path(db_path).expanduser().resolve()
    if not path.is_file():
        raise CatalogError(f'知识目录库不存在或不是文件：{path}')
    try:
        connection = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)
    except sqlite3.Error as exc:
        raise CatalogError(f'无法只读打开知识目录库：{exc}') from exc
    try:
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA query_only=1')
        yield connection
    finally:
        connection.close()


def _columns(connection):
    available = {}
    for table, required in REQUIRED_COLUMNS.items():
        exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        if not exists:
            raise CatalogError(f'知识目录库缺少表：{table}')
        # table names are constants, never values supplied by a caller.
        available[table] = {row['name'] for row in connection.execute(
            f'PRAGMA table_info({table})')}
        missing = set(required) - available[table]
        if missing:
            raise CatalogError(f'知识目录库表 {table} 缺少列：' + ', '.join(sorted(missing)))
    return available


def read_products(db_path):
    """Yield product mappings with a list of {chunk_id, text} evidence chunks.

    equipment_id is the catalog's attribution boundary. Every product remains
    separate even when suppliers reuse model names. Products without text are
    retained. No free-text model matching or cross-product evidence transfer.
    """
    with open_catalog(db_path) as connection:
        try:
            connection.execute('BEGIN')
            columns = _columns(connection)
            fields = list(REQUIRED_COLUMNS['equipment_specs'])
            fields.extend(name for name in OPTIONAL_COLUMNS
                          if name in columns['equipment_specs'])
            selected = ', '.join('e.' + name for name in fields)
            rows = connection.execute(
                f'SELECT {selected}, a.filename AS source_file, a.source_type, '
                'k.chunk_id AS source_chunk_id, k.text AS source_text '
                'FROM equipment_specs e LEFT JOIN multimedia_assets a ON e.asset_id=a.asset_id '
                'LEFT JOIN knowledge_chunks k '
                'ON e.equipment_id=k.equipment_id ORDER BY e.equipment_id, k.chunk_id')
            duplicates = connection.execute(
                'SELECT equipment_id FROM equipment_specs GROUP BY equipment_id '
                'HAVING COUNT(*)>1 LIMIT 1').fetchone()
            if duplicates:
                raise CatalogError('equipment_specs 的 equipment_id 不唯一，无法安全归属产品')
            duplicate_assets = connection.execute(
                'SELECT asset_id FROM multimedia_assets WHERE asset_id IS NOT NULL '
                'GROUP BY asset_id HAVING COUNT(*)>1 LIMIT 1').fetchone()
            if duplicate_assets:
                raise CatalogError('multimedia_assets 的 asset_id 不唯一，无法安全关联来源文件')
            product = None
            for row in rows:
                if row['equipment_id'] is None or not str(row['equipment_id']).strip():
                    raise CatalogError('equipment_specs 存在空 equipment_id，无法归属产品')
                if product is None or row['equipment_id'] != product['equipment_id']:
                    if product is not None:
                        yield product
                    product = {name: row[name] for name in fields}
                    product['name'] = product.get('name') or product.get('product_name')
                    product['source_file'] = row['source_file'] or ''
                    product['source_type'] = row['source_type'] or ''
                    product['label'] = product_label(product)
                    product['chunks'] = []
                if row['source_chunk_id'] is not None:
                    text = row['source_text']
                    if text is not None and not isinstance(text, str):
                        raise CatalogError(f'knowledge_chunks.text 必须是文本，chunk_id={row["source_chunk_id"]}')
                    product['chunks'].append({'chunk_id': row['source_chunk_id'], 'text': text or ''})
            if product is not None:
                yield product
        except sqlite3.Error as exc:
            raise CatalogError(f'读取知识目录库失败：{exc}') from exc
