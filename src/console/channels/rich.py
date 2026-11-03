"""Turn a WeChat message's attachments into typed display blocks.

The raw payload already carries every card's XML; the console just never rendered it. Each block is
small, bounded and free of paths or credentials. Media is referenced by an opaque signed token only.
"""
import re
import xml.etree.ElementTree as ET

MAX_XML = 256 * 1024
# WeChat fills `text` with its own label ("[图片]", "[文件] x.xlsx") when a message is only an attachment.
_LABEL = re.compile(r'\[(图片|视频|语音|文件|链接|聊天记录|笔记|表情|动画表情|位置|名片|小程序|视频号|附件)[^\]]{0,200}\](\s+[^\n]{0,260})?')
MAX_ITEMS = 200
_INVISIBLE = re.compile('[​-‏ -‮⁠﻿\x00-\x08\x0b\x0c\x0e-\x1f]')


def clean(value, limit):
    if not isinstance(value, str):
        return ''
    return _INVISIBLE.sub('', value).replace('\r', '').strip()[:limit]


def safe_xml(text):
    """Parse XML only when it cannot declare entities (billion-laughs) and is bounded in size."""
    if not isinstance(text, str) or not text.strip() or len(text) > MAX_XML:
        return None
    upper = text[:4096].upper()
    if '<!DOCTYPE' in upper or '<!ENTITY' in upper or '<!ENTITY' in text.upper():
        return None
    try:
        return ET.fromstring(text.strip().encode('utf8'))
    except (ET.ParseError, ValueError, UnicodeError, RecursionError):
        return None


def _text(node, path, limit=300):
    found = node.find(path) if node is not None else None
    return clean(found.text, limit) if found is not None and found.text else ''


def _http(url):
    url = clean(url, 2048)
    return url if url.startswith(('http://', 'https://')) else None


def _domain(url):
    match = re.match(r'https?://([^/:?#]+)', url or '')
    return match.group(1)[:80] if match else ''


def _record_items(recorditem_text):
    root = safe_xml(recorditem_text)
    if root is None:
        return [], ''
    items = []
    for node in root.iter('dataitem'):
        if len(items) >= MAX_ITEMS:
            break
        kind = node.get('datatype', '')
        sender = clean(node.findtext('sourcename') or '', 60)
        when = clean(node.findtext('sourcetime') or '', 40)
        desc = clean(node.findtext('datadesc') or '', 2000)
        title = clean(node.findtext('datatitle') or '', 200)
        if kind == '1':
            item = dict(type='text', text=desc)
        elif kind == '2':
            item = dict(type='text', text='[图片]')   # CDN only: no local file to show
        elif kind in ('3', '34'):
            item = dict(type='text', text='[语音]')
        elif kind in ('4', '43'):
            item = dict(type='text', text='[视频]')
        elif kind == '5':
            item = dict(type='text', text='[链接] ' + (title or desc))
        elif kind == '8':
            item = dict(type='text', text='[文件] ' + (title or desc))
        elif kind == '17':
            item = dict(type='text', text='[聊天记录] ' + (title or desc))
        else:
            item = dict(type='text', text=desc or title or '[消息]')
        item.update(sender=sender, time=when)
        items.append(item)
    title = clean(root.findtext('title') or '', 120)
    return items, title


