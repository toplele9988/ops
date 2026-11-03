"""Read bridge metadata only; download is a separate explicit operation."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3


def scan(path, store, limit=500):
    if not isinstance(limit, int) or not 1 <= limit <= 5000:
        raise ValueError('读取数量无效')
    with closing(sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)) as db:
        db.execute('PRAGMA query_only=ON')
        rows = db.execute('SELECT session,message_id,chat_id,media_json FROM messages WHERE has_media=1 ORDER BY received DESC LIMIT ?', (limit,)).fetchall()
    count = 0
    for session, mid, chat_id, raw in rows:
        try:
            media = json.loads(raw)
            if not isinstance(media, dict):
                media = {}
            mime = media.get('mimetype') or ''
            if not isinstance(mime, str):
                mime = ''
            kind = ('image' if mime.startswith('image/') else 'video' if mime.startswith('video/') else
                    'voice' if mime.startswith('audio/') else 'file')
            store.register(session, mid, kind, media.get('filename') or '', chat_id)
            count += 1
        except (ValueError, TypeError, UnicodeError):
            continue
    return count
