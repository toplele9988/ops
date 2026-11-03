#!/usr/bin/env python3
"""阿里 v2 附件按不透明字节归档；不解析、不执行、不访问来源网址。

附件模块 v2 生产基线 sha256: b76a97da8f478c6d5244c5a2cba4371cbf8c676136e5f2929afad8c9985efb37
"""
from __future__ import annotations

from contextlib import contextmanager
import base64
import binascii
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import sqlite3
import stat
import threading
import time
import unicodedata
from urllib.parse import urlsplit, urlunsplit
import uuid

MAX_FILE_BYTES = 50 * 1024 * 1024
DEFAULT_DB = '/var/lib/imgw/icbu/attachments.sqlite3'
DEFAULT_ROOT = '/var/lib/imgw/icbu/attachments'
NAS_ARCHIVE_ROOT = Path('/mnt/nas229/edge/imgw-archive')
SAFE_ID = re.compile(r'[A-Za-z0-9_-]{1,128}\Z')
EVENT_ID = re.compile(r'[A-Za-z0-9._:/@+\-=]{1,256}\Z')
SHA256 = re.compile(r'[a-f0-9]{64}\Z')
MESSAGE_KINDS = frozenset(('text', 'image', 'video', 'file', 'voice', 'product_card',
                           'inquiry_card', 'order_card', 'link_card', 'system'))
MIME_EXTENSIONS = {
    'image/jpeg': {'.jpg', '.jpeg'}, 'image/png': {'.png'}, 'image/gif': {'.gif'},
    'image/webp': {'.webp'}, 'image/bmp': {'.bmp'}, 'image/tiff': {'.tif', '.tiff'},
    'image/avif': {'.avif'}, 'image/heic': {'.heic'}, 'image/heif': {'.heif'},
    'video/mp4': {'.mp4'}, 'video/quicktime': {'.mov'}, 'video/webm': {'.webm'},
    'application/pdf': {'.pdf'}, 'application/msword': {'.doc'},
    'application/vnd.ms-excel': {'.xls'}, 'application/vnd.ms-powerpoint': {'.ppt'},
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document': {'.docx'},
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': {'.xlsx'},
    'application/vnd.openxmlformats-officedocument.presentationml.presentation': {'.pptx'},
    'application/zip': {'.zip'}, 'application/x-zip-compressed': {'.zip'},
    'text/plain': {'.txt'}, 'audio/mpeg': {'.mp3'}, 'audio/mp4': {'.m4a'},
    'audio/ogg': {'.ogg', '.opus'}, 'audio/wav': {'.wav'}, 'audio/x-wav': {'.wav'},
    'audio/webm': {'.webm'}, 'audio/aac': {'.aac'}, 'audio/flac': {'.flac'},
}
_DANGEROUS = frozenset(('svg', 'svgz', 'html', 'htm', 'xhtml', 'js', 'mjs', 'cjs',
                         'exe', 'com', 'bat', 'cmd', 'ps1', 'sh', 'py', 'php', 'jar',
                         'vbs', 'scr', 'lnk', 'url', 'hta', 'msi', 'dll', 'docm', 'xlsm', 'pptm'))
_ATTACHMENT_FIELDS = frozenset(('attachment_id', 'kind', 'filename', 'mime', 'size', 'sha256'))
_CARD_FIELDS = frozenset(('card_type', 'title', 'url', 'product_id', 'image_attachment_id',
                          'price_text', 'moq_text', 'subtitle'))
SCHEMA = '''
CREATE TABLE IF NOT EXISTS blobs (
 sha256 TEXT PRIMARY KEY, size INTEGER NOT NULL, mime TEXT NOT NULL, created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS message_links (
 event_id TEXT PRIMARY KEY, inbound_id TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS attachments (
 event_id TEXT NOT NULL, attachment_id TEXT NOT NULL,
 sha256 TEXT REFERENCES blobs(sha256), declared_sha256 TEXT,
 size INTEGER NOT NULL, mime TEXT NOT NULL, filename TEXT,
 inbound_id TEXT, created_at REAL NOT NULL, cleanup_pending INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(event_id,attachment_id)
);
CREATE INDEX IF NOT EXISTS attachment_message ON attachments(inbound_id,attachment_id);
CREATE TABLE IF NOT EXISTS cleanup_candidates (
 filename TEXT PRIMARY KEY, first_seen_at REAL NOT NULL, cleanup_pending INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS attachment_kinds (
 event_id TEXT NOT NULL, attachment_id TEXT NOT NULL, kind TEXT NOT NULL,
 PRIMARY KEY(event_id,attachment_id)
);
CREATE TABLE IF NOT EXISTS enrichment_ledger (
 event_id TEXT NOT NULL, event_alias TEXT NOT NULL, inbound_id TEXT NOT NULL,
 request_sha256 TEXT NOT NULL, created_at REAL NOT NULL,
 PRIMARY KEY(event_id,inbound_id,request_sha256)
);
PRAGMA user_version=1;
'''


class AttachmentRejected(Exception):
    def __init__(self, code, status=400):
        self.code, self.status = code, status
        super().__init__(code)



