"""Small pure helpers shared by the four internal roles."""
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import re

from console.deals.waiting import seconds, last_known, observation as waiting_observation
from console.kb.text import display

ROLE_INFO = {
    'secretary': ('案子秘书', '说清卡在谁手上，并给出下一步待办', 'L1'),
    'triage': ('分拣员', '建议会话归属，等你确认后关联案子', 'L0'),
    'patrol': ('巡检员', '提醒报价到期、收款核对、回复及履约拖延', 'L0'),
    'brief': ('每日成交简报', '汇总成交结果和今天最该处理的事', 'L0'),
    'audit': ('数据体检员', '检查渠道和档案的数据缺口，说明原因与补全位置', 'L0'),
    'extract': ('报价抽取员', '抽取供应商和货代价格线索，等你确认有效期后入档', 'L0'),
    'listing': ('发品员', '根据已确认产品资料起草商品内容，等老板审核后导出', 'L1'),
}
PARTY_LABELS = dict(customer='客户', supplier='供应商', forwarder='货代', vendor='配套供应商')
SEVERITY = dict(high=0, medium=1, low=2)

_SEPARATORS = r'\s~～_＿\-－—–:：·、,，()（）./／'
PARTY_CODE = re.compile(r'(SUP|CUS)[' + _SEPARATORS + r']*\d+', re.I)
_COMPANY = r'(?:乐初|(?:Lechu|LC)(?![A-Za-z]))'
_GROUP_WORD = r'(?:工作|沟通|对接|供应商|群)'
_GROUP_TOKEN = r'(?:' + _COMPANY + '|' + _GROUP_WORD + ')'
_GROUP_PREFIX = re.compile(r'^(?:' + _GROUP_TOKEN + r'[' + _SEPARATORS + r']*)+', re.I)
_GROUP_SUFFIX = re.compile(r'(?:[' + _SEPARATORS + r']*' + _GROUP_TOKEN + r')+$', re.I)
_BARE_PREFIX = re.compile(r'^(SUP|CUS)(?![A-Za-z])', re.I)
_BARE_SUFFIX = re.compile(r'(?<![A-Za-z])(SUP|CUS)$', re.I)
# Work-group names may carry the tag mid-name: "…乐初 SUP 工作对接", "乐初SUP-…".
_BARE_TOKEN = re.compile(r'(?<![A-Za-z])(SUP|CUS)(?![A-Za-z])', re.I)
_EDGE_SEPARATORS = re.compile(r'^[' + _SEPARATORS + r']+|[' + _SEPARATORS + r']+$')
_NUMBER = r'\d+(?:,\d{3})*(?:\.\d+)?'
_CURRENCY = r'(?:USD|CNY|RMB|EUR|GBP|HKD|JPY|AUD|CAD|美元|人民币|欧元|元)'
# Protect explicit business numbers before checking phone candidates. In
# particular, a date plus an adjacent count must not become one long phone.
_BUSINESS_NUMBER = re.compile(
    r'(?<!\d)\d{4}-\d{2}-\d{2}(?!\d)'
    r'|(?<![A-Za-z])(?:' + _CURRENCY + r'|[$€£¥￥])\s*[+-]?' + _NUMBER +
    r'|(?<![A-Za-z0-9_.+-])' + _NUMBER + r'\s*(?:' + _CURRENCY + r'|单|个|件|台|套|箱|轮|pcs\b|units\b)'
    r'|(?<![A-Za-z0-9_.+-])\d+(?:,\d{3})*\.\d+(?!\d)'
    r'|(?<![A-Za-z])(?:订单号|订单编号|order\s*(?:id|no\.?|number))\s*[:：#]?\s*[A-Za-z0-9_-]+', re.I)
_PHONE = re.compile(r'(?<![A-Za-z0-9_.])\+?\d[\d ()-]{8,}\d(?![A-Za-z0-9_])')


def party_name_parts(value):
    """Peel group labels at either edge, preserving business words and brands."""
    original = str(value or '')
    codes = {m[1].upper() for m in PARTY_CODE.finditer(original)}
    name = PARTY_CODE.sub('', original)
    if re.search(_COMPANY, name, re.I):  # inside our work-group names only; "X SUP Tools" stays a brand
        codes.update(m[1].upper() for m in _BARE_TOKEN.finditer(name))
        name = re.sub(r'\s{2,}', ' ', _BARE_TOKEN.sub('', name)).strip() or name
    while True:
        before = name
        name = _EDGE_SEPARATORS.sub('', name)
        for pattern in (_BARE_PREFIX, _BARE_SUFFIX):
            match = pattern.search(name)
            if match:
                codes.add(match[1].upper())
                name = _EDGE_SEPARATORS.sub('', pattern.sub('', name))
        prefix, suffix = _GROUP_PREFIX.search(name), _GROUP_SUFFIX.search(name)
        labels = ' '.join(m[0] for m in (prefix, suffix) if m)
        if re.search(_COMPANY, labels, re.I) and re.search(_GROUP_WORD, labels):
            name = _GROUP_SUFFIX.sub('', _GROUP_PREFIX.sub('', name))
        if name == before:
            return (name if len(name) > 1 else original), codes


def clean_party_name(value):
    return party_name_parts(value)[0]


