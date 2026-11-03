"""Opaque, content-addressed WhatsApp media and their download status."""
import hashlib
import os
from pathlib import Path
import re
import sqlite3
import stat
import threading
import time
from contextlib import closing

ROOT = Path(os.environ.get('IMGW_WA_MEDIA_ROOT', '/var/lib/imgw/whatsapp-media'))
DB = Path(os.environ.get('IMGW_WA_MEDIA_DB', '/var/lib/imgw/ops-app/whatsapp-media.sqlite3'))
BUFFER = Path(os.environ.get('IMGW_WA_MEDIA_BUFFER', '/var/lib/imgw/ops-app/whatsapp-media-buffer'))
MAX_BYTES = 50 * 1024 * 1024
MIME = frozenset(('image/jpeg', 'image/png', 'image/gif', 'image/webp', 'video/mp4',
                  'video/webm', 'audio/ogg', 'audio/mpeg', 'audio/mp4', 'audio/wav',
                  'application/pdf', 'application/zip', 'application/msword',
                  'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                  'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                  'application/vnd.openxmlformats-officedocument.presentationml.presentation',
                  'text/plain'))
HEX = re.compile(r'[0-9a-f]{64}\Z')
KINDS = frozenset(('image', 'video', 'voice', 'file'))
ERRORS = {
    'archive_metadata': '归档信息不完整，请联系管理员检查 bridge 媒体记录',
    'archive_missing': '归档文件缺失，请联系管理员检查 bridge 媒体目录',
    'archive_path': '归档文件路径不安全或无法读取，请联系管理员检查文件权限',
    'archive_size': '归档文件长度与记录不符，请联系管理员核对原文件',
    'archive_hash': '归档文件哈希与记录不符，请联系管理员核对原文件',
    'archive_source': '归档记录暂时无法读取，请联系管理员检查 bridge 数据库',
    'media_type': '附件类型不支持或与消息不符，请联系管理员核对归档记录',
    'media_size': '附件为空或超过大小上限，请联系管理员核对原文件',
    'mount_unavailable': '附件已保存在本机缓冲，存储挂载不可用，请检查后重试',
    'source_unavailable': '渠道暂时无法提供附件，请稍后刷新；持续不可取时请联系管理员',
}


class MediaError(ValueError):
    def __init__(self, code):
        self.code = code if code in ERRORS else 'source_unavailable'
        super().__init__(ERRORS[self.code])


def identity(session, message_id):
    if not all(isinstance(v, str) and 0 < len(v) <= 500 and '\x00' not in v for v in (session, message_id)):
        raise ValueError('消息标识不可用')
    return hashlib.sha256((str(len(session)) + ':' + session + message_id).encode('utf8')).hexdigest()


def _nearest_mount(root):
    for parent in (root, *root.parents):
        if parent == Path('/'):
            break
        if os.path.ismount(parent):
            return parent
    return None


