"""Local-only validation and projections shared by fulfilment services."""
from datetime import date

from console.archive import validation as v
from console.archive.service import get
from console.deals.validation import currency, identity


def tables(db):
    return {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def record(db, table, row_id):
    if table not in tables(db):
        raise v.Missing('未找到该记录')
    return get(db, table, row_id)


def party(db, values):
    if values.get('party_id') is not None:
        target = record(db, 'parties', values['party_id'])
        if values['responsible'] not in v.PARTY_KINDS or target['kind'] != values['responsible']:
            raise v.BadRequest('负责方与往来单位类型不一致')


def parse(raw, allowed, *, choices=None, dates=(), integers=(), amounts=(), required=()):
    v.whitelist(raw, allowed)
    result = {}
    choices = choices or {}
    for key, value in raw.items():
        if key in choices:
            result[key] = v.choice(value, choices[key])
        elif key in dates:
            result[key] = v.iso_date(value) if value else None
        elif key in integers:
            result[key] = identity(value) if value else None
        elif key in amounts:
            result[key] = v.integer(value, maximum=v.MAX_FEN) if value else None
        elif key == 'currency':
            result[key] = currency(value) if value else None
        else:
            result[key] = v.text(value, 2000 if key in ('description', 'note', 'resolution') else 200,
                                 key in required)
    if any(not result.get(key) for key in required):
        raise v.BadRequest('请填写必填字段')
    return result


def iso_day(value):
    if isinstance(value, date):
        return value.isoformat()
    return v.iso_date(value)
