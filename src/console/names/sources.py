"""Metadata reads only; absent sources never create files or sidecars."""
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
import math
import sqlite3
import time

STATE_PATH = Path('/var/lib/imgw/wechat/api_state.sqlite3')
DIRECTORY_PATH = Path('/var/lib/imgw/ops-app/wechat-directory.sqlite3')
MEMBERS_PATH = Path('/var/lib/imgw/ops-app/names/names.sqlite3')
DOSSIERS_PATH = Path('/var/lib/imgw/ops-app/dossiers/dossiers.sqlite3')
CONTACTS_PATH = Path('/var/lib/imgw/wechat/private-contact-directory-v2/contacts.sqlite3')


def quote(value):
    return '"' + value.replace('"', '""') + '"'


def schema(db):
    return {name: {r[1] for r in db.execute('PRAGMA table_info(' + quote(name) + ')')}
            for name, in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")}


@contextmanager
def readonly(path):
    path = Path(path).absolute()
    if any(p.is_symlink() for p in (path, *path.parents)) or not path.is_file():
        raise FileNotFoundError('名称来源暂不可读')
    wal, journal = Path(str(path) + '-wal'), Path(str(path) + '-journal')
    live = wal.exists() and wal.stat().st_size > 0
    if live and not Path(str(path) + '-shm').exists() or journal.exists() and journal.stat().st_size:
        raise ValueError('名称来源暂不可读')
    db = sqlite3.connect(path.as_uri() + '?mode=ro' + ('' if live else '&immutable=1'), uri=True, timeout=1)
    try:
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        db.execute('PRAGMA temp_store=MEMORY')
        deadline = time.monotonic() + 5
        db.set_progress_handler(lambda: int(time.monotonic() >= deadline), 10000)
        db.execute('BEGIN')
        yield db
    finally:
        db.close()


def stamp(path):
    try:
        def stat(p):
            info = p.stat()
            return info.st_dev, info.st_ino, info.st_mtime_ns, info.st_size
        path = Path(path)
        wal = Path(str(path) + '-wal')
        return stat(path), stat(wal) if wal.exists() else None
    except OSError:
        return None


def seconds(value):
    try:
        number = float(value)
        return number if math.isfinite(number) else 0
    except (ValueError, TypeError):
        try:
            parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
            return parsed.timestamp() if parsed.utcoffset() is not None else 0
        except (ValueError, TypeError, AttributeError, OverflowError, OSError):
            return 0


def groups_with_messages(path):
    with readonly(path) as db:
        columns = schema(db).get('messages', set())
        if 'group_id' in columns:
            rows = db.execute('SELECT DISTINCT group_id FROM messages')
        elif 'payload' in columns:
            # Legacy fixtures have only payload; production uses indexed metadata.
            rows = db.execute("SELECT DISTINCT json_extract(CASE WHEN json_valid(payload) THEN payload END,'$.group_id') FROM messages")
        else:
            raise ValueError('群消息统计暂不可读')
        return sorted({identity for identity, in rows if isinstance(identity, str)
                       and identity.endswith('@chatroom')})