def redact_phones(text):
    def redact(fragment):
        return _PHONE.sub(lambda m: '〔号码已隐藏〕' if sum(c.isdigit() for c in m[0]) >= 10 else m[0], fragment)
    parts, start = [], 0
    for match in _BUSINESS_NUMBER.finditer(text):
        parts.extend((redact(text[start:match.start()]), match[0]))
        start = match.end()
    parts.append(redact(text[start:]))
    return ''.join(parts)


@dataclass(frozen=True)
class Config:
    expiring_days: int = 3
    reply_hours: int = 48
    payment_days: int = 3
    stale_days: int = 7
    delivery_due_days: int = 3
    issue_open_days: int = 7
    pending_payment_days: int = 2
    per_minute: int = 6
    per_hour: int = 60
    scan_limit: int = 100
    timezone: str = 'Asia/Shanghai'
    ai_timeout: float = 365
    knowledge_timeout: float = 3

    def __post_init__(self):
        from zoneinfo import ZoneInfo
        ZoneInfo(self.timezone)
        for name, value in asdict(self).items():
            if name == 'timezone':
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 < value <= 100000:
                raise ValueError('岗位阈值须为有效正数')
        if type(self.scan_limit) is not int:
            raise ValueError('扫描数量须为整数')


def dumps(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def fingerprint(value):
    return hashlib.sha256(dumps(value).encode('utf8')).hexdigest()


def stamp(now):
    return datetime.fromtimestamp(now, timezone.utc).isoformat(timespec='microseconds')


def safe(value, limit=500):
    # Reuse the KB's path/control-character redaction, then remove contact and
    # credential-like strings. Message references stay separate from text.
    text = str(value or '')[:limit + 256]
    text = re.sub(r'https?://\S+', '〔链接已隐藏〕', text, flags=re.I)
    text = re.sub(r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}', '〔邮箱已隐藏〕', text)
    text = re.sub(r'(?i)(?:wxid_|sk-)[\w-]+', '〔标识已隐藏〕', text)
    text = re.sub(r'(?i)(?:bearer\s+|(?:token|password|api[_-]?key|密码|令牌)\s*[:=：]\s*)\S+', '〔凭据已隐藏〕', text)
    text = redact_phones(text)
    return display(text).strip()[:limit]


def observation(conversation):
    value = waiting_observation(conversation)
    if value:
        value['title'] = safe(value.get('title'), 100)
    return value


def message_ref(key, message):
    ident = message.get('message_id')
    dedup = message.get('dedup')
    if not ident and dedup and dedup[0] == 'id':
        ident = dedup[1]
    # The unified WeChat/WhatsApp projection does not expose source message IDs.
    # Make an explicitly derived, stable locator; never invent a channel ID.
    return dict(kind='message', id=str(ident) if ident else 'derived:' + fingerprint(
        [key, message.get('role'), message.get('sent_at'), message.get('text')]),
        conversation_key=key, sent_at=message.get('sent_at'), derived=not bool(ident))


def conversation_data(conversation, limit=30):
    key = conversation['key']
    messages = []
    for m in (conversation.get('messages') or ())[-limit:]:
        if seconds(m.get('sent_at')) is None:
            continue
        messages.append(dict(role=m['role'], text=safe(m['text'], 1000), sent_at=m['sent_at'],
                             reference=message_ref(key, m)))
    return dict(key=key, channel=conversation['channel'], buyer_name=safe(conversation.get('buyer_name'), 100),
                counterpart_role=conversation.get('counterpart_role', 'unclassified'), messages=messages)


def summary(conversation):
    last = last_known(conversation) or {}
    return dict(channel=conversation['channel'], counterpart_name=safe(conversation.get('buyer_name'), 100),
                last_at=last.get('sent_at'), preview=safe(last.get('text'), 80))


def duration(at, now):
    at = seconds(at)
    if at is None:
        return '时长待确认', None
    hours = max(0, int((now - at) // 3600))
    return ('刚刚' if hours == 0 else f'已等 {hours} 小时' if hours < 48 else f'已等 {hours // 24} 天'), hours


def evidence_for(deal, quotes=(), conversations=()):
    result = [dict(kind='deal', id=deal['id'])]
    result += [dict(kind='party', id=p['party_id']) for p in deal.get('participants', ())]
    result += [dict(kind='task', id=t['id']) for tasks in deal.get('tasks', {}).values() for t in tasks]
    result += [dict(kind='event', id=e['id']) for e in deal.get('events', ())]
    result += [dict(kind='waiting_item', id=i['id']) for i in deal.get('waiting_items', ())]
    for quote in quotes:
        result.append(dict(kind='quote', id=quote['id']))
        for line in quote.get('lines', ()):
            result.append(dict(kind='quote_line', id=line['id']))
            if line.get('offer_id'):
                result.append(dict(kind='offer', id=line['offer_id']))
        for cost in quote.get('costs', ()):
            result.append(dict(kind='quote_cost', id=cost['id']))
            if cost.get('freight_quote_id'):
                result.append(dict(kind='freight_quote', id=cost['freight_quote_id']))
    for conv in conversations:
        result.append(dict(kind='conversation', id=conv['key']))
        result.extend(m['reference'] for m in conv['messages'])
    return result
