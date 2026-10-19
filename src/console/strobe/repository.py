"""Read-only catalog access; annotations live in a separate work database."""
from collections import Counter
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from string import Formatter
import json
import math
import sqlite3
import threading
import time
import yaml
from console.security.media import MediaAsset, make_asset_uid, valid_uid
from console.strobe.spec_parser import parse_specs

SUPPLIER_LEGAL_NAME = '广州凤舞灯光有限公司'
FIELD_MAP = {
    'power_w': 'wattage', 'ip_rating': 'ip_rating', 'rgb_count': 'rgb_count',
    'white_count': 'white_count', 'led_type': 'led_type', 'pixel_segments': 'segments',
    'dmx_modes': 'channel_modes', 'control_protocols': 'control_protocols',
    'voltage': 'voltage', 'certifications': 'certifications',
}
ACCEPTED_OFFERS_CTE = '''
WITH accepted_offers AS (
 SELECT o.offer_id, o.supplier_id, o.supplier_model_id, sm.factory_model_code,
        s.legal_name AS supplier_name, o.quoted_on, o.valid_until,
        o.raw_cost_rmb_fen, o.tax_basis, o.includes_flightcase, o.fc_upgrade_rmb_fen
 FROM cat_supplier_offer o
 JOIN cat_supplier s ON s.supplier_id = o.supplier_id
 JOIN cat_supplier_model sm ON sm.supplier_model_id = o.supplier_model_id
                          AND sm.supplier_id = o.supplier_id
 WHERE s.legal_name = :supplier
   AND (o.valid_until IS NULL OR o.valid_until >= :today)
   AND EXISTS (
     SELECT 1 FROM cat_review r
     WHERE r.subject_kind = 'offer' AND r.subject_id = o.offer_id
       AND r.decision = 'accept'
       AND r.review_id = (
         SELECT MAX(r2.review_id) FROM cat_review r2
         WHERE r2.subject_kind = 'offer' AND r2.subject_id = o.offer_id
       )
   )
)
'''
STROBES_SQL = ACCEPTED_OFFERS_CTE + '''
SELECT e.* FROM equipment_specs e
WHERE e.supplier = :supplier AND e.fixture_type = 'strobe'
  AND EXISTS (SELECT 1 FROM accepted_offers a
              WHERE a.factory_model_code = e.model AND a.supplier_name = e.supplier)
ORDER BY e.equipment_id
'''
OFFERS_SQL = ACCEPTED_OFFERS_CTE + 'SELECT * FROM accepted_offers ORDER BY factory_model_code, raw_cost_rmb_fen, offer_id'
ASSETS_SQL = "SELECT asset_id, nas_rel_path, extraction_snapshot_json FROM multimedia_assets WHERE source_type = 'supplier_image' ORDER BY asset_id"
WORK_SQL = {
    'flags': 'SELECT asset_id, customer_safe FROM cpq_asset_flag',
    'skus': 'SELECT model_id, public_sku FROM cpq_public_sku',
    'overrides': 'SELECT model_id, field, value_json FROM cpq_spec_override',
}
ASSET_INDEX_TTL_SECONDS = 60.0
DEFAULT_SKU_CONFIG = Path(__file__).resolve().parents[2] / 'config' / 'cpq.yaml'


@contextmanager
def open_catalog(path):
    conn = sqlite3.connect(Path(path).expanduser().absolute().as_uri() + '?mode=ro', uri=True, timeout=5.0)
    try:
        conn.execute('PRAGMA query_only=ON')
        conn.row_factory = sqlite3.Row
        yield conn
    finally:
        conn.close()


def _object(raw):
    try:
        value = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    return value if isinstance(value, dict) else {}


def _read_work(path):
    result = {name: [] for name in WORK_SQL}
    try:
        with open_catalog(path) as conn:
            for name, sql in WORK_SQL.items():
                try:
                    result[name] = [dict(row) for row in conn.execute(sql)]
                except sqlite3.Error:
                    pass
    except (sqlite3.Error, OSError, ValueError):
        pass
    return result


def _flags(work):
    return {row['asset_id']: row['customer_safe'] != 0 for row in work['flags']}


def _normalized(row, work):
    overrides = {}
    for entry in work['overrides']:
        if entry['model_id'] == row['equipment_id']:
            try:
                overrides[entry['field']] = json.loads(entry['value_json'])
            except (ValueError, TypeError):
                pass
    columns = {field: row[column] for field, column in FIELD_MAP.items() if column in row and row[column] is not None}
    return parse_specs(_object(row.get('extraction_snapshot_json')), columns, overrides)


