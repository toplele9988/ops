"""Secretary rule output. AI may prioritize these actions, never write actions."""
from console.deals.stages import LABELS, KEYS
from .common import PARTY_LABELS, duration, safe


def rules(deal, quote, now, unavailable=(), reply_hours=48):
    waiting, steps, risks = [], [], []
    sources = []
    for party in deal['participants']:
        sources.extend((party, conv) for conv in party.get('conversations', ()))
        if not party.get('conversations'):
            sources.append((party, dict(state=party['state'], since=party['last_at'],
                                       availability=party.get('availability', 'unlinked'))))
    for party, conv in sources:
        label = PARTY_LABELS[party['role']]
        name = safe(party['name'], 80)
        elapsed, hours = duration(conv.get('since'), now)
        state = conv['state']
        if state == 'us':
            what = f'{label}{name}在等我方回复'
            steps.append(dict(who='我', what=f'查看{label}{name}的最新问题并准备回复',
                              why=f'对方等待：{elapsed}，先确认所问资料再答复'))
        elif state == 'them':
            what = f'我方在等{label}{name}回复'
            steps.append(dict(who='我', what=f'跟进{label}{name}尚未回复的事项', why=elapsed))
        elif conv.get('availability') == 'unlinked':
            what = f'{label}{name}尚未关联会话，请先关联会话再核对进度'
            elapsed, hours = '暂无待回复计时', None
        elif conv.get('availability') == 'unreadable':
            what = f'{label}{name}已关联会话暂时无法读取，请到收件箱核对后重新读取'
            elapsed, hours = '时长待确认', None
        elif conv.get('availability') == 'unknown':
            what = f'{label}{name}的消息方向未知，请核对原会话'
            elapsed, hours = '时长待确认', None
        else:
            what = f'{label}{name}暂未发现待回复事项，请核对进度'
            elapsed, hours = '暂无待回复计时', None
        waiting.append(dict(party_id=party['party_id'], who='我方' if state == 'us' else label,
                            party=name, state=state, what=what, since=conv.get('since') if state != 'none' else None,
                            conversation_key=conv.get('key'), availability=conv.get('availability'),
                            last_reminded_at=conv.get('last_reminded_at'), waited_hours=hours, elapsed=elapsed))
    for item in deal.get('waiting_items', ()):
        if item['status'] != 'open':
            continue
        elapsed, hours = duration(item['since'], now)
        party = next((p for p in deal['participants'] if p['party_id'] == item['party_id']), None)
        name = safe(party['name'], 80) if party else '原参与方'
        label = PARTY_LABELS[party['role']] if party else '对方'
        what = f"{label}{name}在等我方处理：{safe(item['title'])}" if item['waiting_on'] == 'us' else f"我方在等{label}{name}：{safe(item['title'])}"
        waiting.append(dict(party_id=item['party_id'], who='我方' if item['waiting_on'] == 'us' else label,
                            party=name, state=item['waiting_on'], what=what, since=item['since'],
                            conversation_key=item['conversation_key'], item_id=item['id'],
                            last_reminded_at=item['last_reminded_at'], waited_hours=hours, elapsed=elapsed))
        steps.append(dict(who='我', what='核对并跟进事项：' + safe(item['title']), why=elapsed))
    if not waiting:
        elapsed, hours = duration(deal['stage_changed_at'], now)
        waiting.append(dict(party_id=None, who='我方', party=None, state='none',
                            what='会话归属待确认，需我方核对当前进度', since=deal['stage_changed_at'],
                            waited_hours=hours, elapsed=elapsed))
        steps.append(dict(who='我', what='补充参与方与关联会话', why='先确认这笔生意卡在谁手上'))
    if quote:
        problems = quote['blocking'] + quote['warnings']
        problems = sorted(problems, key=lambda p: 0 if p['code'] == 'inverted' else 1)
        risks.extend(p['message'] for p in problems)
        if quote['blocking']:
            steps.insert(0, dict(who='我', what=quote['blocking'][0]['message'],
                                 why='先补齐或核对报价依据，再由你确认后对外回复'))
    if unavailable:
        risks.insert(0, '部分关联会话暂时无法读取，请到收件箱核对最新进度')
    for party in waiting:
        if party['state'] in ('us', 'them') and (party['waited_hours'] or 0) > reply_hours:
            risks.append(party['what'] + '，' + party['elapsed'])
    if deal['unlinked_participants']:
        risks.append('有参与方尚未关联会话，需补充关联才能判断进度')
    if deal.get('next_action'):
        steps.append(dict(who='我', what=safe(deal['next_action']), why='案子中已记录的下一步'))
    tasks = [t for group in deal['tasks'].values() for t in group if t['status'] == 'open']
    tasks.sort(key=lambda t: (t['stage'] != deal['stage'], t['due'] or '9999', t['id']))
    steps.extend(dict(who='我', what=safe(t['title']), why='案子尚未完成的待办') for t in tasks)
    if not steps:
        steps.append(dict(who='我', what='核对最新进展并补充下一步待办', why='保持这笔生意的跟进记录完整'))
    focus = next((w for w in waiting if w['state'] == 'us'), None) or next(
        (w for w in waiting if w['state'] == 'them'), waiting[0])
    quote_label = {'draft': '待你核对', 'sent': '已发出', 'accepted': '已被客户接受',
                   'rejected': '被客户拒绝', 'superseded': '已被新轮替代'}
    phase = f"报价第 {quote['round_no']} 轮{quote_label[quote['status']]}" if quote else '当前在' + deal['stage_label']
    timing = '，' + focus['elapsed'] if focus['state'] in ('us', 'them') else (
        '；阶段停留时长：' + focus['elapsed'].removeprefix('已等 ') if focus['party_id'] is None else '')
    headline = f"{phase}；{focus['what']}" + timing
    suggested = deal['stage']
    if quote and quote['status'] == 'accepted' and KEYS.index(suggested) < KEYS.index('awaiting_payment'):
        suggested = 'awaiting_payment'
    # Stable candidate actions keep AI-generated customer wording out of next_steps.
    unique = list({s['what']: s for s in steps}.values())
    result = dict(headline=headline, waiting_on=waiting, next_steps=unique[:3],
                  risks=list(dict.fromkeys(risks))[:3], suggested_stage=suggested,
                  suggested_stage_label=LABELS[suggested])
    return result, unique[:8]


def render(output):
    lines = [output['headline']]
    lines.extend(w['what'] + '；' + w['elapsed'] for w in output['waiting_on'])
    lines.extend(f"{i}. {s['who']}：{s['what']}。{s['why']}" for i, s in enumerate(output['next_steps'], 1))
    lines.extend('提醒：' + risk for risk in output['risks'])
    lines.append('建议阶段：' + output['suggested_stage_label'] + '（由你确认）')
    return '\n'.join(lines)
