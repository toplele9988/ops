"""Project dossier-v1's inspected sd_* schema. Amounts always remain strings."""
import base64
import json
from urllib.parse import urlencode

from .store import DB_PATH, readonly


class Missing(Exception):
    pass


def unpack(row):
    result = dict(row)
    for key in tuple(result):
        if key.endswith('_json'):
            raw = result.pop(key)
            result[key[:-5]] = json.loads(raw) if raw is not None else None
    return result


def family_key(value):
    return base64.urlsafe_b64encode(value.encode()).decode().rstrip('=')


def family_value(value):
    try:
        raw = base64.urlsafe_b64decode(value + '=' * (-len(value) % 4)).decode()
        if family_key(raw) != value or not raw:
            raise ValueError
        return raw
    except (ValueError, UnicodeError):
        raise Missing from None


class Dossiers:
    def __init__(self, path=DB_PATH, *, resolver=None):
        self.path = path
        self.resolver = resolver

    @staticmethod
    def check(db):
        row = db.execute("SELECT value FROM sd_metadata WHERE key='schema_revision'").fetchone()
        if not row or row[0] != 'dossier-v1':
            raise ValueError('unsupported_dossier')

    def suppliers(self, params=None):
        params = params or {}
        with readonly(self.path) as db:
            self.check(db)
            rows = []
            for row in db.execute('SELECT * FROM sd_supplier ORDER BY name,supplier_id'):
                s = unpack(row)
                if params.get('type') and s['supplier_type'] != params['type']:
                    continue
                if params.get('q') and params['q'].casefold() not in (s['name'] + json.dumps(s['categories'], ensure_ascii=False)).casefold():
                    continue
                sid = s['supplier_id']
                stats = db.execute('SELECT count(DISTINCT p.product_family),count(*),'
                    'sum(EXISTS(SELECT 1 FROM sd_offer o WHERE o.supplier_id=sp.supplier_id '
                    'AND o.product_id=p.product_id AND o.amount_decimal IS NOT NULL)) '
                    'FROM sd_supplier_product sp JOIN sd_product p USING(product_id) WHERE sp.supplier_id=?', (sid,)).fetchone()
                s.update(family_count=stats[0], configuration_count=stats[1], priced_count=stats[2] or 0,
                         unpriced_count=stats[1] - (stats[2] or 0),
                         latest_quote=db.execute('SELECT MAX(quoted_on) FROM sd_offer WHERE supplier_id=?', (sid,)).fetchone()[0],
                         gap_count=db.execute("SELECT count(*) FROM sd_gap WHERE supplier_id=? AND state='open'", (sid,)).fetchone()[0])
                s.pop('contacts', None)
                rows.append(s)
            return dict(items=rows, total=len(rows))

    def _evidence(self, db, kind, identity):
        return [unpack(r) for r in db.execute('SELECT * FROM sd_evidence WHERE entity_kind=? AND entity_id=? ORDER BY field_path,evidence_id', (kind, identity))]

    def _offers(self, db, clause, args):
        result = []
        for row in db.execute('SELECT o.*,s.name AS supplier_name FROM sd_offer o JOIN sd_supplier s USING(supplier_id) WHERE ' + clause + ' ORDER BY o.quoted_on DESC,o.offer_id', args):
            offer = unpack(row)
            offer['evidence'] = self._evidence(db, 'offer', offer['offer_id'])
            result.append(offer)
        return result

    def _products(self, db, clause, args):
        result = []
        for row in db.execute('SELECT p.* FROM sd_product p WHERE ' + clause + ' ORDER BY p.product_family,p.variant_label,p.product_id', args):
            p = unpack(row)
            p['family_key'] = family_key(p['product_family'])
            p['evidence'] = self._evidence(db, 'product', p['product_id'])
            p['suppliers'] = [unpack(r) for r in db.execute('SELECT sp.*,s.name FROM sd_supplier_product sp JOIN sd_supplier s USING(supplier_id) WHERE product_id=? ORDER BY s.name', (p['product_id'],))]
            p['offers'] = self._offers(db, 'o.product_id=?', (p['product_id'],))
            p['gaps'] = [unpack(r) for r in db.execute("SELECT * FROM sd_gap WHERE product_id=? AND state='open' ORDER BY priority,gap_id", (p['product_id'],))]
            result.append(p)
        return result

    def supplier(self, sid):
        with readonly(self.path) as db:
            self.check(db)
            row = db.execute('SELECT * FROM sd_supplier WHERE supplier_id=?', (sid,)).fetchone()
            if not row:
                raise Missing
            s = unpack(row)
            s['products'] = self._products(db, 'EXISTS(SELECT 1 FROM sd_supplier_product sp WHERE sp.product_id=p.product_id AND sp.supplier_id=?)', (sid,))
            # This view never presents another supplier's price as this supplier's.
            for p in s['products']:
                p['offers'] = [o for o in p['offers'] if o['supplier_id'] == sid]
            s['other_offers'] = self._offers(db, 'o.supplier_id=? AND o.product_id IS NULL', (sid,))
            s['gaps'] = self._gaps(db, {'supplier': sid})
            s['evidence'] = self._evidence(db, 'supplier', sid)
            return s

    def families(self, params=None):
        params = params or {}
        with readonly(self.path) as db:
            self.check(db)
            products = self._products(db, '1=1', ())
            families = {}
            for p in products:
                if params.get('category') and p['category'] != params['category']:
                    continue
                if params.get('q') and params['q'].casefold() not in (p['display_name'] + ' ' + (p['canonical_model'] or '') + ' ' + p['variant_label']).casefold():
                    continue
                key = p['family_key']
                if key not in families:
                    families[key] = dict(family_key=key, display_name=p['display_name'], category=p['category'], configuration_count=0, supplier_ids=[], gap_count=0, thumbnail_source_id=None, states=[])
                f = families[key]
                f['configuration_count'] += 1
                f['gap_count'] += len(p['gaps'])
                f['supplier_ids'] = sorted(set(f['supplier_ids']) | {s['supplier_id'] for s in p['suppliers']})
                f['states'] = sorted(set(f['states']) | {p['evidence_state']})
                if not f['thumbnail_source_id']:
                    candidates = [e['source_id'] for e in p['evidence']]
                    candidates += [o['source_id'] for o in p['offers']]
                    candidates += [e['source_id'] for o in p['offers'] for e in o['evidence']]
                    for source_id in dict.fromkeys(candidates):
                        src = db.execute('SELECT * FROM sd_source WHERE source_id=?', (source_id,)).fetchone()
                        if src and self.resolver and self.resolver.is_image(dict(src)):
                            f['thumbnail_source_id'] = source_id
                            break
            ordered = sorted(families.values(), key=lambda f: (not bool(f['thumbnail_source_id']), f['category'], f['display_name']))
            return dict(items=ordered, total=len(families),
                        categories=[r[0] for r in db.execute('SELECT DISTINCT category FROM sd_product ORDER BY category')])

    def family(self, key):
        with readonly(self.path) as db:
            self.check(db)
            products = self._products(db, 'p.product_family=?', (family_value(key),))
            if not products:
                raise Missing
            return dict(family_key=key, display_name=products[0]['display_name'], category=products[0]['category'],
                        configurations=products,
                        suppliers=list({s['supplier_id']: dict(supplier_id=s['supplier_id'], name=s['name']) for p in products for s in p['suppliers']}.values()))

    @staticmethod
    def _gaps(db, params):
        clauses, args = ["g.state='open'"], []
        for key, col in [('supplier', 'supplier_id'), ('priority', 'priority'), ('type', 'gap_type')]:
            if params.get(key):
                clauses.append('g.' + col + '=?')
                args.append(params[key])
        return [unpack(r) for r in db.execute('SELECT g.*,s.name AS supplier_name,p.display_name AS product_name FROM sd_gap g JOIN sd_supplier s USING(supplier_id) LEFT JOIN sd_product p USING(product_id) WHERE ' + ' AND '.join(clauses) + ' ORDER BY g.priority,s.name,g.gap_id', args)]

    def gaps(self, params=None):
        params = params or {}
        with readonly(self.path) as db:
            self.check(db)
            rows = self._gaps(db, params)
            page = int(params.get('page') or 1)
            size = int(params.get('page_size') or 30)
            return dict(items=rows[(page-1)*size:page*size], total=len(rows), page=page, page_size=size,
                        types=[r[0] for r in db.execute("SELECT DISTINCT gap_type FROM sd_gap WHERE state='open' ORDER BY gap_type")],
                        priorities=[r[0] for r in db.execute("SELECT DISTINCT priority FROM sd_gap WHERE state='open' ORDER BY priority")])

    def queue_card(self):
        value = self.gaps({'page_size': '5'})
        rows = value['items']
        return dict(total=value['total'], items=[dict(gap_id=r['gap_id'], supplier_name=r['supplier_name'], ask_text=r['ask_text'], impact=r['impact'], priority=r['priority'],
                    to='/dossier-gaps?' + urlencode(dict(supplier=r['supplier_id']))) for r in rows])

    def source(self, source_id):
        with readonly(self.path) as db:
            self.check(db)
            row = db.execute('SELECT * FROM sd_source WHERE source_id=?', (source_id,)).fetchone()
            if not row:
                raise Missing
            source = dict(row)
            evidence = [unpack(r) for r in db.execute('SELECT * FROM sd_evidence WHERE source_id=? ORDER BY evidence_id', (source_id,))]
        public = {key: source[key] for key in ('source_id', 'kind', 'locator', 'source_at', 'parse_state', 'extract_state', 'failure_reason')}
        public.update(evidence=evidence, coverage=json.loads(source['coverage_json']))
        if self.resolver:
            public.update(self.resolver.resolve(source))
        return public

    def source_row(self, source_id):
        with readonly(self.path) as db:
            self.check(db)
            row = db.execute('SELECT * FROM sd_source WHERE source_id=?', (source_id,)).fetchone()
            if not row:
                raise Missing
            return dict(row)
