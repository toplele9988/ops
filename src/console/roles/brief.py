"""Money/counts are deterministic. The model only polishes a narrative."""
from collections import Counter, defaultdict
from datetime import datetime, time, timedelta
import re
from zoneinfo import ZoneInfo

from console.deals.rules import paid
from console.deals.stages import KEYS, STAGES
from console.quotes.money import money
from .common import SEVERITY, duration, safe, seconds
from .patrol import business_date


def fulfilment_counts(inputs):
    """Counts of deals in transit/planned arrivals, and individual open records."""
    nodes = defaultdict(dict)
    for node in inputs.rows.get('fulfilment_nodes', ()):
        nodes[node['deal_id']][node['node']] = node
    week_start = inputs.today - timedelta(days=inputs.today.weekday())
    week_end = week_start + timedelta(days=7)
    in_transit = arriving = 0
    for deal in inputs.deals.values():
        if not paid(deal['stage']):
            continue
        progress = nodes[deal['id']]
        signoff = progress.get('signoff', {})
        signed = bool(signoff.get('signed_on') or signoff.get('status') == 'done'
                      or KEYS.index(deal['stage']) >= KEYS.index('delivered'))
        booking = progress.get('booking', {})
        transit = progress.get('transit', {})
        shipped = bool(booking.get('departed_on') or transit.get('status') in ('doing', 'done')
                       or KEYS.index(deal['stage']) >= KEYS.index('shipped'))
        in_transit += bool(shipped and not signed)
        eta = business_date(booking.get('eta'))
        arriving += bool(not signed and eta is not None and week_start <= eta < week_end)
    return dict(in_transit=in_transit, arriving_this_week=arriving,
                open_issues=sum(i['status'] == 'open' for i in inputs.rows.get('deal_issues', ())),
                pending_payments=sum(p['status'] == 'pending' for p in inputs.rows.get('deal_payments', ())))


