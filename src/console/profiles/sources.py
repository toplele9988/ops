"""Full-history read projection; no media signing, downloads, enqueue or channel sends."""
from urllib.parse import quote

from console.channels import UnifiedInbox
from console.channels.alibaba import AlibabaAdapter
from console.channels.common import ChannelUnavailable, ROLES, placeholder, timestamp
from console.channels.wechat import WechatAdapter
from console.channels.whatsapp import CHAT_ID, WhatsAppAdapter
from console.inbox.model import decode_key


class ReadonlyWhatsApp(WhatsAppAdapter):
    def _register_media(self, raw, session, chat_id):
        return True

    def _message(self, raw, group=False, session=None):
        if not isinstance(raw, dict):
            return None
        at = timestamp(raw.get('timestamp'))
        if at is None:
            return None
        text = raw.get('body') or ''
        if not isinstance(text, str):
            return None
        if raw.get('hasMedia'):
            from console.channels.whatsapp import _BASE64
            if not text or _BASE64.fullmatch(text):
                text = placeholder([raw.get('media') or {}])
        if not text:
            return None
        participant = raw.get('participant') or raw.get('author')
        sender = '成员 ' + participant.split('@')[0][-4:] if group and isinstance(participant, str) else '对方'
        result = dict(role='seller' if raw.get('fromMe') is True else 'buyer', text=text,
                      sent_at=at, sender_label='我方' if raw.get('fromMe') is True else sender)
        media = raw.get('media')
        if raw.get('hasMedia') and isinstance(media, dict):
            kind = 'image' if str(media.get('mimetype', '')).startswith('image/') else 'file'
            block = dict(type=kind)
            if kind == 'file' and isinstance(media.get('filename'), str):
                block['name'] = media['filename'][:200]
            blocks = [block]
            if raw.get('body') and not _BASE64.fullmatch(raw['body']):
                blocks.insert(0, dict(type='text', text=raw['body']))
            result['blocks'] = blocks
        return result

    def messages(self, key):
        with self.lock:
            channel, account, cid = decode_key(key, 'conversation')
            if channel != self.name or not CHAT_ID.fullmatch(cid) or key not in self._view or not self.ready:
                raise ChannelUnavailable()
            config = self._configuration()
            messages, seen, offset = [], set(), 0
            while True:
                rows = self._get(config, '/api/' + quote(config['session'], safe='') + '/chats/' +
                                 quote(cid, safe='') + '/messages?limit=100&offset=' + str(offset) + '&downloadMedia=false')
                if not isinstance(rows, list):
                    raise ChannelUnavailable()
                fresh = 0
                for raw in rows:
                    message = self._message(raw, cid.endswith('@g.us'), config['session'])
                    if message is None:
                        continue
                    identity = raw.get('id') or (message['sent_at'], message['role'], message['text'])
                    if identity not in seen:
                        seen.add(identity)
                        fresh += 1
                        messages.append(message)
                if not rows:
                    break
                if not fresh:
                    # An engine ignoring offset is a failed full-history read, never a ready profile.
                    raise ChannelUnavailable()
                offset += 100
            return sorted(messages, key=lambda m: m['sent_at'])


def build_inbox(channel=None):
    factories = {'wechat': lambda: WechatAdapter(include_roles=tuple(ROLES), media_tokens=False),
                 'alibaba': lambda: AlibabaAdapter(media_tokens=False), 'whatsapp': ReadonlyWhatsApp}
    return UnifiedInbox([factory() for name, factory in factories.items() if channel in (None, name)])
