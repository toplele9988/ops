'''Leak inspection without an export permission mechanism.

Offsets are Unicode character offsets, never excerpts. Duplicate matches with
identical category and offset are collapsed. Callers must reject an artifact
when inspection returns hits or raises an exception; inspection errors never
mean that an artifact is clean.
'''
from __future__ import annotations

from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from io import BytesIO
from math import isfinite
from pathlib import Path, PurePosixPath
import re
import sqlite3
from zipfile import ZipFile


@dataclass(frozen=True, slots=True)
class Hit:
    category: str
    offset: int


@dataclass(frozen=True, slots=True)
class LeakContext:
    supplier_names: tuple[str, ...]
    factory_models: tuple[str, ...]
    cost_values: tuple[float, ...]


SMALL_COST_INTEGER_LIMIT = Decimal(100)  # small whole numbers collide with quantities; they only count when written with decimals
_CNY = r'¥|￥|CNY|RMB|人民币|元'
_CURRENCY = re.compile(r'US\$|USD|\$|¥|￥|CNY|RMB|人民币|元|€', re.IGNORECASE)
_NUMBER = re.compile(r'(?<![\d.,])(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?!\d|[.,]\d)')
_INTERNAL = (
    r'cost_basis|supplier_id|offer_id|底价|出厂|成本|供应商|毛利|/media/'
    r'|(?<!\w)exw(?=\s*(?:_|:|\d))'
    r'|(?<!\w)margin(?=_|:|\s+%)'
    r'|(?<!\w)(?:gross|profit)\s+margin\b'
)
_SUFFIX = re.compile(r'(?:股份有限公司|有限责任公司|有限公司|co\.?\s*,?\s*ltd\.?)\s*$', re.IGNORECASE)
_PDF_KEYS = ('/Title', '/Author', '/Creator', '/Producer', '/Subject', '/Keywords')
_IMAGE_SUFFIXES = {'.jpg', '.jpeg', '.png', '.tif', '.tiff', '.webp', '.gif', '.bmp', '.ico', '.heic', '.heif', '.avif', '.jxl'}
_IMAGE_SIGNATURES = (b'\xff\xd8', b'\x89PNG\r\n\x1a\n', b'GIF87a', b'GIF89a', b'II*\x00', b'MM\x00*')


def _ordered(found: set[tuple[str, int]]) -> list[Hit]:
    return [Hit(category=category, offset=offset) for category, offset in sorted(found, key=lambda entry: (entry[1], entry[0]))]


def _starts(pattern: str, text: str):
    # A zero-width expression also records overlapping occurrences.
    for match in re.finditer('(?=(?:' + pattern + '))', text, re.IGNORECASE):
        yield match.start()


def _cost_number(value) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal, str)):
        raise ValueError('Invalid cost context')
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError('Invalid cost context') from None
    if not number.is_finite() or number < 0:
        raise ValueError('Invalid cost context')
    return number


def _cost_numbers(values) -> set[Decimal]:
    result = set()
    for value in values:
        number = _cost_number(value)
        try:
            rounded = number.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        except InvalidOperation:
            raise ValueError('Invalid cost context') from None
        result.add(number)
        result.add(rounded)
    return result


def scan(text: str, ctx: LeakContext) -> list[Hit]:
    '''Inspect text; matching is case insensitive where applicable.

    Supplier matching tolerates whitespace between name characters while
    preserving offsets in the original input. Currency proximity means at
    most twelve characters between the numeric token and a currency marker.
    Numeric comparison covers integers, decimal forms and comma grouping;
    digits inside a larger number are never treated as a separate cost.
    '''
    if not isinstance(text, str):
        raise TypeError('Expected text')
    found = set()
    for name in ctx.supplier_names:
        if not isinstance(name, str):
            raise ValueError('Invalid supplier context')
        letters = ''.join(name.split())
        if letters:
            pattern = r'\s*'.join(re.escape(letter) for letter in letters)
            found.update(('supplier_name', offset) for offset in _starts(pattern, text))
    for model in ctx.factory_models:
        if not isinstance(model, str):
            raise ValueError('Invalid model context')
        model = model.strip()
        if len(model) >= 4:
            found.update(('factory_model', offset) for offset in _starts(re.escape(model), text))
    found.update(('cny_marker', offset) for offset in _starts(_CNY, text))
    found.update(('internal_key', offset) for offset in _starts(_INTERNAL, text))
    costs = _cost_numbers(ctx.cost_values)
    currencies = tuple((match.start(), match.end()) for match in _CURRENCY.finditer(text))
    if costs and currencies:
        for match in _NUMBER.finditer(text):
            number = Decimal(match.group().replace(',', ''))
            if number not in costs:
                continue
            if number < SMALL_COST_INTEGER_LIMIT and '.' not in match.group():
                continue
            if any(max(start - match.end(), match.start() - end, 0) <= 12 for start, end in currencies):
                found.add(('cost_value', match.start()))
    return _ordered(found)


