"""Bridge archive ingestion, bounded WAHA fallback and a receive-only work queue."""
import json
import fcntl
import os
from pathlib import Path
import re
import sqlite3
import stat
import time
from contextlib import closing
from urllib.parse import quote, unquote, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from .bridge import BridgeArchive, MEDIA_ROOT
from .store import MAX_BYTES, MIME, MediaError

CHAT = re.compile(r'^[0-9A-Za-z._-]+@(c\.us|g\.us|lid)$')
FILE = re.compile(r'[A-Za-z0-9_@.+-]{1,700}\Z')
MAX_ATTEMPTS = 4


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError('附件来源不可用')


class WahaDownload:
    """Only GET the configured loopback WAHA and its validated local file endpoint."""
    def __init__(self, config, opener=None):
        base = urlsplit(config['waha_url'])
        if (base.scheme != 'http' or base.hostname not in ('127.0.0.1', 'localhost') or
                base.username is not None or base.password is not None or base.path not in ('', '/') or
                base.query or base.fragment):
            raise ValueError('渠道地址不可用')
        base.port
        self.base = config['waha_url'].rstrip('/')
        self.origin = (base.scheme, base.hostname, base.port or 80)
        self.session = config['session']
        if not isinstance(self.session, str) or not self.session or self.session in ('.', '..'):
            raise ValueError('渠道会话不可用')
        self.headers = {'X-Api-Key': config['waha_api_key']}
        self.opener = opener or build_opener(ProxyHandler({}), NoRedirect())

    def _read(self, url, limit):
        with self.opener.open(Request(url, headers=self.headers, method='GET'), timeout=10) as response:
            if response.status != 200:
                raise ValueError('附件来源不可用')
            length = response.headers.get('Content-Length')
            if length is not None and (not length.isdigit() or int(length) > limit):
                raise ValueError('附件超过大小上限')
            data = response.read(limit + 1)
            if not data or len(data) > limit:
                raise ValueError('附件为空或超过大小上限')
            return data

    def __call__(self, session, chat_id, message_id):
        if (session != self.session or not isinstance(chat_id, str) or not CHAT.fullmatch(chat_id) or
                not isinstance(message_id, str) or not 0 < len(message_id) <= 500 or
                message_id in ('.', '..') or '\x00' in message_id):
            raise ValueError('消息标识不可用')
        path = ('/api/' + quote(session, safe='') + '/chats/' + quote(chat_id, safe='') +
                '/messages/' + quote(message_id, safe='') + '?downloadMedia=true')
        result = json.loads(self._read(self.base + path, 1024 * 1024))
        if not isinstance(result, dict) or result.get('id') != message_id or result.get('hasMedia') is not True:
            raise ValueError('附件信息不可用')
        media = result.get('media')
        if not isinstance(media, dict) or media.get('mimetype') not in MIME or not isinstance(media.get('url'), str):
            raise ValueError('附件类型或来源不可用')
        source = urlsplit(media['url'])
        if (source.scheme, source.hostname, source.port or 80) != self.origin or source.username is not None or source.password is not None or source.query or source.fragment:
            raise ValueError('附件来源不可用')
        prefix = '/api/files/'
        if not source.path.startswith(prefix):
            raise ValueError('附件来源不可用')
        name = unquote(source.path[len(prefix):])
        if not FILE.fullmatch(name) or '..' in name:
            raise ValueError('附件来源不可用')
        # Rebuild the URL from the trusted base; never forward an upstream URL as-is.
        data = self._read(self.base + prefix + quote(name, safe='@._-+'), MAX_BYTES)
        return data, media['mimetype']


