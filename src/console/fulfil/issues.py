"""Append-only handling history and conservative, copy-only external drafts."""
from collections import defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo

from console.archive import validation as v
from console.archive.store import insert, update
from .common import parse, party, record, tables

KINDS = dict(quality='质量', shortage='少发错发', damage='破损', delay='延误', documents='资料不符',
             feedback='客户意见建议', other='其他')
RESPONSIBLE = ('supplier', 'forwarder', 'vendor', 'us', 'customer', 'unknown')
STATUSES = ('open', 'waiting_party', 'resolved', 'closed')
CREATE = ('kind', 'quote_line_id', 'product_id', 'responsible', 'party_id', 'severity', 'description',
          'impact_kind', 'impact_fen', 'currency')
UPDATE = CREATE + ('status', 'resolution')


def fields(raw, create=False):
    return parse(raw, CREATE if create else UPDATE,
                 choices=dict(kind=tuple(KINDS), responsible=RESPONSIBLE, severity=('low', 'medium', 'high', 'critical'),
                              status=STATUSES, impact_kind=('compensation', 'discount', 'refund', '')),
                 integers=('quote_line_id', 'product_id', 'party_id'), amounts=('impact_fen',),
                 required=('kind', 'description') if create else ())


def validate(db, merged):
    if not merged.get('description'):
        raise v.BadRequest('请填写问题描述')
    party(db, merged)
    if merged.get('quote_line_id'):
        line = record(db, 'quote_lines', merged['quote_line_id'])
        quote = record(db, 'quote_rounds', line['round_id'])
        if quote['deal_id'] != merged['deal_id']:
            raise v.BadRequest('报价行不属于该案子')
        if merged.get('product_id') and line['product_id'] != merged['product_id']:
            raise v.BadRequest('产品与关联报价行不一致')
        merged['product_id'] = line['product_id']
    if merged.get('product_id'):
        record(db, 'products', merged['product_id'])
        linked = db.execute('SELECT 1 FROM deal_items WHERE deal_id=? AND product_id=?',
                            (merged['deal_id'], merged['product_id'])).fetchone()
        quoted = db.execute('SELECT 1 FROM quote_lines l JOIN quote_rounds q ON q.id=l.round_id '
                            'WHERE q.deal_id=? AND l.product_id=?', (merged['deal_id'], merged['product_id'])).fetchone()
        if not linked and not quoted:
            raise v.BadRequest('产品不属于该案子，请先关联产品或报价行')
    impact = (merged.get('impact_kind'), merged.get('impact_fen'), merged.get('currency'))
    if any(x is not None for x in impact) and not all(x is not None for x in impact):
        raise v.BadRequest('金额影响类型、金额与币种须一起填写或一起清空')
    if merged['status'] in ('resolved', 'closed') and not merged.get('resolution'):
        raise v.BadRequest('解决或关闭问题前请填写解决结论')


def create(db, deal_id, raw, who, stamp, deals):
    record(db, 'deals', deal_id)
    values = dict(deal_id=deal_id, status='open', responsible='unknown', severity='medium') | fields(raw, True)
    if values.get('impact_kind') == '':
        values['impact_kind'] = None
    validate(db, values)
    row_id = insert(db, 'deal_issues', dict(values, created_at=stamp, created_by=who, updated_at=stamp), who)
    deals._event(db, deal_id, 'system', '记录问题与反馈：' + KINDS[values['kind']], who, stamp)
    return dict(ok=True, id=row_id)


def change(db, issue_id, raw, who, stamp, deals):
    old = record(db, 'deal_issues', issue_id)
    values = fields(raw)
    if not values:
        raise v.BadRequest('请填写要更新的问题信息')
    if values.get('impact_kind') == '':
        values['impact_kind'] = None
    merged = dict(old, **values)
    validate(db, merged)
    values['product_id'] = merged.get('product_id')
    status = merged['status']
    if status != old['status']:
        if status == 'closed' and old['status'] != 'resolved':
            raise v.BadRequest('请先记录解决结论并解决问题，再关闭')
        values.update(resolved_at=(old['resolved_at'] or stamp) if status in ('resolved', 'closed') else None,
                      closed_at=stamp if status == 'closed' else None)
    changes = {k: value for k, value in values.items() if value != old[k]}
    if changes:
        update(db, 'deal_issues', issue_id, dict(changes, updated_at=stamp), who)
        labels = dict(open='打开', waiting_party='等待责任方', resolved='已解决', closed='已关闭')
        note = '更新问题资料；状态：' + labels[status]
        if 'resolution' in changes and merged.get('resolution'):
            note += '；解决结论：' + merged['resolution']
        insert(db, 'deal_issue_notes', dict(issue_id=issue_id, note=note, at=stamp, who=who), who)
        deals._event(db, old['deal_id'], 'system', '更新问题与反馈：' + labels[status], who, stamp)
    return dict(ok=True, id=issue_id)


