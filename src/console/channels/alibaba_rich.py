"""Bounded Alibaba v2 display blocks; source URLs are never fetched."""
import re
from urllib.parse import urlsplit, urlunsplit

from . import rich

IDENTIFIER = re.compile(r'[A-Za-z0-9_-]{1,128}\Z')
CARD_LABELS = {'product': '商品卡片', 'inquiry': '询盘卡片', 'rfq': '采购需求',
               'order': '订单卡片', 'link': '链接'}


def _text(value, limit):
    text = rich.clean(value, limit)
    try:
        text.encode('utf8')
    except UnicodeError:
        return ''
    return text


def _url(value):
    value = _text(value, 4096)
    if not value or '\\' in value or any(c.isspace() for c in value):
        return None
    try:
        url = urlsplit(value)
        if (url.scheme != 'https' or not url.hostname or not url.hostname.isascii()
                or url.username is not None or url.password is not None or url.port not in (None, 443)):
            return None
        return urlunsplit(('https', url.hostname, url.path or '/', '', ''))
    except ValueError:
        return None


def blocks_for(payload, text, token):
    blocks, images = [], set()
    text = _text(text, 4000)
    attachments = payload.get('attachments')
    for att in attachments[:20] if isinstance(attachments, list) else []:
        if not isinstance(att, dict):
            continue
        identity, kind = att.get('attachment_id'), att.get('kind')
        if not isinstance(identity, str) or not IDENTIFIER.fullmatch(identity):
            continue
        if kind not in ('image', 'video', 'file', 'voice'):
            continue
        media = token(identity)
        size = att.get('size')
        size = size if type(size) is int and 0 < size <= 50 * 1024 * 1024 else None
        if kind in ('image', 'video'):
            blocks.append(dict(type=kind, media=media, size_bytes=size))
            if kind == 'image':
                images.add(identity)
        else:
            blocks.append(dict(type='file', kind=kind, name=_text(att.get('filename'), 200) or '附件',
                               size_bytes=size, media=media))
    cards = payload.get('cards')
    for card in cards[:10] if isinstance(cards, list) else []:
        if not isinstance(card, dict) or card.get('card_type') not in CARD_LABELS:
            continue
        label = CARD_LABELS[card['card_type']]
        details = [_text(card.get('subtitle'), 1000), _text(card.get('price_text'), 200),
                   _text(card.get('moq_text'), 200)]
        product = _text(card.get('product_id'), 128)
        if product:
            details.append('商品编号：' + product)
        image = card.get('image_attachment_id')
        if isinstance(image, str) and IDENTIFIER.fullmatch(image) and image not in images:
            blocks.append(dict(type='image', media=token(image)))
            images.add(image)
        blocks.append(dict(type='link', title=_text(card.get('title'), 300) or label,
                           desc=' · '.join(d for d in details if d), url=_url(card.get('url')), source=label))
    if blocks and rich._LABEL.fullmatch(text):
        text = ''
    return ([dict(type='text', text=text)] if text else []) + blocks


def summary(blocks, text):
    blocks = [dict(b, type='voice') if b.get('kind') == 'voice' else b for b in blocks]
    return rich.summary(blocks, text)