def _text_fragments(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, (bytes, bytearray)):
        payload = bytes(value)
        encodings = ['utf-8', 'utf-16-le', 'utf-16-be', 'latin-1']
        if payload.startswith(b'ASCII\x00\x00\x00'):
            payload = payload[8:]
        elif payload.startswith(b'UNICODE\x00'):
            payload = payload[8:]
        elif payload.startswith(b'JIS\x00\x00\x00\x00\x00'):
            payload = payload[8:]
            encodings.insert(0, 'shift_jis')
        if payload.startswith((b'\xff\xfe', b'\xfe\xff')):
            encodings.insert(0, 'utf-16')
        seen = set()
        for encoding in encodings:
            text = payload.decode(encoding, errors='replace').strip('\x00')
            if text and text not in seen:
                seen.add(text)
                yield text
    elif isinstance(value, Mapping):
        for key in sorted(value, key=str):
            yield from _text_fragments(value[key])
    elif isinstance(value, (tuple, list)):
        if value and all(type(entry) is int and 0 <= entry <= 255 for entry in value):
            yield from _text_fragments(bytes(value))
        else:
            for entry in value:
                yield from _text_fragments(entry)


def scan_pdf_bytes(data: bytes, ctx: LeakContext) -> list[Hit]:
    '''Inspect every page and all six standard descriptive metadata fields.

    Offsets refer to page texts joined with newlines, followed by one newline
    and metadata text in Title, Author, Creator, Producer, Subject, Keywords
    order. All metadata matches are reported solely as pdf_meta. Unreadable
    or encrypted PDFs raise a generic error.
    '''
    try:
        from pypdf import PdfReader

        reader = PdfReader(BytesIO(data))
        if reader.is_encrypted:
            raise ValueError('Encrypted PDF')
        body = '\n'.join(page.extract_text() or '' for page in reader.pages)
        metadata = reader.metadata or {}
        meta_text = '\n'.join('\n'.join(_text_fragments(metadata.get(key))) for key in _PDF_KEYS)
        found = {(hit.category, hit.offset) for hit in scan(body, ctx)}
        base = len(body) + 1
        found.update(('pdf_meta', base + hit.offset) for hit in scan(meta_text, ctx))
        return _ordered(found)
    except Exception:
        raise ValueError('PDF inspection failed') from None


def _image_texts(data: bytes, filename: str) -> list[str]:
    from PIL import ExifTags, Image, UnidentifiedImageError

    try:
        picture = Image.open(BytesIO(data))
    except UnidentifiedImageError:
        suffix = PurePosixPath(filename).suffix.lower()
        if suffix in _IMAGE_SUFFIXES or suffix in Image.registered_extensions() or data.startswith(_IMAGE_SIGNATURES):
            raise ValueError('Image inspection failed') from None
        return []
    result = []
    with picture:
        for frame in range(getattr(picture, 'n_frames', 1)):
            picture.seek(frame)
            picture.load()
            exif = picture.getexif()
            result.extend(_text_fragments(exif))
            if 0x8769 in exif:
                details = exif.get_ifd(0x8769)
                result.extend(_text_fragments(details))
                if 0xA005 in details:
                    result.extend(_text_fragments(exif.get_ifd(0xA005)))
            if 0x8825 in exif:
                result.extend(_text_fragments(exif.get_ifd(0x8825)))
            thumbnail_ifd = getattr(getattr(ExifTags, 'IFD', None), 'IFD1', None)
            if thumbnail_ifd is not None:
                result.extend(_text_fragments(exif.get_ifd(thumbnail_ifd)))
    return result


