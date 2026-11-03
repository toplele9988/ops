"""Suggestion persistence and explicit, transactional owner decisions only."""
import json
import re
import unicodedata

from console.archive.service import Archive, active_product, get
from console.archive.store import insert, update
from console.archive import validation as v
from console.roles.common import dumps, fingerprint, safe, seconds, stamp
from console.roles.inputs import tables
from console.roles.service import TransactionStore
from console.roles.storage import run_lock

SUGGESTION_FIELDS = (
    'kind', 'conversation_key', 'source_message_refs', 'party_id', 'party_hint',
    'product_text', 'matched_product_id', 'price_fen', 'currency', 'unit', 'moq_units',
    'lead_time_text', 'tax_text', 'route', 'mode', 'origin', 'destination_country',
    'destination_detail', 'min_charge_fen', 'validity_text', 'suggested_valid_until',
    'quoted_on', 'excerpt', 'confidence', 'source', 'input_fingerprint', 'has_attachment', 'run_id',
)
FLAGS = ('currency_guess', 'is_bundle')


def normalized_product(value):
    """Ignore typography and explicit quotation prefixes, not product words."""
    text = unicodedata.normalize('NFKC', str(value or '')).casefold().strip()
    text = re.sub(r'^(?:(?:按|按照|根据|复述|确认)\s*)?(?:你们?|贵司|对方|供应商|厂家)'
                  r'\s*(?:报的价?|报价|报|价格|单价|说的?|确认)[的：:\s]*', '', text)
    return ''.join(character for character in text if character.isalnum())


def semantic_key(item):
    # Product costs and freight rates are different record types even when a
    # poorly labelled historical row happens to share the four quote fields.
    return (item.get('kind'), item.get('conversation_key'), (item.get('currency') or '').upper(),
            item.get('price_fen'), normalized_product(item.get('product_text')))


def _refs(item):
    refs = []
    for ref in item.get('source_message_refs', ()):
        copy = dict(ref)
        copy.pop('extraction_metadata', None)
        refs.append(copy)
    return refs


def _first_at(item):
    timestamps = [seconds(ref.get('sent_at')) for ref in item.get('source_message_refs', ())]
    return min((at for at in timestamps if at is not None), default=float('inf'))


def _merge_refs(first, later):
    refs = _refs(first)
    seen = {(ref.get('conversation_key'), ref.get('id')) for ref in refs}
    for ref in _refs(later):
        key = (ref.get('conversation_key'), ref.get('id'))
        if key not in seen:
            refs.append(dict(ref, restated=True))
            seen.add(key)
    # Source chronology matters when a later scan includes an earlier message.
    refs.sort(key=lambda ref: seconds(ref.get('sent_at')) if seconds(ref.get('sent_at')) is not None else float('inf'))
    for index, ref in enumerate(refs):
        if index:
            ref['restated'] = True
        else:
            ref.pop('restated', None)
    return refs


def _merge(first, later, *, facts=True):
    result = dict(first, source_message_refs=_merge_refs(first, later))
    if not facts:
        return result
    if _first_at(later) < _first_at(first):
        # Backfilled older windows can precede the first scan. Retain the row
        # identity/decision but put the earliest statement's facts first.
        for key in SUGGESTION_FIELDS:
            if (key not in ('input_fingerprint', 'run_id', 'source_message_refs', 'party_id', 'party_hint')
                    and later.get(key) not in (None, '')):
                result[key] = later[key]
    # Preserve the original statement and complete missing facts from its
    # repetitions. Never overwrite an explicit earlier unit/date/route.
    for key in SUGGESTION_FIELDS:
        if (key not in ('input_fingerprint', 'run_id', 'source_message_refs') and result.get(key) in (None, '')
                and later.get(key) not in (None, '')):
            result[key] = later[key]
    result['has_attachment'] = bool(first.get('has_attachment') or later.get('has_attachment'))
    result['currency_guess'] = bool(first.get('currency_guess') and later.get('currency_guess'))
    result['is_bundle'] = bool(first.get('is_bundle') or later.get('is_bundle'))
    if first.get('source') == 'ai' or later.get('source') == 'ai':
        result['source'] = 'ai'
        if first.get('source') != later.get('source'):
            # A subsequent rules scan must not manufacture fields the AI left
            # unknown; upgrading an old rules row to AI has the same contract.
            model_item = first if first.get('source') == 'ai' else later
            for key in ('currency', 'unit'):
                result[key] = model_item.get(key)
            if not model_item.get('product_text'):
                result['product_text'] = result['matched_product_id'] = None
            result['currency_guess'] = bool(model_item.get('currency_guess'))
            if model_item.get('run_id') is not None:
                result['run_id'] = model_item['run_id']
    confidence = max(first.get('confidence', 0), later.get('confidence', 0))
    if result['is_bundle']:
        result['product_text'] = None
        confidence = min(confidence, .2)
    elif result.get('source') != 'ai' or not all(result.get(field) is not None and result.get(field) != ''
                                              for field in ('mode' if result.get('kind') == 'freight' else 'product_text',
                                                            'price_fen', 'currency', 'unit')):
        confidence = min(confidence, .5)
    result['confidence'] = confidence
    return result


