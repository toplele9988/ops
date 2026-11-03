"""Unified read-only inbox. Build once and share the instance across API builders."""
from pathlib import Path
import os
import threading
import time

from console.customers import aggregate_customers
from console.inbox.model import account_stats, waiting_state
from console.inbox.store import INBOUND_PATH, WECHAT_PATH
from .alibaba import AlibabaAdapter
from .common import ChannelAdapter, COPY_HINTS
from .wechat import WechatAdapter
from .whatsapp import CONFIG_PATH, WhatsAppAdapter
from .website import WebsiteAdapter


class UnifiedInbox:
    def __init__(self, adapters):
        self.adapters = {a.name: a for a in adapters}
        self.lock = threading.RLock()
        self._key = self._snapshot = None

    def read(self, now=None):
        now = time.time() if now is None else now
        with self.lock:
            sources = [(a, a.list_conversations(now)) for a in self.adapters.values()]
            key = tuple((a.name, a.cache_key, a.ready) for a, _ in sources)
            if key != self._key:
                conversations = {k: c for _, source in sources for k, c in source.items()}
                # A WeChat group nobody has classified yet is not a customer waiting on us: someone posting a
                # picture in an unknown group must not raise the to-do count. It stays visible, flagged for sorting.
                conversations = {k: (dict(c, waiting_reply=False, waiting_hours=None, needs_classification=True)
                                     if c['channel'] == 'wechat' and c['counterpart_role'] == 'unclassified' else c)
                                 for k, c in conversations.items()}
                self._snapshot = dict(ready=any(a.ready for a, _ in sources), conversations=conversations,
                                      customers=aggregate_customers(conversations.values()))
                self._key = key
            return self._snapshot

    def detail(self, key, now=None):
        now = time.time() if now is None else now
        conv = self.read(now)['conversations'].get(key)
        if conv is None:
            return None
        messages = tuple(self.adapters[conv['channel']].messages(key))
        if messages:
            state, _ = waiting_state(messages, now)
            conv = dict(conv, messages=messages, first_seen=messages[0]['sent_at'],
                        last_seen=messages[-1]['sent_at'], n_messages=len(messages), **state)
        return dict(conv, can_send=False, copy_hint=COPY_HINTS[conv['channel']])

    def summary(self, now):
        snapshot = self.read(now)
        conversations = snapshot['conversations'].values()
        accounts = account_stats(conversations, now)
        by_channel = []
        for adapter in self.adapters.values():
            if adapter.name == 'website':
                continue
            rows = [c for c in conversations if c['channel'] == adapter.name]
            by_channel.append(dict(channel=adapter.name, label=adapter.label, ready=adapter.ready,
                conversations=len(rows), waiting=sum(c['waiting_reply'] for c in rows
                    if c['counterpart_role'] in ('customer', 'unclassified')),
                messages_24h=sum(a['messages_24h'] for a in accounts if a['channel'] == adapter.name),
                last_message_at=max((c['last_seen'] for c in rows), default=None)))
        return dict(ready=snapshot['ready'], channels=accounts, by_channel=by_channel,
            waiting_total=sum(c['waiting_reply'] for c in conversations if c['counterpart_role'] in ('customer', 'unclassified')),
            supplier_waiting=sum(c['waiting_reply'] for c in conversations if c['counterpart_role'] == 'supplier'),
            dormant_total=sum(c['dormant'] for c in conversations), new_24h=sum(a['messages_24h'] for a in accounts))


def _build_inbox(inbound_path=INBOUND_PATH, *, wechat_path=None, whatsapp_config_path=None,
                fetch=None, clock=None, include_roles=('customer', 'supplier', 'unclassified'),
                stat_fn=None, group_gid=None):
    """Explicit test inbound paths never fall back to unrelated live sources.

    Pass this same UnifiedInbox as inbox= to all three route builders to share all
    adapter and merged caches. Alibaba's underlying cache is also shared by path.
    """
    default = Path(inbound_path) == Path(INBOUND_PATH)
    root = Path(inbound_path).parent
    wx = wechat_path if wechat_path is not None else WECHAT_PATH if default else root / 'disabled-wechat.sqlite3'
    wa = whatsapp_config_path if whatsapp_config_path is not None else CONFIG_PATH if default else root / 'disabled-whatsapp.json'
    return UnifiedInbox([AlibabaAdapter(inbound_path), WechatAdapter(wx, include_roles),
                         WhatsAppAdapter(wa, fetch, clock, stat_fn, group_gid), WebsiteAdapter()])


_SHARED = {}
_SHARED_LOCK = threading.RLock()


def shared_inbox(inbound_path=INBOUND_PATH, *, wechat_path=None, whatsapp_config_path=None,
                 fetch=None, clock=None, include_roles=('customer', 'supplier', 'unclassified'),
                 stat_fn=None, group_gid=None):
    """One inbox per effective configuration in this process; no I/O at construction.

    Keep injected dependencies alive with their entry so Python cannot reuse an id.
    Source paths are canonicalized; config paths retain symlink rejection semantics.
    """
    inbound = Path(inbound_path).resolve()
    default = inbound == Path(INBOUND_PATH).resolve()
    wx = Path(wechat_path if wechat_path is not None else
              WECHAT_PATH if default else inbound.parent / 'disabled-wechat.sqlite3').resolve()
    # Preserve config symlinks for WhatsAppAdapter's O_NOFOLLOW/lstat rejection.
    wa = Path(os.path.abspath(whatsapp_config_path if whatsapp_config_path is not None else
              CONFIG_PATH if default else inbound.parent / 'disabled-whatsapp.json'))
    roles = tuple(sorted(set(include_roles)))
    dependencies = (fetch, clock, stat_fn)
    key = (str(inbound), str(wx), str(wa), roles, group_gid, *(id(d) for d in dependencies))
    with _SHARED_LOCK:
        if key not in _SHARED:
            instance = _build_inbox(inbound, wechat_path=wx, whatsapp_config_path=wa,
                                    fetch=fetch, clock=clock, include_roles=roles,
                                    stat_fn=stat_fn, group_gid=group_gid)
            _SHARED[key] = (instance, dependencies)
        return _SHARED[key][0]


# Existing API builders automatically use the same cache, preserving inbox= injection.
build_inbox = shared_inbox


__all__ = ['ChannelAdapter', 'UnifiedInbox', 'build_inbox', 'shared_inbox', 'AlibabaAdapter', 'WechatAdapter',
           'WhatsAppAdapter', 'WebsiteAdapter']