def scan_zip_bytes(data: bytes, ctx: LeakContext) -> list[Hit]:
    '''Inspect every member name and EXIF text from every recognized image.

    Files are inspected in memory, including images with unrelated extensions.
    Offsets refer to newline-joined member names and decoded EXIF fragments in
    archive order. Directories also contribute their names. Invalid archives
    and unreadable image members raise a generic error.
    '''
    try:
        parts = []
        with ZipFile(BytesIO(data)) as archive:
            for member in archive.infolist():
                parts.append(member.orig_filename)
                if not member.is_dir():
                    parts.extend(_image_texts(archive.read(member), member.orig_filename))
        return scan('\n'.join(parts), ctx)
    except Exception:
        raise ValueError('ZIP inspection failed') from None


@contextmanager
def _read_only(path):
    uri = Path(path).expanduser().absolute().as_uri() + '?mode=ro'
    connection = sqlite3.connect(uri, uri=True, timeout=5.0)
    try:
        connection.execute('PRAGMA query_only=ON')
        connection.execute('BEGIN')
        yield connection
    finally:
        connection.close()


def _supplier_variants(name: str) -> set[str]:
    result = set()
    original = name.strip()
    subject = _SUFFIX.sub('', original).strip(' \t\r\n,，')
    for value in (original, subject):
        if value:
            compact = ''.join(value.split())
            result.update((value, value.lower(), compact, compact.lower()))
    return result


def _add_cost(result: set[float], value, divisor: int = 1) -> None:
    if value is None:
        return
    number = float(_cost_number(value) / Decimal(divisor))
    if not isfinite(number):
        raise ValueError('Invalid cost context')
    result.add(number)


def load_leak_context(catalog_path, work_db_path, model_ids) -> LeakContext:
    '''Read both databases without creating files or initializing schemas.

    Names and models cover the entire catalog. Costs include every offer for
    the supplied equipment IDs, irrespective of review or validity status,
    plus every accessory, including inactive ones. Unknown IDs and database
    failures raise a generic error instead of yielding an incomplete context.
    '''
    try:
        if isinstance(model_ids, (str, bytes)):
            raise ValueError('Invalid model selection')
        selected = tuple(dict.fromkeys(model_ids))
        if any(not isinstance(mid, str) or not mid.strip() or mid != mid.strip() for mid in selected):
            raise ValueError('Invalid model selection')
        names, models, costs = set(), set(), set()
        with _read_only(catalog_path) as catalog:
            for (name,) in catalog.execute('SELECT legal_name FROM cat_supplier'):
                if not isinstance(name, str) or not name.strip():
                    raise ValueError('Invalid supplier identity')
                names.update(_supplier_variants(name))
            for (model,) in catalog.execute('SELECT model FROM equipment_specs UNION SELECT factory_model_code FROM cat_supplier_model'):
                if isinstance(model, str) and len(model.strip()) >= 4:
                    models.add(model.strip())
            for model_id in selected:
                if catalog.execute('SELECT 1 FROM equipment_specs WHERE equipment_id=?', (model_id,)).fetchone() is None:
                    raise ValueError('Unknown model selection')
                rows = catalog.execute('''
                    SELECT o.raw_cost_rmb_fen, o.fc_upgrade_rmb_fen
                    FROM equipment_specs e
                    JOIN cat_supplier s ON s.legal_name=e.supplier
                    JOIN cat_supplier_model sm
                      ON sm.supplier_id=s.supplier_id AND sm.factory_model_code=e.model
                    JOIN cat_supplier_offer o
                      ON o.supplier_model_id=sm.supplier_model_id AND o.supplier_id=sm.supplier_id
                    WHERE e.equipment_id=?
                ''', (model_id,))
                for raw, flight in rows:
                    _add_cost(costs, raw, 100)
                    _add_cost(costs, flight, 100)
        with _read_only(work_db_path) as work:
            for (cost,) in work.execute('SELECT cost_cny FROM cpq_accessory'):
                _add_cost(costs, cost)
        return LeakContext(
            supplier_names=tuple(sorted(names)),
            factory_models=tuple(sorted(models)),
            cost_values=tuple(sorted(costs)),
        )
    except Exception:
        raise ValueError('Leak context unavailable') from None
