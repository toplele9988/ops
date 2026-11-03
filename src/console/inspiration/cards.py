"""Parse current XML cards and legacy finder descriptors without fetching media."""
import hashlib
import html
import json
import re
import unicodedata
import xml.etree.ElementTree as ET

_ZERO_WIDTH = re.compile('[\u180e\u200b-\u200f\u202a-\u202e\u2060-\u206f\ufeff]')
_URL = re.compile(r'https?://[^\s<>"\'，。！？；、（）【】]+', re.I)
_TAG = re.compile(r'#([^\s#，。！？；：、,.!?;:<>"\'()\[\]{}（）【】]+)')


def clean_text(value):
    if not isinstance(value, str):
        return ''
    for _ in range(2):
        value = html.unescape(value)
    value = value.replace('<![CDATA[', '').replace(']]>', '')
    value = value.replace('\\r\\n', '\n').replace('\\n', '\n').replace('\\r', '\n')
    value = _ZERO_WIDTH.sub('', value).replace('\r\n', '\n').replace('\r', '\n')
    return unicodedata.normalize('NFC', value).strip()


def _xml_fields(xml):
    if not isinstance(xml, str) or re.search(r'<!\s*(?:DOCTYPE|ENTITY)\b', xml, re.I):
        return '', ''
    try:
        root = ET.fromstring(xml)
    except (ET.ParseError, ValueError):
        return '', ''
    feed = next((e for e in root.iter() if e.tag.split('}')[-1] == 'finderFeed'), None)
    if feed is None:
        return '', ''
    def field(name):
        element = next((e for e in feed.iter() if e.tag.split('}')[-1] == name), None)
        return ''.join(element.itertext()) if element is not None else ''
    return field('nickname'), field('desc')


def parse_finder_card(payload_text):
    """Return zero or more cards. Malformed payloads/attachments are ignored."""
    try:
        payload = json.loads(payload_text)
    except (ValueError, TypeError):
        return []
    if not isinstance(payload, dict) or not isinstance(payload.get('attachments'), list):
        return []
    cards = []
    for attachment in payload['attachments']:
        if not isinstance(attachment, dict):
            continue
        msg_type = attachment.get('msg_type', payload.get('msg_type', 49))
        if str(msg_type) != '49':
            continue
        legacy = 'finder_author' in attachment or 'finder_description' in attachment
        if not legacy and str(attachment.get('appmsg_type')) != '51':
            continue
        author, description = _xml_fields(attachment.get('xml'))
        author = clean_text(author) or clean_text(attachment.get('finder_author'))
        description = clean_text(description) or clean_text(attachment.get('finder_description'))
        if not author and not description:
            continue
        url_matches = list(_URL.finditer(description))
        urls = list(dict.fromkeys(m.group().rstrip('.,!?;:，。！？；：)]}') for m in url_matches))
        tags, pieces, start = [], [], 0
        for match in _TAG.finditer(description):
            if any(u.start() <= match.start() < u.end() for u in url_matches):
                continue
            tag = match.group(1)
            if tag not in tags:
                tags.append(tag)
            pieces.append(description[start:match.start()])
            start = match.end()
        pieces.append(description[start:])
        text = re.sub(r'[ \t]+', ' ', ''.join(pieces)).strip()
        # JSON separates the body and ordered tags unambiguously; whitespace in
        # either the author or body does not create a new forwarding identity.
        normalized = json.dumps([re.sub(r'\s+', ' ', text), tags],
                                ensure_ascii=False, separators=(',', ':'))
        identity = re.sub(r'\s+', ' ', author) + '\n' + normalized
        cards.append(dict(card_id=hashlib.sha256(identity.encode('utf8')).hexdigest()[:16],
                          author=author, description=description, tags=tags, text=text, urls=urls))
    return cards
