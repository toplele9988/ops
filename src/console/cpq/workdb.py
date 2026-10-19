"""Writable CPQ storage, separate from the read-only catalog."""
from pathlib import Path
import sqlite3

MIGRATION = Path(__file__).with_name('migrations') / '20261019_task01_cpq.sql'


def open_work(path: str | Path) -> sqlite3.Connection:
    path = Path(path).expanduser().absolute()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=5.0)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA busy_timeout=5000')
        conn.execute('PRAGMA journal_mode=WAL')
        return conn
    except BaseException:
        conn.close()
        raise


def ensure_schema(conn: sqlite3.Connection) -> None:
    if conn.in_transaction:
        raise RuntimeError('ensure_schema requires no active transaction')
    script = MIGRATION.read_text(encoding='utf-8')
    try:
        conn.executescript('BEGIN IMMEDIATE;\n' + script + '\nCOMMIT;')
    except BaseException:
        conn.rollback()
        raise
