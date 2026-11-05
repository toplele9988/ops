"""Append-only dossier projection; gap state is a reversible read overlay."""
from decimal import Decimal
from datetime import datetime
import json
import re
import unicodedata

from .model import validated
from .schema import dumps
from .sources import LOCAL_TZ, identity

KEYS = {'sd_source': ['source_id'], 'sd_supplier': ['supplier_id'], 'sd_product': ['product_id'],
        'sd_supplier_product': ['supplier_id', 'product_id'], 'sd_offer': ['offer_id'], 'sd_evidence': ['evidence_id']}


def append(db, table, row, job, now, changes):
    keys = KEYS[table]
    key = [row[k] for k in keys]
    if db.execute('SELECT 1 FROM ' + table + ' WHERE ' + ' AND '.join(k + '=?' for k in keys), key).fetchone():
        return False
    db.execute('INSERT INTO ' + table + '(' + ','.join(row) + ') VALUES(' + ','.join('?' for _ in row) + ')', tuple(row.values()))
    db.execute('INSERT INTO sd_projection VALUES(?,?,?,?)', (table, dumps(key), job, now))
    changes['added'].append(dict(table=table, key=key))
    return True


def norm(value):
    return re.sub(r'[\s_\-]+', '', unicodedata.normalize('NFKC', str(value))).casefold()


def resolve(db, sid, fields):
    model = fields.get('model', {}).get('value')
    config = {k[7:]: f['value'] for k, f in fields.items() if k.startswith('config.')}
    if not model:
        return None, None, False
    matches = []
    for row in db.execute('SELECT p.*,sp.model_aliases_json FROM sd_supplier_product sp JOIN sd_product p USING(product_id) WHERE sp.supplier_id=?', (sid,)):
        aliases = json.loads(row['model_aliases_json'])
        aliases = aliases if isinstance(aliases, list) else []
        if norm(model) not in {norm(a) for a in [row['canonical_model']] + aliases if isinstance(a, str)}:
            continue
        old = json.loads(row['config_json'])
        if all(k in old and norm(old[k]) == norm(v) for k, v in config.items()):
            matches.append(row['product_id'])
    if len(matches) == 1:
        return matches[0], None, False
    family = 'incremental-candidate:' + sid + ':' + norm(model)
    signature = identity('IC', {k: norm(v) for k, v in config.items()})
    pid = identity('IPR', [family, signature])
    return pid, dict(product_id=pid, product_family=family, category='待核', canonical_model=model,
        display_name=model, variant_label='待核配置', config_json=dumps(config), config_signature=signature,
        evidence_state='待核：增量产品族候选', field_sources_json=dumps(fields)), len(matches) > 1


def review(db, kind, job, sid, pid, oid, details, now, changes):
    rid = identity('IR', [kind, job, sid, pid, oid])
    if db.execute('INSERT OR IGNORE INTO sd_review VALUES(?,?,?,?,?,?,?,?,?)',
                  (rid, job, kind, sid, pid, oid, dumps(details), 'open', now)).rowcount:
        changes['reviews'].append(dict(review_id=rid, review_kind=kind, supplier_id=sid, product_id=pid, offer_id=oid, details=details))


TERMS = ('currency', 'tax_basis', 'tax_rate_text', 'unit', 'price_kind', 'moq_text',
         'quantity_condition', 'lead_time_text', 'packing_json', 'conditions_json')


def comparable(old, new):
    def term(row, key):
        return json.loads(row[key]) if key.endswith('_json') else row[key]
    return all(term(old, k) == term(new, k) for k in TERMS)


def prior_offers(db, offer):
    rows = db.execute('SELECT o.*,s.source_at FROM sd_offer o JOIN sd_source s USING(source_id) WHERE supplier_id=? AND product_id IS ? ORDER BY quoted_on,source_at,o.rowid',
                      (offer['supplier_id'], offer['product_id'])).fetchall()
    return [r for r in rows if comparable(r, offer) and (not offer['quoted_on'] or not r['quoted_on'] or r['quoted_on'] <= offer['quoted_on'])]


GAP_FIELDS = {'moq_missing': {'moq_text'}, 'lead_time_missing': {'lead_time_text'},
              'packing_confirmation': {'packing'}, 'packing_units': {'packing'},
              'unquoted': {'amount_decimal'}, 'bare_price': {'model', 'amount_decimal', 'unit'},
              'model_missing': {'model'}, 'quote_terms': {'currency', 'tax_basis', 'unit', 'moq_text', 'lead_time_text'},
              'quote_conditions': {'conditions'}, 'technical_specs': set()}


