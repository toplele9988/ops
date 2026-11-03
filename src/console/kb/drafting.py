"""Bounded optional retrieval for drafts. No runner calls or writes in the worker."""
from concurrent.futures import Future
import re
import threading

from console.inbox.model import decode_key
from .brief import DEFAULT_BRIEF

_SLOTS = threading.BoundedSemaphore(2)
PLACEHOLDERS = re.compile(r'\[(?:图片|视频|语音|附件|文件|表情|链接|名片|位置|消息|不支持的消息)\]')


def draft_context(hub, conversation, timeout=3):
    try:
        background = hub.background()
    except Exception:
        background = '公司背景\n' + DEFAULT_BRIEF
    fallback = dict(text=background, sources=[])
    messages = [m for m in conversation['messages'] if m['role'] == 'buyer'][-2:]
    query = '\n'.join(PLACEHOLDERS.sub('', m['text']) for m in messages).strip()[:300]
    if not query or not _SLOTS.acquire(blocking=False):
        return fallback
    future = Future()

    def retrieve():
        try:
            purpose = 'product_facts' if conversation['counterpart_role'] == 'supplier' else 'customer_reply'
            channel, account, identity = decode_key(conversation['key'], 'conversation')
            scope = hub.index.conversation_scope(channel, account, identity) if purpose == 'customer_reply' else None
            future.set_result(hub.context(query, purpose, conversation_scope=scope))
        except Exception as error:
            future.set_exception(error)
        finally:
            _SLOTS.release()
    threading.Thread(target=retrieve, daemon=True, name='kb-draft-context').start()
    try:
        return future.result(timeout=timeout)
    except Exception:
        return fallback
