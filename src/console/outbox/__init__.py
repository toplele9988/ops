"""Owner-confirmed WhatsApp outbox. Construction and reads never send."""
import base64
import re
from pathlib import Path
import time

from console.inbox.model import BadRequest, decode_key
from .attachments import Candidates
from .bridge_client import BridgeClient, CHAT_ID
from .model import (DISABLED, LABELS, REASONS, UNAVAILABLE, UNSUPPORTED,
                    public, send_response)
from .store import OUTBOX_PATH, Store


class Outbox:
    def __init__(self, path=OUTBOX_PATH, *, bridge=None, inbox=None, clock=None, candidate_path=None):
        self.store = Store(path)
        self.candidates = Candidates(candidate_path or Path(path).with_name("whatsapp-attachment-candidates.sqlite3"))
        self.bridge = bridge if bridge is not None else BridgeClient()
        self.inbox = inbox
        self.clock = time.time if clock is None else clock

    def settings(self):
        enabled = self.store.enabled()
        return {'channels': [dict(channel=c, label=label, enabled=enabled if c == 'whatsapp' else False,
            available=c == 'whatsapp', **({'reason': REASONS[c]} if c != 'whatsapp' else {}))
            for c, label in LABELS.items()]}

    def send_state(self, channel):
        if channel != 'whatsapp':
            return dict(enabled=False, available=False, reason=REASONS.get(channel, UNSUPPORTED))
        enabled, available = self.store.enabled(), self.bridge.available()
        return dict(enabled=enabled, available=available,
                    reason=DISABLED if not enabled else UNAVAILABLE if not available else '')

    def reject(self, code, *, key=None, request_id=None, action='send_rejected'):
        # Do not persist unchecked input (including arbitrary numbers/exception text).
        try:
            decode_key(key, 'conversation')
        except BadRequest:
            key = None
        if not isinstance(request_id, str) or not re.fullmatch(r'[A-Za-z0-9-]{8,80}', request_id):
            request_id = None
        self.store.audit(self.clock(), action, key, request_id, code=code, actor='owner_explicit')

    def set_settings(self, fields):
        channel, value = fields.get('channel'), fields.get('enabled')
        if channel != 'whatsapp':
            self.reject('unsupported_channel', action='settings_rejected')
            return {'error': REASONS.get(channel, UNSUPPORTED), 'reason': REASONS.get(channel, UNSUPPORTED)}, 400
        if value not in ('0', '1'):
            self.reject('enabled_invalid', action='settings_rejected')
            return {'error': '开关参数须为 0 或 1'}, 400
        self.store.set_enabled(value == '1', self.clock())
        return self.settings(), 200

    def create_attachment(self, key, fields):
        request_id = fields.get('request_id')
        try:
            channel, _, chat = decode_key(key, 'conversation')
            if channel != 'whatsapp' or not CHAT_ID.fullmatch(chat) or chat.endswith('@g.us'):
                raise BadRequest('仅支持向个人 WhatsApp 会话发送附件')
            if self.inbox is None or key not in self.inbox.read()['conversations']:
                return {'error': '未找到会话'}, 404
            if not isinstance(request_id, str) or not re.fullmatch(r'[A-Za-z0-9-]{8,80}', request_id):
                raise ValueError('候选标识无效，请重新选择文件')
            result = self.candidates.create(key, request_id, fields.get('filename'),
                                            fields.get('mimetype'), fields.get('data'))
            self.store.audit(self.clock(), 'attachment_candidate', key, request_id, actor='owner_explicit')
            return {'candidate': result}, 200
        except (BadRequest, ValueError) as exc:
            return {'error': str(exc)}, 400

    def send_attachment(self, key, fields):
        request_id = fields.get('request_id')
        candidate_id = fields.get('candidate_id', '')

        def refused(code, message, status=400):
            self.reject(code, key=key, request_id=request_id)
            return {'error': message}, status

        if fields.get('confirm') != '1':
            return refused('confirmation_required', '请先确认发送对象和文件，再点确认发送')
        if not isinstance(request_id, str) or not re.fullmatch(r'[A-Za-z0-9-]{8,80}', request_id):
            return refused('request_id_invalid', '请求标识无效，请重新确认发送')
        if not isinstance(candidate_id, str) or not re.fullmatch(r'[0-9]{1,18}', candidate_id) or int(candidate_id) < 1:
            return refused('candidate_invalid', '附件候选无效，请重新选择文件')
        if fields.get('confirm_new_recipient', '0') not in ('0', '1'):
            return refused('recipient_confirmation_invalid', '首次联系人确认参数须为 0 或 1')
        try:
            channel, _, chat = decode_key(key, 'conversation')
        except BadRequest:
            return refused('key_invalid', '会话标识无效')
        if channel != 'whatsapp' or not CHAT_ID.fullmatch(chat) or chat.endswith('@g.us'):
            return refused('unsupported_channel', '仅支持向个人 WhatsApp 会话发送附件')
        if not self.store.enabled():
            self.reject('disabled', key=key, request_id=request_id)
            return {'error': DISABLED}, 403
        if self.inbox is None or key not in self.inbox.read()['conversations']:
            return refused('conversation_missing', '未找到会话', 404)
        if not self.bridge.available():
            self.reject('unavailable', key=key, request_id=request_id)
            return {'error': UNAVAILABLE}, 503
        meta = self.candidates.candidate_metadata(int(candidate_id), key)
        if not meta:
            return refused('candidate_missing', '附件候选不存在，请重新选择文件', 409)
        marker = '附件候选 #' + candidate_id + ':' + meta['marker']
        old = self.store.find_request(request_id)
        if old and (old['conversation_key'] != key or old['text'] != marker):
            return refused('request_id_conflict', '请求标识已用于其他发送，请核对发送记录', 409)
        previous_request = meta['send_request_id']
        replace_request = None
        if previous_request and previous_request != request_id and fields.get('confirm_new_recipient') == '1':
            previous = self.store.find_request(previous_request)
            if (previous and previous['conversation_key'] == key and previous['text'] == marker
                    and previous['state'] == 'failed' and previous['error_code'] == 'recipient_not_bound'):
                replace_request = previous_request
        row = self.candidates.claim(int(candidate_id), key, request_id, replace_request=replace_request)
        if not row:
            return refused('candidate_claimed', '附件候选不存在或已用于其他发送，请重新选择文件', 409)
        attachment = dict(filename=row['filename'], mimetype=row['mimetype'],
                          data=base64.b64encode(row['data']).decode('ascii'))
        return self.send(key, dict(text=marker, request_id=request_id, confirm='1',
                                   confirm_new_recipient=fields.get('confirm_new_recipient', '0')),
                         attachment=attachment)

    def send(self, key, fields, *, attachment=None):
        request_id = fields.get('request_id')

        def refused(code, text, status=400):
            self.reject(code, key=key, request_id=request_id)
            return {'error': text}, status

        try:
            channel, _, chat = decode_key(key, 'conversation')
        except BadRequest:
            return refused('key_invalid', '会话标识无效')
        if channel != 'whatsapp':
            return refused('unsupported_channel', UNSUPPORTED)
        if not CHAT_ID.fullmatch(chat):
            return refused('chat_invalid', '会话标识无效')
        if not self.store.enabled():
            return refused('disabled', DISABLED, 403)
        if fields.get('confirm') != '1':
            return refused('confirmation_required', '请先确认发送内容，再勾选确认发送')
        text = fields.get('text', '').strip()
        try:
            text.encode('utf8')
        except UnicodeError:
            return refused('text_invalid', '发送内容须为 1–4000 字')
        if not 1 <= len(text) <= 4000:
            return refused('text_invalid', '发送内容须为 1–4000 字')
        if '【待确认' in text:
            return refused('unconfirmed_draft', '草稿里还有【待确认】，请先改掉再发送')
        if not isinstance(request_id, str) or not re.fullmatch(r'[A-Za-z0-9-]{8,80}', request_id):
            return refused('request_id_invalid', '请求标识必填，须为 8–80 位字母、数字或连字符')
        confirm_new = fields.get('confirm_new_recipient', '0')
        if confirm_new not in ('0', '1'):
            return refused('recipient_confirmation_invalid', '首次联系人确认参数须为 0 或 1')
        raw_draft = fields.get('draft_id')
        if raw_draft is not None and (not re.fullmatch(r'[0-9]{1,18}', raw_draft) or int(raw_draft) < 1):
            return refused('draft_id_invalid', '草稿标识无效')
        old = self.store.find_request(request_id)
        if old is not None:
            if old['conversation_key'] != key or (attachment is not None and old['text'] != text):
                return refused('request_id_conflict', '请求标识已用于其它会话，请核对发送记录', 409)
            self.store.audit(self.clock(), 'duplicate', key, request_id, code='existing_request')
            return send_response(old), 200
        # Keys are encoded identities, not authority to invent an arbitrary recipient.
        if self.inbox is None or key not in self.inbox.read()['conversations']:
            return refused('conversation_missing', '未找到会话', 404)
        row, new, rejection, retry = self.store.reserve(key, chat, text, request_id,
                                                     int(raw_draft) if raw_draft else None, self.clock(),
                                                     attachment_marker=text if attachment is not None else None)
        if rejection == 'disabled':
            return {'error': DISABLED}, 403
        if rejection == 'request_id_conflict':
            return {'error': '请求标识已用于其它会话，请核对发送记录'}, 409
        if rejection == 'rate_limited':
            return {'error': '发送太频繁，请稍后再试', 'retry_after': retry}, 429
        if new:
            def audit(action, **detail):
                self.store.audit(self.clock(), action, key, request_id, **detail)
            try:
                if attachment is None:
                    state, code = self.bridge.deliver(chat, text, row['idempotency_key'], confirm_new == '1', audit)
                else:
                    state, code = self.bridge.deliver(chat, text, row['idempotency_key'], confirm_new == '1', audit,
                                                      attachment=attachment)
            except Exception:
                state, code = 'unknown', 'send_outcome_unknown'
            row = self.store.finish(row, state, code, self.clock())
        return send_response(row), 200

    def recent(self, key=None):
        return {'items': [public(row) for row in self.store.recent(key)]}


__all__ = ['Outbox', 'Store', 'BridgeClient', 'OUTBOX_PATH']