def project(db, job, window, offers, now, changes):
    sid = window['supplier_id']
    for supplier in window['candidates']:
        if append(db, 'sd_supplier', supplier, job, now, changes):
            changes['suppliers'].append(supplier)
    # Parents may belong to a preceding split window that failed. Register them here too.
    for s in window.get('parents', []) + window['sources']:
        row = {k: v for k, v in s.items() if k not in ('text', 'method')}
        row['extract_state'] = 'extracted' if s['text'] and s['direction'] == 'incoming' else row['extract_state']
        if append(db, 'sd_source', row, job, now, changes):
            changes['sources'].append(row)
    if window['merge_conflict']:
        review(db, 'merge_conflict', job, sid, None, None, dict(reason='group_bound_to_multiple_suppliers'), now, changes)
        return
    for raw in offers:
        fields = validated(raw, window['sources'])
        if not fields or not ({'amount_decimal', 'model'} & set(fields)):
            continue
        pid, candidate, conflict = resolve(db, sid, fields)
        if candidate:
            if append(db, 'sd_product', candidate, job, now, changes):
                changes['products'].append(candidate)
            append(db, 'sd_supplier_product', dict(supplier_id=sid, product_id=pid,
                model_aliases_json=dumps([fields['model']['value']]), evidence_json=dumps(list(fields.values()))), job, now, changes)
        if conflict:
            review(db, 'merge_conflict', job, sid, pid, None, dict(reason='ambiguous_supplier_model'), now, changes)
        def value(k):
            return fields.get(k, {}).get('value')
        primary = fields.get('amount_decimal') or fields.get('model')
        source = next(s for s in window['sources'] if s['source_id'] == primary['source_id'])
        source_date = datetime.fromisoformat(source['source_at']).astimezone(LOCAL_TZ).date().isoformat() if source['source_at'] else None
        quoted_on = value('quoted_on') or source_date
        dedup = identity('IOD', [sid, pid, {k: dict(value=f['value'], source_id=f['source_id']) for k, f in fields.items()}])
        if db.execute('SELECT 1 FROM sd_offer WHERE dedup_key=?', (dedup,)).fetchone():
            continue
        oid = identity('IO', dedup)
        row = dict(offer_id=oid, supplier_id=sid, product_id=pid, amount_decimal=value('amount_decimal'),
            amount_raw=value('amount_raw') or value('amount_decimal') or '', currency=value('currency'),
            # dossier-v1 NOT NULL constraint; all other unknown tax fields stay SQL NULL.
            tax_basis=value('tax_basis') or 'unknown', tax_rate_text=value('tax_rate_text'), unit=value('unit'),
            price_kind=value('price_kind') or 'unit_price', quote_state='observed', moq_text=value('moq_text'),
            quantity_condition=value('quantity_condition'), lead_time_text=value('lead_time_text'),
            packing_json=dumps(dict(text=value('packing'))) if value('packing') else '{}',
            conditions_json=dumps(dict(text=value('conditions'))) if value('conditions') else '{}',
            quoted_on=quoted_on, quoted_on_basis='explicit' if value('quoted_on') else 'message_date' if source_date else 'unknown',
            document_period=value('document_period'), valid_until=value('valid_until'), supersedes_offer_id=None,
            source_id=source['source_id'], locator=source['locator'], field_sources_json=dumps(fields), dedup_key=dedup)
        history = prior_offers(db, row) if pid else []
        if history and row['amount_decimal'] is not None:
            superseded = {r['supersedes_offer_id'] for r in history}
            heads = [r for r in history if r['offer_id'] not in superseded]
            row['supersedes_offer_id'] = (heads or history)[-1]['offer_id']
        append(db, 'sd_offer', row, job, now, changes)
        changes['offers'].append(dict(row))
        evidence_ids = {}
        for path, field in fields.items():
            field_source = next(s for s in window['sources'] if s['source_id'] == field['source_id'])
            is_ocr = field_source.get('method') == 'ocr'
            method = 'ocr' if is_ocr else 'incremental:validated_literal'
            state = 'ocr_candidate' if is_ocr else '逐字校验'
            eid = identity('IE', [oid, path, field])
            evidence_ids[path] = eid
            append(db, 'sd_evidence', dict(evidence_id=eid, entity_kind='offer', entity_id=oid, field_path=path,
                source_id=field['source_id'], locator=next(s['locator'] for s in window['sources'] if s['source_id'] == field['source_id']),
                excerpt=field['excerpt'], bbox_json=None, method=method, evidence_state=state), job, now, changes)
            if pid and (path == 'model' or path.startswith('config.')):
                product_path = 'canonical_model' if path == 'model' else path
                product_eid = identity('IEP', [pid, product_path, field])
                append(db, 'sd_evidence', dict(evidence_id=product_eid, entity_kind='product', entity_id=pid,
                    field_path=product_path, source_id=field['source_id'],
                    locator=next(s['locator'] for s in window['sources'] if s['source_id'] == field['source_id']),
                    excerpt=field['excerpt'], bbox_json=None, method=method, evidence_state=state), job, now, changes)
        if row['amount_decimal'] is not None:
            prices = sorted(Decimal(r['amount_decimal']) for r in history if r['amount_decimal'] is not None and re.fullmatch(r'[0-9]+(?:\.[0-9]+)?', r['amount_decimal']))
            if prices:
                n = len(prices)
                median = (prices[(n - 1) // 2] + prices[n // 2]) / 2
                current = Decimal(row['amount_decimal'])
                anomalous = abs(current - median) > abs(median) * Decimal('0.30') if median else current != 0
                if anomalous:
                    review(db, 'price_anomaly', job, sid, pid, oid, dict(median=str(median), amount_decimal=str(current), threshold='0.30'), now, changes)
        for gap in db.execute('SELECT * FROM sd_gap WHERE supplier_id=? AND product_id IS ? AND state=\'open\' AND NOT EXISTS(SELECT 1 FROM sd_gap_fill f WHERE f.gap_id=sd_gap.gap_id)', (sid, pid)).fetchall():
            required = GAP_FIELDS.get(gap['gap_type'], {gap['gap_type'][:-8]} if gap['gap_type'].endswith('_missing') else set())
            if not required or not required <= set(fields):
                continue
            if gap['offer_id'] and gap['offer_id'] not in {r['offer_id'] for r in history}:
                continue
            ids = [evidence_ids[k] for k in sorted(required)]
            fill_source = fields[sorted(required)[0]]['source_id']
            db.execute('INSERT INTO sd_gap_fill VALUES(?,?,?,?,?,?)', (gap['gap_id'], job, oid, fill_source, dumps(ids), now))
            changes['gap_fills'].append(dict(gap_id=gap['gap_id'], offer_id=oid, source_id=fill_source, evidence_ids=ids))
