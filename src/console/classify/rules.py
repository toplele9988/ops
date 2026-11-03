"""Bounded, explainable clues. Message bodies never leave this function."""
from collections import Counter
import re

LABELS = dict(supplier='供应商', customer='客户', forwarder='物流', vendor='配套',
              internal='内部', test='测试', spam='垃圾', unclassified='待核对')
BUSINESS = ('supplier', 'customer', 'forwarder', 'vendor')
IGNORED = ('internal', 'test', 'spam', 'ignore')


def suggest(name, channel, messages=(), member_roles=(), native_language=None):
    scores, reasons = Counter(), []

    def clue(role, weight, reason):
        scores[role] += weight
        reasons.append(reason)

    for marker, role in (('SUP', 'supplier'), ('CUS', 'customer')):
        if re.search(r'(?<![A-Za-z])' + marker + r'(?![A-Za-z])', name, re.I):
            clue(role, 4, '名称含供应商编号' if role == 'supplier' else '名称含客户编号')
    for pattern, role, reason in (
        (r'工厂|厂家|制造|\bfactory\b|\bmanufacturer\b', 'supplier', '名称含生产企业线索'),
        (r'物流|货代|货运|快递|\blogistics\b|\bfreight\b', 'forwarder', '名称含物流业务线索'),
        (r'线材|配套|包材|航空箱|\baccessories\b', 'vendor', '名称含配套业务线索'),
        (r'内部|同事|员工|\binternal\b', 'internal', '名称含内部工作线索'),
        (r'测试|演练|\btest\b|\bsandbox\b', 'test', '名称含测试或演练线索'),
        (r'垃圾|博彩|刷单|\bspam\b', 'spam', '名称含垃圾推广线索'),
    ):
        if re.search(pattern, name, re.I):
            clue(role, 4, reason)
    if re.search(r'有限公司|有限责任公司|\b(?:ltd|inc|llc)\b', name, re.I):
        reasons.append('名称像公司，但公司名称不能单独区分买卖方向')
    text = '\n'.join(str(m.get('text') or '')[:2000] for m in tuple(messages)[:20]
                     if m.get('role') == 'buyer')
    for pattern, role, reason in (
        (r'询价|请报价|多少钱|求购|\b(?:need|want|looking for)\b|how much|send.*(?:price|quotation)',
         'customer', '首批对方消息含询价或采购意向'),
        (r'我司供应|出厂价|现货供应|报价如下|\bour (?:price|quotation)\b|\bwe (?:supply|manufacture)\b',
         'supplier', '首批对方消息含供货或报价表达'),
        (r'运单|提单|物流单号|单号\s*[:：]?\s*[A-Z0-9]{6,}|\btracking\b|\bET[AD]\b',
         'forwarder', '首批对方消息含运输或单号线索'),
        (r'博彩|刷单返利|稳赚不赔', 'spam', '首批对方消息含垃圾推广线索'),
    ):
        if re.search(pattern, text, re.I):
            clue(role, 3, reason)
    members = Counter(role for role in member_roles if role in BUSINESS)
    if members:
        role, count = members.most_common(1)[0]
        if len(members) == 1 and count >= 2:
            clue(role, 2, '首批发言者的名称线索方向一致，尚未核验完整群成员')
        else:
            reasons.append('首批发言者线索较少或方向不同，不能据此确定群用途')
    if channel == 'alibaba':
        clue('customer', 1, '来源是阿里询盘渠道，作为弱客户线索')
    if native_language and native_language not in ('zh-CN', 'zh-TW'):
        reasons.append('语言服务已有外语记录；语言本身不能区分买卖方向')
    ordered = scores.most_common()
    role = ordered[0][0] if ordered else 'unclassified'
    # Any competing direction caps confidence, including SUP versus inquiry.
    conflict = len(ordered) > 1
    confidence = min(.95, .45 + .07 * ordered[0][1]) if ordered else .15
    if conflict:
        confidence = min(confidence, .4)
        reasons.append('线索方向有冲突，请核对后再确认')
    if not reasons:
        reasons.append('现有线索不足，请核对用途后选择分类')
    return dict(suggested_role=role, confidence=round(confidence, 2),
                reasons=reasons, conflict=conflict)
