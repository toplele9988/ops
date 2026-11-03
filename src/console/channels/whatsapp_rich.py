"""Bounded WEBJS link preview; never fetch a message URL or thumbnail URL."""
import base64
import binascii
from urllib.parse import urlsplit

from . import rich

MAX_THUMBNAIL = 64 * 1024


def _url(value):
    if not isinstance(value, str) or not value or len(value) > 2048:
        return None
    if '\\' in value or any(ch.isspace() or ord(ch) < 32 for ch in value):
        return None
    try:
        value.encode('utf8')
        parsed = urlsplit(value)
        if (parsed.scheme not in ('http', 'https') or not parsed.hostname or
                not parsed.hostname.isascii() or parsed.username is not None or
                parsed.password is not None):
            return None
        parsed.port
        return value
    except (ValueError, UnicodeError):
        return None


def _thumbnail(value):
    if not isinstance(value, str):
        return None
    prefix = 'data:image/jpeg;base64,'
    if value.startswith(prefix):
        value = value[len(prefix):]
    if len(value) > ((MAX_THUMBNAIL + 2) // 3) * 4:
        return None
    try:
        data = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error):
        return None
    if not 4 <= len(data) <= MAX_THUMBNAIL or not data.startswith(b'\xff\xd8\xff') or not data.endswith(b'\xff\xd9'):
        return None
    return prefix + base64.b64encode(data).decode('ascii')


def link(raw, body):
    data = raw.get('_data')
    if not isinstance(data, dict) or data.get('subtype') != 'url':
        return None
    matched = _url(data.get('matchedText'))
    url = _url(data.get('canonicalUrl')) or matched
    if not matched or matched not in body or not url:
        return None
    def text(value, limit):
        value = rich.clean(value, limit)
        try:
            value.encode('utf8')
        except UnicodeError:
            return ''
        return value

    block = dict(type='link', title=text(data.get('title'), 300) or '链接',
                 desc=text(data.get('description'), 1000), url=url, source='链接预览')
    thumbnail = _thumbnail(data.get('thumbnail'))
    if thumbnail:
        block['thumbnail'] = thumbnail
    return block
