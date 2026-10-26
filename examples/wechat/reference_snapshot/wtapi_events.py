# Review snapshot: comments and docstrings removed. Not a deployed service.
from __future__ import annotations
import re
import unicodedata
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any
_UNSAFE_XML = re.compile('<!\\s*(?:DOCTYPE|ENTITY)\\b', re.IGNORECASE)
MAX_ATTACHMENT_XML_BYTES = 128 * 1024
MAX_ATTACHMENT_XML_NODES = 2048
NOTE_APPMSG_TYPES = frozenset((19, 24, 40))
MAX_NOTE_XML_BYTES = 2 * 1024 * 1024
MAX_NOTE_XML_NODES = 64000
MAX_REFERENCE_TEXT_CHARS = 24000
PAYMENT_APPMSG_TYPES = frozenset({2000, 2001})
_SYSTEM_SENDERS = frozenset({'fmessage', 'filehelper', 'notification', 'system'})

@dataclass(frozen=True)
class GroupMessage:
    app_id: str
    own_wxid: str
    group_id: str
    sender_id: str
    message_id: str
    text: str
    created_at: int
    at_ids: tuple[str, ...]
    attachments: tuple[dict, ...] = ()

@dataclass(frozen=True)
class PrivateMessage:
    app_id: str
    own_wxid: str
    sender_id: str
    message_id: str
    text: str
    created_at: int
    attachments: tuple[dict, ...] = ()

def _identifier(value: Any) -> str | None:
    if not isinstance(value, str) or not value or any((char.isspace() for char in value)):
        return None
    if any((ord(char) < 32 or ord(char) == 127 for char in value)):
        return None
    return value

