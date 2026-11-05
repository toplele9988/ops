"""Stable chronological message numbers, bounded windows and deterministic admission."""
from datetime import datetime, timezone
import hashlib
import re

from .schema import dumps

BUSINESS = re.compile(r'产品|型号|报价|询价|订单|付款|发货|售后|工厂|供应|物流|灯|货|客户|业务|合同|发票|采购|\b(price|quote|product|order|payment|ship|lamp|led|model|factory|freight|invoice)\b', re.I)
PERSONAL = re.compile(r'家人|家庭|亲友|亲戚|一家人|同学|同窗|闺蜜|family|friends', re.I)
SYSTEM = re.compile(r'公众号|系统通知|服务通知|订阅号|文件传输助手|微信团队')


def body(message):
    blocks = message.get('blocks')
    if not blocks:
        return message.get('text') or '[消息]'
    parts = []
    for block in blocks:
        kind = block.get('type')
        if kind == 'image':
            parts.append('[图片]')
        elif kind == 'file':
            parts.append('[文件' + (' ' + str(block['name']) if block.get('name') else '') + ']')
        elif kind in ('text', 'quote', 'note'):
            parts.append(str(block.get('text') or ''))
        else:
            parts.append('[' + {'voice': '语音', 'video': '视频', 'link': '链接', 'chat_record': '聊天记录'}.get(kind, '消息') + ']')
    # Adapter preview text is truncated to 300 chars; text blocks retain full content.
    return '\n'.join(parts) or message.get('text') or '[消息]'


def normalise(messages):
    ordered = sorted(messages, key=lambda m: m['sent_at'])
    return [dict(message_index=i, sent_at=m['sent_at'], role=m['role'],
                 sender_label=m.get('sender_label') or ('我方' if m['role'] == 'seller' else '对方'), text=body(m))
            for i, m in enumerate(ordered, 1)]


def digest(messages):
    return hashlib.sha256(dumps(messages).encode()).hexdigest()


def line(message):
    when = datetime.fromtimestamp(message['sent_at'], timezone.utc).strftime('%Y-%m-%d %H:%M:%S.%f UTC')
    return f"#{message['message_index']} {when} {message['role']} {message['sender_label'][:100]} (sent_at={message['sent_at']})：{message['text']}"


def windows(messages, limit=6000):
    if limit < 300:
        raise ValueError('窗口过小')
    result, current, size = [], [], 0
    for message in messages:
        # Oversized messages retain every character and the same source number.
        prefix = len(line(dict(message, text=''))) + 1
        width = limit - prefix
        for offset in range(0, max(1, len(message['text'])), width):
            part = dict(message, text=message['text'][offset:offset + width])
            length = len(line(part)) + 1
            if current and size + length > limit:
                result.append(current)
                current, size = [], 0
            current.append(part)
            size += length
    if current:
        result.append(current)
    return result


def priority(rows):
    """Newest, oldest, then work inward; no window can starve across runs."""
    rows = sorted(rows, key=lambda r: r['ordinal'])
    out = []
    while rows:
        out.append(rows.pop())
        if rows:
            out.append(rows.pop(0))
    return out


def rule_kind(conv, messages):
    title = conv.get('buyer_name') or ''
    if conv.get('is_official') or SYSTEM.search(title):
        return 'system'
    if PERSONAL.search(title) and not any(BUSINESS.search(m['text']) for m in messages):
        return 'personal'
    return None