def rejection_message(code):
    messages = {
        'invalid_message_kind': '消息类型不支持，请使用契约中的 message_kind。',
        'invalid_attachments': '附件列表格式不对或超过 20 个，请分条采集后重新提交。',
        'invalid_cards': '卡片列表格式不对或超过 10 个，请分条采集后重新提交。',
        'invalid_attachment_fields': '附件字段缺少必填项或含未知字段，请按 v2 契约提交。',
        'duplicate_attachment_id': '同一消息里的附件编号重复，请为每个附件使用稳定且不同的编号。',
        'duplicate_card_identity': '同一消息里的卡片标识重复，请合并同类同编号、网址或标题的卡片后提交。',
        'attachment_kind_mime_mismatch': '附件类别与文件类型不一致，请核对 kind 和 mime。',
        'invalid_card_fields': '卡片字段缺少必填项或含未知字段，请按 v2 契约提交。',
        'attachment_encoding_not_supported': '上传只接受完整原始字节，请去掉分块、压缩和范围上传头。',
        'attachment_content_length_required': '上传缺少唯一有效的 Content-Length，请填写完整文件字节数。',
        'attachment_body_too_large': '附件必须为 1 字节到 50 MiB，请减小文件后上传。',
        'attachment_content_type_not_supported': '文件类型不在白名单中，请核对 Content-Type。',
        'attachment_sha256_required': '上传缺少唯一有效的 X-Content-SHA256，请填写完整文件的小写 SHA-256。',
        'attachment_sha256_mismatch': '文件校验值不一致，本次上传未入库，请核对文件后从头重传。',
        'attachment_incomplete_body': '文件上传不完整，本次上传未入库，请从头重传相同附件编号。',
        'attachment_upload_timeout': '上传超时，本次上传未入库，请核对网络后从头重传相同附件编号。',
        'attachment_metadata_conflict': '这个附件编号已有不同的文件或描述，请核对原记录，勿用新内容覆盖。',
        'attachment_event_conflict': '这个事件编号已关联另一条消息，请核对稳定编号。',
        'attachment_event_limit': '这个事件的附件已超过 20 个，请核对稳定编号并拆分消息后提交。',
        'attachment_blob_conflict': '本地同校验值文件不一致，请让管理员检查附件目录后再上传。',
        'attachment_capacity_exceeded': '附件容量已到上限，请让管理员审核容量后再上传。',
        'attachment_disk_full': '附件磁盘空间不足，请让管理员释放空间后再上传。',
        'attachment_storage_unsafe': '附件存储权限或路径不安全，请让管理员检查目录和文件权限。',
        'attachment_storage_unavailable': '附件存储暂不可用，请让管理员检查存储后重传相同编号。',
        'attachment_configuration_invalid': '附件配置不正确，请让管理员检查目录、容量和清理时限。',
        'attachment_database_version_invalid': '附件库版本不支持，请让管理员核对部署版本。',
        'event_id_reserved_prefix': '事件编号以保留前缀 e1_ 开头，请保留原事件编号，勿把传输别名作为正文编号。',
        'event_id_alias_too_long': '这个旧事件编号超过 93 个 UTF-8 字节，无法生成附件传输别名，请让管理员核对原编号。',
        'invalid_event_alias': '事件传输别名无效，请按原事件编号重新计算无填充的 base64url 别名。',
    }
    if code in messages:
        return messages[code]
    labels = {
        'invalid_event_id': '事件编号', 'invalid_attachment_id': '附件编号',
        'invalid_attachment_kind': '附件类别', 'invalid_attachment_mime': '附件文件类型',
        'invalid_attachment_size': '附件字节数', 'invalid_attachment_sha256': '附件校验值',
        'invalid_attachment_filename': '附件文件名', 'invalid_card_type': '卡片类型',
        'invalid_card_title': '卡片标题', 'invalid_card_url': '卡片网址',
        'invalid_card_product_id': '卡片商品编号', 'invalid_card_image_attachment_id': '卡片图片编号',
        'invalid_card_price_text': '卡片价格文字', 'invalid_card_moq_text': '卡片起订量文字',
        'invalid_card_subtitle': '卡片副标题',
    }
    return (labels[code] + '格式不正确，请按 v2 契约核对长度、类型和内容。') if code in labels else '附件请求未能完成，请核对 v2 契约后重试。'

def _require(ok, code, status=400):
    if not ok:
        raise AttachmentRejected(code, status)


def _text(value, name, limit, *, empty=False):
    _require(isinstance(value, str) and len(value) <= limit, 'invalid_' + name)
    _require(not any(unicodedata.category(c).startswith('C') for c in value), 'invalid_' + name)
    value = value.strip()
    _require(empty or bool(value), 'invalid_' + name)
    return value


def _id(value, name):
    _require(isinstance(value, str) and SAFE_ID.fullmatch(value), 'invalid_' + name)
    return value


