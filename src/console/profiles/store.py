"""Lazy reads; dry runs use a read-only backup into memory and never create a sidecar."""
from contextlib import contextmanager
from pathlib import Path
import fcntl
import os
import sqlite3

from console.dossiers.store import readonly
from .schema import SCHEMA, profile_view

DB_PATH = Path('/var/lib/imgw/ops-app/profiles/profiles.sqlite3')


@contextmanager
def working_database(path, dry_run=False):
    path = Path(path)
    if dry_run:
        db = sqlite3.connect(':memory:')
        try:
            if path.exists():
                with readonly(path) as source:
                    source.backup(db)
            db.row_factory = sqlite3.Row
            db.executescript(SCHEMA)
            yield db
        finally:
            db.close()
        return
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(str(path) + '.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        target = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        os.fchmod(target, 0o600)
        os.close(target)
        db = sqlite3.connect(path)
        try:
            db.row_factory = sqlite3.Row
            db.executescript(SCHEMA)
            yield db
        finally:
            db.close()
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


class Profiles:
    def __init__(self, path=DB_PATH):
        self.path = Path(path)

    def all(self):
        if not self.path.exists():
            return []
        with readonly(self.path) as db:
            return [profile_view(r) for r in db.execute('SELECT * FROM cp_profile ORDER BY updated_at DESC,conversation_key')]

    def get(self, key):
        if not self.path.exists():
            return None
        with readonly(self.path) as db:
            row = db.execute('SELECT * FROM cp_profile WHERE conversation_key=?', (key,)).fetchone()
            return profile_view(row) if row else None

    def list(self, values):
        from collections import Counter
        rows = self.all()
        filtered = [r for r in rows if all(not values.get(q) or r[f] == values[q]
                    for q, f in (('kind', 'kind'), ('stage', 'customer_stage'), ('channel', 'channel')))
                    and (not values.get('q') or values['q'].casefold() in ' '.join(str(r[k] or '') for k in
                         ('title', 'org_name', 'summary', 'products_json', 'people_json')).casefold())]
        return dict(items=filtered, counts=dict(kind=dict(Counter(r['kind'] for r in rows)),
                    stage=dict(Counter(r['customer_stage'] for r in rows if r['customer_stage']))),
                    updated_at=max((r['updated_at'] for r in rows), default=None))
