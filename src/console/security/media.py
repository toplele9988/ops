"""Only registered assets may supply source paths; URLs contain opaque UIDs."""
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
import hashlib
import hmac
import json
import os
import re
import stat
import tempfile
import threading
import warnings
from PIL import Image, ImageOps, UnidentifiedImageError

UID_PATTERN = re.compile(r'a_[0-9a-f]{16}\Z')
ALLOWED_WIDTHS = frozenset({320, 640, 1280})
FORMATS = {'.png': ('PNG', 'image/png'), '.jpg': ('JPEG', 'image/jpeg'),
           '.jpeg': ('JPEG', 'image/jpeg'), '.webp': ('WEBP', 'image/webp')}


class MediaError(Exception):
    def __init__(self, status_code: int, code: str):
        super().__init__(code)
        self.status_code, self.code = status_code, code


@dataclass(frozen=True)
class MediaAsset:
    asset_id: str
    equipment_id: str | None
    local_path: str | None
    relative_path: str | None = None


@dataclass(frozen=True)
class MediaPayload:
    body: bytes
    content_type: str
    etag: str


def make_asset_uid(secret: str | bytes, asset_id: str) -> str:
    key = secret.encode('utf-8') if isinstance(secret, str) else secret
    if not isinstance(key, bytes) or not key:
        raise ValueError('MEDIA_UID_SECRET must be nonempty')
    if not isinstance(asset_id, str) or not asset_id:
        raise ValueError('asset_id must be nonempty text')
    return 'a_' + hmac.new(key, asset_id.encode('utf-8'), hashlib.sha256).hexdigest()[:16]


def valid_uid(uid: str) -> bool:
    return UID_PATTERN.fullmatch(uid) is not None


def parse_width(values: list[str]) -> int | None:
    if not values:
        return None
    if len(values) != 1 or values[0] not in {'320', '640', '1280'}:
        raise MediaError(400, 'BAD_WIDTH')
    return int(values[0])


def _path(value: str) -> Path:
    if not value or '\x00' in value or '\\' in value:
        raise MediaError(404, 'NOT_FOUND')
    path = Path(value)
    if '..' in path.parts:
        raise MediaError(404, 'NOT_FOUND')
    return path


def _registered_path(root: Path, asset: MediaAsset) -> tuple[Path, Path, str, str]:
    lexical_root = root.expanduser().absolute()
    root = lexical_root.resolve(strict=True)
    if asset.local_path is not None:
        relative = _path(asset.local_path)
        # Live local_path is absolute. Only an already-contained registered
        # absolute path may be converted into a relative path.
        if relative.is_absolute():
            try:
                relative = relative.relative_to(lexical_root)
            except ValueError:
                try:
                    relative = relative.relative_to(root)
                except ValueError as exc:
                    raise MediaError(404, 'NOT_FOUND') from exc
    elif asset.relative_path is not None:
        relative = _path(asset.relative_path)
        if relative.is_absolute():
            raise MediaError(404, 'NOT_FOUND')
    else:
        raise MediaError(404, 'NOT_FOUND')
    if relative.suffix.lower() not in FORMATS:
        raise MediaError(404, 'NOT_FOUND')
    expected, mime = FORMATS[relative.suffix.lower()]
    resolved = (root / relative).resolve(strict=True)
    if not resolved.is_relative_to(root) or resolved.suffix.lower() not in FORMATS:
        raise MediaError(404, 'NOT_FOUND')
    if FORMATS[resolved.suffix.lower()][0] != expected:
        raise MediaError(404, 'NOT_FOUND')
    return root, resolved.relative_to(root), expected, mime