def merge_items(items):
    """Collapse repetitions in a preview or a single scan, retaining all refs."""
    result, indices = [], {}
    for item in sorted(items, key=_first_at):
        key = semantic_key(item)
        if key in indices:
            index = indices[key]
            result[index] = _merge(result[index], item)
        else:
            indices[key] = len(result)
            result.append(dict(item))
    return result


def _encode_refs(item):
    refs = _refs(item)
    metadata = {flag: True for flag in FLAGS if item.get(flag)}
    if refs and metadata:
        # v7's reference JSON accepts additive fields. This preserves its
        # deployed schema and keeps the original message IDs unchanged.
        refs[0]['extraction_metadata'] = metadata
    return dumps(refs)


def suggestion_view(row):
    if row is None:
        return None
    result = dict(row)
    result['source_message_refs'] = json.loads(result['source_message_refs'])
    metadata = next((ref.get('extraction_metadata', {}) for ref in result['source_message_refs']
                     if ref.get('extraction_metadata')), {})
    for flag in FLAGS:
        result[flag] = bool(metadata.get(flag))
    result['has_attachment'] = bool(result['has_attachment'])
    result['is_cost'] = True
    return result


def save_suggestion(db, values, who, now):
    old = db.execute('SELECT * FROM extraction_suggestions WHERE input_fingerprint=?',
                     (values['input_fingerprint'],)).fetchone()
    if old is None:
        # Preserve decisions made before D1b even if AI labels or inferred
        # currencies subsequently change for the original message and amount.
        refs = values.get('source_message_refs') or []
        if refs:
            legacy = fingerprint(['extract-price-v1', values['conversation_key'], refs[0]['id'], values.get('price_fen')])
            old = db.execute("SELECT * FROM extraction_suggestions WHERE input_fingerprint=? AND status!='pending'",
                             (legacy,)).fetchone()
    if old is None:
        for row in db.execute('SELECT * FROM extraction_suggestions WHERE conversation_key=? AND price_fen IS ? '
                              'ORDER BY created_at,id', (values['conversation_key'], values.get('price_fen'))):
            if semantic_key(suggestion_view(row)) == semantic_key(values):
                old = row
                break
    if old is not None:
        previous = suggestion_view(old)
        merged = _merge(previous, values, facts=previous['status'] == 'pending' and semantic_key(previous) == semantic_key(values))
        changes = {key: merged[key] for key in SUGGESTION_FIELDS if key in merged and key != 'source_message_refs'
                   and merged[key] != previous.get(key)}
        refs = _encode_refs(merged)
        if refs != old['source_message_refs']:
            changes['source_message_refs'] = refs
        if changes:
            for key in ('party_hint', 'product_text', 'lead_time_text', 'tax_text', 'route', 'origin',
                        'destination_country', 'destination_detail', 'validity_text', 'excerpt'):
                if key in changes:
                    changes[key] = safe(changes[key], 200) or None
            update(db, 'extraction_suggestions', previous['id'], changes, who)
        return suggestion_view(get(db, 'extraction_suggestions', previous['id'])), False
    fields = {key: values[key] for key in SUGGESTION_FIELDS if key in values}
    fields['source_message_refs'] = _encode_refs(values)
    for key in ('party_hint', 'product_text', 'lead_time_text', 'tax_text', 'route', 'origin',
                'destination_country', 'destination_detail', 'validity_text', 'excerpt'):
        if key in fields:
            fields[key] = safe(fields[key], 200)
    fields.update(excerpt=fields.get('excerpt') or '', created_at=stamp(now), status='pending',
                  has_attachment=int(bool(fields.get('has_attachment', False))))
    row_id = insert(db, 'extraction_suggestions', fields, who)
    return suggestion_view(get(db, 'extraction_suggestions', row_id)), True


