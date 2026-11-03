"""Pure alert calculation; never invokes AI or mutates business records."""
from datetime import date

from console.deals.rules import paid
from console.deals.stages import KEYS
from .common import PARTY_LABELS, SEVERITY, duration, safe, seconds

CHAT_KINDS = frozenset(('no_reply', 'waiting_too_long', 'deal_stale'))


def business_date(value):
    """Optional human-entered dates never crash the whole patrol."""
    try:
        return date.fromisoformat(value) if value else None
    except (TypeError, ValueError):
        return None


def fulfilment_rules(inputs, config, add):
    nodes = {}
    for row in inputs.rows.get('fulfilment_nodes', ()):
        nodes.setdefault(row['deal_id'], {})[row['node']] = row
    for deal in inputs.deals.values():
        if not paid(deal['stage']):
            continue
        progress = nodes.get(deal['id'], {})
        signoff = progress.get('signoff', {})
        signed = bool(signoff.get('signed_on') or signoff.get('status') == 'done'
                      or KEYS.index(deal['stage']) >= KEYS.index('delivered'))
        booking = progress.get('booking', {})
        transit = progress.get('transit', {})
        shipped = bool(signed or booking.get('departed_on') or transit.get('status') in ('doing', 'done')
                       or KEYS.index(deal['stage']) >= KEYS.index('shipped'))
        promise = progress.get('supplier_confirmation', {})
        due = business_date(promise.get('promised_on'))
        if not shipped and due is not None:
            remaining = (due - inputs.today).days
            if remaining < 0:
                add(deal, 'delivery_overdue', 'fulfilment_node', promise['id'],
                    '供应商承诺交期已过且尚未发货，请核对备货和出货安排', 'high')
            elif remaining <= config.delivery_due_days:
                add(deal, 'delivery_due', 'fulfilment_node', promise['id'],
                    f'供应商承诺交期将在 {remaining} 天内到达，请核对备货和出货安排', 'medium')
        inspection = progress.get('inspection', {})
        if inspection.get('inspection_result') in ('fail', 'needs_fix'):
            add(deal, 'inspection_failed', 'fulfilment_node', inspection['id'],
                '验货未通过，整改尚未验收，请跟进责任方并安排复验', 'high')
        eta = business_date(booking.get('eta'))
        if not signed and eta is not None and eta < inputs.today:
            add(deal, 'eta_overdue', 'fulfilment_node', booking['id'],
                '预计到达日期已过且尚未签收，请核对物流和客户收货情况', 'high')
        if signed:
            for issue in inputs.rows.get('deal_issues', ()):
                at = seconds(issue['created_at'])
                if issue['deal_id'] == deal['id'] and issue['status'] == 'open' and at is not None \
                        and inputs.now - at > config.issue_open_days * 86400:
                    add(deal, 'issue_open_overdue', 'issue', issue['id'],
                        f'签收后仍有超过 {config.issue_open_days} 天未处理的问题，请跟进处理结论', 'high')
    for payment in inputs.rows.get('deal_payments', ()):
        at = seconds(payment['created_at'])
        deal = inputs.deals.get(payment['deal_id'])
        if deal and payment['status'] == 'pending' and at is not None \
                and inputs.now - at > config.pending_payment_days * 86400:
            add(deal, 'payment_pending', 'payment', payment['id'],
                f'收款记录已待核对超过 {config.pending_payment_days} 天，请人工核对到账后确认或作废', 'medium')