class IngestWorker:
    def __init__(self, store, queue_path, fetch, clock=time.time, *, bridge_db=None, bridge_media=MEDIA_ROOT):
        self.store, self.path, self.fetch, self.clock = store, Path(queue_path), fetch, clock
        self.bridge_media = Path(bridge_media)
        self.archive = BridgeArchive(bridge_db, self.bridge_media) if bridge_db is not None else None

    def _connect(self):
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError('附件队列不可用')
            os.fchmod(fd, 0o600)
        finally:
            os.close(fd)
        db = sqlite3.connect(self.path, timeout=10)
        db.execute('PRAGMA busy_timeout=10000')
        db.execute('CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, session TEXT NOT NULL, message_id TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, next_at REAL NOT NULL DEFAULT 0, lease_until REAL NOT NULL DEFAULT 0, done INTEGER NOT NULL DEFAULT 0)')
        db.execute('CREATE TABLE IF NOT EXISTS progress (source TEXT PRIMARY KEY, cursor INTEGER NOT NULL)')
        if 'archive_updated' not in {r[1] for r in db.execute('PRAGMA table_info(jobs)')}:
            with db:
                db.execute('BEGIN IMMEDIATE')
                if 'archive_updated' not in {r[1] for r in db.execute('PRAGMA table_info(jobs)')}:
                    db.execute('ALTER TABLE jobs ADD COLUMN archive_updated REAL')
        return db

    def enqueue(self, session, message_id, chat_id, media):
        if not isinstance(chat_id, str) or not CHAT.fullmatch(chat_id):
            raise ValueError('会话标识不可用')
        media = media if isinstance(media, dict) else {}
        mime = media.get('mimetype') if isinstance(media.get('mimetype'), str) else ''
        kind = ('image' if mime.startswith('image/') else 'video' if mime.startswith('video/') else
                'voice' if mime.startswith('audio/') else 'file')
        filename = media.get('filename')
        filename = filename if isinstance(filename, str) else ''
        key = self.store.register(session, message_id, kind, filename, chat_id)
        with closing(self._connect()) as db, db:
            db.execute('INSERT OR IGNORE INTO jobs(id,session,message_id) VALUES(?,?,?)', (key, session, message_id))
        return key

    def scan(self, bridge_db, limit=500):
        if not isinstance(limit, int) or not 1 <= limit <= 5000:
            raise ValueError('读取数量无效')
        source = str(Path(bridge_db).resolve())
        self.archive = BridgeArchive(source, self.bridge_media)
        with closing(self._connect()) as db:
            row = db.execute('SELECT cursor FROM progress WHERE source=?', (source,)).fetchone()
        cursor = row[0] if row else 0
        with closing(sqlite3.connect(Path(source).as_uri() + '?mode=ro', uri=True)) as db:
            db.execute('PRAGMA query_only=ON')
            rows = db.execute('SELECT rowid,session,message_id,chat_id,has_media,media_json FROM messages WHERE rowid>? ORDER BY rowid LIMIT ?', (cursor, limit)).fetchall()
        for rowid, session, mid, chat_id, has_media, raw in rows:
            if has_media:
                try:
                    try:
                        media = json.loads(raw)
                    except (ValueError, TypeError):
                        media = {}
                    self.enqueue(session, mid, chat_id, media)
                except (ValueError, TypeError, UnicodeError):
                    pass
            with closing(self._connect()) as db, db:
                db.execute('INSERT INTO progress VALUES(?,?) ON CONFLICT(source) DO UPDATE SET cursor=MAX(cursor,excluded.cursor)', (source, rowid))
        return len(rows)

    def reset_failed(self):
        """Explicit operator action: allow exhausted receive jobs another bounded cycle."""
        with closing(self._connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            result = db.execute('UPDATE jobs SET attempts=0,next_at=0 WHERE done=0 AND attempts>=? AND lease_until<=?',
                                (MAX_ATTEMPTS, self.clock()))
            return result.rowcount

    def _refresh_archived(self, now):
        if self.archive is None:
            return
        with self.archive.connect() as source:
            if not self.archive.available(source):
                return
            # Both attached sources are read-only; only our separate queue transaction writes.
            source.execute('ATTACH DATABASE ? AS queue', (self.path.absolute().as_uri() + '?mode=ro',))
            rows = source.execute("SELECT q.id,MAX(j.updated) FROM queue.jobs q JOIN messages m "
                                  "ON m.session=q.session AND m.message_id=q.message_id "
                                  "JOIN media_jobs j ON j.message_key=m.message_key "
                                  "WHERE m.has_media=1 AND j.status='archived' AND q.lease_until<=? "
                                  "GROUP BY q.id HAVING q.archive_updated IS NULL OR MAX(j.updated)>q.archive_updated "
                                  "ORDER BY q.id LIMIT 500", (now,)).fetchall()
        if not rows:
            return
        with closing(self._connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            for key, updated in rows:
                db.execute('UPDATE jobs SET attempts=0,next_at=0,done=0,archive_updated=? '
                           'WHERE id=? AND lease_until<=? AND (archive_updated IS NULL OR archive_updated<?)',
                           (updated, key, now, updated))

    def run_one(self):
        # A stalled download can outlive its lease. Only one live worker may publish results.
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(str(self.path) + '.worker.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        try:
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                    or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o600):
                raise ValueError('附件队列锁不可用，请管理员检查权限')
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return False
            return self._run_one()
        finally:
            os.close(fd)

    def _run_one(self):
        now = self.clock()
        with closing(self._connect()):
            pass
        self._refresh_archived(now)
        with closing(self._connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT id,session,message_id,attempts FROM jobs WHERE done=0 AND attempts<? AND next_at<=? AND lease_until<=? ORDER BY next_at,id LIMIT 1', (MAX_ATTEMPTS, now, now)).fetchone()
            if row:
                db.execute('UPDATE jobs SET attempts=attempts+1,lease_until=? WHERE id=?', (now + 120, row[0]))
        if not row:
            return False
        code = 'source_unavailable'
        try:
            result = self.store.retry(row[1], row[2], self.fetch, archive=self.archive)
            success = bool(result and result['state'] == 'ready')
        except Exception as error:
            success = False
            if isinstance(error, MediaError):
                code = error.code
        if not success:
            self.store.fail(row[0], code, retryable=row[3] + 1 < MAX_ATTEMPTS, replace=self.archive is not None)
        with closing(self._connect()) as db, db:
            db.execute('UPDATE jobs SET done=?,next_at=?,lease_until=0 WHERE id=?', (int(success), self.clock() + 60, row[0]))
        return True


def main(argv=None):
    import argparse
    from .store import MediaStore

    parser = argparse.ArgumentParser(description='WhatsApp 附件入站采集（只读取，不发送）')
    parser.add_argument('--bridge-db', required=True, help='bridge 消息数据库路径，只读')
    parser.add_argument('--bridge-media', default=str(MEDIA_ROOT), help='bridge 已归档媒体目录，只读')
    parser.add_argument('--queue', default=os.environ.get('IMGW_WA_MEDIA_QUEUE', '/var/lib/imgw/ops-app/whatsapp-media-queue.sqlite3'))
    parser.add_argument('--config', default='/etc/imgw/whatsapp-bridge.json')
    parser.add_argument('--retry-failed', action='store_true', help='人工重新排队已耗尽重试次数的入站附件，然后退出')
    parser.add_argument('--once', action='store_true', help='处理当前已到重试时间的队列后退出，不等待下一轮重试')
    args = parser.parse_args(argv)
    store = MediaStore()
    # Read configuration on every download so rotated credentials do not require a restart.
    def fetch(session, chat_id, message_id):
        from console.channels.whatsapp import WhatsAppAdapter
        adapter = WhatsAppAdapter(config_path=args.config)
        return WahaDownload(adapter._configuration())(session, chat_id, message_id)
    worker = IngestWorker(store, args.queue, fetch, bridge_db=args.bridge_db, bridge_media=args.bridge_media)
    if args.retry_failed:
        print('已重新排队附件数：' + str(worker.reset_failed()))
        return 0
    while True:
        try:
            scanned = worker.scan(args.bridge_db)
            worked = worker.run_one()
            if not worked and scanned < 500:
                if args.once:
                    print('本轮附件队列已处理；未到重试时间或已耗尽次数的记录仍保留', flush=True)
                    return 0
                time.sleep(5)
        except Exception:
            # Do not expose source data, credentials, paths or remote exception text.
            if args.once:
                print('附件采集暂时不可用，请检查 bridge 数据库和目录后重新运行', flush=True)
                return 1
            print('附件采集暂时不可用，将稍后重试', flush=True)
            time.sleep(30)


if __name__ == '__main__':
    raise SystemExit(main())