def list_suggestions(store, params=None):
    params = params or {}
    page, size = params.get('page', 1), params.get('page_size', 30)
    with store.read() as db:
        if 'extraction_suggestions' not in tables(db):
            return dict(items=[], total=0, page=page, page_size=size)
        where, args = [], []
        for key in ('status', 'kind', 'conversation_key'):
            value = params.get(key, 'pending' if key == 'status' else None)
            if value and value != 'all':
                where.append(key + '=?')
                args.append(value)
        if params.get('include_low') not in (True, 1, '1'):
            where.append("(status!='pending' OR confidence>0.2)")
        clause = ' WHERE ' + ' AND '.join(where) if where else ''
        total = db.execute('SELECT count(*) FROM extraction_suggestions' + clause, args).fetchone()[0]
        items = [suggestion_view(r) for r in db.execute('SELECT * FROM extraction_suggestions' + clause +
                 ' ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?', (*args, size, (page - 1) * size))]
        return dict(items=items, total=total, page=page, page_size=size)


def summary(store):
    pending = dict(offer=0, freight=0)
    quality = dict(trusted=0, low=0)
    with store.read() as db:
        available = tables(db)
        if 'extraction_suggestions' in available:
            pending.update({r[0]: r[1] for r in db.execute(
                "SELECT kind,count(*) FROM extraction_suggestions WHERE status='pending' GROUP BY kind")})
            row = db.execute("SELECT coalesce(sum(confidence>0.2),0),coalesce(sum(confidence<=0.2),0) "
                             "FROM extraction_suggestions WHERE status='pending'").fetchone()
            quality.update(trusted=row[0], low=row[1])
        latest = db.execute("SELECT max(created_at) FROM role_runs WHERE role='extract'").fetchone()[0] if 'role_runs' in available else None
    return dict(pending=pending, pending_quality=quality, total=sum(pending.values()), last_scan_at=latest)


def _source(row):
    refs = row['source_message_refs']
    source = ('由抽取员建议，老板确认；extract:' + str(row['id']) +
              '；conversation:' + row['conversation_key'] +
              '；messages:' + ','.join(str(ref['id']) for ref in refs))
    # Archive's established source_ref limit is 500. Long opaque keys and the
    # complete message references remain durable in this immutable suggestion.
    if len(source) > 500:
        source = '由抽取员建议，老板确认；extract:' + str(row['id']) + '；来源指纹:' + fingerprint(
            [row['conversation_key'], refs])
    return source


def _quote_fields(row, values):
    valid_until = values.get('valid_until')
    if not valid_until and values.get('confirm_valid_until'):
        valid_until = row['suggested_valid_until']
    if not valid_until:
        raise v.BadRequest('请填写有效期，或明确确认建议中的有效期')
    result = dict(valid_until=valid_until, quoted_on=values.get('quoted_on', row['quoted_on']),
                  price=values.get('price', v.fen_to_money(row['price_fen']) if row['price_fen'] is not None else None),
                  currency=values.get('currency', row['currency']), unit=values.get('unit', row['unit']),
                  source_ref=_source(row))
    if result['price'] is None:
        raise v.BadRequest('请填写报价金额')
    if not result['currency']:
        raise v.BadRequest('请填写报价币种')
    if not result['unit']:
        raise v.BadRequest('请填写计价单位')
    return result


def _accept_offer(db, archive, row, values, who, fields):
    freight_fields = {'forwarder_id', 'mode', 'origin', 'destination_country', 'destination_detail',
                      'service', 'min_charge', 'transit_days_min', 'transit_days_max', 'notes', 'channel'}
    if freight_fields & values.keys():
        raise v.BadRequest('产品报价不能填写货代运价字段')
    party_id = values.get('party_id', row['party_id'])
    party = get(db, 'parties', party_id) if party_id is not None else None
    if party and party['kind'] not in ('supplier', 'vendor'):
        raise v.BadRequest('产品报价须属于供应商或配套供应商')
    product_id = values.get('product_id', row['matched_product_id'])
    if values.get('create_product'):
        name = values.get('product_name') or row['product_text']
        if not name:
            raise v.BadRequest('请填写候选产品名称')
        product_fields = dict(name=name)
        if values.get('product_model'):
            product_fields['model'] = values['product_model']
        if party and party['kind'] == 'supplier':
            product_fields['supplier_id'] = str(party_id)
        product_id = archive.write('product_create', None, product_fields, who)['id']
    elif 'product_name' in values or 'product_model' in values:
        raise v.BadRequest('修改候选产品名称时须选择新建产品候选')
    if product_id is None:
        raise v.BadRequest('请选择档案产品，或先新建产品候选')
    product = active_product(db, product_id)
    if product['status'] == 'hidden':
        raise v.BadRequest('不能为隐藏产品确认报价')
    if party and party['kind'] == 'supplier' and product['supplier_id'] not in (None, party_id):
        raise v.Conflict('产品供应商与建议的往来单位不一致，请核对')
    if row['moq_units'] is not None:
        fields['moq_units'] = str(row['moq_units'])
    if row['tax_text']:
        fields['tax_basis'] = row['tax_text']
    # Free-form lead time cannot safely become numeric bounds automatically.
    if row['lead_time_text']:
        fields['price_basis'] = '交期原话：' + safe(row['lead_time_text'], 190)
    for key in ('moq_units', 'lead_days_min', 'lead_days_max', 'tax_basis', 'price_basis', 'includes_flightcase'):
        if key in values:
            fields[key] = values[key] or ''
    record_id = archive.write('offer_create', product_id, fields, who)['id']
    return record_id, product_id


