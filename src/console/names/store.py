"""Independent member-name cache; only validated metadata is retained."""
from contextlib import closing
import os
from pathlib import Path
import sqlite3

from console.directory.refresh import _check_path
from .clean import clean_name
from .sources import MEMBERS_PATH, readonly

SCHEMA = '''CREATE TABLE IF NOT EXISTS nm_members (
    group_id TEXT NOT NULL, wxid TEXT NOT NULL, display_name TEXT, nick_name TEXT,
    fetched_at REAL NOT NULL, PRIMARY KEY(group_id,wxid))'''


def member_rows(payload):
    if (not isinstance(payload, dict) or type(payload.get('ret')) is not int or payload['ret'] != 200
            or not isinstance(payload.get('data'), dict)
            or not isinstance(payload['data'].get('memberList'), list)
            or len(payload['data']['memberList']) > 100000):
        raise ValueError('接口没返回可确认的成员昵称')
    rows = {}
    for entry in payload['data']['memberList']:
        if not isinstance(entry, dict):
            raise ValueError('接口没返回可确认的成员昵称')
        identity = entry.get('wxid')
        if (not isinstance(identity, str) or not 0 < len(identity) <= 128
                or any(c.isspace() or ord(c) < 32 for c in identity) or identity in rows
                or identity.endswith('@chatroom') or identity.lower() == 'notify@all'):
            raise ValueError('接口没返回可确认的成员昵称')
        identity.encode('utf8')
        if any(entry.get(k) is not None and not isinstance(entry[k], str) for k in ('displayName', 'nickName')):
            raise ValueError('接口没返回可确认的成员昵称')
        rows[identity] = clean_name(entry.get('displayName')), clean_name(entry.get('nickName'))
    return rows


class MembersCache:
    def __init__(self, path=MEMBERS_PATH):
        self.path = Path(path).absolute()

    def prepare(self):
        _check_path(self.path)
        if self.path.exists() and self.path.stat().st_size:
            with readonly(self.path) as db:
                tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if not tables <= {'nm_members'}:
                    raise ValueError('成员昵称缓存须使用独立文件')
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        except FileExistsError:
            _check_path(self.path)
        else:
            os.close(fd)
        self.path.chmod(0o600)

    def save(self, group_id, rows, fetched_at):
        self.prepare()
        with closing(sqlite3.connect(self.path.as_uri() + '?mode=rw', uri=True, timeout=5)) as db, db:
            db.execute(SCHEMA)
            # Empty, partial and failed replies never erase a previously known name.
            db.executemany('INSERT INTO nm_members VALUES(?,?,?,?,?) ON CONFLICT(group_id,wxid) DO UPDATE SET '
                           'display_name=COALESCE(excluded.display_name,nm_members.display_name),'
                           'nick_name=COALESCE(excluded.nick_name,nm_members.nick_name),fetched_at=excluded.fetched_at',
                           [(group_id, identity, display, nick, fetched_at)
                            for identity, (display, nick) in rows.items()])
