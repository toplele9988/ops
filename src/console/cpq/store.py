"""Read-only catalog access; every mutation targets the CPQ work database."""
from datetime import date, datetime, timezone
from pathlib import Path
from math import isfinite
import json
import threading
from console.strobe.repository import open_catalog, ACCEPTED_OFFERS_CTE, FIELD_MAP, SUPPLIER_LEGAL_NAME
from console.strobe.spec_parser import parse_specs
from .models import Lane, ExpressBand, Packing, json_value
from .workdb import open_work, ensure_schema

PACK_FIELDS = ('units_per_pkg', 'l_cm', 'w_cm', 'h_cm', 'gw_kg', 'case_cost_cny')


def num(value, positive=False):
    try:
        valid = type(value) in (int, float) and isfinite(value) and value >= 0 and (not positive or value > 0)
    except OverflowError:
        valid = False
    if not valid:
        raise ValueError('invalid number')
    return float(value)


def pack(value):
    if type(value.get('units_per_pkg')) is not int or value['units_per_pkg'] <= 0:
        raise ValueError('invalid units_per_pkg')
    return Packing(value['units_per_pkg'], *(num(value.get(k), True) for k in ('l_cm', 'w_cm', 'h_cm')), num(value.get('gw_kg')), num(value['case_cost_cny']) if value.get('case_cost_cny') is not None else None)


def lane_from_dict(value):
    data = dict(value)
    until = data.get('valid_until')
    data['valid_until'] = date.fromisoformat(until) if isinstance(until, str) else until
    if type(data['valid_until']) is not date:
        raise ValueError('invalid lane date')
    try:
        data['bands'] = tuple(ExpressBand(**b) for b in data.get('bands', ()))
        lane = Lane(**data)
    except (TypeError, KeyError) as exc:
        raise ValueError('invalid lane fields') from exc
    if not isinstance(lane.lane_id, str) or not lane.lane_id or lane.mode not in ('sea', 'express'):
        raise ValueError('invalid lane identity')
    return lane