class MediaStore:
    def __init__(self, db=DB, root=ROOT, *, mount=None, buffer_root=BUFFER):
        self.db, self.root = Path(db), Path(root).absolute()
        self.buffer_root = Path(buffer_root).absolute()
        configured_mount = mount if mount is not None else os.environ.get('IMGW_WA_MEDIA_MOUNT')
        parts = self.root.parts
        if configured_mount is None or configured_mount == '':
            configured_mount = None
            # Pin the chosen mount for this process; an absent NAS must never become local storage.
            if len(parts) >= 3 and parts[1] == 'mnt':
                configured_mount = _nearest_mount(self.root) or Path('/mnt') / parts[2]
        self.mount = Path(configured_mount).absolute() if configured_mount else None
        self._mount_identity = None
        self._mount_lock = threading.Lock()
        if self.mount and (not self.root.is_relative_to(self.mount) or self.buffer_root.is_relative_to(self.mount)
                           or self.buffer_root.is_relative_to('/mnt') or self.buffer_root.resolve() != self.buffer_root):
            raise ValueError('附件目录或本机缓冲目录配置不可用')

    def _directory(self, root, *, mounted=False):
        if mounted:
            # Never create any part of an unavailable mount on the local disk.
            if (root.resolve() != root or self.mount.resolve() != self.mount or
                    not os.path.ismount(self.mount)):
                raise OSError('附件存储挂载不可用')
        else:
            root.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            info = os.fstat(fd)
            if not stat.S_ISDIR(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o700:
                raise OSError('附件目录权限不安全')
            if mounted:
                current = root.stat()
                if (not os.path.ismount(self.mount) or info.st_dev != self.mount.stat().st_dev or
                        (info.st_dev, info.st_ino) != (current.st_dev, current.st_ino)):
                    raise OSError('附件存储挂载不可用')
                # The sentinel normally sits at the mount root, an ancestor of this attachment root.
                try:
                    marker = os.open('.imgw-archive-id', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
                except FileNotFoundError:
                    mount_fd = os.open(self.mount, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                    try:
                        marker = os.open('.imgw-archive-id', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=mount_fd)
                    finally:
                        os.close(mount_fd)
                try:
                    marker_info = os.fstat(marker)
                    mount_info = self.mount.stat()
                    if not stat.S_ISREG(marker_info.st_mode) or marker_info.st_nlink != 1:
                        raise OSError('附件存储标记不可用')
                    # Pin the mount and its sentinel only: callers open different subdirectories of one mount.
                    identity = (mount_info.st_dev, mount_info.st_ino, marker_info.st_dev, marker_info.st_ino)
                    with self._mount_lock:
                        if self._mount_identity is not None and self._mount_identity != identity:
                            raise OSError('附件存储已更换，请恢复原挂载和标记后重试')
                        self._mount_identity = identity
                finally:
                    os.close(marker)
            return fd
        except BaseException:
            os.close(fd)
            raise

    def _write_blob(self, root, digest, body, *, mounted=False):
        directory = self._directory(root, mounted=mounted)
        temp = '.media-' + os.urandom(16).hex()
        try:
            fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
            try:
                with os.fdopen(fd, 'wb') as stream:
                    stream.write(body)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temp, digest + '.blob', src_dir_fd=directory, dst_dir_fd=directory)
                os.fsync(directory)
            finally:
                try:
                    os.unlink(temp, dir_fd=directory)
                except FileNotFoundError:
                    pass
        finally:
            os.close(directory)

    def _read_blob(self, root, row, *, mounted=False):
        try:
            directory = self._directory(root, mounted=True) if mounted else os.open(
                root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                fd = os.open(row['sha256'] + '.blob', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
                with os.fdopen(fd, 'rb') as stream:
                    info = os.fstat(stream.fileno())
                    if not stat.S_ISREG(info.st_mode) or info.st_size != row['size'] or not 0 < info.st_size <= MAX_BYTES:
                        return None
                    data = stream.read(MAX_BYTES + 1)
            finally:
                os.close(directory)
            return data if hashlib.sha256(data).hexdigest() == row['sha256'] else None
        except OSError:
            return None

    def _connect(self):
        self.db.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(self.db, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise OSError('附件记录不可用')
            os.fchmod(fd, 0o600)
        finally:
            os.close(fd)
        db = sqlite3.connect(self.db, timeout=10)
        db.execute('PRAGMA busy_timeout=10000')
        db.execute('CREATE TABLE IF NOT EXISTS media (id TEXT PRIMARY KEY, session TEXT NOT NULL, message_id TEXT NOT NULL, chat_id TEXT NOT NULL, kind TEXT NOT NULL, filename TEXT NOT NULL, state TEXT NOT NULL, mime TEXT, size INTEGER, sha256 TEXT, updated REAL NOT NULL)')
        db.execute('CREATE TABLE IF NOT EXISTS media_errors (id TEXT PRIMARY KEY, error_code TEXT NOT NULL)')
        return db

    def register(self, session, message_id, kind, filename='', chat_id=''):
        key = identity(session, message_id)
        if not isinstance(chat_id, str) or len(chat_id) > 1000 or '\x00' in chat_id:
            raise ValueError('会话标识不可用')
        if kind not in KINDS:
            raise ValueError('附件类型不支持')
        if not isinstance(filename, str):
            raise ValueError('文件名不可用')
        filename = ''.join(ch for ch in filename if ch.isprintable() and ch not in '/\\')[:200]
        with closing(self._connect()) as db, db:
            db.execute('INSERT OR IGNORE INTO media(id,session,message_id,chat_id,kind,filename,state,mime,size,sha256,updated) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                       (key, session, message_id, chat_id, kind, filename, 'pending', None, None, None, time.time()))
        return key

    def get(self, key):
        if not isinstance(key, str) or not HEX.fullmatch(key):
            return None
        try:
            info = self.db.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                return None
            with closing(sqlite3.connect(self.db.resolve().as_uri() + '?mode=ro', uri=True)) as db:
                db.row_factory = sqlite3.Row
                errors = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='media_errors'").fetchone()
                column = "COALESCE(e.error_code,'') AS error_code" if errors else "'' AS error_code"
                join = ' LEFT JOIN media_errors e ON e.id=m.id ' if errors else ' '
                row = db.execute('SELECT m.id,chat_id,kind,filename,state,mime,size,sha256,' + column +
                                 ' FROM media m' + join + 'WHERE m.id=?', (key,)).fetchone()
                if not row:
                    return None
                result = dict(row)
                result['error'] = ERRORS.get(result['error_code'], '')
                result['buffered'] = False
                if self.mount and result['state'] != 'ready' and isinstance(result['sha256'], str) and HEX.fullmatch(result['sha256']):
                    try:
                        info = (self.buffer_root / (result['sha256'] + '.blob')).lstat()
                        result['buffered'] = stat.S_ISREG(info.st_mode) and info.st_size == result['size']
                    except OSError:
                        pass
                return result
        except (sqlite3.Error, OSError):
            return None

    def fail(self, key, code='source_unavailable', *, retryable=False, replace=False):
        code = code if code in ERRORS else 'source_unavailable'
        with closing(self._connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            changed = db.execute("UPDATE media SET state=?,updated=? WHERE id=? AND (state!='ready' OR ?)",
                                 ('pending' if retryable else 'failed', time.time(), key, replace))
            if changed.rowcount:
                db.execute('INSERT INTO media_errors VALUES(?,?) ON CONFLICT(id) DO UPDATE SET error_code=excluded.error_code',
                           (key, code))

    def save(self, key, mime, body, *, replace=False):
        row = self.get(key)
        if not row:
            raise ValueError('消息不存在')
        prefix = {'image': 'image/', 'video': 'video/', 'voice': 'audio/'}.get(row['kind'])
        if mime not in MIME or (prefix and not mime.startswith(prefix)):
            self.fail(key, 'media_type', replace=replace)
            raise MediaError('media_type')
        if not isinstance(body, bytes) or not 0 < len(body) <= MAX_BYTES:
            self.fail(key, 'media_size', replace=replace)
            raise MediaError('media_size')
        kind = row['kind']
        if kind == 'file':
            kind = ('image' if mime.startswith('image/') else 'video' if mime.startswith('video/') else
                    'voice' if mime.startswith('audio/') else kind)
        digest = hashlib.sha256(body).hexdigest()
        if self.mount:
            # Keep a local copy before attempting the removable storage.
            self._write_blob(self.buffer_root, digest, body)
            with closing(self._connect()) as db, db:
                db.execute('BEGIN IMMEDIATE')
                changed = db.execute("UPDATE media SET state='failed',kind=?,mime=?,size=?,sha256=?,updated=? WHERE id=? AND (state!='ready' OR ?)",
                                     (kind, mime, len(body), digest, time.time(), key, replace))
                if changed.rowcount:
                    db.execute("INSERT INTO media_errors VALUES(?,'mount_unavailable') ON CONFLICT(id) DO UPDATE SET error_code=excluded.error_code", (key,))
            try:
                self._write_blob(self.root, digest, body, mounted=True)
            except OSError:
                raise MediaError('mount_unavailable') from None
        else:
            self._write_blob(self.root, digest, body)
        with closing(self._connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            changed = db.execute("UPDATE media SET state='ready',kind=?,mime=?,size=?,sha256=?,updated=? WHERE id=? AND (state!='ready' OR ?)",
                                 (kind, mime, len(body), digest, time.time(), key, replace))
            if changed.rowcount:
                db.execute('DELETE FROM media_errors WHERE id=?', (key,))

    def retry(self, session, message_id, fetch, *, archive=None):
        """Prefer validated bridge bytes; the bounded WAHA transport is a fallback only."""
        key = identity(session, message_id)
        row = self.get(key)
        if not row:
            raise ValueError('消息不存在')
        try:
            archived = archive.load(session, message_id) if archive is not None else None
            if archived is not None:
                body, mime = archived
                self.save(key, mime, body, replace=True)
                return self.get(key)
            if row['state'] == 'ready':
                return row
            buffered = (self.mount and isinstance(row['sha256'], str) and HEX.fullmatch(row['sha256'])
                        and self._read_blob(self.buffer_root, row))
            if buffered:
                self.save(key, row['mime'], buffered)
            else:
                body, mime = fetch(session, row['chat_id'], message_id)
                self.save(key, mime, body)
        except Exception as error:
            code = error.code if isinstance(error, MediaError) else 'source_unavailable'
            self.fail(key, code, replace=archive is not None)
            raise MediaError(code) from None
        return self.get(key)

    def read(self, key):
        row = self.get(key)
        if not row or row['state'] != 'ready' or not isinstance(row['sha256'], str) or not HEX.fullmatch(row['sha256']):
            return None
        data = self._read_blob(self.root, row, mounted=bool(self.mount))
        return (row, data) if data is not None else None
