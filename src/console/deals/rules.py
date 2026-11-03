"""Payment-driven transitions, without storage or wall-clock dependencies."""
from console.archive.validation import BadRequest
from .stages import KEYS, LABELS, STATUS_LABELS, templates


def paid(stage):
    return KEYS.index(stage) >= KEYS.index('paid')


def transition(old, values, stamp, *, creating=False, payment_confirmed=False):
    result, events = dict(values), []
    stage = values.get('stage', old['stage'])
    status = values.get('status', old['status'])
    changed = creating or stage != old['stage']
    if paid(stage) and (creating or not paid(old['stage'])) and not (payment_confirmed and not creating):
        raise BadRequest('请先登记并人工确认客户付款，再进入已成交或后续阶段')
    payment = payment_confirmed and paid(stage) and not paid(old['stage'])
    rollback = changed and not creating and paid(old['stage']) and not paid(stage)
    if payment or rollback:
        forced = 'won' if payment else 'open'
        if 'status' in values and status != forced:
            raise BadRequest('阶段与状态冲突，请先按付款阶段调整状态')
        status = forced
        result.update(status=status, won_at=stamp if payment else None, closed_at=None)
        events.append(('system', '客户已付款，案子标记为已成交' if payment else '回退至付款前阶段，恢复进行中并清除成交时间'))
    if status == 'won' and not paid(stage):
        raise BadRequest('客户付款才算成交，请先进入已成交·备货或后续阶段')
    if status == 'open' and paid(stage):
        raise BadRequest('付款后阶段不能标记为进行中，请先回退阶段')
    if status == 'lost':
        reason = values.get('lost_reason') if 'status' in values else values.get('lost_reason', old.get('lost_reason'))
        if not reason or len(reason) > 200:
            raise BadRequest('丢单必须填写不超过二百字的原因')
        if old['status'] != 'lost':
            result['closed_at'] = stamp
    elif status != old['status']:
        result['closed_at'] = None
    if changed:
        result['stage_changed_at'] = stamp
        if not creating:
            events.append(('stage', f"阶段：{LABELS[old['stage']]} → {LABELS[stage]}"))
    if status != old['status']:
        events.append(('status', f"状态：{STATUS_LABELS[old['status']]} → {STATUS_LABELS[status]}"))
    return result, events


def pending_templates(stage, status, existing_keys):
    if status == 'lost' or (status == 'won' and not paid(stage)):
        return []
    return [t for t in templates(stage) if t['template_key'] not in existing_keys]
