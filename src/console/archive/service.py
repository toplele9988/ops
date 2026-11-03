"""Archive business operations and internal owner-facing projections."""
from collections import Counter, defaultdict
from datetime import date
import json
import math
import sqlite3

from . import validation as v
from .store import Store, insert, update, audit, now
from .validity import counts, current_offer, current_quote, quote_state, route_key


def rows(db, table):
    return [dict(r) for r in db.execute('SELECT * FROM ' + table + ' ORDER BY id')]


def get(db, table, row_id):
    row = db.execute('SELECT * FROM ' + table + ' WHERE id=?', (row_id,)).fetchone()
    if row is None:
        raise v.Missing('未找到该档案记录')
    return dict(row)


def party_kind(db, row_id, kind):
    if row_id is not None and get(db, 'parties', row_id)['kind'] != kind:
        raise v.BadRequest('供应商或货代类型不符')


def active_product(db, row_id):
    row = get(db, 'products', row_id)
    if db.execute('SELECT 1 FROM product_merges WHERE product_id=?', (row_id,)).fetchone():
        raise v.Conflict('产品已合并，请操作目标产品')
    return row


def duplicate_product(db, values, exclude=None):
    key = v.normalize_model(values.get('model') or values['name'])
    for row in db.execute('SELECT id,model,name FROM products WHERE supplier_id IS ?', (values.get('supplier_id'),)):
        if row['id'] != exclude and v.normalize_model(row['model'] or row['name']) == key:
            raise v.Conflict('同一供应商已有该型号或名称，请核对或合并')


def source(db, product_id, kind, ref, title, who, note=None):
    old = db.execute('SELECT * FROM product_sources WHERE product_id=? AND kind=? AND ref=?',
                     (product_id, kind, ref)).fetchone()
    if old:
        if note and note != old['note']:
            update(db, 'product_sources', old['id'], {'note': note}, who, 'source_note')
        return old['id']
    return insert(db, 'product_sources', dict(product_id=product_id, kind=kind, ref=ref,
                                            title=title, note=note), who)


def packing_ready(row):
    return all(row.get(k) is not None for k in
               ('carton_l_cm', 'carton_w_cm', 'carton_h_cm', 'gross_kg', 'units_per_carton'))


def packing_covered(row):
    return all(row.get(k) is not None for k in ('carton_l_cm', 'carton_w_cm', 'carton_h_cm', 'gross_kg'))


def money_view(row, today, current=None):
    result = dict(row)
    result.setdefault('channel', 'forwarder')
    result['price'] = v.fen_to_money(result.pop('price_fen'))
    if 'min_charge_fen' in result:
        fen = result.pop('min_charge_fen')
        result['min_charge'] = v.fen_to_money(fen) if fen is not None else None
    result.update(is_cost=True, is_current=row['id'] == current,
                  **quote_state(row, today, current=row['id'] == current))
    return result


def page(items, params):
    start = (params['page'] - 1) * params['page_size']
    return dict(items=items[start:start + params['page_size']], total=len(items),
                page=params['page'], page_size=params['page_size'])


def parameters(params, resource):
    result = dict(page=1, page_size=30)
    allowed = {'page', 'page_size'} | {
        'parties': {'kind', 'q', 'status'},
        'products': {'supplier', 'q', 'status', 'packing', 'price'},
        'freight': {'forwarder', 'country', 'mode', 'state'},
    }.get(resource, set())
    v.whitelist(params, allowed)
    for key, value in params.items():
        if hasattr(params, 'getlist') and len(params.getlist(key)) != 1:
            raise v.BadRequest('同一参数只能传入一次')
        if key in ('page', 'page_size'):
            result[key] = v.integer(value, maximum=100 if key == 'page_size' else 9999999)
        elif value:
            if key in ('supplier', 'forwarder'):
                result[key] = v.integer(value, maximum=2**63-1)
            elif key in ('q', 'country'):
                result[key] = v.text(value, 500)
            else:
                options = {'kind': v.PARTY_KINDS, 'packing': ('ready', 'missing'),
                           'price': ('valid', 'expiring', 'expired', 'none'),
                           'state': ('valid', 'expiring', 'expired', 'none'), 'mode': v.MODES,
                           'status': ('active', 'paused', 'archived') if resource == 'parties'
                           else ('candidate', 'confirmed', 'hidden')}
                result[key] = v.choice(value, options[key])
    return result