def _query_catalog(path):
    params = {'supplier': SUPPLIER_LEGAL_NAME, 'today': date.today().isoformat()}
    with open_catalog(path) as conn:
        conn.execute('BEGIN')
        rows = [dict(row) for row in conn.execute(STROBES_SQL, params)]
        offers = [dict(row) for row in conn.execute(OFFERS_SQL, params)]
    return rows, offers


def _for_model(row, offers):
    return [offer for offer in offers if offer['factory_model_code'] == row['model'] and offer['supplier_name'] == row['supplier']]


def _money(value):
    return value / 100.0 if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None


def _minimum(offers):
    values = [_money(offer['raw_cost_rmb_fen']) for offer in offers]
    known = [value for value in values if value is not None]
    return min(known) if known else None


def _sku_config(path):
    try:
        config = yaml.safe_load(path.read_text(encoding='utf-8')) or {}
    except FileNotFoundError:
        return None, 'XL'
    if not isinstance(config, dict) or not isinstance(config.get('public_sku', {}), dict):
        raise ValueError('Invalid public_sku configuration')
    public = config.get('public_sku', {})
    pattern, brand = public.get('pattern'), public.get('brand', config.get('brand', 'XL'))
    if not isinstance(brand, str) or not brand:
        raise ValueError('brand must be nonempty text')
    if pattern is None:
        return None, brand
    if not isinstance(pattern, str) or not pattern:
        raise ValueError('public_sku.pattern must be nonempty text')
    for _, field, spec, conversion in Formatter().parse(pattern):
        if field is not None and (field not in {'brand', 'seq', 'index', 'n', 'num', 'number', 'sequence'} or '{' in spec or '}' in spec or conversion):
            raise ValueError('Unsupported public_sku.pattern placeholder')
    return pattern, brand


def _sku(model_id, sequence, work, pattern, brand):
    for row in work['skus']:
        if row['model_id'] == model_id:
            return row['public_sku']
    if pattern is None:
        return None  # No invented format when configuration is unavailable.
    return pattern.format(brand=brand, seq=sequence, index=sequence, n=sequence,
                          num=sequence, number=sequence, sequence=sequence)


