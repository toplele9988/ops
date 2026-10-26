# Review snapshot: comments and docstrings removed. Not a deployed service.
from __future__ import annotations
from dataclasses import dataclass, field, replace
import re
import unicodedata
import xml.etree.ElementTree as ET
from urllib.parse import urlsplit
from wtapi_events import MAX_ATTACHMENT_XML_BYTES, _UNSAFE_XML, normalize_owner_media_message

class VideoError(ValueError):

    def __init__(self, code):
        self.code = code
        super().__init__(code)

def _leaf(parent, name, limit=2000):
    nodes = parent.findall(name)
    if len(nodes) > 1 or (nodes and len(nodes[0])):
        raise VideoError('ambiguous_finder_metadata')
    value = nodes[0].text or '' if nodes else ''
    if len(value) > limit:
        raise VideoError('finder_metadata_too_large')
    return value.strip()

def safe_text(value, limit=1000):
    value = ''.join((c for c in value if c in '\n\t' or unicodedata.category(c) not in ('Cc', 'Cf')))
    value = re.sub('https?://\\S+|/(?:Users|private|var|tmp|home|mnt|Volumes|opt)/\\S+', '[链接或路径已省略]', value)
    value = re.sub('(?:token|aeskey|api[_-]?key|secret|password|authorization|cdnvideourl)\\s*[:=]\\s*[^\\s,;，；]+', '[凭据已省略]', value, flags=re.I)
    value = re.sub('\\bwxid_[\\w-]+\\b|\\b[\\w-]+@chatroom\\b', '[身份已省略]', value)
    value = re.sub('(?<![0-9])1[3-9][0-9]{9}(?![0-9])|\\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\.[A-Za-z]{2,}\\b', '[联系信息已省略]', value)
    return value.strip()[:limit]

def _observed_url(value):
    if not value:
        return ''
    try:
        value.encode('ascii')
        p = urlsplit(value)
        if p.scheme != 'https' or not p.hostname or p.username or p.password or (p.port not in (None, 443)) or p.fragment or (len(value) > 8192) or any((c.isspace() or ord(c) < 32 for c in value)):
            return ''
    except (ValueError, UnicodeError):
        return ''
    return value

@dataclass(frozen=True)
class VideoMetadata:
    kind: str
    title: str
    description: str = ''
    author: str = ''
    cover_url: str = field(default='', repr=False)
    video_url: str = field(default='', repr=False)
    credentials: dict = field(default_factory=dict, repr=False)
    original_xml: str = field(default='', repr=False)
    size_bytes: int | None = None
    download_status: str = 'finder_download_contract_unverified'

    def model_metadata(self):
        return {'kind': self.kind, 'title': safe_text(self.title, 200), 'description': safe_text(self.description, 1500), 'author': safe_text(self.author, 100)}

def parse_finder_xml(xml):
    if not isinstance(xml, str) or _UNSAFE_XML.search(xml):
        raise VideoError('unsafe_finder_xml')
    try:
        if len(xml.encode('utf-8')) > MAX_ATTACHMENT_XML_BYTES:
            raise VideoError('finder_xml_too_large')
        root = ET.fromstring(xml)
    except (ET.ParseError, ValueError, UnicodeError):
        raise VideoError('invalid_finder_xml') from None
    if root.tag != 'msg' or sum((1 for _ in root.iter())) > 2048:
        raise VideoError('invalid_finder_xml')
    apps = root.findall('appmsg')
    if len(apps) != 1 or _leaf(apps[0], 'type', 5) != '51':
        raise VideoError('unsupported_finder_type')
    app = apps[0]
    feeds = app.findall('finderFeed')
    if len(feeds) != 1:
        raise VideoError('unsupported_finder_structure')
    feed = feeds[0]
    media = feed.findall('mediaList/media')
    if len(media) != 1:
        raise VideoError('unsupported_finder_media_count')
    item = media[0]
    credentials = {key: _leaf(item, key, 8192) for key in ('urlToken', 'decodeKey', 'nonceId')}
    credentials = {key: value for key, value in credentials.items() if value}
    return VideoMetadata(kind='finder', title=safe_text(_leaf(app, 'title'), 200), description=safe_text(_leaf(feed, 'desc'), 1500), author=safe_text(_leaf(feed, 'nickname'), 100), cover_url=_observed_url(_leaf(item, 'coverUrl', 8192)), video_url=_observed_url(_leaf(item, 'url', 8192)), credentials=credentials, original_xml=xml)

def parse_video_attachment(descriptor):
    if not isinstance(descriptor, dict):
        raise VideoError('invalid_video_descriptor')
    if descriptor.get('msg_type') == 49:
        from wtapi_events import _owner_attachment
        if _owner_attachment(descriptor.get('xml'), 49) != descriptor:
            raise VideoError('invalid_finder_descriptor')
        return parse_finder_xml(descriptor.get('xml'))
    if descriptor.get('msg_type') == 43:
        from wtapi_events import _video_attachment
        canonical = _video_attachment(descriptor.get('xml'), 43)
        if canonical is None or canonical != descriptor or (not canonical['download_supported']):
            raise VideoError('unsupported_native_video')
        return VideoMetadata(kind='native_video', title='微信视频', original_xml=canonical['xml'], size_bytes=canonical['size_bytes'], download_status='native_video_ready')
    raise VideoError('unsupported_video_kind')

def normalize_video_message(payload):
    message = normalize_owner_media_message(payload)
    if message is None or len(message.attachments) != 1:
        return None
    descriptor = message.attachments[0]
    try:
        metadata = parse_video_attachment(descriptor)
    except VideoError:
        return None
    return replace(message, text='[视频，待知识提取]', attachments=(descriptor,))
