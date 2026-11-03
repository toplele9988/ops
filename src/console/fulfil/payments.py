"""Human-confirmed payments; integer amounts and existing deal transactions only.

Amounts are positive magnitudes at rest. A confirmed refund contributes a
negative signed amount, without undoing a deal's manually controlled stage.
"""
from collections import defaultdict

from console.archive import validation as v
from console.archive.service import get
from console.archive.store import insert, update
from console.deals.rules import paid
from console.deals.validation import currency, identity


FIELDS = {
    'payment_create': ('kind', 'amount_fen', 'currency', 'received_on', 'method',
                       'quote_round_id', 'note', 'evidence', 'currency_note'),
    'payment_confirm': ('confirm', 'qualifies', 'currency_note'),
    'payment_void': ('note',),
}


def fields(operation, raw):
    v.whitelist(raw, FIELDS[operation])
    result = {}
    for key, value in raw.items():
        if key == 'amount_fen':
            result[key] = v.integer(value, minimum=1, maximum=v.MAX_FEN)
        elif key == 'currency':
            result[key] = currency(value)
        elif key == 'received_on':
            result[key] = v.iso_date(value)
        elif key == 'quote_round_id':
            result[key] = identity(value) if value else None
        elif key in ('confirm', 'qualifies'):
            result[key] = int(v.choice(value, ('0', '1')))
        elif key == 'kind':
            result[key] = v.choice(value, ('deposit', 'balance', 'full', 'refund'))
        elif key == 'method':
            result[key] = v.choice(value, ('wire', 'paypal', 'lc', 'other'))
        else:
            result[key] = v.text(value, 2000 if key == 'note' else 1000)
    if operation == 'payment_create' and any(result.get(k) is None for k in
            ('kind', 'amount_fen', 'currency', 'received_on', 'method')):
        raise v.BadRequest('请填写收款类型、金额、币种、收到日期和收款方式')
    if operation == 'payment_confirm' and result.get('confirm') != 1:
        raise v.BadRequest('确认收款须由人点击确认')
    return result


def latest_quote(db, deal_id):
    exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='quote_rounds'").fetchone()
    return db.execute("SELECT * FROM quote_rounds WHERE deal_id=? AND status='accepted' "
                      "ORDER BY round_no DESC,id DESC LIMIT 1", (deal_id,)).fetchone() if exists else None


def _quote(db, deal_id, quote_round_id):
    if quote_round_id is None:
        return None
    quote = get(db, 'quote_rounds', quote_round_id)
    if quote['deal_id'] != deal_id:
        raise v.Missing('未找到该案子的报价轮')
    return quote


def create(db, deal_id, raw, who, stamp, deals):
    values = fields('payment_create', raw)
    get(db, 'deals', deal_id)
    if 'quote_round_id' not in values:
        quote = latest_quote(db, deal_id)
        values['quote_round_id'] = quote['id'] if quote else None
    _quote(db, deal_id, values['quote_round_id'])
    row_id = insert(db, 'deal_payments', dict(values, deal_id=deal_id, status='pending',
                    created_at=stamp, created_by=who, updated_at=stamp), who)
    deals._event(db, deal_id, 'system', '登记待核对收款' if values['kind'] != 'refund' else '登记待核对退款', who, stamp)
    return dict(ok=True, id=row_id)


def confirm(db, row_id, raw, who, stamp, deals):
    values = fields('payment_confirm', raw)
    payment = get(db, 'deal_payments', row_id)
    if payment['status'] == 'void':
        raise v.Conflict('已作废收款不能确认，请重新登记')
    if payment['status'] == 'confirmed':
        return dict(ok=True, id=row_id)
    if type(payment['amount_fen']) is not int or not 0 < payment['amount_fen'] <= v.MAX_FEN:
        raise v.BadRequest('确认收款金额须大于零')
    qualifies = values.get('qualifies', 0)
    if payment['kind'] == 'refund' and qualifies:
        raise v.BadRequest('退款不能作为成交条件')
    quote = _quote(db, payment['deal_id'], payment['quote_round_id'])
    explanation = values.get('currency_note', payment['currency_note'])
    if quote and payment['currency'] != quote['currency'] and not explanation:
        raise v.BadRequest('收款币种与报价轮不同，请明确填写币种差异说明')
    earlier = db.execute("SELECT 1 FROM deal_payments WHERE deal_id=? AND status='confirmed' "
                         "AND (kind IN ('deposit','full') OR qualifies=1) LIMIT 1", (payment['deal_id'],)).fetchone()
    update(db, 'deal_payments', row_id, dict(status='confirmed', confirmed_at=stamp,
           confirmed_by=who, updated_at=stamp, qualifies=qualifies, currency_note=explanation), who, action='confirm')
    deals._event(db, payment['deal_id'], 'system',
                 '确认退款' if payment['kind'] == 'refund' else '收到客户付款', who, stamp)
    deal = get(db, 'deals', payment['deal_id'])
    first_payment = payment['kind'] in ('deposit', 'full') and not earlier
    if not paid(deal['stage']) and (first_payment or qualifies):
        deals._write(db, 'update', payment['deal_id'], {'stage': 'paid'}, who, stamp, None, payment_confirmed=True)
    return dict(ok=True, id=row_id)


def void(db, row_id, raw, who, stamp, deals):
    values = fields('payment_void', raw)
    payment = get(db, 'deal_payments', row_id)
    if payment['status'] == 'void':
        return dict(ok=True, id=row_id)
    update(db, 'deal_payments', row_id, dict(status='void', voided_at=stamp, voided_by=who,
           void_note=values.get('note'), updated_at=stamp), who, action='void')
    deals._event(db, payment['deal_id'], 'system', '作废收款记录', who, stamp)
    return dict(ok=True, id=row_id)


def list_payments(db, deal_id):
    get(db, 'deals', deal_id)
    exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='deal_payments'").fetchone()
    items = [dict(r) for r in db.execute('SELECT * FROM deal_payments WHERE deal_id=? ORDER BY id',
                                       (deal_id,))] if exists else []
    received = defaultdict(int)
    for row in items:
        row['signed_amount_fen'] = -row['amount_fen'] if row['kind'] == 'refund' else row['amount_fen']
        received.setdefault(row['currency'], 0)
        if row['status'] == 'confirmed':
            received[row['currency']] += row['signed_amount_fen']
    quote = latest_quote(db, deal_id)
    receivable = None
    if quote:
        lines = list(db.execute('SELECT qty,sell_unit_minor,qty_confirmed FROM quote_lines WHERE round_id=?', (quote['id'],)))
        if lines and all(l['sell_unit_minor'] is not None and l['qty_confirmed'] for l in lines):
            receivable = sum(l['sell_unit_minor'] * l['qty'] for l in lines)
        received.setdefault(quote['currency'], 0)
    summary = []
    for denomination, amount in sorted(received.items()):
        due = receivable if quote and quote['currency'] == denomination else None
        summary.append(dict(currency=denomination, received_fen=amount, receivable_fen=due,
                       unreceived_fen=due - amount if due is not None else None,
                       quote_round_id=quote['id'] if quote and quote['currency'] == denomination else None))
    return dict(items=items, summary=summary)