def rules(inputs, config):
    alerts, protected = [], set()

    def add(deal, kind, ref_kind, ref_id, message, severity):
        alerts.append(dict(kind=kind, deal_id=deal['id'], ref_kind=ref_kind, ref_id=str(ref_id),
                           message=safe(deal['title'], 100) + '：' + message, severity=severity))

    def expiry(deal, value, prefix, ref_kind, ref_id, label):
        if not value:
            return
        remaining = (date.fromisoformat(value) - inputs.today).days
        action = {'quote': '请核对成本后准备新一轮报价', 'offer': '请向供应商确认新价',
                  'freight': '请向货代确认新价', 'cost': '请向费用提供方确认新价'}[prefix]
        if remaining < 0:
            add(deal, prefix + '_expired', ref_kind, ref_id, label + '已过期，' + action, 'high')
        elif remaining <= config.expiring_days:
            action = '请跟进客户意向并核对报价有效期' if prefix == 'quote' else action
            add(deal, prefix + '_expiring', ref_kind, ref_id, f'{label}将在 {remaining} 天内到期，' + action, 'medium')

    for deal in inputs.deals.values():
        if deal['status'] != 'open':
            continue
        quote = inputs.latest_quote(deal['id'])
        if quote and quote['status'] in ('draft', 'sent', 'accepted'):
            expiry(deal, quote['valid_until'], 'quote', 'quote', quote['id'], '客户报价')
            for line in quote['lines']:
                expiry(deal, line['cost_valid_until'], 'offer', 'quote_line', line['id'], '产品“' + safe(line['name'], 80) + '”成本报价')
            for cost in quote['costs']:
                expiry(deal, cost['valid_until'], 'freight' if cost['kind'] == 'freight' else 'cost',
                       'quote_cost', cost['id'], '运费报价' if cost['kind'] == 'freight' else '配套费用报价')
        links = deal['conversations']
        missing = any(c['key'] in inputs.unavailable for c in links)
        if missing:
            protected.add(deal['id'])
        if quote and quote['status'] == 'sent':
            sent = seconds(quote['sent_at'])
            customer_ids = {p['party_id'] for p in deal['participants'] if p['role'] == 'customer'}
            customer_links = [c for c in links if c['party_id'] in customer_ids]
            customer_convs = [inputs.conversations.get(c['key']) for c in customer_links]
            customer_missing = any(c['key'] in inputs.unavailable for c in customer_links)
            # Even "thanks" is a reply to a quote; do not keep chasing after it.
            replied = any(m['role'] == 'buyer' and seconds(m.get('sent_at')) is not None and m['sent_at'] >= sent
                          for c in customer_convs if c for m in c.get('messages', ())) if sent is not None else False
            if sent is not None and inputs.now - sent > config.reply_hours * 3600 and customer_convs and all(customer_convs) and not customer_missing and not replied:
                add(deal, 'no_reply', 'quote', quote['id'], '报价发出后客户尚未回复，' + duration(sent, inputs.now)[0] + '，建议跟进意向', 'medium')
            elif sent is not None and inputs.now - sent > config.reply_hours * 3600 and not customer_convs:
                add(deal, 'no_reply', 'quote', quote['id'], '报价发出后' + duration(sent, inputs.now)[0] + '，尚未关联客户会话，请核对客户是否回复', 'medium')
        if deal['stage'] == 'awaiting_payment':
            at = seconds(deal['stage_changed_at'])
            if at is not None and inputs.now - at >= config.payment_days * 86400:
                add(deal, 'payment_overdue', 'deal', deal['id'],
                    '等待付款，' + duration(at, inputs.now)[0] + '，请核对到账并跟进付款安排', 'high')
        for conv in links:
            if conv['key'] in inputs.unavailable:
                continue
            since = conv.get('since')
            if conv.get('state') == 'them' and since is not None and inputs.now - since > config.reply_hours * 3600:
                party = next((p for p in deal['participants'] if p['party_id'] == conv['party_id']), None)
                label = PARTY_LABELS[party['role']] if party else '对方'
                add(deal, 'waiting_too_long', 'deal_conversation', conv['id'],
                    label + '在原事项提出后尚未回应，' + duration(since, inputs.now)[0] + '，建议核对后起草催问', 'medium')
        for item in deal.get('waiting_items', ()):
            if item['status'] == 'open' and item['waiting_on'] == 'them' and inputs.now - item['since'] > config.reply_hours * 3600:
                add(deal, 'waiting_too_long', 'waiting_item', item['id'],
                    '具体事项“' + safe(item['title'], 100) + '”尚未完成，' + duration(item['since'], inputs.now)[0] + '，请核对对象后起草催问', 'medium')
        activity = deal['last_activity_at'] or seconds(deal['created_at'])
        if not missing and activity is not None and inputs.now - activity >= config.stale_days * 86400:
            add(deal, 'deal_stale', 'deal', deal['id'],
                f'进行中案子已超过 {config.stale_days} 天无动静，请确认继续跟进或暂停', 'low')
    fulfilment_rules(inputs, config, add)
    alerts.sort(key=lambda a: (SEVERITY[a['severity']], a['deal_id'], a['kind'], a['ref_id']))
    return alerts, protected