class StrobeRepository:
    def __init__(self, db_path, work_db, media_uid_secret, sku_config_path=None):
        if not media_uid_secret:
            raise ValueError('MEDIA_UID_SECRET is required')
        self.db_path, self.work_db = Path(db_path), Path(work_db)
        self.media_uid_secret = media_uid_secret
        self.sku_config_path = Path(sku_config_path) if sku_config_path is not None else DEFAULT_SKU_CONFIG
        self._asset_index = {}
        self._asset_stamp, self._asset_expires_at = None, 0.0
        self._asset_lock = threading.RLock()

    def _stamp(self):
        result = []
        for path in (self.db_path, Path(str(self.db_path) + '-wal')):
            try:
                info = path.stat()
                result.append((info.st_dev, info.st_ino, info.st_mtime_ns, info.st_size))
            except FileNotFoundError:
                result.append(None)
        return tuple(result)

    def _refresh_assets(self):
        with self._asset_lock:
            stamp = self._stamp()
            if stamp == self._asset_stamp and time.monotonic() < self._asset_expires_at:
                return
            with open_catalog(self.db_path) as conn:
                rows = conn.execute(ASSETS_SQL).fetchall()
            index = {}
            for row in rows:
                asset_id = row['asset_id']
                if not isinstance(asset_id, str) or not asset_id:
                    continue
                snapshot = _object(row['extraction_snapshot_json'])
                uid = make_asset_uid(self.media_uid_secret, asset_id)
                if uid in index and index[uid].asset_id != asset_id:
                    raise RuntimeError('Media UID collision')
                index[uid] = MediaAsset(
                    asset_id,
                    snapshot.get('equipment_id') if isinstance(snapshot.get('equipment_id'), str) else None,
                    snapshot.get('local_path') if isinstance(snapshot.get('local_path'), str) else None,
                    row['nas_rel_path'] if isinstance(row['nas_rel_path'], str) else None,
                )
            self._asset_index, self._asset_stamp = index, stamp
            self._asset_expires_at = time.monotonic() + ASSET_INDEX_TTL_SECONDS

    def asset_by_uid(self, uid):
        if not valid_uid(uid):
            return None
        self._refresh_assets()
        with self._asset_lock:
            return self._asset_index.get(uid)

    def _images(self, model_id, flags, safe_only):
        self._refresh_assets()
        with self._asset_lock:
            assets = sorted(self._asset_index.items(), key=lambda pair: pair[1].asset_id)
        images = []
        for uid, asset in assets:
            safe = flags.get(asset.asset_id, True)
            if asset.equipment_id != model_id or (safe_only and not safe):
                continue
            images.append({'asset_uid': uid, 'role': 'main' if not images else 'detail',
                           'customer_safe': safe, 'thumb_url': f'/media/a/{uid}?w=640',
                           'large_url': f'/media/a/{uid}?w=1280', 'orig_url': f'/media/a/{uid}'})
        return images

    def images_for(self, model_id, *, customer_safe_only=False):
        return self._images(model_id, _flags(_read_work(self.work_db)), customer_safe_only)

    def _item(self, row, sequence, offers, work, pattern, brand, detail=False):
        normalized = _normalized(row, work)
        values, model_id = normalized['values'], row['equipment_id']
        power = values['power_w']
        item = {field: values[field] for field in (
            'ip_rating', 'pixel_segments', 'dmx_modes', 'control_protocols', 'power_w',
            'voltage', 'net_weight_kg', 'dimensions_cm', 'carton', 'flight_case', 'certifications')}
        item.update({
            'model_id': model_id, 'internal_sku': _sku(model_id, sequence, work, pattern, brand),
            'product_name': f'{power:g}W Strobe' if power is not None else 'Strobe',
            'light_source': {field: values[field] for field in ('rgb_count', 'white_count', 'led_type', 'dual', 'label')},
            'images': self._images(model_id, _flags(work), True),
            'spec_completeness': normalized['spec_completeness'],
            'spec_sources': normalized['sources'], 'spec_conflicts': normalized['conflicts'],
            'has_cost_basis': _minimum(_for_model(row, offers)) is not None,
        })
        if detail:
            item['specs_full'] = [{'label': field, 'value': value, 'source': normalized['sources'][field]} for field, value in values.items()]
            snapshot = _object(row.get('extraction_snapshot_json'))
            for field in ('description_en', 'spec_zh_full'):
                if isinstance(snapshot.get(field), str) and snapshot[field].strip():
                    item['specs_full'].append({'label': field, 'value': snapshot[field], 'source': 'column'})
        return item

    def list_strobes(self):
        rows, offers = _query_catalog(self.db_path)
        work = _read_work(self.work_db)
        pattern, brand = _sku_config(self.sku_config_path)
        return [self._item(row, index, offers, work, pattern, brand) for index, row in enumerate(rows, 1)]

    def get_strobe(self, model_id):
        rows, offers = _query_catalog(self.db_path)
        work = _read_work(self.work_db)
        pattern, brand = _sku_config(self.sku_config_path)
        for index, row in enumerate(rows, 1):
            if row['equipment_id'] == model_id:
                return self._item(row, index, offers, work, pattern, brand, detail=True)
        return None

    def cost_basis(self, model_id):
        rows, offers = _query_catalog(self.db_path)
        row = next((row for row in rows if row['equipment_id'] == model_id), None)
        return _minimum(_for_model(row, offers)) if row is not None else None

    def offers_for_admin(self, model_id):
        rows, offers = _query_catalog(self.db_path)
        row = next((row for row in rows if row['equipment_id'] == model_id), None)
        if row is None:
            return []
        return [{
            'offer_id': offer['offer_id'], 'supplier_id': offer['supplier_id'],
            'supplier_name': offer['supplier_name'], 'quoted_on': offer['quoted_on'],
            'valid_until': offer['valid_until'], 'raw_cost_cny': _money(offer['raw_cost_rmb_fen']),
            'tax_basis': offer['tax_basis'],
            'includes_flightcase': bool(offer['includes_flightcase']) if offer['includes_flightcase'] is not None else None,
            'flight_case_upgrade_cny': _money(offer['fc_upgrade_rmb_fen']),
        } for offer in _for_model(row, offers)]


def audit_catalog(db_path, work_db):
    """Counts registered assets without requiring a UID secret or writing files."""
    rows, offers = _query_catalog(db_path)
    work = _read_work(work_db)
    flags, total, safe = _flags(work), Counter(), Counter()
    with open_catalog(db_path) as conn:
        for asset in conn.execute(ASSETS_SQL):
            equipment_id = _object(asset['extraction_snapshot_json']).get('equipment_id')
            if isinstance(equipment_id, str):
                total[equipment_id] += 1
                if flags.get(asset['asset_id'], True):
                    safe[equipment_id] += 1
    result = []
    for row in rows:
        normalized = _normalized(row, work)
        model_id = row['equipment_id']
        result.append({'model': row['model'], 'model_id': model_id,
                       'score': normalized['spec_completeness']['score'],
                       'missing': normalized['spec_completeness']['missing'],
                       'conflicts': normalized['conflicts'],
                       'safe_images': safe[model_id], 'total_images': total[model_id],
                       'valid_offers': len(_for_model(row, offers)), 'flight_case_source': 'none'})
    return result
