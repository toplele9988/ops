"""Human-readable rehearsal projection; hypothetical timing never changes gates."""
import json
from .policy import evaluate, pace

LABELS = dict(inbound='新入站消息', supplier_gap='供应商高优先级资料缺口', task_due='案子待办到期',
              reply_timeout='我方发出后超过两天未获回复', supplier_reply='供应商回复已归档',
              forwarder_reply='货代回复已归档', payment_confirmed='人工收款记录',cost_expired='资料有效期已过')


def preview(db, action, now, profiles):
    data = action['data']
    profile = dict((data.get('context') or {}).get('contacts', {}).get(action['contact_key'], {}))
    profile.update(profiles.get(action['contact_key'], {}))
    row = db.execute('SELECT payload FROM operator_events WHERE id=?', (action['event_id'],)).fetchone()
    event = json.loads(row[0]) if row else {}
    payload = data.get('payload', {})
    sources = list(payload.get('sources', []))
    if event.get('message_id'):
        sources.append(dict(label='触发消息（原会话）',ref='inbox:'+event['message_id'],conversation_key=event['conversation_key']))
    facts = (data.get('context') or {}).get('facts', [])
    for fact in facts:
        if fact['id'] in data.get('fact_ids', []):
            sources.append(dict(label='核准资料',ref=fact.get('source_ref', ''),at=fact.get('observed_at')))
    settings = dict(enabled=True,dry_run=False,levels={str(action['level']):True},
                    types={f"{action['level']}:{profile.get('type','')}":True},
                    contacts={f"{action['level']}:{action['contact_key']}":True})
    state, reason, due = evaluate(settings,action,profile,now)
    from .facts import locale
    if due is not None and not locale(profile):
        reason, due = 'language_required', None
    if action['state'] == 'blocked':
        reason, due = action['reason'], None
    if due is not None:
        due = max(due, pace(db,action['contact_key'],profile,now))
        # A quota delay can itself cross into the recipient's night.
        from .policy import daytime
        due = daytime(due, profile)
    return dict(who=profile.get('name', '待核对联系人'), type=profile.get('type','unknown'),
                trigger=LABELS.get(event.get('kind'), '运营事件'),
                why=payload.get('why') or LABELS.get(event.get('kind'), '请核对触发来源'),
                sources=sources, would_send_at=due,
                timing_reason='contact_binding_required' if data.get('rehearsal_only') else reason,
                hypothetical=True)
