"""Authenticated GET-only view of two unsendable dry-run stores. Never migrates."""
from pathlib import Path
from datetime import datetime, timezone
from starlette.routing import Route
from .archive.store import ARCHIVE_PATH
from .outbox.store import OUTBOX_PATH
from .inbox.store import read_database
from .inbox.http import get_handler
from .inbox.model import BadRequest
import sqlite3


def read_drafts(archive_path, outbox_path):
    result = []
    for source, path, table in [('inbox', outbox_path, 'inbox_dry_run_drafts'),
                                ('parties', archive_path, 'parties_outbox')]:
        if not Path(path).exists():
            continue
        with read_database(Path(path)) as db:
            db.row_factory = sqlite3.Row
            if not db.execute('SELECT 1 FROM sqlite_master WHERE name=?', (table,)).fetchone():
                continue
            # Table names are constant; caller input never reaches SQL identifiers.
            for row in db.execute('SELECT * FROM ' + table + ' WHERE dry_run=1 ORDER BY id DESC LIMIT 500'):
                d = dict(row)
                result.append({k: d[k] for k in ('id', 'conversation_key', 'text', 'state', 'created_at')} |
                              dict(source=source, dry_run=True, reviewed_at=d.get('reviewed_at')))
    def stamp(row):
        at = row['created_at']
        return at if isinstance(at, (int, float)) else datetime.fromisoformat(at.replace('Z', '+00:00')).astimezone(timezone.utc).timestamp()
    result.sort(key=stamp, reverse=True)
    return dict(items=result, dry_run=True)


def build(app_module, archive_path=ARCHIVE_PATH, outbox_path=OUTBOX_PATH):
    def parse(request):
        if request.query_params:
            raise BadRequest('草稿列表不接受筛选参数')
    return [Route('/api/v1/ops/drafts', get_handler(app_module, parse,
        lambda _: (read_drafts(archive_path, outbox_path), 200), '草稿暂时无法读取'), methods=['GET'], name='ops_drafts')]
