"""Conservative rules first; only ambiguous conversations need a model."""
import json
import re

from console.inbox.model import is_closing
from console.kb.text import tokens
from . import dossier_links
from .common import PARTY_LABELS, clean_party_name, fingerprint, last_known, party_name_parts, safe, seconds

IDENTITY_KINDS = frozenset(('supplier', 'forwarder', 'vendor'))
FORWARDER_WORDS = ('快递', '物流', '货代', '运费', '空运', '海运', 'freight', 'forwarder')
VENDOR_WORDS = ('打样', '包装', '验货', '报关', '认证', '配套')
PRICE_WORDS = re.compile(r'\b(?:prices?|quotes?|quotation|pricing|how\s+much)\b|报价|询价|询盘|问价|多少钱|价格', re.I)
QUANTITY_WORDS = re.compile(r'\b(?:moq|quantity|qty)\b|数量|起订量', re.I)
PRODUCT_WORDS = re.compile(r'\b(?:products?|lamps?|lighting|beam|led)\b|产品|灯具|灯光|光束灯|摇头灯', re.I)
NOISE = {'虚构', '产品', '报价', 'price', 'quote', 'the', 'and'}
SYSTEM_COMPANY_NAME = '乐初电子有限公司'
PEER_WORDS = ('同行', '资源共享', '行业群', '交流群', '供需')
DUPLICATE_REASON = '与另一条会话是同名买家，建议一起处理'


def named_kind(name):
    codes = party_name_parts(name)[1]
    if 'SUP' in codes:
        if any(word in name for word in FORWARDER_WORDS):
            return 'forwarder'
        if any(word in name for word in VENDOR_WORDS):
            return 'vendor'
        return 'supplier'
    return 'customer' if 'CUS' in codes else None


def placeholder_name(name):
    return not (name or '').strip() or bool(re.fullmatch(r'微信群\s*·\s*[A-Za-z0-9_]{4}', name.strip()))


def ignore_hint(conv):
    name = (conv.get('buyer_name') or '').strip()
    if conv.get('is_group', conv['channel'] == 'wechat'):
        if name.startswith('A-') and SYSTEM_COMPANY_NAME in name and re.search(r'\d+$', name):
            return '像是系统通知，不是客户或供应商', .8
        if any(word in name for word in PEER_WORDS):
            return '像是同行交流群', .7
    if placeholder_name(name):
        last = last_known(conv) or {}
        text = ' '.join(m['text'] for m in (conv.get('messages') or ())[-12:] if m['role'] == 'buyer')
        if last.get('role') == 'buyer':
            text += ' ' + last['text']
        if len((last.get('text') or '').strip()) <= 6 or not (PRICE_WORDS.search(text) or QUANTITY_WORDS.search(text)):
            return '暂时看不出是哪笔生意', .5
    return None


def buyer_group(channel, name):
    """Use the complete counterpart name, never a stripped or redacted brand."""
    name = (name or '').strip().casefold()
    return fingerprint((channel, name)) if not placeholder_name(name) else None


def link_duplicate(output, primary, party, deal_id):
    return dict(output, action='link', suggested_deal_id=deal_id, suggested_party_id=party['id'],
                suggested_party_name=safe(party['name'], 100), suggested_party_kind='customer',
                duplicate_of=primary['conversation_key'], confidence=.9,
                reason=('加入刚建的案子' if primary['action'] == 'create' else '加入已确认的案子') + '；' + DUPLICATE_REASON)