def instant(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if type(value) is date:
        value = datetime.combine(value, datetime.min.time(), tzinfo=timezone.utc)
    if not isinstance(value, datetime):
        raise ValueError('invalid timestamp')
    return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def stamp(value=None):
    return instant(value if value is not None else datetime.now(timezone.utc)).isoformat().replace('+00:00', 'Z')


def dump(value):
    return json.dumps(json_value(value), ensure_ascii=False, sort_keys=True, allow_nan=False)


def money(value):
    try:
        return num(value) / 100
    except ValueError:
        return None


class CPQStore:
    def __init__(self, db_path, work_db):
        self.db_path = Path(db_path) if db_path is not None else None
        self.work_db = Path(work_db)
        self._ready, self._lock = False, threading.Lock()

    def initialize(self):
        with self._lock:
            if not self._ready:
                conn = open_work(self.work_db)
                try:
                    ensure_schema(conn)
                finally:
                    conn.close()
                self._ready = True

    def read(self, sql, params=(), *, catalog=False):
        if not catalog:
            self.initialize()
        with open_catalog(self.db_path if catalog else self.work_db) as conn:
            return [dict(r) for r in conn.execute(sql, params)]

    def write(self, sql, params):
        self.initialize()
        conn = open_work(self.work_db)
        try:
            with conn:
                conn.execute('BEGIN IMMEDIATE')
                return conn.execute(sql, params).lastrowid
        finally:
            conn.close()

    def fx(self, today):
        rows = self.read('SELECT cny_per_usd_micro, as_of FROM cat_fx_rate ORDER BY as_of DESC, fx_id DESC LIMIT 1', catalog=True)
        if rows:
            rate = num(rows[0]['cny_per_usd_micro'], True) / 1_000_000
            at = datetime.fromtimestamp(rows[0]['as_of'], timezone.utc)
        else:
            rows = self.read('SELECT rate, effective_at FROM cpq_fx_rate WHERE pair=? ORDER BY julianday(effective_at) DESC, id DESC LIMIT 1', ('USDCNY',))
            if not rows:
                return None
            rate, at = num(rows[0]['rate'], True), instant(rows[0]['effective_at'])
        age = (today - at.date()).days
        if age < 0:
            raise ValueError('future FX effective date')
        return {'usd_cny': rate, 'updated_at': stamp(at), 'age_days': age}

    def add_fx(self, rate, as_of, actor=None):
        rate, at = num(rate, True), stamp(as_of)
        self.write('INSERT INTO cpq_fx_rate(pair,rate,effective_at,entered_by) SELECT ?,?,?,? WHERE NOT EXISTS (SELECT 1 FROM cpq_fx_rate WHERE pair=? AND rate=? AND effective_at=?)', ('USDCNY', rate, at, actor, 'USDCNY', rate, at))

    def model(self, model_id):
        rows = self.read('SELECT * FROM equipment_specs WHERE equipment_id=? AND supplier=? AND fixture_type=?', (model_id, SUPPLIER_LEGAL_NAME, 'strobe'), catalog=True)
        if not rows:
            return None
        row = rows[0]
        try:
            snapshot = json.loads(row.get('extraction_snapshot_json') or '{}')
        except (ValueError, TypeError):
            snapshot = {}
        overrides = {r['field']: json.loads(r['value_json']) for r in self.read('SELECT field,value_json FROM cpq_spec_override WHERE model_id=?', (model_id,))}
        columns = {f: row[c] for f, c in FIELD_MAP.items() if row.get(c) is not None}
        parsed = parse_specs(snapshot, columns, overrides)
        return {'model_id': model_id, 'category': row['fixture_type'], 'net_weight_kg': parsed['values']['net_weight_kg'], 'spec_complete': not parsed['spec_completeness']['missing']}

    def offers(self, model_id, today):
        sql = ACCEPTED_OFFERS_CTE + '''SELECT a.* FROM accepted_offers a JOIN equipment_specs e
        ON e.model=a.factory_model_code AND e.supplier=a.supplier_name
        WHERE e.equipment_id=:model_id AND e.fixture_type=:kind
        ORDER BY a.raw_cost_rmb_fen IS NULL,a.raw_cost_rmb_fen,a.offer_id'''
        rows = self.read(sql, {'supplier': SUPPLIER_LEGAL_NAME, 'today': today.isoformat(), 'model_id': model_id, 'kind': 'strobe'}, catalog=True)
        return [{'offer_id': r['offer_id'], 'supplier_id': r['supplier_id'], 'supplier_name': r['supplier_name'], 'exw_cny': money(r['raw_cost_rmb_fen']), 'valid_until': r['valid_until'], 'case_cost_cny': money(r['fc_upgrade_rmb_fen'])} for r in rows]

    @staticmethod
    def basis(offers):
        known = [o for o in offers if o['exw_cny'] is not None]
        best = min(known, key=lambda o: o['exw_cny']) if known else None
        return {'cost_basis_cny': best['exw_cny'] if best else None, 'cost_basis_mode': 'lowest_valid_offer', 'offer_valid_until': best['valid_until'] if best else None, 'offer_id': best['offer_id'] if best else None}

    def packing(self, model_id):
        sql = '''SELECT p.* FROM cat_packing p JOIN cat_supplier_model sm ON sm.supplier_model_id=p.supplier_model_id
        JOIN cat_supplier s ON s.supplier_id=sm.supplier_id
        JOIN equipment_specs e ON e.model=sm.factory_model_code AND e.supplier=s.legal_name
        WHERE e.equipment_id=? AND e.supplier=? AND e.fixture_type=?
        AND p.packing_id=(SELECT MAX(p2.packing_id) FROM cat_packing p2 WHERE p2.supplier_model_id=p.supplier_model_id AND p2.packing=p.packing)
        ORDER BY p.packing_id DESC'''
        result = {'carton': None, 'flight_case': None}
        for row in self.read(sql, (model_id, SUPPLIER_LEGAL_NAME, 'strobe'), catalog=True):
            mode = {'carton': 'carton', 'flightcase': 'flight_case'}.get(row['packing'])
            if mode is None or result[mode] is not None or any(row[k] is None for k in ('units_per_pack', 'l_mm', 'w_mm', 'h_mm', 'gross_g')):
                continue
            value = pack({'units_per_pkg': row['units_per_pack'], 'l_cm': row['l_mm']/10, 'w_cm': row['w_mm']/10, 'h_cm': row['h_mm']/10, 'gw_kg': row['gross_g']/1000})
            result[mode] = {**json_value(value), 'source': 'catalog'}
        for row in self.read('SELECT * FROM cpq_packing_profile WHERE model_id=?', (model_id,)):
            result[row['packing_mode']] = {**json_value(pack(row)), 'source': row['source']}
        return result

    def case_cost(self, model_id, offers):
        rows = self.read('SELECT case_cost_cny FROM cpq_packing_profile WHERE model_id=? AND packing_mode=?', (model_id, 'flight_case'))
        if rows and rows[0]['case_cost_cny'] is not None:
            return num(rows[0]['case_cost_cny'])
        known = sorted((o for o in offers if o['case_cost_cny'] is not None), key=lambda o: (o['exw_cny'] is None, o['exw_cny'] or 0))
        return known[0]['case_cost_cny'] if known else None

    def put_packing(self, model_id, mode, value, *, source='manual', actor=None):
        if not model_id or mode not in ('carton', 'flight_case'):
            raise ValueError('invalid packing identity')
        value = pack(value)
        self.write('''INSERT INTO cpq_packing_profile(model_id,packing_mode,units_per_pkg,l_cm,w_cm,h_cm,gw_kg,case_cost_cny,source,updated_by,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(model_id,packing_mode) DO UPDATE SET units_per_pkg=excluded.units_per_pkg,l_cm=excluded.l_cm,w_cm=excluded.w_cm,h_cm=excluded.h_cm,gw_kg=excluded.gw_kg,case_cost_cny=excluded.case_cost_cny,source=excluded.source,updated_by=excluded.updated_by,updated_at=excluded.updated_at''', (model_id, mode, *(getattr(value, k) for k in PACK_FIELDS), source, actor, stamp()))

    def lanes(self):
        return [lane_from_dict(json.loads(r['payload_json'])) for r in self.read('SELECT payload_json FROM cpq_freight_lane ORDER BY lane_id')]

    def lane(self, lane_id):
        rows = self.read('SELECT payload_json FROM cpq_freight_lane WHERE lane_id=?', (lane_id,))
        return lane_from_dict(json.loads(rows[0]['payload_json'])) if rows else None

    def put_lane(self, lane):
        lane = lane_from_dict(json_value(lane) if isinstance(lane, Lane) else lane)
        self.write('''INSERT INTO cpq_freight_lane(lane_id,mode,payload_json,valid_until,updated_at) VALUES (?,?,?,?,?)
        ON CONFLICT(lane_id) DO UPDATE SET mode=excluded.mode,payload_json=excluded.payload_json,valid_until=excluded.valid_until,updated_at=excluded.updated_at
        WHERE cpq_freight_lane.payload_json!=excluded.payload_json''', (lane.lane_id, lane.mode, dump(lane), lane.valid_until.isoformat(), stamp()))

    def accessories(self):
        return self.read('SELECT accessory_id,name_en,name_zh,cost_cny FROM cpq_accessory WHERE active=? ORDER BY accessory_id', (1,))

    def put_accessory(self, value):
        if any(not isinstance(value.get(k), str) or not value[k].strip() for k in ('accessory_id', 'name_en')):
            raise ValueError('accessory identity required')
        active = value.get('active', True)
        if type(active) not in (int, bool) or active not in (0, 1):
            raise ValueError('invalid active flag')
        self.write('''INSERT INTO cpq_accessory(accessory_id,name_en,name_zh,cost_cny,active) VALUES (?,?,?,?,?)
        ON CONFLICT(accessory_id) DO UPDATE SET name_en=excluded.name_en,name_zh=excluded.name_zh,cost_cny=excluded.cost_cny,active=excluded.active''', (value['accessory_id'], value['name_en'], value.get('name_zh'), num(value.get('cost_cny')), int(active)))

    def public_sku(self, model_id):
        rows = self.read('SELECT public_sku FROM cpq_public_sku WHERE model_id=?', (model_id,))
        return rows[0]['public_sku'] if rows else None

    def put_public_sku(self, model_id, sku):
        self.write('INSERT INTO cpq_public_sku(model_id,public_sku,created_at) VALUES (?,?,?) ON CONFLICT(model_id) DO UPDATE SET public_sku=excluded.public_sku', (model_id, sku, stamp()))

    def asset_flags(self):
        return {r['asset_id']: bool(r['customer_safe']) for r in self.read('SELECT asset_id,customer_safe FROM cpq_asset_flag')}

    def put_asset_flag(self, asset_id, safe, *, reason=None, actor=None):
        if type(safe) is not bool:
            raise ValueError('boolean flag required')
        self.write('''INSERT INTO cpq_asset_flag(asset_id,customer_safe,reason,updated_by,updated_at) VALUES (?,?,?,?,?)
        ON CONFLICT(asset_id) DO UPDATE SET customer_safe=excluded.customer_safe,reason=excluded.reason,updated_by=excluded.updated_by,updated_at=excluded.updated_at''', (asset_id, int(safe), reason, actor, stamp()))

    def append_audit(self, action, *, actor=None, quote_token=None, detail=None):
        return self.write('INSERT INTO cpq_audit_log(at,actor,action,quote_token,detail_json) VALUES (?,?,?,?,?)', (stamp(), actor, action, quote_token, dump(detail)))

    def audit_log(self, quote_token=None):
        rows = self.read('SELECT * FROM cpq_audit_log ORDER BY id') if quote_token is None else self.read('SELECT * FROM cpq_audit_log WHERE quote_token=? ORDER BY id', (quote_token,))
        for row in rows:
            raw = row.pop('detail_json')
            row['detail'] = json.loads(raw) if raw is not None else None
        return rows
