"""Opt-in adapters for existing configured clients. Nothing is constructed on import.

Every call is already reserved in operator_attempts. No adapter retries or binds
recipients. Resolvers must use the original account AND conversation identity.
"""
import re
from console.inbox.model import decode_key


class ChannelBridge:
    name = 'configured'

    def __init__(self, *, senders, domain):
        self.senders, self.domain = dict(senders), domain

    def available(self, channel):
        return channel in ('wechat', 'whatsapp') and channel in self.senders

    def send(self, *, channel, conversation_key, text, idempotency_key, actor):
        if not self.available(channel) or decode_key(conversation_key, 'conversation')[0] != channel:
            return 'failed'
        return self.senders[channel].send(conversation_key, text, idempotency_key, actor)

    def apply(self, **values):
        return self.domain.apply(**values)


class WhatsAppSender:
    """Existing outbox BridgeClient configuration/transport, real WAHA actor scope."""
    def __init__(self, client, resolve):
        self.client, self.resolve = client, resolve

    def send(self, conversation_key, text, idempotency_key, actor):
        try:
            channel, _, identity = decode_key(conversation_key, 'conversation')
            if channel != 'whatsapp':
                return 'failed'
            target = self.resolve(conversation_key)
            client = self.client
            key, session, api = client._configuration()
            if not target or target.get('session') != session or not target.get('account_id') or target.get('chat_id') != identity:
                return 'failed'
            headers = {'Authorization': 'Bearer ' + key}
            status, binding = client._call(client.get, api, '/api/binding', headers)
            if not (200 <= status < 300 and binding.get('session') == session
                    and binding.get('account_id') == target['account_id']):
                return 'failed'
            automatic = actor == 'operator_auto'
            packet = dict(idempotency_key=idempotency_key, session=session, account_id=target['account_id'],
                          chat_id=target['chat_id'], text=text, auto_send=automatic,
                          actor='model_auto' if automatic else 'approved_operator')
            status, payload = client._call(client.post, api, '/api/sendText', headers, packet)
            return client._outcome(status, payload, idempotency_key)[0]
        except Exception:
            return 'unknown'


class WeChatSender:
    """The installed WTAPI client's send_text(app_id,to_wxid,content) contract.

resolve returns an explicitly verified account binding; allowed supplies the
deployment channel allowlist in addition to the engine's three policy gates.
WTAPI has no idempotency parameter: the durable operator reservation is essential.
"""
    def __init__(self, client, *, resolve, allowed):
        self.client, self.resolve, self.allowed = client, resolve, allowed

    def send(self, conversation_key, text, idempotency_key, actor):
        try:
            channel, _, identity = decode_key(conversation_key, 'conversation')
            if channel != 'wechat' or not self.allowed(conversation_key, actor == 'operator_auto'):
                return 'failed'
            target = self.resolve(conversation_key)
            if not target or not target.get('app_id') or target.get('to_wxid') != identity:
                return 'failed'
            result = self.client.send_text(target['app_id'], target['to_wxid'], text)
            message_id = str(result.get('newMsgId', '')) if isinstance(result, dict) else ''
            if result.get('toWxid') != identity or not re.fullmatch(r'[0-9]{1,128}', message_id) or int(message_id) <= 0:
                return 'unknown'
            return 'sent'
        except Exception:
            return 'unknown'