def combine_buyers(prepared, previous, inputs):
    """Keep the newest buyer as primary; the rest wait for a confirmed deal."""
    accepted, owners = {}, {}
    for item in previous:
        group = item.get('buyer_fingerprint')
        deal = inputs.deals.get(item['decided_deal_id'])
        if (group and item['status'] == 'accepted' and deal and deal['status'] == 'open'
                and item['suggested_party_kind'] == 'customer'):
            accepted.setdefault(group, []).append(item)
    for conv, rule in prepared:
        group = buyer_group(conv['channel'], conv.get('buyer_name'))
        rule.update(buyer_fingerprint=group, duplicate_of=None)
        if group and rule['suggested_party_id']:
            owners.setdefault(group, set()).add(rule['suggested_party_id'])
    parties = {p['id']: p for p in inputs.rows['parties']}
    primary = {}
    for conv, rule in prepared:
        group = rule['buyer_fingerprint']
        if rule.get('source') == 'ai_profile' or rule['reason'].startswith('档案：') or '档案冲突：' in rule['reason']:
            continue
        if not group or rule['action'] == 'ignore' or rule['suggested_party_kind'] != 'customer' or len(owners.get(group, ())) > 1:
            continue
        confirmed = accepted.get(group, [])
        targets = {(s['decided_deal_id'], s['decided_party_id']) for s in confirmed}
        if len(targets) == 1:
            did, pid = next(iter(targets))
            deal, party = inputs.deals.get(did), parties.get(pid)
            if (deal and deal['status'] == 'open' and party and party['kind'] == 'customer'
                    and rule['suggested_party_id'] in (None, pid)):
                rule.update(link_duplicate(rule, confirmed[0], party, did), certain=True)
                continue
        if group in primary:
            rule.update(action='identity', suggested_deal_id=None, duplicate_of=primary[group],
                        reason=DUPLICATE_REASON, confidence=min(rule['confidence'], .7), certain=True)
        else:
            primary[group] = conv['key']


def suggestion_order(suggestion):
    action, confidence = suggestion['action'], suggestion['confidence']
    priority = (0 if action == 'identity' and confidence >= .8 else 1 if action == 'create'
                else 3 if action == 'ignore' else 2)
    at = seconds(suggestion['conversation'].get('last_at'))
    return priority, -confidence, at if at is not None else float('inf'), suggestion['conversation_key']


def product_overlap(words, deal):
    product_words = set(tokens(' '.join(i['name'] for i in deal['items']))) - NOISE
    overlap = words & product_words
    return overlap if len(overlap) >= 2 and len(overlap) / max(1, len(product_words)) >= .6 else set()


def create_context(conv, inputs):
    messages = [m for m in (conv.get('messages') or ())[-12:] if m['role'] == 'buyer']
    # Unknown tail observations do not erase the last effective buyer message.
    if not messages:
        latest = last_known(conv)
        if latest and latest['role'] == 'buyer':
            messages = [latest]
    text = ' '.join(safe(m['text'], 1000) for m in messages)
    prices = sorted({m[0].casefold() for m in PRICE_WORDS.finditer(text)})
    quantities = sorted({m[0].casefold() for m in QUANTITY_WORDS.finditer(text)})
    words = set(tokens(text)) - NOISE
    matched = set()
    for deal in inputs.deals.values():
        if deal['status'] == 'open':
            matched.update(product_overlap(words, deal))
    products = sorted(matched or {m[0].casefold() for m in PRODUCT_WORDS.finditer(text)})
    inquiry = bool(prices or quantities)
    confidence = .7 if inquiry and matched else .5 if inquiry else .3
    channel = {'alibaba': '阿里巴巴', 'wechat': '微信', 'whatsapp': 'WhatsApp'}.get(conv['channel'], safe(conv['channel'], 30))
    excerpt = safe(messages[-1]['text'], 1000)[:40] if messages else '暂无对方消息'
    reason = (f"渠道：{channel}；对方：{safe(conv.get('buyer_name'), 100) or '名称待确认'}；"
              f'最近对方消息：“{excerpt}”；价格关键词：' + ('、'.join(prices) or '未见') +
              '；数量关键词：' + ('、'.join(quantities) or '未见') +
              '；产品关键词：' + ('、'.join(products[:5]) or '未见') +
              ('（与在谈产品匹配）' if matched else '') + '；请核对身份和产品后新建案子')
    return reason, confidence


def latest_message(conversation):
    messages = list(conversation.get('messages') or ())
    if conversation.get('last'):
        messages.append(conversation['last'])
    return max((m for m in messages if seconds(m.get('sent_at')) is not None),
               key=lambda m: seconds(m['sent_at']), default=None)


