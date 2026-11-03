"""Recording a node never changes a deal stage or sends shipping information."""
from datetime import date

from console.archive import validation as v
from console.archive.store import insert, update
from console.deals.rules import paid
from console.deals.stages import KEYS
from .common import parse, party, record, tables

NODES = dict(po='下单给供应商', supplier_confirmation='供应商确认交期', production='生产中',
             inspection='验货', booking='出货订舱', transit='物流在途', customs='到港/清关', signoff='客户签收')
DEFAULT_OWNER = dict(po='us', supplier_confirmation='supplier', production='supplier', inspection='vendor',
                     booking='forwarder', transit='forwarder', customs='forwarder', signoff='us')
COMMON = ('status', 'planned_on', 'completed_on', 'responsible', 'party_id', 'note')
SPECIAL = dict(po=(), supplier_confirmation=('promised_on',), production=(),
               inspection=('inspection_result', 'inspector'),
               booking=('carrier', 'tracking_number', 'etd', 'eta', 'departed_on'),
               transit=(), customs=(), signoff=('signed_on',))
DATES = ('planned_on', 'completed_on', 'promised_on', 'etd', 'eta', 'departed_on', 'signed_on')
FIELDS = COMMON + tuple(k for fields in SPECIAL.values() for k in fields)
STATUS_LABELS = dict(todo='待办', doing='进行中', done='完成', blocked='受阻')


def eligible(deal):
    if not paid(deal['stage']):
        raise v.BadRequest('请先人工确认收款进入已成交阶段，再记录履约节点')


def write(db, deal_id, node, raw, who, stamp, deals, today):
    deal = record(db, 'deals', deal_id)
    eligible(deal)
    if node not in NODES:
        raise v.Missing('未找到该履约节点')
    values = parse(raw, COMMON + SPECIAL[node], choices={
        'status': tuple(STATUS_LABELS), 'responsible': ('us', 'supplier', 'forwarder', 'vendor'),
        'inspection_result': ('pass', 'fail', 'needs_fix', '')}, dates=DATES, integers=('party_id',))
    if not values:
        raise v.BadRequest('请填写要更新的履约信息')
    if values.get('inspection_result') == '':
        values['inspection_result'] = None
    old = db.execute('SELECT * FROM fulfilment_nodes WHERE deal_id=? AND node=?', (deal_id, node)).fetchone()
    before = dict(old) if old else dict(deal_id=deal_id, node=node, status='todo', responsible=DEFAULT_OWNER[node])
    merged = dict(before, **values)
    party(db, merged)
    if any(merged.get(key) and merged[key] > today for key in ('completed_on', 'departed_on', 'signed_on')):
        raise v.BadRequest('实际完成、离港或签收日期不能晚于今天')
    if merged['status'] == 'done':
        if node == 'inspection' and not merged.get('inspection_result'):
            raise v.BadRequest('完成验货前请填写验货结果')
        if node == 'supplier_confirmation' and not merged.get('promised_on'):
            raise v.BadRequest('请填写供应商承诺交期')
        if not merged.get('completed_on'):
            values['completed_on'] = today
        if node == 'signoff' and not merged.get('signed_on'):
            values['signed_on'] = today
    elif 'status' in values and before['status'] == 'done':
        values.setdefault('completed_on', None)
        if node == 'signoff':
            values.setdefault('signed_on', None)
    if node == 'signoff' and values.get('signed_on') and merged['status'] != 'done':
        raise v.BadRequest('填写实际签收日期时请将签收节点设为完成')
    if old:
        changes = {k: value for k, value in values.items() if value != old[k]}
        if not changes:
            return dict(ok=True, id=old['id'])
        row_id = old['id']
        update(db, 'fulfilment_nodes', row_id, dict(changes, updated_at=stamp), who)
    else:
        row_id = insert(db, 'fulfilment_nodes', dict(before, **values, created_at=stamp, updated_at=stamp), who)
    deals._event(db, deal_id, 'system', '履约：' + NODES[node] + '，' + STATUS_LABELS[merged['status']], who, stamp)
    return dict(ok=True, id=row_id)


def progress(db, deal, today):
    saved = {r['node']: dict(r) for r in db.execute('SELECT * FROM fulfilment_nodes WHERE deal_id=?', (deal['id'],))} \
        if 'fulfilment_nodes' in tables(db) else {}
    items = []
    for node, label in NODES.items():
        row = saved.get(node, dict(id=None, deal_id=deal['id'], node=node, status='todo',
                                  responsible=DEFAULT_OWNER[node], **{key: None for key in FIELDS if key not in ('status', 'responsible')}))
        due = row.get({'supplier_confirmation': 'promised_on', 'booking': 'etd', 'signoff': 'signed_on'}.get(node, 'planned_on')) or row.get('planned_on')
        days = (date.fromisoformat(due) - date.fromisoformat(today)).days if due else None
        items.append(dict(row, label=label, due_on=due, days_remaining=days,
                          overdue=bool(row['status'] != 'done' and days is not None and days < 0)))
    suggestions = []
    by_node = {n['node']: n for n in items}
    for node, stage, reason in (('production', 'inspection', '生产已完成，可安排验货'),
                                ('inspection', 'shipped', '验货已通过，请核对实际交货；验货通过本身不代表已发货'),
                                ('transit', 'shipped', '已记录物流在途，请核对发货阶段'),
                                ('signoff', 'delivered', '已记录签收，请核对签收阶段')):
        n = by_node[node]
        ready = n['status'] == 'done' or (node == 'transit' and n['status'] == 'doing')
        if node == 'inspection':
            ready = ready and n.get('inspection_result') == 'pass'
        if ready and KEYS.index(stage) > KEYS.index(deal['stage']):
            suggestions.append(dict(stage=stage, reason=reason + '，由人确认后修改案子阶段'))
    due = [n['due_on'] for n in items if n['status'] != 'done' and n['due_on']]
    # Arrival remains actionable after booking itself is done.
    eta = by_node['booking'].get('eta')
    signed = by_node['signoff']['status'] == 'done' or KEYS.index(deal['stage']) >= KEYS.index('delivered')
    if eta and not signed:
        due.append(eta)
    promise = by_node['supplier_confirmation'].get('promised_on')
    shipped = signed or KEYS.index(deal['stage']) >= KEYS.index('shipped') or by_node['booking'].get('departed_on') \
        or by_node['transit']['status'] in ('doing', 'done')
    if promise and not shipped:
        due.append(promise)
    next_due = min(due) if due else None
    return dict(deal_id=deal['id'], title=deal['title'], stage=deal['stage'], eligible=paid(deal['stage']),
                items=items, done=sum(n['status'] == 'done' for n in items), total=len(NODES),
                next_due=next_due, overdue=bool(next_due and next_due < today), suggestions=suggestions)


def board(db, today):
    deals = db.execute('SELECT * FROM deals') if 'deals' in tables(db) else ()
    items = [progress(db, dict(d), today) for d in deals if paid(d['stage'])]
    items.sort(key=lambda d: (not d['overdue'], d['next_due'] or '9999-12-31', d['deal_id']))
    return dict(items=items, total=len(items), generated_on=today)