def _read_beneath(root: Path, relative: Path) -> tuple[bytes, os.stat_result]:
    """Descriptor traversal prevents symlink replacement between check and open."""
    if relative.is_absolute() or not relative.parts or '..' in relative.parts:
        raise MediaError(404, 'NOT_FOUND')
    directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in relative.parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
        fd = os.open(relative.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        with os.fdopen(fd, 'rb') as handle:
            before = os.fstat(handle.fileno())
            if not stat.S_ISREG(before.st_mode):
                raise MediaError(404, 'NOT_FOUND')
            body = handle.read()
            after = os.fstat(handle.fileno())
            if (before.st_mtime_ns, before.st_ctime_ns, before.st_size) != (after.st_mtime_ns, after.st_ctime_ns, after.st_size):
                raise MediaError(404, 'NOT_FOUND')
            return body, after
    finally:
        os.close(directory)


def _magic(body: bytes) -> str | None:
    if body.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'PNG'
    if body.startswith(b'\xff\xd8\xff'):
        return 'JPEG'
    if len(body) >= 12 and body[:4] == b'RIFF' and body[8:12] == b'WEBP':
        return 'WEBP'
    return None


def _atomic_write(path: Path, body: bytes) -> None:
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.strobe-', delete=False) as handle:
            temporary = handle.name
            handle.write(body)
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass


class MediaService:
    def __init__(self, root: str | Path, cache: str | Path):
        self.root, self.cache = Path(root), Path(cache)
        self._lock = threading.RLock()

    def render(self, uid: str, asset: MediaAsset, width: int | None) -> MediaPayload:
        if not valid_uid(uid):
            raise MediaError(404, 'NOT_FOUND')
        if width is not None and width not in ALLOWED_WIDTHS:
            raise MediaError(400, 'BAD_WIDTH')
        try:
            root, relative, expected, mime = _registered_path(self.root, asset)
            body, info = _read_beneath(root, relative)
            if _magic(body) != expected:
                raise MediaError(404, 'NOT_FOUND')
            with warnings.catch_warnings():
                warnings.simplefilter('error', Image.DecompressionBombWarning)
                with Image.open(BytesIO(body)) as image:
                    if image.format != expected:
                        raise MediaError(404, 'NOT_FOUND')
                    image.verify()
        except MediaError:
            raise
        except (OSError, ValueError, RuntimeError, UnidentifiedImageError,
                Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
            raise MediaError(404, 'NOT_FOUND') from exc
        if width is not None:
            token = hashlib.sha256(json.dumps([
                str(root / relative), info.st_dev, info.st_ino, info.st_mtime_ns,
                info.st_ctime_ns, info.st_size, hashlib.sha256(body).hexdigest(),
                width, 'webp-quality82-v1',
            ]).encode()).hexdigest()
            try:
                body = self._thumbnail(uid, width, body, token)
            except MediaError:
                raise
            except (ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
                raise MediaError(404, 'NOT_FOUND') from exc
            except OSError as exc:
                raise MediaError(503, 'MEDIA_UNAVAILABLE') from exc
            mime = 'image/webp'
        return MediaPayload(body, mime, '"' + hashlib.sha256(body).hexdigest() + '"')

    def _thumbnail(self, uid: str, width: int, source: bytes, token: str) -> bytes:
        with self._lock:
            self.cache.mkdir(parents=True, exist_ok=True)
            cache = self.cache.resolve(strict=True)
            directory = cache / 'thumbs'
            directory.mkdir(parents=True, exist_ok=True)
            directory = directory.resolve(strict=True)
            if not directory.is_relative_to(cache):
                raise MediaError(503, 'MEDIA_UNAVAILABLE')
            target = directory / f'{uid}_{width}.webp'
            metadata = directory / f'{uid}_{width}.json'
            try:
                raw, _ = _read_beneath(directory, Path(metadata.name))
                saved = json.loads(raw)
                cached, _ = _read_beneath(directory, Path(target.name))
                if isinstance(saved, dict) and saved.get('source') == token and saved.get('sha256') == hashlib.sha256(cached).hexdigest() and _magic(cached) == 'WEBP':
                    return cached
            except (OSError, ValueError, UnicodeError, MediaError):
                pass
            with warnings.catch_warnings():
                warnings.simplefilter('error', Image.DecompressionBombWarning)
                with Image.open(BytesIO(source)) as original:
                    image = ImageOps.exif_transpose(original)
                    try:
                        image.load()
                        height = max(1, round(image.height * width / image.width))
                        image.thumbnail((width, height), Image.Resampling.LANCZOS)
                        converted = image.convert('RGBA' if 'A' in image.getbands() or 'transparency' in image.info else 'RGB')
                        try:
                            output = BytesIO()
                            converted.save(output, format='WEBP', quality=82)
                            body = output.getvalue()
                        finally:
                            converted.close()
                    finally:
                        image.close()
            _atomic_write(target, body)
            _atomic_write(metadata, json.dumps({'source': token, 'sha256': hashlib.sha256(body).hexdigest()}).encode())
            return body