def eligible(conversation, now, dossier=False, profile=False):
    if profile:
        return True
    if conversation.get('counterpart_role') in ('learning', 'internal', 'ignored'):
        return False
    if dossier:
        last = latest_message(conversation)
        at = seconds(last.get('sent_at')) if last else seconds(conversation.get('last_seen'))
        return at is not None and 0 <= now - at <= 180 * 86400
    last = last_known(conversation)
    at = seconds(last.get('sent_at')) if last else None
    return bool(last and last['role'] == 'buyer' and (not is_closing(last['text']) or ignore_hint(conversation))
                and at is not None and 0 <= now - at <= 30 * 86400)


def candidates(inputs):
    return [dict(id=d['id'], title=safe(d['title'], 150),
                 products=[safe(item['name'], 100) for item in d['items']])
            for d in inputs.deals.values() if d['status'] == 'open']


def rules(conv, inputs):
    links = getattr(inputs, 'dossier_links', {}).get(conv['key'], [])
    link, party, conflict = dossier_links.match(links, inputs.rows['parties'])
    if link:
        return dict(action='identity', suggested_deal_id=None,
                    suggested_party_id=party['id'] if party else None,
                    suggested_party_name=safe(party['name'] if party else link['name'], 100),
                    suggested_party_kind=party['kind'] if party else link['kind'],
                    confidence=.95 if party else .9, certain=True,
                    reason=f"档案：该群是供应商「{safe(link['name'], 100)}」的工作群（来源 {safe(link['source_id'], 200) or '未标注'}）")
    result = name_rules(conv, inputs)
    profile = getattr(inputs, 'profiles', {}).get(conv['key'])
    if profile and usable_profile(profile):
        result = profile_rule(conv, inputs, profile, result)
    if conflict:
        result.update(certain=False, reason=result['reason'] + '；档案冲突：' + conflict)
    return result


def preserve_conflict(output, rule):
    if '；档案冲突：' in rule['reason']:
        output['reason'] = '档案冲突：' + rule['reason'].split('；档案冲突：', 1)[1] + '；' + output['reason']
    return output


def name_rules(conv, inputs):
    name = (conv.get('buyer_name') or '').strip().casefold()
    identities = {i['conversation_key']: i for i in inputs.rows['identities']}
    parties = inputs.rows['parties']
    owner = identities.get(conv['key'])
    party = next((p for p in parties if owner and p['id'] == owner['party_id']), None)
    exact_identity = party is not None
    if not party and name:
        matches = []
        for p in parties:
            try:
                aliases = json.loads(p.get('aliases_json') or '[]')
            except (ValueError, TypeError):
                aliases = []
            names = [p['name'], p.get('short_name')] + (aliases if isinstance(aliases, list) else [])
            if p['status'] == 'active' and name in {n.strip().casefold() for n in names if isinstance(n, str) and n.strip()}:
                matches.append(p)
        if len(matches) == 1:
            party = matches[0]
    linked = [d for d in inputs.deals.values() if d['status'] == 'open' and party and (
        d['customer_party_id'] == party['id'] or any(p['party_id'] == party['id'] for p in d['participants']))]
    code_kind = named_kind(name)
    kind = party['kind'] if party else code_kind
    if kind is None and conv.get('counterpart_role') in IDENTITY_KINDS:
        kind = conv['counterpart_role']
    if kind is None:
        if any(word in name for word in FORWARDER_WORDS):
            kind = 'forwarder'
        elif any(word in name for word in ('供应', '工厂', '报价', '发货', 'supplier', 'factory')):
            kind = 'supplier'
        elif any(word in name for word in VENDOR_WORDS):
            kind = 'vendor'
        elif any(word in name for word in ('客户', 'customer', 'buyer')):
            kind = 'customer'
    reason, confidence = create_context(conv, inputs)
    result = dict(suggested_deal_id=None, suggested_party_id=party['id'] if party else None,
                  suggested_party_name=safe(party['name'] if party else clean_party_name(conv.get('buyer_name')), 100),
                  suggested_party_kind=kind or 'customer', action='create',
                  confidence=.9 if code_kind and not party else confidence,
                  reason=reason, certain=False)
    ignored = ignore_hint(conv)
    if ignored:
        result.update(action='ignore', reason=ignored[0], confidence=ignored[1], certain=True)
        return result
    if code_kind and not party:
        result.update(certain=True, reason='群名编号表明对方是' + PARTY_LABELS[kind] + '；' + reason)
    # Supply-side conversations are identity work even when their products or
    # existing participation match a deal. Only a human may choose another action.
    if kind in IDENTITY_KINDS:
        basis = ('已有身份' if party else '群名编号' if code_kind else
                 '渠道分类' if conv.get('counterpart_role') in IDENTITY_KINDS else '群名线索')
        result.update(action='identity', confidence=.95 if exact_identity else .9 if code_kind else .8,
                      reason=basis + '表明对方是' + PARTY_LABELS[kind] + '；请确认往来单位身份', certain=True)
        return result
    if len(linked) == 1:
        result.update(action='link', suggested_deal_id=linked[0]['id'], confidence=0.98 if exact_identity else 0.9,
                      reason='渠道身份已确认且只有一个在谈案子' if exact_identity else '对方名称与已有单位一致，且只有一个在谈案子', certain=True)
        return result
    text = ' '.join(m['text'] for m in (conv.get('messages') or ())[-12:])
    words = set(tokens(text + ' ' + name)) - NOISE
    scores = []
    for deal in inputs.deals.values():
        if deal['status'] != 'open' or (party and linked and deal not in linked):
            continue
        if party and party['kind'] == 'customer' and deal['customer_party_id'] not in (None, party['id']):
            continue
        overlap = product_overlap(words, deal)
        if overlap:
            scores.append((len(overlap), deal))
    scores.sort(key=lambda item: (-item[0], item[1]['id']))
    if scores and (len(scores) == 1 or scores[0][0] >= scores[1][0] + 2):
        result.update(action='link', suggested_deal_id=scores[0][1]['id'], confidence=.9 if code_kind else .82,
                      reason='会话中多个产品关键词与该案子清单一致，请确认归属', certain=True)
    elif len(linked) > 1:
        result['reason'] += '；同一单位有多个在谈案子，请先核对本次询问属于哪一个'
    elif party or kind:
        result.update(certain=True)
        if party:
            result['reason'] += '；已有身份可确认往来单位，请确认是否另建案子'
    return result