def _blocks_for_attachment(att, ordinal, token):
    kind = str(att.get('kind') or '')
    msg_type = att.get('msg_type')
    hint = kind or (msg_type if isinstance(msg_type, str) else '')   # older rows carry the word in msg_type
    kind = hint.lower()
    if isinstance(msg_type, str) and msg_type.isdigit():
        msg_type = int(msg_type)
    appmsg = att.get('appmsg_type')
    root = safe_xml(att.get('xml')) if isinstance(att.get('xml'), str) else None
    app = root.find('appmsg') if root is not None else None
    name = clean(att.get('name'), 200)

    if kind == 'image' or msg_type == 3:
        return [dict(type='image', media=token(ordinal))]
    if kind == 'video' or msg_type == 43:
        return [dict(type='video', media=token(ordinal), size_bytes=att.get('size_bytes'))]
    if kind == 'voice' or msg_type == 34:
        seconds = None
        voice = root.find('.//voicemsg') if root is not None else None
        if voice is not None and str(voice.get('voicelength', '')).isdigit():
            seconds = round(int(voice.get('voicelength')) / 1000)
        return [dict(type='voice', duration_s=seconds, playable=False)]
    if appmsg == 6 or kind in ('file', 'document'):
        return [dict(type='file', name=name, size_bytes=att.get('size_bytes'), media=token(ordinal))]
    if appmsg == 57 and app is not None:
        refer = app.find('refermsg')
        content = clean(refer.findtext('content') if refer is not None else '', 300)
        if content.startswith('<'):
            content = '[图片或卡片]'
        return [dict(type='quote', sender=clean(refer.findtext('displayname') if refer is not None else '', 60) or '对方', text=content)]
    if appmsg == 5 and app is not None:
        url = _http(app.findtext('url') or '')
        source = clean(root.findtext('.//appinfo/appname') or '', 60) or _domain(url)
        return [dict(type='link', title=_text(app, 'title', 200), desc=_text(app, 'des', 300), url=url, source=source)]
    if appmsg == 19 and app is not None:
        items, title = _record_items(app.findtext('recorditem') or '')
        return [dict(type='chat_record', title=_text(app, 'title', 120) or title or '聊天记录', count=len(items), items=items)]
    if appmsg == 24 and app is not None:
        items, title = _record_items(app.findtext('recorditem') or '')
        body = '\n'.join(i['text'] for i in items if i['text'] and not i['text'].startswith('['))[:1000] or _text(app, 'des', 1000)
        return [dict(type='note', title=_text(app, 'title', 120) or title or '笔记', text=body)]
    if appmsg == 51 and app is not None:
        feed = app.find('finderFeed')
        return [dict(type='card', kind='finder', title=_text(feed, 'nickname', 80) or _text(app, 'title', 80) or '视频号',
                     subtitle=_text(feed, 'desc', 300))]
    if appmsg == 2001 or kind == 'payment_notification':
        return [dict(type='card', kind='redpacket', title='红包通知', subtitle='')]
    if appmsg in (33, 36) and app is not None:
        return [dict(type='card', kind='miniprogram', title=_text(app, 'title', 120) or '小程序',
                     subtitle=clean(root.findtext('.//appinfo/appname') or '', 60))]
    return [dict(type='card', kind='other', title=name or ('[附件]' if kind in ('unknown', 'unsupported', '') else '[暂不支持的消息]'), subtitle='')]


def blocks_for(payload, token):
    """payload: parsed WeChat message JSON. token(ordinal) returns an opaque media token or None."""
    text = clean(payload.get('text'), 4000)
    attachments = [a for a in (payload.get('attachments') or []) if isinstance(a, dict)]
    out = []
    for ordinal, att in enumerate(attachments):
        try:
            out.extend(_blocks_for_attachment(att, ordinal, token))
        except (AttributeError, TypeError, ValueError):
            out.append(dict(type='card', kind='other', title='[暂不支持的消息]', subtitle=''))
    quotes = [b for b in out if b['type'] == 'quote']
    rest = [b for b in out if b['type'] != 'quote']
    if rest and _LABEL.fullmatch(text):
        text = ''
    blocks = quotes + ([dict(type='text', text=text)] if text else []) + rest
    return blocks or ([dict(type='text', text=text)] if text else [])


def summary(blocks, text=''):
    """One readable line for list previews: never raw XML, base64 or paths."""
    if text and all(b['type'] in ('text', 'quote') for b in blocks):
        return text
    parts = []
    for b in blocks:
        kind = b['type']
        parts.append({'image': '[图片]', 'video': '[视频]', 'voice': '[语音]'}.get(kind) or
                     (('[文件] ' + b['name']) if kind == 'file' and b.get('name') else '[文件]' if kind == 'file' else
                      '[链接] ' + b.get('title', '') if kind == 'link' else
                      '[聊天记录] ' + b.get('title', '') if kind == 'chat_record' else
                      '[笔记] ' + b.get('title', '') if kind == 'note' else
                      '[视频号] ' + b.get('title', '') if kind == 'card' and b.get('kind') == 'finder' else
                      b.get('title', '[消息]') if kind == 'card' else
                      b.get('text', '') if kind in ('text',) else '[引用] ' + b.get('text', '')[:30] if kind == 'quote' else ''))
    line = ' '.join(p for p in parts if p).strip()
    return (line or text or '[消息]')[:300]