def _accept_freight(db, archive, row, values, who, fields):
    offer_fields = {'product_id', 'create_product', 'product_name', 'product_model', 'moq_units',
                    'lead_days_min', 'lead_days_max', 'tax_basis', 'price_basis', 'includes_flightcase'}
    if offer_fields & values.keys():
        raise v.BadRequest('货代运价不能填写产品报价字段')
    party_id = values.get('forwarder_id', values.get('party_id', row['party_id']))
    if party_id is None:
        raise v.BadRequest('请选择档案货代')
    fields['forwarder_id'] = str(party_id)
    for key in ('mode', 'origin', 'destination_country', 'destination_detail'):
        value = values.get(key, row[key])
        if value is not None:
            fields[key] = value
    # A described route is evidence, not an invented service name.
    fields['service'] = values.get('service') or ''
    fields['channel'] = values.get('channel', 'forwarder')
    if row['min_charge_fen'] is not None:
        fields['min_charge'] = v.fen_to_money(row['min_charge_fen'])
    for key in ('min_charge', 'transit_days_min', 'transit_days_max', 'notes'):
        if key in values:
            fields[key] = values[key] or ''
    for key, label in (('mode', '运输方式'), ('origin', '始发地'), ('destination_country', '目的国'), ('service', '服务名称')):
        if not fields.get(key):
            raise v.BadRequest('请填写' + label)
    return archive.write('freight_create', None, fields, who)['id'], None


def decide(store, operation, row_id, values, who, now):
    if operation not in ('accept', 'dismiss'):
        raise v.BadRequest('抽取决定无效')
    with run_lock(store), store.write() as db:
        row = suggestion_view(get(db, 'extraction_suggestions', row_id))
        if operation == 'dismiss':
            if row['status'] == 'accepted':
                raise v.Conflict('该建议已经接受，不能再忽略')
            if row['status'] != 'dismissed':
                update(db, 'extraction_suggestions', row_id,
                       dict(status='dismissed', decided_at=stamp(now), decided_by=who), who, 'dismiss')
            return dict(ok=True, id=row_id)
        if row['status'] == 'dismissed':
            raise v.Conflict('该建议已经忽略，不能确认')
        if row['status'] == 'accepted':
            record_id = row['accepted_record_id']
            product_id = get(db, 'offers', record_id)['product_id'] if row['kind'] == 'offer' else None
            reused = True
        else:
            # The identity is authoritative at acceptance time. An old suggestion
            # cannot silently attach a price to a former owner after reassignment.
            identity = db.execute('SELECT i.party_id,p.kind FROM identities i JOIN parties p ON p.id=i.party_id '
                                  'WHERE i.conversation_key=?', (row['conversation_key'],)).fetchone()
            selected_party = values.get('forwarder_id', values.get('party_id', row['party_id']))
            if identity is not None and (identity['party_id'] != selected_party or
                                          identity['kind'] not in ('supplier', 'vendor', 'forwarder')):
                raise v.Conflict('会话归属已变化，请核对当前往来单位并明确选择后再确认')
            fields = _quote_fields(row, values)
            archive = Archive(store=TransactionStore(store, db))
            accept = _accept_offer if row['kind'] == 'offer' else _accept_freight
            record_id, product_id = accept(db, archive, row, values, who, fields)
            update(db, 'extraction_suggestions', row_id,
                   dict(status='accepted', decided_at=stamp(now), decided_by=who, accepted_record_id=record_id), who, 'accept')
            reused = False
        return dict(ok=True, id=row_id, kind=row['kind'], record_id=record_id, product_id=product_id,
                    **{('offer_id' if row['kind'] == 'offer' else 'freight_quote_id'): record_id}, reused=reused)