def render(output):
    if output.get('duplicate_of') and output['action'] == 'identity':
        return output['reason'] + '。请先处理同名买家的主建议。'
    action = {'link': '关联已有案子', 'create': '新建案子', 'identity': '确认往来单位', 'ignore': '忽略此会话'}[output['action']]
    return f"建议{action}；对方可能是{PARTY_LABELS[output['suggested_party_kind']]}。{output['reason']}。请你确认后再处理。"


def usable_profile(profile):
    return (profile.get('state') in ('ready', 'partial') and
            type(profile.get('confidence')) in (int, float) and .7 <= profile['confidence'] <= 1)


def profile_rule(conv, inputs, profile, fallback):
    kind = profile['kind']
    if kind not in IDENTITY_KINDS | {'customer', 'peer', 'industry', 'personal', 'system', 'internal'}:
        return fallback
    result = dict(fallback, reason=f"AI 读了 {profile['messages_read']} 条消息：{safe(profile['summary'], 200)}",
                  confidence=profile['confidence'], certain=True, source='ai_profile', suggested_deal_id=None)
    if kind in IDENTITY_KINDS:
        result.update(action='identity', suggested_party_kind=kind)
    elif kind == 'customer':
        result.update(action='create', suggested_party_kind='customer')
        # Retain a link only when existing confirmed party/name/product rules already found it.
        if fallback['action'] == 'link' and fallback['suggested_party_kind'] == 'customer':
            result.update(action='link', suggested_deal_id=fallback['suggested_deal_id'])
    else:
        result.update(action='ignore', suggested_party_kind='customer', suggested_party_id=None)
    if result['suggested_party_id']:
        party = next((p for p in inputs.rows['parties'] if p['id'] == result['suggested_party_id']), None)
        if not party or party['kind'] != result['suggested_party_kind']:
            result['suggested_party_id'] = None
    return result


def profile_run(run):
    """Expose provenance stored in v4 JSON, preserving its fixed ai/rules SQL enum."""
    if run.get('output_json', {}).get('profile_source') != 'ai_profile':
        return run
    output = dict(run['output_json'])
    output.pop('profile_source')
    return dict(run, source='ai_profile', output_json=output)