def rules(inputs, alerts, secretaries, pending, config):
    day = inputs.today
    tz = ZoneInfo(config.timezone)
    starts = dict(today=day, week=day - timedelta(days=day.weekday()), month=day.replace(day=1))
    bounds = {k: datetime.combine(d, time.min, tzinfo=tz).timestamp() for k, d in starts.items()}
    upper = min(inputs.now, datetime.combine(day + timedelta(days=1), time.min, tzinfo=tz).timestamp())
    deals = list(inputs.deals.values())
    results, evidence = {}, []
    for period, lower in bounds.items():
        won = [d for d in deals if paid(d['stage']) and seconds(d['won_at']) is not None
               and lower <= seconds(d['won_at']) <= upper]
        amounts, missing, entries = defaultdict(int), 0, []
        for deal in won:
            payments = [p for p in inputs.rows.get('deal_payments', ())
                        if p['deal_id'] == deal['id'] and p['status'] == 'confirmed']
            if payments:
                by_currency = defaultdict(int)
                for payment in payments:
                    by_currency[payment['currency']] += payment['amount_fen'] * (-1 if payment['kind'] == 'refund' else 1)
                    evidence.append(dict(kind='payment', id=payment['id']))
                source = 'payment'
                currency = next(iter(by_currency)) if len(by_currency) == 1 else None
                amount = by_currency[currency] if currency is not None else None
                amount_label = '、'.join(k + ' ' + money(v) for k, v in sorted(by_currency.items()))
            else:
                accepted = [q for q in inputs.quotes[deal['id']] if q['status'] == 'accepted']
                quote = accepted[-1] if accepted else None
                complete = bool(quote and quote['lines'] and all(
                    l['sell_unit_minor'] is not None and l['qty_confirmed'] for l in quote['lines']))
                amount = sum(l['sell_unit_minor'] * l['qty'] for l in quote['lines']) if complete else None
                currency = quote['currency'] if quote else deal['currency']
                by_currency = {currency: amount} if amount is not None else {}
                source = 'quote'
                if amount is None:
                    missing += 1
                if quote:
                    evidence.append(dict(kind='quote', id=quote['id']))
                amount_label = '金额待补' if amount is None else money(amount)
            for code, value in by_currency.items():
                amounts[code] += value
            entries.append(dict(deal_id=deal['id'], title=safe(deal['title'], 100),
                                currency=currency, amount=money(amount), amount_label=amount_label, source=source,
                                amounts={k: money(v) for k, v in sorted(by_currency.items())}))
            evidence.append(dict(kind='deal', id=deal['id']))
        results[period] = dict(count=len(won), amounts={k: money(v) for k, v in sorted(amounts.items())},
                               amount_pending=missing, deals=entries,
                               amount_sources=dict(Counter(e['source'] for e in entries)))
    sent = [q for q in inputs.rows['quote_rounds'] if seconds(q['sent_at']) is not None
            and bounds['week'] <= seconds(q['sent_at']) <= upper]
    accepted = set()
    # Acceptance updates also change updated_at when notes are edited. The
    # existing transactional, exact quote lifecycle event is the reliable date.
    rounds = {(q['deal_id'], q['round_no']): q['id'] for q in inputs.rows['quote_rounds']}
    for event in inputs.rows['deal_events']:
        match = re.fullmatch(r'报价第 (\d+) 轮已接受', event['summary']) if event['kind'] == 'system' else None
        at = seconds(event['at'])
        if match and at is not None and bounds['week'] <= at <= upper:
            rid = rounds.get((event['deal_id'], int(match[1])))
            if rid is not None:
                accepted.add(rid)
                evidence.append(dict(kind='event', id=event['id']))
    lost = [d for d in deals if d['status'] == 'lost' and seconds(d['closed_at']) is not None
            and bounds['week'] <= seconds(d['closed_at']) <= upper]
    latest = {int(r['subject_key']): r for r in secretaries}
    priorities = []
    seen = set()
    for alert in sorted(alerts, key=lambda a: (SEVERITY[a['severity']], a['id'])):
        did = alert['deal_id']
        secretary = latest.get(did)
        priorities.append(dict(kind='alert', deal_id=did, alert_id=alert['id'], severity=alert['severity'],
                               message=alert['message'], headline=secretary['output_json']['headline'] if secretary else None,
                               headline_generated_at=secretary['created_at'] if secretary else None))
        evidence.append(dict(kind='alert', id=alert['id']))
        if secretary:
            evidence.append(dict(kind='role_run', id=secretary['id']))
        seen.add(did)
    if pending:
        priorities.append(dict(kind='triage', deal_id=None, severity='medium', message=f'有 {pending} 个会话等待你确认归属'))
    for did, secretary in latest.items():
        if did not in seen and did in inputs.deals and inputs.deals[did]['status'] == 'open':
            priorities.append(dict(kind='secretary', deal_id=did, severity='low',
                                   message=secretary['output_json']['headline'], headline_generated_at=secretary['created_at']))
            evidence.append(dict(kind='role_run', id=secretary['id']))
    priorities.sort(key=lambda p: SEVERITY[p['severity']])
    if not priorities:
        priorities.append(dict(kind='review', deal_id=None, severity='low', message='今天暂无待处理提醒；请核对在谈案子的下一步'))
    by_stage = Counter(d['stage'] for d in deals)
    open_stage = Counter(d['stage'] for d in deals if d['status'] == 'open')
    waiting = [p for d in deals if d['status'] == 'open' for p in d['participants'] if p['state'] in ('us', 'them')]
    oldest = min((p['last_at'] for p in waiting if p['last_at'] is not None), default=None)
    waiting_summary = (f"有 {sum(p['state'] == 'us' for p in waiting)} 方等我方回复，"
                       f"我方在等 {sum(p['state'] == 'them' for p in waiting)} 方；最长等待：{duration(oldest, inputs.now)[0]}") if waiting else '目前未发现待回复事项，等待时长待有新消息后确认'
    output = dict(day=day.isoformat(), results=results, negotiating=sum(d['status'] == 'open' for d in deals),
                  waiting_summary=waiting_summary,
                  by_stage=[dict(stage=k, label=label, count=by_stage[k], open_count=open_stage[k]) for k, label, _ in STAGES],
                  priorities=priorities[:5], pending_triage=pending,
                  fulfilment=fulfilment_counts(inputs),
                  quotes_this_week=dict(sent=len(sent), accepted=len(accepted)),
                  lost_this_week=dict(count=len(lost), deals=[dict(id=d['id'], title=safe(d['title'], 100), reason=safe(d['lost_reason'])) for d in lost]))
    evidence += [dict(kind='deal', id=d['id']) for d in deals]
    evidence += [dict(kind='quote', id=q['id']) for q in sent]
    output['narrative'] = template(output)
    return output, list({(e['kind'], e['id']): e for e in evidence}.values())


def amount_text(result):
    text = '、'.join(currency + ' ' + amount for currency, amount in result['amounts'].items()) or '暂无已确认金额'
    return text + (f"；{result['amount_pending']} 个案子金额待补" if result['amount_pending'] else '')


def template(output):
    today, week, month = (output['results'][p] for p in ('today', 'week', 'month'))
    fulfil = output['fulfilment']
    return (f"今日新成交 {today['count']} 单（{amount_text(today)}）；本周 {week['count']} 单，本月 {month['count']} 单。"
            f"还有 {output['negotiating']} 个案子在谈，本周发出 {output['quotes_this_week']['sent']} 轮报价，"
            f"接受 {output['quotes_this_week']['accepted']} 轮，丢单 {output['lost_this_week']['count']} 个。"
            f"在途货物 {fulfil['in_transit']} 单，本周计划到货（未签收）{fulfil['arriving_this_week']} 单，"
            f"未处理问题 {fulfil['open_issues']} 个，待核对收款 {fulfil['pending_payments']} 笔。"
            + output['waiting_summary'] + '。优先处理：' + output['priorities'][0]['message'])


def render(output):
    lines = [output['narrative']]
    for key, label in (('today', '今天'), ('week', '本周'), ('month', '本月')):
        result = output['results'][key]
        lines.append(f"{label}新成交 {result['count']} 单：{amount_text(result)}")
    lines.extend(f"{i}. {p['message']}" for i, p in enumerate(output['priorities'], 1))
    return '\n'.join(lines)
