"""Read-only source snapshots shared by all three API builders."""
from contextlib import closing, contextmanager
from functools import lru_cache
from pathlib import Path
import sqlite3
import threading

from .model import aggregate_messages
from console.customers import aggregate_customers

INBOUND_PATH = Path('/var/lib/imgw/icbu/inbound.sqlite3')
WECHAT_PATH = Path('/var/lib/imgw/wechat/api_state.sqlite3')
WHATSAPP_PATH = Path('/var/lib/imgw/whatsapp')
DRAFTS_PATH = Path('/var/lib/imgw/ops-app/drafts.sqlite3')


@contextmanager
def read_database(path):
    with closing(sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)) as db:
        db.execute('PRAGMA query_only=ON')
        yield db


def _stat(path):
    try:
        stat = path.stat()
        return stat.st_mtime_ns, stat.st_size, stat.st_dev, stat.st_ino
    except FileNotFoundError:
        return None


class InboundCache:
    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.Lock()
        self.stamp = self.snapshot = None

    def _stamp(self):
        main = _stat(self.path)
        # WAL commits do not necessarily touch the main database mtime/size.
        return (main, _stat(Path(str(self.path) + '-wal'))) if main else None

    def _load(self):
        with read_database(self.path) as db:
            conversations = aggregate_messages(db.execute('SELECT id,payload FROM inbound ORDER BY id'))
        return dict(ready=True, conversations=conversations,
                    customers=aggregate_customers(conversations.values()))

    def read(self):
        with self.lock:
            stamp = self._stamp()
            if stamp is None:
                self.stamp = self.snapshot = None
                return dict(ready=False, conversations={}, customers={})
            if stamp == self.stamp:
                return self.snapshot
            self.stamp = self.snapshot = None
            snapshot = self._load()
            if self._stamp() == stamp:
                self.stamp, self.snapshot = stamp, snapshot
            return snapshot


@lru_cache(maxsize=32)
def _cache(path):
    return InboundCache(path)


def inbound_cache(path=INBOUND_PATH):
    return _cache(str(Path(path).resolve()))
