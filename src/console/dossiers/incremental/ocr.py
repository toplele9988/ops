"""Local, hash-bound OCR. Originals and decoded pixels never leave memory."""
import errno
import hashlib
from io import BytesIO
import json
import math
import os
from pathlib import Path
import re
import stat
import time
import warnings

from .schema import dumps

ARCHIVE_ROOT = Path('/var/lib/imgw/wechat/owner_workspace/media_archive')
MAX_BYTES = 20 * 1024 * 1024
MAX_PIXELS = 25_000_000
MIN_CONFIDENCE = 0.5
ENGINE_REVISION = 'rapidocr_onnxruntime-1.4.4:local-v1:confidence-0.5'
FORMATS = {'jpeg', 'png', 'webp', 'bmp'}


class OcrFailure(Exception):
    pass


def eligible(source):
    media = json.loads(source['coverage_json'])
    return (source['kind'] == 'message_attachment' and media.get('media_table') == 'company_media'
            and not source['text'].strip() and source['direction'] == 'incoming'
            and media.get('format') in FORMATS and media.get('archive_status') == 'ready'
            and bool(media.get('archive_sha256')))


def read_blob(root, sha256):
    """Open every directory component and the regular blob with O_NOFOLLOW."""
    if not isinstance(sha256, str) or not re.fullmatch('[0-9a-f]{64}', sha256):
        raise OcrFailure('invalid_archive_hash')
    path = Path(root).absolute()
    if '..' in path.parts:
        raise OcrFailure('unsafe_archive')
    directory = fd = None
    try:
        directory = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        for part in path.parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=directory)
            os.close(directory)
            directory = child
        # NONBLOCK prevents a substituted FIFO from blocking before fstat.
        fd = os.open(sha256 + '.blob', os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK, dir_fd=directory)
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            raise OcrFailure('unsafe_archive')
        if before.st_size > MAX_BYTES:
            raise OcrFailure('blob_too_large')
        chunks, size = [], 0
        while size <= MAX_BYTES:
            chunk = os.read(fd, min(65536, MAX_BYTES + 1 - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
        after = os.fstat(fd)
        if size > MAX_BYTES:
            raise OcrFailure('blob_too_large')
        if any(getattr(before, key) != getattr(after, key) for key in
               ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns')) or size != before.st_size:
            raise OcrFailure('blob_changed')
        raw = b''.join(chunks)
        if hashlib.sha256(raw).hexdigest() != sha256:
            raise OcrFailure('blob_hash_mismatch')
        return raw
    except OSError as exc:
        raise OcrFailure('unsafe_archive' if exc.errno in (errno.ELOOP, errno.ENOTDIR) else 'blob_unavailable') from None
    finally:
        if fd is not None:
            os.close(fd)
        if directory is not None:
            os.close(directory)


def decode_image(raw):
    try:
        from PIL import Image, ImageOps
    except ImportError:
        raise OcrFailure('image_decoder_unavailable') from None
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(BytesIO(raw), formats=['JPEG', 'PNG', 'WEBP', 'BMP']) as image:
                if image.width * image.height > MAX_PIXELS:
                    raise OcrFailure('image_too_large')
                if getattr(image, 'n_frames', 1) != 1:
                    raise OcrFailure('animated_image')
                image.verify()
            with Image.open(BytesIO(raw), formats=['JPEG', 'PNG', 'WEBP', 'BMP']) as image:
                image.load()  # Reject corrupt/truncated pixel data before invoking OCR.
                return ImageOps.exif_transpose(image).convert('RGB')
    except (Image.DecompressionBombWarning, Image.DecompressionBombError):
        raise OcrFailure('image_too_large') from None
    except OcrFailure:
        raise
    except Exception:
        raise OcrFailure('invalid_image') from None


def local_engine():
    # These models ship with the installed package. No downloads or fallback.
    from rapidocr_onnxruntime import RapidOCR
    return RapidOCR(intra_op_num_threads=2, inter_op_num_threads=1)


def ordered_lines(raw):
    lines = []
    for bbox, text, score in raw if raw is not None else []:
        confidence = float(score)
        if not math.isfinite(confidence) or not 0 <= confidence <= 1:
            raise ValueError
        if confidence < MIN_CONFIDENCE or not isinstance(text, str) or not text.strip():
            continue
        box = [[float(x), float(y)] for x, y in bbox]
        if len(box) != 4 or not all(math.isfinite(v) for point in box for v in point):
            raise ValueError
        lines.append(dict(bbox=box, text=text.strip(), confidence=confidence))
    lines.sort(key=lambda line: (min(p[1] for p in line['bbox']), min(p[0] for p in line['bbox'])))
    return lines


class OCR:
    def __init__(self, db, *, root=ARCHIVE_ROOT, engine_factory=None, max_ocr=200, persist=True):
        self.db, self.root, self.max_ocr, self.persist = db, root, max_ocr, persist
        self.engine_factory, self.engine = engine_factory or local_engine, None
        self.memo = {}
        self.stats = dict(attempted=0, cache_hits=0, ready=0, no_text=0, failed=0, limited=0, seconds=0.0)

    def recognize(self, source, now):
        if not eligible(source):
            return None
        sha = json.loads(source['coverage_json'])['archive_sha256']
        if sha in self.memo:
            self.stats['cache_hits'] += 1
            return self.memo[sha]
        cached = self.db.execute('SELECT * FROM sd_ocr WHERE archive_sha256=?', (sha,)).fetchone()
        if cached:
            self.stats['cache_hits'] += 1
            self.memo[sha] = dict(cached)
            return self.memo[sha]
        if self.stats['attempted'] >= self.max_ocr:
            self.stats['limited'] += 1
            return None
        self.stats['attempted'] += 1
        started = time.monotonic()
        result = dict(archive_sha256=sha, engine_revision=ENGINE_REVISION, state='failed', text='',
                      lines_json='[]', mean_confidence=None, created_at=now, error_code=None)
        try:
            image = decode_image(read_blob(self.root, sha))
            try:
                if self.engine is None:
                    try:
                        self.engine = self.engine_factory()
                    except Exception:
                        raise OcrFailure('engine_unavailable') from None
                try:
                    raw, _ = self.engine(image)
                except Exception:
                    raise OcrFailure('ocr_failed') from None
            finally:
                image.close()
            try:
                lines = ordered_lines(raw)
            except Exception:
                raise OcrFailure('invalid_ocr_output') from None
            result.update(state='ready' if lines else 'no_text', text='\n'.join(line['text'] for line in lines),
                          lines_json=dumps(lines), mean_confidence=sum(line['confidence'] for line in lines) / len(lines) if lines else None)
        except OcrFailure as exc:
            result['error_code'] = str(exc)
        self.stats[result['state']] += 1
        self.stats['seconds'] = round(self.stats['seconds'] + time.monotonic() - started, 6)
        if self.persist:
            # The worker holds its discovery transaction, serializing same-hash work.
            self.db.execute('INSERT INTO sd_ocr(' + ','.join(result) + ') VALUES(' + ','.join('?' for _ in result) + ')', tuple(result.values()))
        self.memo[sha] = result
        return result
