"""Private classification database. No source database is created or migrated."""
from contextlib import closing, contextmanager
import os
from pathlib import Path
import sqlite3
import stat
import threading

PATH = Path('/var/lib/imgw/ops-app/classify.sqlite3')
LOCK = threading.RLock()
SCHEMA = (
    """CREATE TABLE IF NOT EXISTS suggestions (
      key TEXT PRIMARY KEY, channel TEXT NOT NULL, name TEXT NOT NULL, kind TEXT NOT NULL,
      first_seen REAL, discovered_at REAL NOT NULL, day TEXT NOT NULL,
      suggested_role TEXT NOT NULL, confidence REAL NOT NULL, reasons_json TEXT NOT NULL,
      conflict INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'pending', decision_role TEXT,
      revision INTEGER NOT NULL DEFAULT 0, party_id INTEGER, updated_at REAL NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS classification_events (
      id INTEGER PRIMARY KEY, at REAL NOT NULL, who TEXT NOT NULL, key TEXT NOT NULL,
      action TEXT NOT NULL, revision INTEGER NOT NULL)""",
    'CREATE INDEX IF NOT EXISTS suggestions_day ON suggestions(day,status,confidence)',
)


def safe_path(path):
    path = Path(os.path.abspath(path))
    if any(parent.is_symlink() for parent in (path, *path.parents)):
        raise ValueError('分类库路径无效，请检查本地路径配置')
    if path.exists():
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError('分类库路径无效，请使用独立普通文件')
    for suffix in ('-wal', '-shm', '-journal'):
        side = Path(str(path) + suffix)
        if side.exists() or side.is_symlink():
            info = side.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError('分类库附属路径无效，请检查本地路径配置')
    return path


class Store:
    def __init__(self, path=None):
        self.path = Path(path if path is not None else os.environ.get('OPS_CLASSIFY_PATH', PATH))

    @contextmanager
    def read(self):
        path = safe_path(self.path)
        if not path.exists():
            yield None
            return
        with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as db:
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA query_only=ON')
            yield db

    @contextmanager
    def write(self, archive_path=None):
        with LOCK:
            path = safe_path(self.path)
            # Reject a misdirected existing source before changing permissions or schema.
            with self.read() as existing:
                tables = {r[0] for r in existing.execute("SELECT name FROM sqlite_master WHERE type='table'")} if existing else set()
                if tables - {'suggestions', 'classification_events'}:
                    raise ValueError('分类库必须使用独立文件，请检查路径配置')
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            fd = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
            try:
                os.fchmod(fd, 0o600)
            finally:
                os.close(fd)
            with closing(sqlite3.connect(path.as_uri() + '?mode=rw', uri=True, timeout=10)) as db:
                db.row_factory = sqlite3.Row
                db.execute('PRAGMA foreign_keys=ON')
                # An attached archive shares the short writer transaction. Archive.write
                # receives this transaction; its migration entrypoint is never called.
                if archive_path is not None:
                    archive = safe_path(archive_path)
                    if archive == path or not archive.exists():
                        raise ValueError('往来单位名录尚未准备好，请先检查档案库')
                    db.execute('ATTACH DATABASE ? AS directory', (archive.as_uri() + '?mode=rw',))
                    required = {'id', 'kind', 'name', 'short_name', 'aliases_json', 'conversation_ref',
                                'notes', 'status', 'created_at', 'updated_at', 'country'}
                    columns = {r[1] for r in db.execute('PRAGMA directory.table_info(parties)')}
                    if not required <= columns:
                        raise ValueError('往来单位名录结构不兼容，请交由管理员检查')
                db.execute('BEGIN IMMEDIATE')
                with db:
                    tables = {r[0] for r in db.execute("SELECT name FROM main.sqlite_master WHERE type='table'")}
                    if tables - {'suggestions', 'classification_events'}:
                        raise ValueError('分类库必须使用独立文件，请检查路径配置')
                    for statement in SCHEMA:
                        db.execute(statement)
                    yield db
