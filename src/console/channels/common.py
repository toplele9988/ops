"""Documented read-only channel contract and source/projection cache primitives."""
import math
import threading
from typing import Protocol

from console.inbox.model import make_key, waiting_state
from console.inbox.store import _stat

ROLES = frozenset(('customer', 'supplier', 'learning', 'internal', 'unclassified', 'unknown'))
COPY_HINTS = {'alibaba': '请复制草稿到阿里国际站后台发送', 'wechat': '请复制草稿到微信发送',
              'whatsapp': '后台发送功能即将开放，先复制草稿到 WhatsApp', 'website': '请复制草稿到网站客服发送'}


class ChannelAdapter(Protocol):
    """All timestamps are Unix seconds. Sources are never mutated.

    Conversations retain the inbox model, with tuple(messages), counterpart_role,
    channel_label and dormant. Each message has role/text/sent_at/sender_label.
    cache_key changes when source data, availability or waiting state changes.
    messages(key) may lazily load bounded history, never download attachments.
    health returns only safe operational metadata, never credentials or raw errors.
    """
    name: str
    label: str
    cache_key: object

    def list_conversations(self, now: float) -> dict: ...
    def messages(self, key: str) -> list: ...
    def health(self, now: float) -> dict: ...


class ChannelUnavailable(RuntimeError):
    def __init__(self):
        super().__init__('渠道暂时无法读取')


def timestamp(value):
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None


def file_stamp(path):
    main = _stat(path)
    return (main, _stat(type(path)(str(path) + '-wal'))) if main else None


def placeholder(attachments):
    """Use type hints only; never reveal attachment paths, URLs or filenames."""
    labels = []
    for attachment in attachments if isinstance(attachments, list) else []:
        if not isinstance(attachment, dict):
            continue
        kind = str(attachment.get('kind') or attachment.get('msg_type') or attachment.get('mimetype') or '').lower()
        label = ('[图片]' if kind in ('3', 'image', 'photo', 'picture') or kind.startswith('image/') else
                 '[视频]' if kind in ('43', '62', 'video') or kind.startswith('video/') else
                 '[语音]' if kind in ('34', 'audio', 'voice', 'ptt') or kind.startswith('audio/') else
                 '[文件]' if kind in ('49', '6', 'file', 'document') or kind.startswith('application/') else '[附件]')
        if label not in labels:
            labels.append(label)
    return ''.join(labels) or '[附件]'


def conversation(channel, label, account, identity, name, messages, role='customer', is_group=False):
    messages = tuple(sorted(messages, key=lambda m: m['sent_at']))
    key = make_key('conversation', channel, account, identity)
    return dict(key=key, channel=channel, channel_label=label, account=account, buyer_name=name,
                counterpart_role=role, is_group=is_group, country='', source_url=None,
                first_seen=messages[0]['sent_at'], last_seen=messages[-1]['sent_at'],
                n_messages=len(messages), messages=messages)


class CachedAdapter:
    """Cache raw aggregation by source fingerprint and project at policy boundaries."""
    def __init__(self):
        self.lock = threading.RLock()
        self._stamp = object()
        self._raw = {}
        self._view = {}
        self._projected_stamp = object()
        self._view_at = float('inf')
        self._expires = None
        self.ready = False
        self.cache_key = 0

    def _read(self, now):
        raise NotImplementedError

    def list_conversations(self, now):
        with self.lock:
            stamp, ready, raw = self._read(now)
            self.ready = ready
            self._raw = raw
            if (stamp != self._projected_stamp or now < self._view_at
                    or (self._expires is not None and now > self._expires)):
                view, expiries = {}, []
                for key, conv in raw.items():
                    state, expiry = waiting_state(conv['messages'], now)
                    view[key] = dict(conv, **state)
                    if expiry is not None and expiry >= now:
                        expiries.append(expiry)
                self._view, self._projected_stamp = view, stamp
                self._view_at, self._expires = now, min(expiries, default=None)
                self.cache_key += 1
            return self._view

    def messages(self, key):
        with self.lock:
            return list(self._view.get(key, {}).get('messages', ()))

    def health(self, now):
        conversations = self.list_conversations(now)
        return dict(channel=self.name, label=self.label, ready=self.ready,
                    conversations=len(conversations), waiting=sum(c['waiting_reply'] for c in conversations.values()),
                    messages_24h=sum(now - 86400 <= m['sent_at'] <= now for c in conversations.values() for m in c['messages']),
                    last_message_at=max((c['last_seen'] for c in conversations.values()), default=None))