def _wrapped_string(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    text = value.get('string')
    return text if isinstance(text, str) else None

def _at_ids(source: Any) -> tuple[str, ...]:
    if isinstance(source, dict):
        source = _wrapped_string(source)
    if not isinstance(source, str) or not source or _UNSAFE_XML.search(source):
        return ()
    try:
        root = ET.fromstring(source)
    except (ET.ParseError, ValueError):
        return ()
    if root.tag != 'msgsource':
        return ()
    nodes = root.findall('atuserlist')
    if len(nodes) != 1 or len(nodes[0]):
        return ()
    value = nodes[0].text
    if not value:
        return ()
    result: list[str] = []
    for part in value.split(','):
        member_id = _identifier(part.strip())
        if member_id is not None and member_id not in result:
            result.append(member_id)
    return tuple(result)

def normalize_group_message(payload: Any) -> GroupMessage | None:
    if not isinstance(payload, dict) or payload.get('TypeName') != 'AddMsg':
        return None
    app_id = _identifier(payload.get('Appid'))
    own_wxid = _identifier(payload.get('Wxid'))
    data = payload.get('Data')
    if app_id is None or own_wxid is None or (not isinstance(data, dict)):
        return None
    if type(data.get('MsgType')) is not int or data['MsgType'] != 1:
        return None
    group_id = _identifier(_wrapped_string(data.get('FromUserName')))
    recipient = _identifier(_wrapped_string(data.get('ToUserName')))
    if group_id is None or not group_id.endswith('@chatroom') or group_id == '@chatroom' or (recipient != own_wxid):
        return None
    raw_id = data.get('NewMsgId')
    if isinstance(raw_id, str):
        message_id = _identifier(raw_id)
    elif type(raw_id) is int and raw_id > 0:
        message_id = str(raw_id)
    else:
        return None
    if message_id is None:
        return None
    created_at = data.get('CreateTime')
    if type(created_at) is not int or created_at <= 0:
        return None
    content = _wrapped_string(data.get('Content'))
    if content is None or ':\n' not in content:
        return None
    raw_sender, text = content.split(':\n', 1)
    sender_id = _identifier(raw_sender)
    if sender_id is None or sender_id == own_wxid or ':' in sender_id or sender_id.endswith('@chatroom') or (not text.strip()):
        return None
    return GroupMessage(app_id=app_id, own_wxid=own_wxid, group_id=group_id, sender_id=sender_id, message_id=message_id, text=text, created_at=created_at, at_ids=_at_ids(data.get('MsgSource')))

def normalize_private_message(payload: Any) -> PrivateMessage | None:
    if not isinstance(payload, dict) or payload.get('TypeName') != 'AddMsg':
        return None
    app_id = _identifier(payload.get('Appid'))
    own_wxid = _identifier(payload.get('Wxid'))
    data = payload.get('Data')
    if app_id is None or own_wxid is None or (not isinstance(data, dict)):
        return None
    if type(data.get('MsgType')) is not int or data['MsgType'] not in (1, 49, 57, 24):
        return None
    sender_id = _identifier(_wrapped_string(data.get('FromUserName')))
    recipient = _identifier(_wrapped_string(data.get('ToUserName')))
    if sender_id is None or recipient != own_wxid or sender_id == own_wxid or sender_id.endswith('@chatroom') or recipient.endswith('@chatroom') or (sender_id in {'fmessage', 'filehelper', 'notification', 'system'}):
        return None
    raw_id = data.get('NewMsgId')
    if isinstance(raw_id, str):
        if not re.fullmatch('[0-9]+', raw_id) or not any((char != '0' for char in raw_id)):
            return None
        message_id = raw_id
    elif type(raw_id) is int and raw_id > 0:
        message_id = str(raw_id)
    else:
        return None
    created_at = data.get('CreateTime')
    if type(created_at) is not int or created_at <= 0:
        return None
    text = _wrapped_string(data.get('Content'))
    attachments: tuple[dict[str, Any], ...] = ()
    if data['MsgType'] != 1:
        reference = private_reference(payload)
        text = reference['text'] if reference else None
        if reference is not None:
            media = normalize_owner_media_message(payload)
            if isinstance(media, PrivateMessage):
                attachments = media.attachments
    if text is None or not text.strip():
        return None
    return PrivateMessage(app_id=app_id, own_wxid=own_wxid, sender_id=sender_id, message_id=message_id, text=text, created_at=created_at, attachments=attachments)

def private_reference(payload: Any) -> dict[str, str] | None:
    from defusedxml import ElementTree as safe_xml
    from defusedxml.common import DefusedXmlException
    if not isinstance(payload, dict) or not isinstance(payload.get('Data'), dict):
        return None
    data = payload['Data']
    if type(data.get('MsgType')) is not int or data['MsgType'] not in (49, 57, 24):
        return None
    raw = _wrapped_string(data.get('Content'))
    if not isinstance(raw, str) or len(raw.encode('utf-8')) > MAX_ATTACHMENT_XML_BYTES or _UNSAFE_XML.search(raw):
        return None
    sender = _wrapped_string(data.get('FromUserName'))
    if isinstance(sender, str) and raw.startswith(sender + ':\n'):
        raw = raw[len(sender) + 2:]
    try:
        root = safe_xml.fromstring(raw)
    except (ET.ParseError, DefusedXmlException, ValueError, UnicodeError):
        return None
    apps = root.findall('appmsg')
    if root.tag != 'msg' or len(apps) != 1 or sum((1 for _ in root.iter())) > 2048:
        return None
    app = apps[0]
    if _reference_leaf(app, 'type') not in ('57', '24'):
        return None
    title = _reference_leaf(app, 'title')
    refs = app.findall('refermsg')
    if title is None or not 1 <= len(title.strip()) <= 2000 or len(refs) != 1:
        return None
    values = {key: _reference_leaf(refs[0], key) for key in ('type', 'svrid', 'fromusr', 'chatusr', 'content')}
    if any((value is None for value in values.values())) or values['type'] != '1':
        return None
    return {'text': title, **values}

def _attachment_size(value: Any) -> int | None:
    if not isinstance(value, str) or len(value) > 12 or (not re.fullmatch('[0-9]+', value)):
        return None
    return int(value)

def _reference_leaf(parent: ET.Element, name: str) -> str | None:
    nodes = parent.findall(name)
    if len(nodes) != 1 or len(nodes[0]):
        return None
    return nodes[0].text or ''

def _reference_text(value: str, *, material: bool=False) -> str:
    value = ''.join((c for c in value if c in '\n\t' or unicodedata.category(c) not in ('Cc', 'Cf'))).strip()
    if material:
        value = re.sub('https?://\\S+|/(?:Users|private|var|tmp|home)/\\S+', '[路径或链接已省略]', value)
        value = re.sub('(?<![A-Za-z0-9_])(?:token|aeskey|api[_-]?key|secret|password)\\s*[:=]\\s*[^\\s,;，；]+', '[凭据已省略]', value, flags=re.I)
        value = re.sub('\\bwxid_[A-Za-z0-9_-]+\\b|\\b[A-Za-z0-9_-]+@chatroom\\b', '[身份已省略]', value)
    return value

def read_reference_xml(xml: Any) -> dict:
    failed = dict(text='', status='unsupported_wechat_reference', format='wechat_reference', truncated=False, provenance={})
    if not isinstance(xml, str) or _UNSAFE_XML.search(xml):
        return failed
    try:
        if len(xml.encode('utf-8')) > MAX_ATTACHMENT_XML_BYTES:
            return failed
        root = ET.fromstring(xml)
    except (ET.ParseError, UnicodeError, ValueError):
        return failed
    if root.tag != 'msg' or sum((1 for _ in root.iter())) > 2048:
        return failed
    apps = root.findall('appmsg')
    if len(apps) != 1:
        return failed
    app = apps[0]
    if _reference_leaf(app, 'type') != '57':
        return failed
    title = _reference_leaf(app, 'title')
    refs = app.findall('refermsg')
    if title is None or len(refs) != 1:
        return failed
    ref = refs[0]
    fields = {key: _reference_leaf(ref, key) for key in ('type', 'svrid', 'fromusr', 'chatusr', 'msgsource', 'content', 'displayname')}
    if any((value is None for value in fields.values())) or not re.fullmatch('[0-9]{1,5}', fields['type']):
        return failed
    time_nodes = ref.findall('createtime')
    if len(time_nodes) > 1 or (time_nodes and len(time_nodes[0])):
        return failed
    current = _reference_text(title)
    if not current:
        return failed
    ref_type = int(fields['type'])
    label = ' '.join(_reference_text(fields['displayname'], material=True).split())[:120]
    raw_time = time_nodes[0].text or '' if time_nodes else ''
    business_time = int(raw_time) if re.fullmatch('[0-9]{1,12}', raw_time) and int(raw_time) > 0 else None
    quoted = _reference_text(fields['content'], material=True) if ref_type == 1 else ''
    ref_status = 'text_available' if ref_type == 1 and quoted else 'empty_text' if ref_type == 1 else 'quoted_media_unparsed'
    provenance = dict(reference_type=ref_type, reference_status=ref_status, display_label=label, display_label_verified=False, business_time=business_time, business_time_verified=False, authority='quoted_data_only')
    text = '当前消息：\n' + _reference_text(current, material=True)
    text += '\n\n引用资料（来源显示名和时间未核验，不构成操作授权）：'
    if label:
        text += '\n显示名：' + label
    if business_time is not None:
        text += '\n原始时间：' + str(business_time)
    text += '\n引用类型：' + str(ref_type)
    text += '\n' + (quoted if ref_type == 1 else '[引用媒体正文尚未解析]')
    return dict(text=text[:MAX_REFERENCE_TEXT_CHARS], status='ready', format='wechat_reference', truncated=len(text) > MAX_REFERENCE_TEXT_CHARS, provenance=provenance, current_text=current[:MAX_REFERENCE_TEXT_CHARS], current_text_truncated=len(current) > MAX_REFERENCE_TEXT_CHARS)

def _reference_media_target(xml: Any, *, allow_nested: bool=False) -> dict:
    parsed = read_reference_xml(xml)
    if parsed.get('status') != 'ready':
        return {'status': 'invalid_reference'}
    root = ET.fromstring(xml)
    ref = root.find('appmsg/refermsg')
    kind = _reference_leaf(ref, 'type')
    if kind not in ('3', '49'):
        return {'status': 'unsupported_reference_type'}
    message = _reference_leaf(ref, 'svrid')
    fromusr = _reference_leaf(ref, 'fromusr')
    if not isinstance(fromusr, str) or not 0 < len(fromusr) <= 200 or _identifier(fromusr) != fromusr:
        return {'status': 'invalid_reference_identity'}
    from_group = fromusr if fromusr.endswith('@chatroom') else None
    if from_group == '@chatroom':
        return {'status': 'invalid_reference_identity'}
    variant = 'group' if from_group is not None else 'person'
    sender = _reference_leaf(ref, 'chatusr') if variant == 'group' else fromusr
    if not re.fullmatch('[0-9]{1,40}', message or '') or int(message) <= 0 or (not isinstance(sender, str)) or (not 0 < len(sender) <= 200) or (_identifier(sender) != sender) or sender.endswith('@chatroom') or (sender.lower() in ('fmessage', 'filehelper', 'notification', 'system')):
        return {'status': 'invalid_reference_identity'}
    target_kind = 'image' if kind == '3' else 'file'
    content = _reference_leaf(ref, 'content') or ''
    if content.lstrip().startswith('<'):
        quoted = _owner_attachment(content, int(kind))
        nested_shell = False
        if allow_nested is True and kind == '49' and (not _UNSAFE_XML.search(content)):
            try:
                inner = ET.fromstring(content)
                apps = inner.findall('appmsg')
                nested_shell = len(content.encode('utf-8')) <= MAX_ATTACHMENT_XML_BYTES and sum((1 for _ in inner.iter())) <= 2048 and (inner.tag == 'msg') and (len(apps) == 1) and (_reference_leaf(apps[0], 'type') == '57')
            except (ET.ParseError, ValueError, UnicodeError):
                pass
        if nested_shell:
            target_kind = 'reference'
        elif quoted is None or quoted.get('kind') != target_kind or (kind == '49' and quoted.get('appmsg_type') != 6):
            return {'status': 'unsupported_reference_content'}
    return {'status': 'eligible', 'variant': variant, 'from_group': from_group, 'message_id': message, 'sender_id': sender, 'kind': target_kind, 'created_at': parsed['provenance']['business_time']}

def _video_attachment(xml: Any, msg_type: int) -> dict | None:
    if not isinstance(xml, str):
        return None
    descriptor = dict(kind='video', msg_type=msg_type, appmsg_type=None, name='视频', size_bytes=None, xml='', download_supported=False, unsupported_reason='unsupported_native_video')
    try:
        if len(xml.encode('utf-8')) > MAX_ATTACHMENT_XML_BYTES:
            descriptor['unsupported_reason'] = 'attachment_too_large'
            return descriptor
        if _UNSAFE_XML.search(xml):
            descriptor['unsupported_reason'] = 'unsafe_video_xml'
            return descriptor
        root = ET.fromstring(xml)
    except (ET.ParseError, UnicodeError, ValueError):
        descriptor['unsupported_reason'] = 'invalid_video_metadata'
        return descriptor
    if root.tag != 'msg' or sum((1 for _ in root.iter())) > 2048:
        descriptor['unsupported_reason'] = 'invalid_video_metadata'
        return descriptor
    videos = root.findall('videomsg')
    if len(videos) != 1:
        descriptor['unsupported_reason'] = 'invalid_video_metadata'
        return descriptor
    descriptor['xml'] = xml
    video = videos[0]
    descriptor['size_bytes'] = _attachment_size(video.get('length'))
    if msg_type == 43 and video.get('isplaceholder') == '0' and re.fullmatch('[0-9a-fA-F]{32}', video.get('aeskey', '')) and re.fullmatch('[0-9a-fA-F]{2,8192}', video.get('cdnvideourl', '')) and (descriptor['size_bytes'] is not None) and (descriptor['size_bytes'] > 0):
        descriptor.update(name='视频.mp4', download_supported=True, unsupported_reason=None)
    return descriptor

def _payment_notification(app: ET.Element, subtype: int) -> bool:
    payments = app.findall('wcpayinfo')
    titles = app.findall('title')
    if len(payments) != 1 or payments[0].attrib or len(titles) > 1 or any((len(title) for title in titles)):
        return False
    payment = payments[0]
    if not 1 <= len(payment) <= 40 or (payment.text or '').strip():
        return False
    leaves: dict[str, list[str]] = {}
    for node in payment:
        value = node.text or ''
        if len(node) or node.attrib or (not re.fullmatch('[A-Za-z_][A-Za-z0-9_]{0,63}', node.tag)) or (len(value) > 4096) or any((unicodedata.category(c) in ('Cc', 'Cf') for c in value)) or (node.tail or '').strip():
            return False
        leaves.setdefault(node.tag, []).append(value)
    for name, values in leaves.items():
        if len(values) != 1 and (not (subtype == 2001 and name == 'scenetext' and (len(values) == 2) and (values[0] == values[1]))):
            return False
    if subtype == 2000:
        kind, fee = (leaves.get('paysubtype', [''])[0], leaves.get('feedesc', [''])[0])
        return bool(re.fullmatch('[0-9]{1,5}', kind) and fee.strip() and (len(fee) <= 128))
    if subtype == 2001:
        identifier = leaves.get('paymsgid', [''])[0]
        title = leaves.get('receivertitle', [''])[0] or leaves.get('sendertitle', [''])[0]
        return bool(identifier and len(identifier) <= 256 and (not any((c.isspace() for c in identifier))) and title.strip() and (len(title) <= 256))
    return False

def _owner_attachment(xml: Any, msg_type: int, provider_msg_id: Any=None) -> dict | None:
    if type(msg_type) is int and msg_type in (43, 62):
        return _video_attachment(xml, msg_type)
    if type(msg_type) is not int or msg_type not in (3, 34, 49) or (not isinstance(xml, str)) or _UNSAFE_XML.search(xml):
        return None
    try:
        xml_bytes = len(xml.encode('utf-8'))
        root = ET.fromstring(xml)
    except (ET.ParseError, UnicodeError, ValueError):
        return None
    if root.tag != 'msg':
        return None
    xml_nodes = sum((1 for _ in root.iter()))
    too_large = xml_bytes > MAX_ATTACHMENT_XML_BYTES or xml_nodes > MAX_ATTACHMENT_XML_NODES
    note_too_large = xml_bytes > MAX_NOTE_XML_BYTES or xml_nodes > MAX_NOTE_XML_NODES
    oversized = {'kind': 'unsupported', 'msg_type': msg_type, 'appmsg_type': None, 'name': '附件', 'size_bytes': None, 'xml': '', 'download_supported': False, 'unsupported_reason': 'attachment_too_large'}
    descriptor = {'kind': 'unsupported', 'msg_type': msg_type, 'appmsg_type': None, 'name': '', 'size_bytes': None, 'xml': xml, 'download_supported': False, 'unsupported_reason': 'invalid_metadata'}
    if msg_type == 34:
        voice_id = provider_msg_id if type(provider_msg_id) is int and provider_msg_id > 0 else None
        descriptor.update(provider_msg_id=voice_id, duration_ms=None)
        oversized.update(provider_msg_id=voice_id, duration_ms=None)
        voices = root.findall('voicemsg')
        if len(voices) != 1:
            return None
        if too_large:
            return oversized
        voice = voices[0]
        duration = _attachment_size(voice.get('voicelength'))
        duration = duration if duration is not None and duration > 0 else None
        descriptor.update(kind='voice', name='语音.silk', size_bytes=_attachment_size(voice.get('length')), duration_ms=duration)
        if duration is not None and duration > 10 * 60 * 1000:
            descriptor['unsupported_reason'] = 'attachment_too_large'
        elif voice_id is not None and voice.get('aeskey') and voice.get('voiceurl') and (voice.get('endflag') == '1') and (voice.get('cancelflag') == '0'):
            descriptor.update(download_supported=True, unsupported_reason=None)
        return descriptor
    if type(msg_type) is int and msg_type == 3:
        images = root.findall('img')
        if len(images) != 1:
            return None
        if too_large:
            return oversized
        image = images[0]
        descriptor.update(kind='image', size_bytes=_attachment_size(image.get('length')))
        handles = [image.get(key) for key in ('cdnmidimgurl', 'cdnbigimgurl', 'cdnthumburl')]
        if image.get('aeskey') and any((isinstance(value, str) and value for value in handles)):
            descriptor.update(download_supported=True, unsupported_reason=None)
        return descriptor
    if type(msg_type) is not int or msg_type != 49:
        return None
    apps = root.findall('appmsg')
    if len(apps) != 1:
        return None
    app = apps[0]
    types = app.findall('type')
    if len(types) != 1 or len(types[0]):
        return None
    subtype_text = types[0].text
    if not isinstance(subtype_text, str) or len(subtype_text) > 5 or (not re.fullmatch('[0-9]+', subtype_text)):
        return None
    if note_too_large if int(subtype_text) in NOTE_APPMSG_TYPES else too_large:
        return oversized
    subtype = int(subtype_text)
    title = app.findtext('title', '')
    title = ' '.join(''.join((c for c in title if unicodedata.category(c) not in ('Cc', 'Cf'))).split())[:200]
    descriptor.update(appmsg_type=subtype, name=title, unsupported_reason='unverified_appmsg_type')
    if subtype == 51:
        from video_knowledge.events import parse_finder_xml, VideoError
        try:
            metadata = parse_finder_xml(xml)
        except VideoError:
            descriptor['unsupported_reason'] = 'unverified_finder_structure'
            return descriptor
        descriptor.update(kind='finder_video', name=metadata.title or '视频号', finder_author=metadata.author, finder_description=metadata.description, unsupported_reason='finder_download_contract_unverified')
        return descriptor
    if subtype in PAYMENT_APPMSG_TYPES:
        descriptor['name'] = '转账通知' if subtype == 2000 else '红包通知'
        if _payment_notification(app, subtype):
            descriptor.update(kind='payment_notification', payment_notification=True, unsupported_reason=None)
        return descriptor
    if subtype == 57:
        reference = read_reference_xml(xml)
        if reference['status'] == 'ready':
            descriptor.update(kind='reference', name='引用消息', unsupported_reason=None, current_text=reference['current_text'], current_text_truncated=reference['current_text_truncated'], reference_type=reference['provenance']['reference_type'], reference_status=reference['provenance']['reference_status'])
        else:
            descriptor['unsupported_reason'] = 'unverified_reference_structure'
        return descriptor
    if subtype == 74:
        descriptor['unsupported_reason'] = 'file_pending'
        return descriptor
    if subtype != 6:
        return descriptor
    descriptor.update(kind='file', unsupported_reason='invalid_metadata')
    attachments = app.findall('appattach')
    if len(attachments) != 1:
        return descriptor
    attachment = attachments[0]
    descriptor['size_bytes'] = _attachment_size(attachment.findtext('totallen'))
    if attachment.findtext('attachid') or (attachment.findtext('cdnattachurl') and attachment.findtext('aeskey')):
        descriptor.update(download_supported=True, unsupported_reason=None)
    return descriptor

def normalize_owner_media_message(payload: Any) -> GroupMessage | PrivateMessage | None:
    if not isinstance(payload, dict) or payload.get('TypeName') != 'AddMsg':
        return None
    app_id = _identifier(payload.get('Appid'))
    own_wxid = _identifier(payload.get('Wxid'))
    data = payload.get('Data')
    if app_id is None or own_wxid is None or (not isinstance(data, dict)):
        return None
    msg_type = data.get('MsgType')
    if type(msg_type) is not int or msg_type not in (3, 34, 43, 49, 62):
        return None
    origin = _identifier(_wrapped_string(data.get('FromUserName')))
    recipient = _identifier(_wrapped_string(data.get('ToUserName')))
    if origin is None or recipient != own_wxid or origin == own_wxid or recipient.endswith('@chatroom'):
        return None
    raw_id = data.get('NewMsgId')
    if type(raw_id) is int and raw_id > 0:
        message_id = str(raw_id)
    elif isinstance(raw_id, str) and re.fullmatch('[0-9]+', raw_id) and any((c != '0' for c in raw_id)):
        message_id = raw_id
    else:
        return None
    created_at = data.get('CreateTime')
    if type(created_at) is not int or created_at <= 0:
        return None
    xml = _wrapped_string(data.get('Content'))
    if xml is None:
        return None
    group_id = origin if origin.endswith('@chatroom') else None
    sender_id = origin
    if group_id is not None:
        if group_id == '@chatroom' or ':\n' not in xml:
            return None
        raw_sender, xml = xml.split(':\n', 1)
        sender_id = _identifier(raw_sender)
        if sender_id is None or sender_id == own_wxid or ':' in sender_id or sender_id.endswith('@chatroom'):
            return None
    if sender_id in _SYSTEM_SENDERS:
        return None
    attachment = _owner_attachment(xml, msg_type, data.get('MsgId'))
    if attachment is None:
        return None
    text = {'image': '[图片]', 'voice': '[语音]', 'file': '[文件]'}.get(attachment['kind'], '[暂不支持的微信附件]')
    if attachment['unsupported_reason'] == 'file_pending':
        text = '[文件发送中]'
    if attachment['kind'] == 'video':
        text = '[视频，尚未提取]'
    if attachment['kind'] == 'finder_video':
        text = '[视频号，尚未提取]'
    if attachment['kind'] == 'reference':
        text = attachment['current_text']
    common = dict(app_id=app_id, own_wxid=own_wxid, sender_id=sender_id, message_id=message_id, text=text, created_at=created_at, attachments=(attachment,))
    if group_id is not None:
        return GroupMessage(group_id=group_id, at_ids=_at_ids(data.get('MsgSource')), **common)
    return PrivateMessage(**common)
