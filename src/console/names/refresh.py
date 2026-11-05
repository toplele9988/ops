"""Durable admissions for read-only directory and member-name refreshes."""
import hashlib
import math
import time

from console.directory import WechatDirectory
from console.directory.errors import RefreshFailed
from console.directory.refresh import RefreshCache
from console.directory.wtapi_source import build_readonly_refresh_client, build_readonly_members_client
from .resolver import NamesResolver
from .sources import STATE_PATH, groups_with_messages
from .store import MembersCache, member_rows

DIRECTORY_INTERVAL = 6 * 3600
MEMBERS_INTERVAL = 86400
FAILURE_BACKOFF = 600
RETRY_INTERVAL = 3600


class NamesRefresh:
    def __init__(self, store, clock, *, state_path=STATE_PATH, directory_path=None,
                 members_path=None, directory=None, members_client_factory=None,
                 group_reader=None, timeout=25):
        self.store, self.clock = store, clock
        self.resolver = NamesResolver(state_path, directory_path=directory_path, members_path=members_path)
        self.directory = directory if directory is not None else WechatDirectory(
            state_path=state_path, cache_path=self.resolver.directory_path, clock=clock,
            readonly_client_factory=lambda: build_readonly_refresh_client(self.directory.config_path),
            refresh_timeout=timeout)
        self.members_client_factory = members_client_factory or (
            lambda: build_readonly_members_client(self.directory.config_path))
        self.group_reader = group_reader or (lambda: groups_with_messages(state_path))
        self.cache = MembersCache(self.resolver.members_path)
        # Reuse the existing directory deadline runner. Calling _call_once does
        # not prepare/write a directory cache or reserve any network admission.
        self.calls = RefreshCache(self.resolver.directory_path, timeout=timeout)
        protected = (self.resolver.state_path, self.resolver.directory_path,
                     self.resolver.dossiers_path, self.resolver.contacts_path, self.store.path)
        if any(self.cache.path.resolve() == p.resolve() or (self.cache.path.exists() and p.exists()
                   and self.cache.path.samefile(p)) for p in protected):
            raise ValueError('成员昵称缓存须独立于来源和工作记录')

    def _claim(self, name, now, interval):
        with self.store.write() as db:
            row = db.execute('SELECT position FROM operator_cursors WHERE name=?', (name,)).fetchone()
            if row and now * 1000 - row['position'] < interval * 1000:
                return False
            self._write_cursor(db, name, now)
        return True

    @staticmethod
    def _write_cursor(db, name, now):
        db.execute("INSERT INTO operator_cursors VALUES(?,'clock-ms',?) ON CONFLICT(name) "
                   'DO UPDATE SET generation=excluded.generation,position=excluded.position',
                   (name, math.ceil(now * 1000)))

    def _claim_groups(self, groups, now):
        with self.store.write() as db:
            previous = db.execute("SELECT position FROM operator_cursors WHERE name='names:members-backoff'").fetchone()
            if previous and now * 1000 - previous['position'] < FAILURE_BACKOFF * 1000:
                return []
            due = []
            for group in sorted(set(groups)):
                name = 'names:member:' + hashlib.sha256(group.encode()).hexdigest()
                row = db.execute('SELECT position FROM operator_cursors WHERE name=?', (name,)).fetchone()
                if not row or now * 1000 - row['position'] >= MEMBERS_INTERVAL * 1000:
                    due.append((row['position'] if row else -1, group, name))
            chosen = sorted(due)[:3]
            for _, _, name in chosen:
                self._write_cursor(db, name, now)
        return [group for _, group, _ in chosen]

    def poll(self):
        now = self.clock()
        result = dict(directory_attempted=0, groups_attempted=0, groups_saved=0, failures=0)
        if self._claim('names:directory', now, DIRECTORY_INTERVAL):
            result['directory_attempted'] = 1
            try:
                refreshed = self.directory.refresh()
                if refreshed.get('group_failures'):
                    raise RefreshFailed('部分群名暂未取得')
            except Exception:
                result['failures'] += 1
                # The provider often answers group names only partly; try again in an hour, not six.
                with self.store.write() as db:
                    self._write_cursor(db, 'names:directory', now - DIRECTORY_INTERVAL + RETRY_INTERVAL)
                self.store.health('names-directory', now, RefreshFailed('通讯录刷新未完成'))
            else:
                self.store.health('names-directory', now)
        member_failures = 0
        try:
            # Reading failure also has a durable backoff, including after restart.
            with self.store.read() as db:
                row = db.execute("SELECT position FROM operator_cursors WHERE name='names:members-backoff'").fetchone() if db else None
                if row and now * 1000 - row['position'] < FAILURE_BACKOFF * 1000:
                    return result
            groups = self._claim_groups(self.group_reader(), now)
            if not groups:
                return result
            client = self.members_client_factory()
            failed = False
            for group in groups:
                result['groups_attempted'] += 1
                try:
                    payload = self.calls._call_once(lambda: client.get_chatroom_member_list(group),
                                                    time.monotonic() + self.calls.timeout)
                    rows = member_rows(payload)
                    if not any(display or nick for display, nick in rows.values()):
                        raise RefreshFailed('接口没返回成员昵称')
                    self.cache.save(group, rows, self.clock())
                    result['groups_saved'] += 1
                except Exception:
                    result['failures'] += 1
                    member_failures += 1
                    failed = True
            if failed:
                raise RefreshFailed('部分成员昵称暂未取得')
        except Exception:
            if member_failures == 0:
                result['failures'] += 1
            with self.store.write() as db:
                self._write_cursor(db, 'names:members-backoff', now)
            self.store.health('names-members', now, RefreshFailed('成员昵称刷新未完成'))
        else:
            self.store.health('names-members', now)
        return result
