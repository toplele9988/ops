"""Fixed, public Chinese messages. Never present upstream text or exceptions."""
import re

LABELS = {'whatsapp': 'WhatsApp', 'alibaba': '阿里国际站', 'wechat': '微信', 'website': '独立站'}
REASONS = {
    'alibaba': '阿里需在 Win10 已登录页面代发，尚未验收；请先复制草稿去阿里发送',
    'wechat': '微信发送需要单独授权，暂未开放',
    'website': '独立站发送尚未开放，请复制草稿到网站客服发送',
}
UNSUPPORTED = '该渠道暂不支持后台发送，请复制草稿到原渠道发送'
DISABLED = '后台发送未开启，请先在设置里打开'
UNAVAILABLE = '发送服务配置不可用，请联系管理员'
UNKNOWN = '结果不确定：请先到 WhatsApp 里确认这条是否已发出，不要重复发送'
STATE_TEXT = {'sending': '发送中', 'sent': '已发出', 'failed': '发送失败', 'unknown': UNKNOWN}

# Only errors verified in bridge.py are definite refusals. Unknown errors fail closed.
REFUSALS = frozenset('''send_kind_invalid request_invalid idempotency_key_required
    session_mismatch recipient_invalid explicit_actor_required automatic_send_scope_denied
    automatic_send_actor_mismatch text_invalid base64_file_required filename_invalid
    mimetype_invalid base64_invalid file_size_invalid caption_invalid account_mismatch
    idempotency_conflict recipient_not_bound session_not_working account_rebind_denied
    api_auth_failed body_size_invalid body_incomplete path_not_allowed not_found
    recipient_confirmation_required binding_reason_required'''.split())


def known_refusal(code):
    return isinstance(code, str) and (code in REFUSALS or bool(re.fullmatch(r'provider_http_4[0-9]{2}', code)))


def error_text(code):
    if not code:
        return None
    if code == 'recipient_not_bound':
        return '这位联系人还没有绑定'
    if code == 'api_auth_failed' or code in ('provider_http_401', 'provider_http_403'):
        return '发送服务认证失败，请联系管理员'
    if code in ('send_kind_invalid', 'request_invalid', 'text_invalid', 'body_size_invalid',
                'body_incomplete', 'recipient_invalid', 'caption_invalid'):
        return '内容不符合要求'
    if code == 'send_outcome_unknown':
        return UNKNOWN
    return '发送没有成功'


def public(row):
    values = {k: row[k] for k in ('id', 'conversation_key', 'channel', 'text', 'state', 'created_at')}
    values['text'] = re.sub(r'^(附件候选 #[0-9]+):[0-9a-f]{64}$', r'\1', values['text'])
    return {**values,
            'state_text': STATE_TEXT[row['state']], 'error_text': error_text(row['error_code'])}


def send_response(row):
    return {'message': {k: v for k, v in public(row).items()
                        if k in ('id', 'state', 'state_text', 'created_at')},
            'needs_new_recipient_confirmation': row['error_code'] == 'recipient_not_bound',
            **({'error_text': error_text(row['error_code'])} if row['error_code'] else {})}
