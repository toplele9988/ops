"""Read-only reorder opportunities and explicitly requested inquiry drafts."""
from datetime import date, datetime
from zoneinfo import ZoneInfo

from console.archive import validation as v
from console.deals.stages import KEYS
from console.deals.waiting import seconds
from .common import record, tables
from .issues import KINDS


def signed_date(db, deal):
    if 'fulfilment_nodes' in tables(db):
        row = db.execute("SELECT signed_on,completed_on FROM fulfilment_nodes WHERE deal_id=? "
                         "AND node='signoff' AND status='done'", (deal['id'],)).fetchone()
        if row and (row['signed_on'] or row['completed_on']):
            return row['signed_on'] or row['completed_on']
    if KEYS.index(deal['stage']) < KEYS.index('delivered'):
        return None
    row = db.execute("SELECT at FROM deal_events WHERE deal_id=? AND kind='stage' "
                     "AND summary LIKE '%→ 已签收' ORDER BY at DESC,id DESC LIMIT 1", (deal['id'],)).fetchone()
    stamp = row['at'] if row else deal['stage_changed_at'] if deal['stage'] == 'delivered' else None
    at = seconds(stamp)
    return datetime.fromtimestamp(at, ZoneInfo('Asia/Shanghai')).date().isoformat() if at is not None else None


def latest_quote(db, deal_id):
    if 'quote_rounds' not in tables(db):
        return None
    return db.execute("SELECT id,currency FROM quote_rounds WHERE deal_id=? AND status='accepted' "
                      'ORDER BY round_no DESC,id DESC LIMIT 1', (deal_id,)).fetchone()


def products(db, deal_id):
    quote = latest_quote(db, deal_id)
    if quote:
        rows = db.execute('SELECT product_id,name,qty,sell_unit_minor FROM quote_lines WHERE round_id=? ORDER BY id', (quote['id'],))
        return [dict(r, currency=quote['currency']) for r in rows]
    return [dict(product_id=r['product_id'], name=r['name'], qty=r['qty'], sell_unit_minor=None, currency=None)
            for r in db.execute('SELECT product_id,name,qty FROM deal_items WHERE deal_id=? ORDER BY id', (deal_id,))]


def preview(db, deal_id, items):
    suppliers = {r['id']: dict(r, source_label='本案供应商参与方，需核对实际供货方')
                 for r in db.execute("SELECT p.id,p.name FROM parties p JOIN deal_participants d ON d.party_id=p.id "
                                     "WHERE d.deal_id=? AND d.role='supplier' AND p.kind='supplier' ORDER BY p.id", (deal_id,))}
    # Product suppliers are current archive links, not historical fulfilment snapshots.
    for item in items:
        if item['product_id'] is None:
            continue
        row = db.execute("SELECT p.id,p.name FROM parties p JOIN products product ON product.supplier_id=p.id "
                         "WHERE product.id=? AND p.kind='supplier'", (item['product_id'],)).fetchone()
        if row and row['id'] not in suppliers:
            suppliers[row['id']] = dict(row, source_label='产品档案当前供应商，需核对上次实际供货方')
    feedback = [dict(r, kind_label=KINDS[r['kind']]) for r in db.execute(
        'SELECT id,kind,status,description,resolution,product_id,party_id FROM deal_issues WHERE deal_id=? ORDER BY id',
        (deal_id,))] if 'deal_issues' in tables(db) else []
    return dict(suppliers=[suppliers[key] for key in sorted(suppliers)], feedback=feedback)


def thresholds(value):
    if isinstance(value, str):
        value = value.split(',')
    if not isinstance(value, (tuple, list)) or not 1 <= len(value) <= 12:
        raise v.BadRequest('返单天数须为一至十二个有效整数')
    parsed = []
    for item in value:
        if type(item) is int:
            item = str(item)
        parsed.append(v.integer(item, maximum=3650))
    return sorted(set(parsed))


def leads(db, params, today):
    v.whitelist(params, ('days',))
    days = thresholds(params.get('days', (30, 60, 90)))
    items = []
    if 'deals' not in tables(db):
        return dict(items=items, days=days, generated_on=today)
    # A newer inquiry, including a previous reorder draft, suppresses the lead.
    # Comparing id as a tie breaker also handles a fake/frozen clock correctly.
    rows = db.execute('SELECT d.* FROM deals d WHERE customer_party_id IS NOT NULL AND NOT EXISTS '
                      '(SELECT 1 FROM deals newer WHERE newer.customer_party_id=d.customer_party_id AND '
                      '(newer.created_at>d.created_at OR (newer.created_at=d.created_at AND newer.id>d.id))) ORDER BY d.id')
    for row in rows:
        deal = dict(row)
        signed = signed_date(db, deal)
        if not signed:
            continue
        age = (date.fromisoformat(today) - date.fromisoformat(signed)).days
        reached = [n for n in days if age >= n]
        if not reached:
            continue
        customer = record(db, 'parties', deal['customer_party_id'])
        items_preview = products(db, deal['id'])
        items.append(dict(deal_id=deal['id'], customer=dict(id=customer['id'], name=customer['name']),
                          title=deal['title'], signed_on=signed, days_since_signed=age, reached_days=reached,
                          products=items_preview, **preview(db, deal['id'], items_preview),
                          suggestion=f'签收已 {age} 天，可先了解使用反馈和库存，再人工判断是否准备返单报价；历史销售价需重新核对。'))
    items.sort(key=lambda d: (-d['days_since_signed'], d['deal_id']))
    return dict(items=items, days=days, generated_on=today)


def copy(db, deal_id, raw, who, stamp, deals):
    v.whitelist(raw, ())
    old = record(db, 'deals', deal_id)
    if not signed_date(db, old):
        raise v.BadRequest('已签收的案子才能复制为返单草稿')
    if old['customer_party_id'] is None:
        raise v.BadRequest('请先为原案子关联客户')
    result = deals._write(db, 'create', None, dict(title=(old['title'][:495] + ' · 返单'),
                          customer_party_id=old['customer_party_id'], stage='inquiry'), who, stamp, None)
    new_id = result['id']
    rows = list(db.execute('SELECT product_id,name,qty FROM deal_items WHERE deal_id=? ORDER BY id', (deal_id,)))
    items = [dict(r) for r in rows] if rows else products(db, deal_id)
    for item in items:
        values = {k: item[k] for k in ('product_id', 'name', 'qty')}
        deals._write(db, 'item', new_id, values, who, stamp, None)
    deals._event(db, new_id, 'system', f'从案子 #{deal_id} 复制为返单草稿，价格待重新核对', who, stamp)
    deals._event(db, deal_id, 'system', f'人工创建返单草稿 #{new_id}', who, stamp)
    return dict(result, source_deal_id=deal_id)
