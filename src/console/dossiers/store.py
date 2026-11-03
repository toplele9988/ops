"""No constructor opens a database or runs a migration."""
from contextlib import contextmanager
import os
from pathlib import Path
import sqlite3
import time

ROOT = Path(os.environ.get('OPS_DOSSIER_ROOT', '/var/lib/imgw/ops-app/dossiers'))
DB_PATH = ROOT / 'dossiers.sqlite3'


@contextmanager
def readonly(path):
    path = Path(path).absolute()
    if path.is_symlink() or not path.is_file():
        raise ValueError('dossier_unavailable')
    wal = Path(str(path) + '-wal')
    journal = Path(str(path) + '-journal')
    live = wal.exists() and wal.stat().st_size > 0
    if live and not Path(str(path) + '-shm').exists():
        raise ValueError('wal_snapshot_unavailable')
    if journal.exists() and journal.stat().st_size:
        raise ValueError('journal_snapshot_unavailable')
    before = path.stat()
    db = sqlite3.connect(path.as_uri() + '?mode=ro' + ('' if live else '&immutable=1'), uri=True, timeout=2)
    try:
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        db.execute('PRAGMA temp_store=MEMORY')
        deadline = time.monotonic() + 5
        db.set_progress_handler(lambda: int(time.monotonic() > deadline), 10000)
        db.execute('BEGIN')
        yield db
    finally:
        db.close()
    after = path.stat()
    if not live and ((wal.exists() and wal.stat().st_size > 0)
            or (journal.exists() and journal.stat().st_size > 0)
            or (before.st_ino, before.st_size, before.st_mtime_ns) !=
               (after.st_ino, after.st_size, after.st_mtime_ns)):
        raise ValueError('snapshot_changed')