def canonical_event_id(value, *, transport=False):
    """别名只出现在传输路径，库内始终保存原事件编号。"""
    _require(isinstance(value, str), 'invalid_event_id')
    if transport:
        _require(SAFE_ID.fullmatch(value), 'invalid_event_alias' if value.startswith('e1_') else 'invalid_event_id')
        if not value.startswith('e1_'):
            return value
        encoded = value[3:]
        try:
            raw = base64.b64decode(encoded + '=' * (-len(encoded) % 4), altchars=b'-_', validate=True)
            original = raw.decode('utf-8')
        except (ValueError, UnicodeError, binascii.Error):
            raise AttachmentRejected('invalid_event_alias') from None
        _require(0 < len(raw) <= 93 and EVENT_ID.fullmatch(original) and not SAFE_ID.fullmatch(original)
                 and not original.startswith('e1_')
                 and base64.urlsafe_b64encode(raw).decode('ascii').rstrip('=') == encoded,
                 'invalid_event_alias')
        return original
    _require(EVENT_ID.fullmatch(value), 'invalid_event_id')
    _require(not value.startswith('e1_'), 'event_id_reserved_prefix')
    if not SAFE_ID.fullmatch(value):
        _require(len(value.encode('utf-8')) <= 93, 'event_id_alias_too_long')
    return value


def event_alias(value):
    value = canonical_event_id(value)
    return value if SAFE_ID.fullmatch(value) else 'e1_' + base64.urlsafe_b64encode(value.encode('utf-8')).decode('ascii').rstrip('=')


def _filename(value, mime):
    _require(isinstance(value, str) and value == value.strip(), 'invalid_attachment_filename')
    value = _text(value, 'attachment_filename', 200)
    _require(not any(c in value for c in '/\\:<>"|?*%') and '..' not in value
             and not value.startswith('.') and not value.endswith(('.', ' ')), 'invalid_attachment_filename')
    pieces = value.lower().split('.')
    _require(len(pieces) >= 2 and not (_DANGEROUS & set(pieces[1:]))
             and pieces[0] not in {'con', 'prn', 'aux', 'nul', *(f'com{i}' for i in range(1, 10)),
                                   *(f'lpt{i}' for i in range(1, 10))}
             and Path(value).suffix.lower() in MIME_EXTENSIONS[mime], 'invalid_attachment_filename')
    return value


def _url(value):
    value = _text(value, 'card_url', 4096)
    _require(not any(c.isspace() for c in value) and '\\' not in value, 'invalid_card_url')
    try:
        parsed = urlsplit(value)
        host = parsed.hostname or ''
        _require(parsed.scheme == 'https' and parsed.username is None and parsed.password is None
                 and parsed.port in (None, 443) and bool(host) and host.isascii()
                 and re.fullmatch(r'[A-Za-z0-9.-]+', host) and not host.startswith('.')
                 and not host.endswith('.') and '..' not in host, 'invalid_card_url')
    except ValueError:
        raise AttachmentRejected('invalid_card_url') from None
    return urlunsplit(('https', host.lower(), parsed.path or '/', '', ''))


def validate_fields(payload, *, allow_legacy_event_id=False):
    """只返回存在的新字段，旧载荷不添加任何默认值。"""
    out = {}
    if 'message_kind' in payload:
        value = payload['message_kind']
        _require(isinstance(value, str) and value in MESSAGE_KINDS, 'invalid_message_kind')
        out['message_kind'] = value
    if 'attachments' in payload:
        values = payload['attachments']
        _require(isinstance(values, list) and len(values) <= 20, 'invalid_attachments')
        attachments, seen = [], set()
        for item in values:
            _require(isinstance(item, dict) and not (set(item) - _ATTACHMENT_FIELDS)
                     and {'attachment_id', 'kind', 'filename', 'mime', 'size'} <= set(item),
                     'invalid_attachment_fields')
            identifier = _id(item['attachment_id'], 'attachment_id')
            _require(identifier not in seen, 'duplicate_attachment_id')
            seen.add(identifier)
            kind, mime, size = item['kind'], item['mime'], item['size']
            _require(isinstance(kind, str) and kind in ('image', 'video', 'file', 'voice'), 'invalid_attachment_kind')
            _require(isinstance(mime, str) and mime in MIME_EXTENSIONS, 'invalid_attachment_mime')
            _require(kind == 'file' or (kind == 'voice' and mime.startswith('audio/'))
                     or mime.startswith(kind + '/'), 'attachment_kind_mime_mismatch')
            _require(type(size) is int and 1 <= size <= MAX_FILE_BYTES, 'invalid_attachment_size')
            attachment = dict(attachment_id=identifier, kind=kind,
                              filename=_filename(item['filename'], mime), mime=mime, size=size)
            if 'sha256' in item:
                digest = item['sha256']
                _require(isinstance(digest, str) and SHA256.fullmatch(digest), 'invalid_attachment_sha256')
                attachment['sha256'] = digest
            attachments.append(attachment)
        out['attachments'] = attachments
    if 'cards' in payload:
        values = payload['cards']
        _require(isinstance(values, list) and len(values) <= 10, 'invalid_cards')
        cards, seen = [], set()
        for item in values:
            _require(isinstance(item, dict) and not (set(item) - _CARD_FIELDS)
                     and {'card_type', 'title'} <= set(item), 'invalid_card_fields')
            kind = item['card_type']
            _require(isinstance(kind, str) and kind in ('product', 'inquiry', 'rfq', 'order', 'link'), 'invalid_card_type')
            card = dict(card_type=kind, title=_text(item['title'], 'card_title', 300))
            if 'url' in item:
                card['url'] = _url(item['url'])
            for name, limit in (('product_id', 128), ('price_text', 200), ('moq_text', 200), ('subtitle', 1000)):
                if name in item:
                    card[name] = _text(item[name], 'card_' + name, limit, empty=True)
            if 'image_attachment_id' in item:
                card['image_attachment_id'] = _id(item['image_attachment_id'], 'card_image_attachment_id')
            identity = (card['card_type'], card.get('product_id') or card.get('url') or card['title'])
            _require(identity not in seen, 'duplicate_card_identity')
            seen.add(identity)
            cards.append(card)
        out['cards'] = cards
    if out.get('attachments') or out.get('cards'):
        if allow_legacy_event_id:
            canonical_event_id(payload.get('event_id'))
        else:
            _id(payload.get('event_id'), 'event_id')
    return out