class Archive:
    def __init__(self, path=None, *, store=None, today=None, conversation_lookup=None):
        self.store = store or (Store(path) if path is not None else Store())
        self.today = today or date.today
        self.conversation_lookup = conversation_lookup

    def write(self, operation, row_id, fields, who):
        # Parse before opening/creating the database. Raw form values never reach audit.
        if operation in ('product_create', 'product_update'):
            values = v.product_fields(fields, operation == 'product_create')
        elif operation in ('offer_create', 'freight_create'):
            values = v.quote_fields(fields, operation == 'freight_create')
        elif operation == 'freight_update':
            values = v.freight_update_fields(fields)
        elif operation in ('party_create', 'party_update'):
            v.whitelist(fields, ('kind', 'name', 'short_name', 'notes', 'conversation_ref', 'aliases', 'country')
                        if operation == 'party_create' else ('short_name', 'notes', 'status', 'conversation_ref', 'aliases', 'country'))
            values = {}
            for key in ('name', 'short_name', 'notes', 'country'):
                if key in fields:
                    values[key] = v.text(fields[key], 2000 if key == 'notes' else 100 if key == 'country' else 500, key == 'name')
            if operation == 'party_create':
                values.update(kind=v.choice(fields.get('kind'), v.PARTY_KINDS),
                              name=v.party_name(fields.get('name')))
                if values['kind'] in ('customer', 'vendor'):
                    values['short_name'] = v.party_name(values.get('short_name') or values['name'])
            if 'status' in fields:
                values['status'] = v.choice(fields['status'], ('active', 'paused', 'archived'))
            if 'conversation_ref' in fields:
                values['conversation_ref'] = v.conversation_ref(fields['conversation_ref'])
            if 'aliases' in fields:
                values['aliases_json'] = v.aliases(fields['aliases'])
        elif operation == 'term_create':
            v.whitelist(fields, ('field', 'value', 'source_ref'))
            values = dict(field=v.choice(fields.get('field'), v.TERM_FIELDS),
                          value=v.text(fields.get('value'), 2000, True),
                          source_ref=v.text(fields.get('source_ref', ''), 500), status='confirmed')
        elif operation.endswith('_status'):
            v.whitelist(fields, ('action',))
            values = {'status': {'confirm': 'confirmed', 'reject': 'rejected'}[
                v.choice(fields.get('action'), ('confirm', 'reject'))]}
        elif operation == 'product_merge':
            v.whitelist(fields, ('into',))
            values = {'into': v.integer(fields.get('into'), maximum=2**63-1)}
        else:
            v.whitelist(fields, ())
            values = {}
        try:
            writer = self.store
            if (operation in ('freight_create', 'freight_update') and writer.connection is None
                    and writer.target_version < 9):
                writer = Store(writer.path, target_version=9)
            with writer.write() as db:
                return self._write(db, operation, row_id, values, who)
        except sqlite3.IntegrityError:
            raise v.Conflict('记录重复或关联、字段约束不满足') from None

    def _write(self, db, op, row_id, values, who):
        stamp = now()
        if op == 'party_create':
            values.update(created_at=stamp, updated_at=stamp)
            row_id = insert(db, 'parties', values, who)
        elif op == 'party_update':
            party = get(db, 'parties', row_id)
            if party['kind'] in ('customer', 'vendor') and values.get('short_name'):
                values['short_name'] = v.party_name(values['short_name'])
            update(db, 'parties', row_id, dict(values, updated_at=stamp), who)
        elif op == 'term_create':
            get(db, 'parties', row_id)
            row_id = insert(db, 'terms', dict(values, party_id=row_id, updated_at=stamp), who)
        elif op in ('offer_status', 'freight_status', 'term_status'):
            table = {'offer_status': 'offers', 'freight_status': 'freight_quotes', 'term_status': 'terms'}[op]
            old = get(db, table, row_id)
            if old['status'] != 'suggested':
                raise v.Conflict('只能确认或拒绝待确认记录')
            if table == 'offers' and values['status'] == 'confirmed':
                self._correction(db, old, row_id)
            update(db, table, row_id, dict(values, updated_at=stamp), who, values['status'])
        elif op in ('product_create', 'product_update'):
            old = active_product(db, row_id) if op == 'product_update' else {}
            combined = {**old, **values}
            party_kind(db, combined.get('supplier_id'), 'supplier')
            duplicate_product(db, combined, row_id)
            state = json.loads(old.get('field_state_json', '{}'))
            for field in values:
                state[field] = {'state': 'manual', 'source': 'manual', 'at': stamp}
            values.update(field_state_json=json.dumps(state, ensure_ascii=False), updated_at=stamp)
            if op == 'product_create':
                row_id = insert(db, 'products', dict(values, created_at=stamp), who)
            else:
                update(db, 'products', row_id, values, who)
        elif op in ('product_confirm', 'product_hide', 'product_restore'):
            old = active_product(db, row_id)
            state = json.loads(old['field_state_json'])
            if op == 'product_confirm':
                if old['status'] != 'candidate':
                    raise v.Conflict('只能确认候选产品')
                status = 'confirmed'
                for field in v.PRODUCT_FIELDS:
                    if old.get(field) is not None and state.get(field, {}).get('state') != 'manual':
                        state[field] = {**state.get(field, {}), 'state': 'confirmed', 'at': stamp}
            elif op == 'product_restore':
                if old['status'] != 'hidden':
                    raise v.Conflict('只能恢复已隐藏产品')
                status = 'candidate'
            else:
                status = 'hidden'
            update(db, 'products', row_id, dict(status=status, field_state_json=json.dumps(state), updated_at=stamp), who, op)
        elif op == 'product_merge':
            self._merge(db, row_id, values['into'], who, stamp)
        elif op == 'offer_create':
            active_product(db, row_id)
            values['product_id'] = row_id
            self._correction(db, values)
            row_id = insert(db, 'offers', dict(values, created_at=stamp, updated_at=stamp), who)
        elif op == 'freight_create':
            party_kind(db, values['forwarder_id'], 'forwarder')
            row_id = insert(db, 'freight_quotes', dict(values, created_at=stamp, updated_at=stamp), who)
        elif op == 'freight_update':
            old = get(db, 'freight_quotes', row_id)
            combined = dict(old, **values)
            party_kind(db, combined['forwarder_id'], 'forwarder')
            if combined['valid_until'] and combined['valid_until'] < combined['quoted_on']:
                raise v.BadRequest('有效期不能早于报价日期')
            if (combined['transit_days_min'] is not None and combined['transit_days_max'] is not None
                    and combined['transit_days_min'] > combined['transit_days_max']):
                raise v.BadRequest('天数范围的起点不能大于终点')
            update(db, 'freight_quotes', row_id, dict(values, updated_at=stamp), who)
        else:
            raise v.BadRequest('操作无效')
        return dict(ok=True, id=row_id)

    @staticmethod
    def _correction(db, values, row_id=None):
        target = values.get('corrects_offer_id')
        if target is None:
            return
        seen = {row_id} if row_id else set()
        while target is not None:
            if target in seen:
                raise v.Conflict('报价更正不能形成循环')
            seen.add(target)
            old = get(db, 'offers', target)
            if old['product_id'] != values['product_id']:
                raise v.BadRequest('只能更正同一产品的报价')
            target = old['corrects_offer_id']

    @staticmethod
    def _merge(db, source_id, target_id, who, stamp):
        old, target = active_product(db, source_id), active_product(db, target_id)
        if source_id == target_id or old['supplier_id'] != target['supplier_id'] or target['status'] == 'hidden':
            raise v.Conflict('请选同一供应商下另一个未隐藏产品')
        for row in db.execute('SELECT * FROM product_sources WHERE product_id=?', (source_id,)).fetchall():
            existing = db.execute('SELECT * FROM product_sources WHERE product_id=? AND kind=? AND ref=?',
                                  (target_id, row['kind'], row['ref'])).fetchone()
            if existing:
                # Preserve both readable notes when deduplicating references.
                notes = list(dict.fromkeys(n for n in (existing['note'], row['note']) if n))
                merged_note = '\n'.join(notes) or None
                if merged_note != existing['note']:
                    update(db, 'product_sources', existing['id'], {'note': merged_note}, who, 'merge')
                db.execute('DELETE FROM product_sources WHERE id=?', (row['id'],))
                audit(db, who, 'merge_duplicate', 'product_sources', row['id'], ('product_id',))
            else:
                update(db, 'product_sources', row['id'], {'product_id': target_id}, who, 'merge')
        for row in db.execute('SELECT id FROM offers WHERE product_id=?', (source_id,)).fetchall():
            update(db, 'offers', row['id'], dict(product_id=target_id, updated_at=stamp), who, 'merge')
        insert(db, 'product_merges', dict(product_id=source_id, into_id=target_id), who, 'merge')
        update(db, 'products', source_id, dict(status='hidden', updated_at=stamp), who, 'merge')

    def read(self, resource, params=None, row_id=None):
        params = params or {}
        today = self.today()
        with self.store.read() as db:
            parties, products = rows(db, 'parties'), rows(db, 'products')
            offers, freight = rows(db, 'offers'), rows(db, 'freight_quotes')
            by_product = defaultdict(list)
            by_route = defaultdict(list)
            for row in offers:
                by_product[row['product_id']].append(row)
            for row in freight:
                by_route[route_key(row)].append(row)
            current_ids = {r['id'] for r in (current_quote(g) for g in by_route.values()) if r}

            def product_view(p, detail=False):
                group = by_product[p['id']]
                current = current_offer(group)
                cid = current['id'] if current else None
                result = dict(p)
                result['field_state'] = json.loads(result.pop('field_state_json'))
                result.update(packing_ready=packing_ready(p),
                              current_offer=money_view(current, today, cid) if current else None,
                              validity=quote_state(current, today)['validity'] if current else None)
                if detail:
                    result['offers'] = [money_view(o, today, cid) for o in group]
                    result['sources'] = [dict(s) for s in db.execute('SELECT * FROM product_sources WHERE product_id=? ORDER BY id', (p['id'],))]
                    merge = db.execute('SELECT into_id FROM product_merges WHERE product_id=?', (p['id'],)).fetchone()
                    result['merged_into'] = merge[0] if merge else None
                return result

            if resource == 'summary':
                visible = {p['id'] for p in products if p['status'] != 'hidden'}
                return dict(suppliers=sum(p['kind'] == 'supplier' for p in parties),
                            forwarders=sum(p['kind'] == 'forwarder' for p in parties),
                            products={s: sum(p['status'] == s for p in products) for s in ('candidate', 'confirmed', 'hidden')},
                            offers=counts([o for o in offers if o['product_id'] in visible], today),
                            freight=counts(freight, today),
                            packing_ready_products=sum(packing_ready(p) for p in products if p['id'] in visible))
            if resource == 'products':
                if row_id is not None:
                    return product_view(get(db, 'products', row_id), True)
                items = []
                for p in products:
                    if params.get('supplier') and p['supplier_id'] != params['supplier']:
                        continue
                    if params.get('status') and p['status'] != params['status']:
                        continue
                    if not params.get('status') and p['status'] == 'hidden':
                        continue
                    if params.get('q') and not any(params['q'].casefold() in (p[k] or '').casefold() for k in ('name', 'model', 'category')):
                        continue
                    if params.get('packing') and packing_ready(p) != (params['packing'] == 'ready'):
                        continue
                    item = product_view(p)
                    state = item['validity']
                    if params.get('price') and (state not in (None, 'no_expiry') if params['price'] == 'none' else state != params['price']):
                        continue
                    items.append(item)
                return page(items, params)
            if resource == 'freight':
                items = []
                for row in freight:
                    if any(params.get(q) and row[f] != params[q] for q, f in
                           (('forwarder', 'forwarder_id'), ('country', 'destination_country'), ('mode', 'mode'))):
                        continue
                    item = money_view(row, today, row['id'] if row['id'] in current_ids else None)
                    if params.get('state') and (row['id'] not in current_ids or item['validity'] !=
                                               ('no_expiry' if params['state'] == 'none' else params['state'])):
                        continue
                    items.append(item)
                return page(items, params)
            if resource == 'parties':
                def party_view(p, detail=False):
                    ps = [r for r in products if r['supplier_id'] == p['id']]
                    visible = [r for r in ps if r['status'] != 'hidden']
                    ids = {r['id'] for r in visible}
                    result = dict(p)
                    result['aliases'] = json.loads(result.pop('aliases_json') or '[]')
                    result.update(products={s: sum(r['status'] == s for r in ps) for s in ('candidate', 'confirmed', 'hidden')},
                                  offers=counts([r for r in offers if r['product_id'] in ids], today),
                                  packing_coverage=sum(packing_covered(r) for r in visible) / len(visible) if visible else 0)
                    if p['conversation_ref'] and self.conversation_lookup:
                        try:
                            value = self.conversation_lookup(p['conversation_ref'])
                            if type(value) in (int, float) and math.isfinite(value) and value >= 0:
                                result['last_activity'] = value
                        except Exception:
                            pass  # Optional adapter failures must not suppress the archive.
                    if detail:
                        terms = defaultdict(list)
                        for t in db.execute('SELECT * FROM terms WHERE party_id=? ORDER BY id', (p['id'],)):
                            terms[t['field']].append(dict(t))
                        result['terms'] = dict(terms)
                        result['product_items'] = [product_view(r) for r in visible[:50]]
                        if p['kind'] == 'forwarder':
                            result['freight_quotes'] = [money_view(r, today, r['id']) for r in freight
                                if r['forwarder_id'] == p['id'] and r['id'] in current_ids and quote_state(r, today)['usable_for_quote']]
                    else:
                        result['notes'] = (p['notes'] or '')[:80]
                    return result
                if row_id is not None:
                    return party_view(get(db, 'parties', row_id), True)
                filtered = [p for p in parties if (not params.get('kind') or p['kind'] == params['kind'])
                            and (not params.get('status') or p['status'] == params['status'])
                            and (not params.get('q') or any(params['q'].casefold() in (p[k] or '').casefold()
                                 for k in ('name', 'short_name', 'aliases_json')))]
                result = page(filtered, params)
                result['items'] = [party_view(p) for p in result['items']]
                return result
            raise v.BadRequest('查询无效')