def note(db, issue_id, raw, who, stamp, deals):
    issue = record(db, 'deal_issues', issue_id)
    values = parse(raw, ('note',), required=('note',))
    row_id = insert(db, 'deal_issue_notes', dict(issue_id=issue_id, at=stamp, who=who, **values), who)
    update(db, 'deal_issues', issue_id, dict(updated_at=stamp), who)
    deals._event(db, issue['deal_id'], 'system', '追加问题处理记录', who, stamp)
    return dict(ok=True, id=row_id, issue_id=issue_id)


def listing(db, deal_id):
    record(db, 'deals', deal_id)
    rows = db.execute('SELECT * FROM deal_issues WHERE deal_id=? ORDER BY id', (deal_id,)) if 'deal_issues' in tables(db) else ()
    return dict(items=[dict(r, notes=[dict(n) for n in db.execute(
        'SELECT * FROM deal_issue_notes WHERE issue_id=? ORDER BY at,id', (r['id'],))]) for r in rows])


def summary(db, params):
    v.whitelist(params, ('start', 'end'))
    start = v.iso_date(params['start']) if params.get('start') else None
    end = v.iso_date(params['end']) if params.get('end') else None
    if start and end and start > end:
        raise v.BadRequest('开始日期不能晚于结束日期')
    groups = {key: defaultdict(lambda: dict(count=0, unresolved=0)) for key in ('products', 'parties')}
    rows = db.execute('SELECT * FROM deal_issues ORDER BY id') if 'deal_issues' in tables(db) else ()
    count = unresolved = 0
    for r in rows:
        day = datetime.fromisoformat(r['created_at']).astimezone(ZoneInfo('Asia/Shanghai')).date().isoformat()
        if (start and day < start) or (end and day > end):
            continue
        count += 1
        active = r['status'] in ('open', 'waiting_party')
        unresolved += active
        groups['products'][r['product_id']]['count'] += 1
        groups['products'][r['product_id']]['unresolved'] += active
        if r['responsible'] in ('supplier', 'forwarder'):
            key = (r['responsible'], r['party_id'])
            groups['parties'][key]['count'] += 1
            groups['parties'][key]['unresolved'] += active
    products = [dict(product_id=key, **value) for key, value in groups['products'].items()]
    parties = [dict(responsible=key[0], party_id=key[1], **value) for key, value in groups['parties'].items()]
    return dict(total=count, unresolved=unresolved, products=products, parties=parties, start=start, end=end)


def draft(db, issue_id, params):
    v.whitelist(params, ('target',))
    issue = record(db, 'deal_issues', issue_id)
    target = params.get('target') or (issue['responsible'] if issue['responsible'] in ('supplier', 'forwarder') else 'supplier')
    v.choice(target, ('supplier', 'forwarder'))
    # Free-form names/descriptions can contain arbitrary internal data. A
    # keyword blacklist cannot prove their safety; never interpolate them.
    # Only fixed enum labels cross this projection. Human operators fill the
    # outward-facing facts in their copied draft after checking the record.
    lines = ['请协助核实以下问题与反馈：', '问题类型：' + KINDS[issue['kind']]]
    labels = dict(low='一般', medium='需要跟进', high='优先处理', critical='紧急')
    lines += ['处理优先级：' + labels[issue['severity']],
              '涉及产品：【请补充对方可识别的产品型号及批次】',
              '情况说明：【请核对内部问题记录，补充可对外提供的现象、数量和发生日期】',
              '请核实原因、处理方案及预计完成时间。责任尚待共同核实，请回复可确认的事实。']
    return dict(issue_id=issue_id, target=target, text='\n'.join(lines), source='rules', copy_only=True,
                needs_manual_details=True)