def _headers(headers):
    _require(not any(headers.get_all(name, []) for name in ('Transfer-Encoding', 'Content-Encoding', 'Content-Range')),
             'attachment_encoding_not_supported')
    lengths = headers.get_all('Content-Length', [])
    _require(len(lengths) == 1 and re.fullmatch(r'[0-9]{1,10}', lengths[0]), 'attachment_content_length_required', 411)
    size = int(lengths[0])
    _require(1 <= size <= MAX_FILE_BYTES, 'attachment_body_too_large', 413)
    types = headers.get_all('Content-Type', [])
    _require(len(types) == 1 and types[0] in MIME_EXTENSIONS, 'attachment_content_type_not_supported', 415)
    hashes = headers.get_all('X-Content-SHA256', [])
    _require(len(hashes) == 1 and SHA256.fullmatch(hashes[0]), 'attachment_sha256_required')
    return size, types[0], hashes[0]


def _check(info, *, directory=False):
    expected = 0o700 if directory else 0o600
    _require((stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
             and info.st_uid == os.geteuid() and stat.S_IMODE(info.st_mode) == expected
             and (directory or info.st_nlink == 1), 'attachment_storage_unsafe', 503)


def _directory(path, *, create=True, private=True):
    path = str(path)
    _require(path.startswith('/') and path != '/' and all(p not in ('.', '..') for p in path.split('/')),
             'attachment_storage_unsafe', 503)
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in PurePosixPath(path).parts[1:]:
            if create:
                try:
                    os.mkdir(part, 0o700, dir_fd=fd)
                    os.fsync(fd)
                except FileExistsError:
                    pass
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        if private:
            _check(os.fstat(fd), directory=True)
        return fd
    except BaseException:
        os.close(fd)
        raise


def _file(parent, name, flags):
    fd = os.open(name, flags | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=parent)
    try:
        _check(os.fstat(fd))
        return fd
    except BaseException:
        os.close(fd)
        raise


def _write(fd, data):
    view = memoryview(data)
    while view:
        count = os.write(fd, view)
        if count <= 0:
            raise OSError('short_write')
        view = view[count:]


class AttachmentStore:
    """同一归档目录的写操作由线程锁和文件锁串行，SQLite 内使用事务。"""
    def __init__(self, db_path=None, root=None, *, max_total_bytes=None, orphan_hours=None, archive_root=None):
        self.path = Path(db_path or os.environ.get('ICBU_ATTACHMENTS_DB', DEFAULT_DB))
        self.root = Path(root or os.environ.get('ICBU_ATTACHMENTS_ROOT', DEFAULT_ROOT))
        self.archive_root = Path(archive_root) if archive_root is not None else (
            NAS_ARCHIVE_ROOT if NAS_ARCHIVE_ROOT in self.root.parents else None)
        _require(self.archive_root is None or self.archive_root.is_absolute()
                 and self.archive_root in self.root.parents, 'attachment_configuration_invalid', 503)
        self.max_bytes = int(max_total_bytes if max_total_bytes is not None else os.environ.get('ICBU_ATTACHMENTS_MAX_BYTES', 10 * 1024**3))
        self.orphan_hours = float(orphan_hours if orphan_hours is not None else os.environ.get('ICBU_ATTACHMENTS_ORPHAN_HOURS', 24))
        _require(self.path.is_absolute() and self.path.name not in ('', '.', '..') and '..' not in self.path.parts
                 and self.max_bytes > 0 and 0 < self.orphan_hours < float('inf'), 'attachment_configuration_invalid', 503)
        self._lock = threading.RLock()
        self._local = threading.local()
        self._db_identity = None
        self._root_identity = None
        self._parent_identity = None
        self._archive_identity = None
        try:
            with self.guard(), self._connect() as db:
                version = db.execute('PRAGMA user_version').fetchone()[0]
                _require(version in (0, 1), 'attachment_database_version_invalid', 503)
                db.execute('PRAGMA journal_mode=WAL')
                db.executescript(SCHEMA)
        except AttachmentRejected:
            raise
        except (OSError, sqlite3.Error, ValueError):
            raise AttachmentRejected('attachment_storage_unavailable', 503) from None

    def _check_archive(self):
        if self.archive_root is None:
            return
        directory = sentinel = None
        try:
            directory = _directory(self.archive_root, create=False, private=False)
            sentinel = os.open('.imgw-archive-id', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
            archive, marker = os.fstat(directory), os.fstat(sentinel)
            _require(stat.S_ISREG(marker.st_mode) and marker.st_nlink == 1 and not marker.st_mode & 0o022,
                     'attachment_storage_unavailable', 503)
            identity = (archive.st_dev, archive.st_ino, marker.st_dev, marker.st_ino)
            _require(self._archive_identity is None or self._archive_identity == identity,
                     'attachment_storage_unavailable', 503)
            self._archive_identity = identity
        except (OSError, ValueError):
            raise AttachmentRejected('attachment_storage_unavailable', 503) from None
        finally:
            if sentinel is not None:
                os.close(sentinel)
            if directory is not None:
                os.close(directory)

    @contextmanager
    def guard(self):
        """可包住主消息库的去重/提交，避免上传与元数据预检之间的竞态。"""
        with self._lock:
            if getattr(self._local, 'root', None) is not None:
                yield self._local.root
                return
            root = lock = None
            try:
                self._check_archive()
                root = _directory(self.root, create=self.archive_root is None)
                identity = os.fstat(root)
                identity = (identity.st_dev, identity.st_ino)
                _require(self._root_identity is None or self._root_identity == identity,
                         'attachment_storage_unavailable' if self.archive_root else 'attachment_storage_unsafe', 503)
                self._root_identity = identity
                lock = _file(root, '.attachments.lock', os.O_RDWR | os.O_CREAT)
                fcntl.flock(lock, fcntl.LOCK_EX)
                self._local.root = root
                yield root
                self._check_archive()
            except AttachmentRejected:
                raise
            except TimeoutError:
                raise AttachmentRejected('attachment_upload_timeout', 408) from None
            except OSError as exc:
                full = exc.errno in (errno.ENOSPC, errno.EDQUOT)
                raise AttachmentRejected('attachment_disk_full' if full else 'attachment_storage_unavailable', 507 if full else 503) from None
            except sqlite3.Error as exc:
                full = getattr(exc, 'sqlite_errorcode', None) == sqlite3.SQLITE_FULL
                raise AttachmentRejected('attachment_disk_full' if full else 'attachment_storage_unavailable', 507 if full else 503) from None
            except ValueError:
                raise AttachmentRejected('attachment_storage_unavailable', 503) from None
            finally:
                self._local.root = None
                if lock is not None:
                    os.close(lock)
                if root is not None:
                    os.close(root)

    @contextmanager
    def _connect(self):
        parent = _directory(self.path.parent)
        db = None
        try:
            info = os.fstat(parent)
            identity = (info.st_dev, info.st_ino)
            _require(self._parent_identity is None or self._parent_identity == identity, 'attachment_storage_unsafe', 503)
            self._parent_identity = identity
            fd = _file(parent, self.path.name, os.O_RDWR | os.O_CREAT)
            try:
                info = os.fstat(fd)
                identity = (info.st_dev, info.st_ino)
                _require(self._db_identity is None or self._db_identity == identity, 'attachment_storage_unsafe', 503)
                self._db_identity = identity
            finally:
                os.close(fd)
            for suffix in ('-wal', '-shm', '-journal'):
                try:
                    sidecar = _file(parent, self.path.name + suffix, os.O_RDONLY)
                except FileNotFoundError:
                    continue
                else:
                    os.close(sidecar)
            # 固定目录描述符锚定 SQLite 与其 WAL，防止目录在检查后被换成符号链接。
            db = sqlite3.connect(f'/proc/self/fd/{parent}/{self.path.name}', timeout=5)
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA foreign_keys=ON')
            db.execute('PRAGMA synchronous=FULL')
            with db:
                yield db
        finally:
            if db is not None:
                db.close()
            os.close(parent)

    def _check_message(self, db, inbound_id, payload):
        event = payload.get('event_id')
        if event is None:
            _require(not payload.get('attachments') and not payload.get('cards'), 'invalid_event_id')
            return
        event = canonical_event_id(event)
        match = db.execute('SELECT inbound_id FROM message_links WHERE event_id=?', (event,)).fetchone()
        _require(match is None or match['inbound_id'] == inbound_id, 'attachment_event_conflict', 409)
        event_rows = db.execute('SELECT * FROM attachments WHERE event_id=?', (event,)).fetchall()
        identifiers = {row['attachment_id'] for row in event_rows} | {item['attachment_id'] for item in payload.get('attachments') or []}
        _require(len(identifiers) <= 20, 'attachment_event_limit')
        # 卡片也能引用先到的文件；即使没有附件描述，也不能把不同字节关联到同一消息编号。
        for row in event_rows:
            _require(row['inbound_id'] in (None, inbound_id), 'attachment_event_conflict', 409)
            aliases = db.execute('''SELECT * FROM attachments WHERE inbound_id=?
              AND attachment_id=? AND event_id<>?''', (inbound_id, row['attachment_id'], event)).fetchall()
            hashes = {digest for item in [row, *aliases] for digest in (item['sha256'], item['declared_sha256']) if digest}
            _require(len(hashes) <= 1 and all(item['size'] == row['size'] and item['mime'] == row['mime']
                     and (not row['filename'] or not item['filename'] or item['filename'] == row['filename'])
                     for item in aliases), 'attachment_metadata_conflict', 409)
        for item in payload.get('attachments') or []:
            kinds = db.execute('''SELECT k.kind FROM attachment_kinds k JOIN attachments a
              ON k.event_id=a.event_id AND k.attachment_id=a.attachment_id
              WHERE a.attachment_id=? AND (a.event_id=? OR a.inbound_id=?)''',
                               (item['attachment_id'], event, inbound_id)).fetchall()
            _require(all(row['kind'] == item['kind'] for row in kinds), 'attachment_metadata_conflict', 409)
            rows = db.execute('''SELECT * FROM attachments WHERE attachment_id=?
              AND (event_id=? OR inbound_id=?)''', (item['attachment_id'], event, inbound_id)).fetchall()
            hashes = {digest for row in rows for digest in (row['sha256'], row['declared_sha256']) if digest}
            if item.get('sha256'):
                hashes.add(item['sha256'])
            _require(len(hashes) <= 1, 'attachment_metadata_conflict', 409)
            for row in rows:
                _require(row['inbound_id'] in (None, inbound_id)
                         and row['size'] == item['size'] and row['mime'] == item['mime']
                         and (row['filename'] is None or row['filename'] == item['filename'])
                         and (not item.get('sha256') or row['sha256'] in (None, item['sha256']))
                         and (not item.get('sha256') or row['declared_sha256'] in (None, item['sha256'])),
                         'attachment_metadata_conflict', 409)

    def _check_messages(self, db, inbound_id, payloads):
        # 同一消息的多个事件可能都在主库提交后、附件库提交前中断。
        # 合并预检所有别名，防止未关联的两份文件先后绕过冲突检查。
        groups = {}
        def add(item):
            item = dict(item)
            groups.setdefault(item['attachment_id'], []).append(item)
        for row in db.execute('SELECT * FROM attachments WHERE inbound_id=?', (inbound_id,)):
            add(row)
        for payload in payloads:
            event = payload.get('event_id')
            if event is not None:
                event = canonical_event_id(event)
                for row in db.execute('SELECT * FROM attachments WHERE event_id=?', (event,)):
                    add(row)
            for item in payload.get('attachments') or []:
                add(item)
        for items in groups.values():
            hashes = {digest for item in items for digest in (item.get('sha256'), item.get('declared_sha256')) if digest}
            _require(len(hashes) <= 1 and len({item['size'] for item in items}) <= 1
                     and len({item['mime'] for item in items}) <= 1
                     and len({item['kind'] for item in items if item.get('kind')}) <= 1
                     and len({item['filename'] for item in items if item.get('filename')}) <= 1,
                     'attachment_metadata_conflict', 409)
        for payload in payloads:
            self._check_message(db, inbound_id, payload)

    def check_messages(self, inbound_id, payloads):
        payloads = list(payloads)
        for payload in payloads:
            validate_fields(payload, allow_legacy_event_id=True)
        with self.guard(), self._connect() as db:
            self._check_messages(db, inbound_id, payloads)

    def check_message(self, inbound_id, payload):
        self.check_messages(inbound_id, [payload])

    def _link_message(self, db, inbound_id, payload, stamp):
        event = payload.get('event_id')
        if event is None:
            return
        event = canonical_event_id(event)
        db.execute('INSERT OR IGNORE INTO message_links(event_id,inbound_id) VALUES(?,?)', (event, inbound_id))
        db.execute('UPDATE attachments SET inbound_id=?,cleanup_pending=0 WHERE event_id=?', (inbound_id, event))
        for item in payload.get('attachments') or []:
            db.execute('''INSERT INTO attachments
              (event_id,attachment_id,declared_sha256,size,mime,filename,inbound_id,created_at)
              VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(event_id,attachment_id) DO UPDATE SET
              declared_sha256=COALESCE(attachments.declared_sha256,excluded.declared_sha256),
              filename=excluded.filename,inbound_id=excluded.inbound_id,cleanup_pending=0''',
                       (event, item['attachment_id'], item.get('sha256'), item['size'], item['mime'], item['filename'], inbound_id, stamp))
            db.execute('''INSERT INTO attachment_kinds(event_id,attachment_id,kind) VALUES(?,?,?)
              ON CONFLICT(event_id,attachment_id) DO UPDATE SET kind=excluded.kind''',
                       (event, item['attachment_id'], item['kind']))

    def link_messages(self, inbound_id, payloads, *, now=None, request_sha256=None):
        payloads = list(payloads)
        for payload in payloads:
            validate_fields(payload, allow_legacy_event_id=True)
        if request_sha256 is None:
            request_sha256 = hashlib.sha256(json.dumps(payloads, sort_keys=True, ensure_ascii=False,
                                            separators=(',', ':')).encode('utf-8')).hexdigest()
        _require(isinstance(request_sha256, str) and SHA256.fullmatch(request_sha256), 'attachment_configuration_invalid', 503)
        stamp = time.time() if now is None else now
        with self.guard(), self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self._check_messages(db, inbound_id, payloads)
            for payload in payloads:
                self._link_message(db, inbound_id, payload, stamp)
                if payload.get('event_id') is not None:
                    event = canonical_event_id(payload['event_id'])
                    db.execute('''INSERT OR IGNORE INTO enrichment_ledger
                      (event_id,event_alias,inbound_id,request_sha256,created_at) VALUES(?,?,?,?,?)''',
                               (event, event_alias(event), inbound_id, request_sha256, stamp))
            self._mark(db, stamp)
            self._check_archive()

    def link_message(self, inbound_id, payload, *, now=None):
        self.link_messages(inbound_id, [payload], now=now)

    def readback(self, event_id, attachment_id, *, transport=True):
        """只回显登记与内容状态，不返回文件字节、名称或存储路径。"""
        event_id = canonical_event_id(event_id, transport=transport)
        attachment_id = _id(attachment_id, 'attachment_id')
        with self.guard() as root, self._connect() as db:
            row = db.execute('SELECT * FROM attachments WHERE event_id=? AND attachment_id=?',
                             (event_id, attachment_id)).fetchone()
            link = db.execute('SELECT inbound_id FROM message_links WHERE event_id=?', (event_id,)).fetchone()
            kind = db.execute('SELECT kind FROM attachment_kinds WHERE event_id=? AND attachment_id=?',
                              (event_id, attachment_id)).fetchone()
            digest = (row['sha256'] or row['declared_sha256']) if row else None
            received = bool(row and row['sha256'] and self._existing(root, row['sha256'], row['size']))
            inbound_id = row['inbound_id'] if row and row['inbound_id'] else link['inbound_id'] if link else None
            self._check_archive()
            return dict(ok=True, persisted=row is not None, registered=row is not None,
                        file_received=received, size=row['size'] if row else None, sha256=digest,
                        kind=kind['kind'] if kind else None, inbound_id=inbound_id,
                        event_id=event_id, event_alias=event_alias(event_id), attachment_id=attachment_id,
                        duplicate=received)

    def _mark(self, db, now):
        count = db.execute('''UPDATE attachments SET cleanup_pending=1 WHERE inbound_id IS NULL
          AND sha256 IS NOT NULL AND created_at<=? AND cleanup_pending=0''',
                           (now - self.orphan_hours * 3600,)).rowcount
        root = self._local.root
        unregistered = set()
        for name in os.listdir(root):
            blob = re.fullmatch(r'([a-f0-9]{64})\.bin', name)
            staging = re.fullmatch(r'\.upload-[a-f0-9]{32}', name)
            if not blob and not staging:
                continue
            fd = _file(root, name, os.O_RDONLY)
            os.close(fd)
            if blob and db.execute('SELECT 1 FROM attachments WHERE sha256=? LIMIT 1', (blob[1],)).fetchone():
                continue
            unregistered.add(name)
            db.execute('INSERT OR IGNORE INTO cleanup_candidates(filename,first_seen_at) VALUES(?,?)', (name, now))
        for row in db.execute('SELECT filename FROM cleanup_candidates').fetchall():
            if row['filename'] not in unregistered:
                db.execute('DELETE FROM cleanup_candidates WHERE filename=?', (row['filename'],))
        count += db.execute('''UPDATE cleanup_candidates SET cleanup_pending=1
          WHERE first_seen_at<=? AND cleanup_pending=0''', (now - self.orphan_hours * 3600,)).rowcount
        return count

    def mark_orphans(self, *, now=None):
        with self.guard(), self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            return self._mark(db, time.time() if now is None else now)

    def _existing(self, root, digest, size):
        try:
            fd = _file(root, digest + '.bin', os.O_RDONLY)
        except FileNotFoundError:
            return False
        try:
            _require(os.fstat(fd).st_size == size, 'attachment_blob_conflict', 409)
            calculated = hashlib.sha256()
            while True:
                chunk = os.read(fd, 65536)
                if not chunk:
                    break
                calculated.update(chunk)
            _require(calculated.hexdigest() == digest, 'attachment_blob_conflict', 409)
            return True
        finally:
            os.close(fd)

    def _used(self, root):
        total = 0
        for name in os.listdir(root):
            if name == '.attachments.lock':
                continue
            info = os.stat(name, dir_fd=root, follow_symlinks=False)
            _check(info)
            total += info.st_size
        return total

    def receive_upload(self, event_id, attachment_id, headers, stream, *, now=None):
        """调用方必须先做与消息入口相同的来源限制、Origin 检查和鉴权。"""
        event_id, attachment_id = canonical_event_id(event_id, transport=True), _id(attachment_id, 'attachment_id')
        size, mime, expected = _headers(headers)
        stamp = time.time() if now is None else now
        temporary = None
        root = None
        try:
            with self.guard() as root, self._connect() as db:
                db.execute('BEGIN IMMEDIATE')
                row = db.execute('SELECT * FROM attachments WHERE event_id=? AND attachment_id=?',
                                 (event_id, attachment_id)).fetchone()
                link = db.execute('SELECT inbound_id FROM message_links WHERE event_id=?', (event_id,)).fetchone()
                kind = db.execute('SELECT kind FROM attachment_kinds WHERE event_id=? AND attachment_id=?',
                                  (event_id, attachment_id)).fetchone()
                inbound_id = link['inbound_id'] if link else None
                if row is None:
                    count = db.execute('SELECT COUNT(*) FROM attachments WHERE event_id=?', (event_id,)).fetchone()[0]
                    _require(count < 20, 'attachment_event_limit')
                rows = list(db.execute('''SELECT * FROM attachments WHERE attachment_id=?
                  AND (event_id=? OR (inbound_id IS NOT NULL AND inbound_id=?))''',
                                       (attachment_id, event_id, inbound_id)))
                for item in rows:
                    _require(item['size'] == size and item['mime'] == mime
                             and item['sha256'] in (None, expected) and item['declared_sha256'] in (None, expected),
                             'attachment_metadata_conflict', 409)
                duplicate = self._existing(root, expected, size)
                fd = None
                if not duplicate:
                    _require(self._used(root) + size <= self.max_bytes, 'attachment_capacity_exceeded', 507)
                    temporary = '.upload-' + uuid.uuid4().hex
                    fd = _file(root, temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
                try:
                    digest = hashlib.sha256()
                    remaining = size
                    read = getattr(stream, 'read1', stream.read)
                    deadline = time.monotonic() + 60
                    while remaining:
                        _require(time.monotonic() < deadline, 'attachment_upload_timeout', 408)
                        chunk = read(min(65536, remaining))
                        _require(isinstance(chunk, bytes) and 0 < len(chunk) <= remaining, 'attachment_incomplete_body')
                        digest.update(chunk)
                        if fd is not None:
                            self._check_archive()
                            _write(fd, chunk)
                        remaining -= len(chunk)
                    _require(digest.hexdigest() == expected, 'attachment_sha256_mismatch')
                    if fd is not None:
                        os.fsync(fd)
                finally:
                    if fd is not None:
                        os.close(fd)
                if temporary is not None:
                    self._check_archive()
                    os.rename(temporary, expected + '.bin', src_dir_fd=root, dst_dir_fd=root)
                    temporary = None
                os.fsync(root)
                blob = db.execute('SELECT size FROM blobs WHERE sha256=?', (expected,)).fetchone()
                _require(blob is None or blob['size'] == size, 'attachment_blob_conflict', 409)
                db.execute('INSERT OR IGNORE INTO blobs(sha256,size,mime,created_at) VALUES(?,?,?,?)', (expected, size, mime, stamp))
                db.execute('''INSERT INTO attachments
                  (event_id,attachment_id,sha256,size,mime,inbound_id,created_at)
                  VALUES(?,?,?,?,?,?,?) ON CONFLICT(event_id,attachment_id) DO UPDATE SET
                  sha256=excluded.sha256,inbound_id=COALESCE(attachments.inbound_id,excluded.inbound_id),cleanup_pending=0''',
                           (event_id, attachment_id, expected, size, mime, inbound_id, stamp))
                self._mark(db, stamp)
                self._check_archive()
            return dict(ok=True, persisted=True, event_id=event_id, attachment_id=attachment_id,
                        event_alias=event_alias(event_id), inbound_id=inbound_id,
                        kind=kind['kind'] if kind else None, sha256=expected, size_bytes=size, duplicate=duplicate,
                        status='linked' if inbound_id else 'orphan')
        except AttachmentRejected:
            raise
        except (TimeoutError,):
            raise AttachmentRejected('attachment_upload_timeout', 408) from None
        except OSError as exc:
            code = 'attachment_disk_full' if exc.errno in (errno.ENOSPC, errno.EDQUOT) else 'attachment_storage_unavailable'
            raise AttachmentRejected(code, 507 if code == 'attachment_disk_full' else 503) from None
        except (sqlite3.Error, ValueError, TypeError):
            raise AttachmentRejected('attachment_storage_unavailable', 503) from None
        finally:
            # 只移除本请求创建的临时文件；孤儿附件只作标记，绝不自动删除。
            if temporary is not None:
                try:
                    with self.guard() as cleanup_root:
                        os.unlink(temporary, dir_fd=cleanup_root)
                except (OSError, AttachmentRejected):
                    pass
