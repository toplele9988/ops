'''Independent scanning; no production scanner imports. Test-only synthetic inputs.'''
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from zipfile import ZipFile
import base64
import inspect
import json
import re
import sqlite3
import pytest
from PIL import Image
from pypdf import PdfReader
from starlette.applications import Starlette
import console.cpq.export as exports
import console.routes.export_routes as routes_module
from console.cpq.models import Lane
from console.cpq.store import CPQStore
from console.routes.export_routes import build_export_routes
from console.routes.strobe_routes import build_routes
from console.strobe.repository import STROBES_SQL, SUPPLIER_LEGAL_NAME, StrobeRepository
FORMATS = ('customer.pdf', 'customer.html', 'whatsapp.txt', 'images.zip')

@pytest.fixture
def app(settings):
    routes = build_routes(settings)
    paths = {r.path for r in routes}
    routes.extend(r for r in build_export_routes(settings) if r.path not in paths)
    result = Starlette(routes=routes)
    result.state.strobe_api = next(r for r in routes if r.name == 'strobe_list').endpoint.__self__
    result.state.strobe_repository = result.state.strobe_api.repository
    result.state.cpq_service = next(r for r in routes if r.name == 'cpq_context').endpoint.__self__.service
    result.state.export_api = next(r for r in routes if r.path == '/quotes/{token}/customer.html').endpoint.__self__
    return result

@pytest.fixture
def seeded_all(settings):
    today = date.today()
    epoch = int(datetime.combine(today, datetime.min.time(), tzinfo=timezone.utc).timestamp())
    repository = StrobeRepository(settings.db_path, settings.work_db, settings.media_uid_secret)
    products = {item['model_id']: item for item in repository.list_strobes()}
    store = CPQStore(settings.db_path, settings.work_db)
    store.initialize()
    mids = []
    connection = sqlite3.connect(settings.db_path)
    connection.row_factory = sqlite3.Row
    try:
        with connection:
            rows = connection.execute(STROBES_SQL, {'supplier': SUPPLIER_LEGAL_NAME, 'today': today.isoformat()}).fetchall()
            assert len(rows) == 37, 'Exactly 37 real accepted strobe models are required'
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name IN ('cat_fx_rate','cat_packing')").fetchall():
                connection.execute('DROP TRIGGER "' + row['name'].replace('"', '""') + '"')
            connection.execute('DELETE FROM cat_fx_rate')
            connection.execute('INSERT INTO cat_fx_rate(fx_id,cny_per_usd_micro,source,as_of,created_at) VALUES (?,?,?,?,?)', (1, 7120000, 'synthetic-export-test', epoch, epoch))
            connection.execute('DELETE FROM cat_packing')
            pid = 0
            for row in rows:
                mid = row['equipment_id']
                mids.append(mid)
                ids = [r[0] for r in connection.execute('SELECT sm.supplier_model_id FROM cat_supplier_model sm JOIN cat_supplier s ON s.supplier_id=sm.supplier_id WHERE sm.factory_model_code=? AND s.legal_name=?', (row['model'], row['supplier']))]
                assert ids
                gross = max(12.0, 2 * (products[mid]['net_weight_kg'] or 0) + 5)
                for mode, dims, weight, cost in [('carton', (60, 40, 30), gross, None), ('flight_case', (70, 50, 40), gross + 12, 180.0)]:
                    store.put_packing(mid, mode, dict(zip(('units_per_pkg','l_cm','w_cm','h_cm','gw_kg','case_cost_cny'), (2, *dims, weight, cost))), actor='synthetic-export-test')
                    for smid in ids:
                        pid += 1
                        connection.execute('INSERT INTO cat_packing(packing_id,supplier_model_id,packing,units_per_pack,l_mm,w_mm,h_mm,gross_g,created_at) VALUES (?,?,?,?,?,?,?,?,?)', (pid, smid, 'flightcase' if mode == 'flight_case' else mode, 2, *(v * 10 for v in dims), round(weight * 1000), epoch))
    finally:
        connection.close()
    store.add_fx(7.12, today, 'synthetic-export-test')
    store.put_lane(Lane('TEST-SEA-LCL', 'sea', today + timedelta(days=30), inland_cny_per_cbm=70, inland_min_cny=70, origin_fixed_usd=30, lcl_rate_usd_per_wm=20, fcl_rate_usd={'20GP':400,'40GP':600,'40HQ':700}, fcl_origin_fixed_usd_per_container=20, fcl_dest_fixed_usd_per_container=30, dest_fixed_usd=40, last_mile_usd=20))
    for aid, cost in [('EXPORT-HOOK',6.5),('EXPORT-SAFETY',2),('EXPORT-LINK',3)]:
        store.put_accessory({'accessory_id':aid,'name_en':'Synthetic test '+aid,'cost_cny':cost})
    payload = {'lines':[{'model_id':mids[0],'qty':40,'packing_mode':'carton','packing_override':None,'accessories':[{'accessory_id':'EXPORT-HOOK','qty_per_unit':2}],'unit_price_usd':None}], 'shipping':{'mode':'sea_lcl','incoterm':'DDP','lane_id':'TEST-SEA-LCL'}, 'target_margin':0.20,'pricing_mode':'margin'}
    return SimpleNamespace(settings=settings, store=store, repository=repository, today=today, model_ids=mids, payload=payload)

FLOOR_LIMITED = []

def ready(manager, seed, mid):
    payload = deepcopy(seed.payload)
    payload['lines'][0]['model_id'] = mid
    response = manager.post('/api/cpq/calculate', json=payload)
    assert response.status_code == 200, response.text
    if not response.json()['gate']['can_save'] and 'PRICE_FLOOR_FUSE' in response.json()['gate']['blocked_by']:
        # Cheap models cannot clear the CNY price floor at a normal margin; quote them in price mode instead.
        FLOOR_LIMITED.append(mid)
        for price in (60, 90, 130, 200, 300, 450):
            payload = deepcopy(seed.payload)
            payload['lines'][0].update(model_id=mid, unit_price_usd=price)
            payload['pricing_mode'] = 'price'
            response = manager.post('/api/cpq/calculate', json=payload)
            assert response.status_code == 200, response.text
            if response.json()['gate']['can_save']:
                break
    return payload | {'acks':response.json()['gate']['requires_ack'], 'payment_terms_id':'TT_30_70', 'lead_time_days':{mid:'15-20'}}

def freeze_quote_for(manager, seed, mid):
    response = manager.post('/api/cpq/quotes', json=ready(manager, seed, mid))
    assert response.status_code == 201, (mid, response.text)
    assert response.json()['status'] == 'final' and response.json()['gate']['can_export']
    return response.json()['quote_token']

def dto_for(seed, token):
    return json.loads(seed.store.read('SELECT customer_dto_json FROM cpq_quote WHERE quote_token=?', (token,))[0]['customer_dto_json'])

@pytest.fixture
def a_token(manager, seeded_all):
    return freeze_quote_for(manager, seeded_all, seeded_all.model_ids[0])

def terms_for(seed, mid):
    names, models, costs = set(), set(), set()
    with sqlite3.connect(seed.settings.db_path) as connection:
        for (name,) in connection.execute('SELECT legal_name FROM cat_supplier'):
            names.add(name.strip())
            short = re.sub(r'(股份有限公司|有限责任公司|有限公司|co\.?\s*,?\s*ltd\.?)\s*$', '', name, flags=re.I).strip(' ,，')
            if short:
                names.add(short)
        for (model,) in connection.execute('SELECT model FROM equipment_specs UNION SELECT factory_model_code FROM cat_supplier_model'):
            if isinstance(model, str) and len(model.strip()) >= 4:
                models.add(model.strip())
        sql = 'SELECT o.raw_cost_rmb_fen,o.fc_upgrade_rmb_fen FROM equipment_specs e JOIN cat_supplier s ON s.legal_name=e.supplier JOIN cat_supplier_model sm ON sm.supplier_id=s.supplier_id AND sm.factory_model_code=e.model JOIN cat_supplier_offer o ON o.supplier_model_id=sm.supplier_model_id AND o.supplier_id=sm.supplier_id WHERE e.equipment_id=?'
        for values in connection.execute(sql, (mid,)):
            costs.update(Decimal(str(v)) / 100 for v in values if v is not None)
    with sqlite3.connect(seed.settings.work_db) as connection:
        for (v,) in connection.execute('SELECT cost_cny FROM cpq_accessory UNION ALL SELECT case_cost_cny FROM cpq_packing_profile WHERE model_id=?', (mid,)):
            if v is not None:
                costs.add(Decimal(str(v)))
    costs |= {v.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP) for v in costs}
    assert names and models and costs
    return names, models, costs

def independent_scan(text, terms):
    names, models, costs = terms
    compact = ''.join(text.casefold().split())
    hits = ['supplier_name' for name in names if ''.join(name.casefold().split()) in compact]
    hits += ['factory_model' for model in models if model.casefold() in text.casefold()]
    if re.search(r'¥|￥|CNY|RMB|人民币|元', text, re.I):
        hits.append('cny_marker')
    if re.search(r'/media/|exw_|cost_basis|supplier_id|offer_id|底价|出厂|成本|供应商|毛利', text, re.I):
        hits.append('internal_key')
    markers = list(re.finditer(r'USD|US\$|\$|¥|￥|CNY|RMB|人民币|元|€', text, re.I))
    for m in re.finditer(r'(?<![0-9.,])(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?:\.[0-9]+)?(?![0-9]|[.,][0-9])', text):
        if Decimal(m[0].replace(',', '')) in costs and (Decimal(m[0].replace(',', '')) >= 100 or '.' in m[0]):
            if any(max(c.start()-m.end(), m.start()-c.end(), 0) <= 12 for c in markers):
                hits.append('cost_value')
    return hits

class HTMLText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.images, self.hidden = [], [], 0
    def handle_starttag(self, tag, attrs):
        if tag in ('style','script'):
            self.hidden += 1
        values = dict(attrs)
        if tag == 'img':
            self.images.append(values.get('src',''))
        self.parts.extend(values[k] for k in ('alt','title','href') if values.get(k))
    def handle_endtag(self, tag):
        if tag in ('style','script') and self.hidden:
            self.hidden -= 1
    def handle_data(self, text):
        if not self.hidden:
            self.parts.append(text)

def exif_text(value):
    if isinstance(value, bytes):
        return '\n'.join(value.decode(enc, errors='replace') for enc in ('utf-8','utf-16-le','utf-16-be','latin-1'))
    if hasattr(value, 'items'):
        return '\n'.join(exif_text(v) for _,v in value.items())
    if isinstance(value, (tuple,list)):
        return '\n'.join(exif_text(v) for v in value)
    return str(value) if value is not None else ''

def extract_text(fmt, data):
    if fmt == 'customer.pdf':
        reader = PdfReader(BytesIO(data))
        return '\n'.join([*(p.extract_text() or '' for p in reader.pages), *(str(v) for v in (reader.metadata or {}).values())])
    if fmt == 'images.zip':
        parts = []
        with ZipFile(BytesIO(data)) as archive:
            for member in archive.infolist():
                parts.append(member.filename)
                with Image.open(BytesIO(archive.read(member))) as image:
                    image.load()
                    exif = image.getexif()
                    parts.append(exif_text(exif))
                    for directory in (0x8769,0x8825):
                        if directory in exif:
                            parts.append(exif_text(exif.get_ifd(directory)))
        return '\n'.join(parts)
    text = data.decode('utf-8')
    if fmt == 'customer.html':
        parser = HTMLText()
        parser.feed(text)
        return '\n'.join(parser.parts)
    return text

@pytest.mark.parametrize('fmt', FORMATS)
def test_all_37_exports_clean(manager, seeded_all, fmt):
    assert len(seeded_all.model_ids) == 37
    for mid in seeded_all.model_ids:
        token = freeze_quote_for(manager, seeded_all, mid)
        response = manager.get(f'/quotes/{token}/{fmt}')
        assert response.status_code == 200, (mid, fmt, response.text[:200])
        assert independent_scan(extract_text(fmt, response.content), terms_for(seeded_all, mid)) == [], (mid, fmt)

def injected(manager, monkeypatch, seed, token, secret, fmt, category):
    monkeypatch.setattr('console.cpq.export._footer_note', lambda dto: secret)
    response = manager.get(f'/quotes/{token}/{fmt}')
    assert response.status_code == 422 and response.json()['error']['code'] == 'LEAK_BLOCKED'
    assert category in response.json()['error']['details']['categories'] and secret not in response.text
    rows = [r for r in seed.store.audit_log(token) if r['action'] == 'LEAK_BLOCKED']
    assert rows and rows[-1]['actor'] == 'test-manager'
    detail = rows[-1]['detail']
    assert set(detail) == {'export_type','categories','offsets'}
    assert category in detail['categories'] and secret not in json.dumps(detail, ensure_ascii=False)
    assert all(set(v) == {'category','offset'} and type(v['offset']) is int for v in detail['offsets'])

def test_breaker_trips_on_injected_supplier(manager, monkeypatch, seeded_all, a_token):
    name = seeded_all.store.offers(seeded_all.model_ids[0], seeded_all.today)[0]['supplier_name']
    injected(manager, monkeypatch, seeded_all, a_token, name, 'customer.html', 'supplier_name')

def test_breaker_trips_on_cny_marker(manager, monkeypatch, seeded_all, a_token):
    injected(manager, monkeypatch, seeded_all, a_token, 'Approx. ¥1,200', 'whatsapp.txt', 'cny_marker')

def test_breaker_trips_on_exw_value(manager, monkeypatch, seeded_all, a_token):
    value = next(r['exw_cny'] for r in seeded_all.store.offers(seeded_all.model_ids[0], seeded_all.today) if r['exw_cny'] is not None)
    injected(manager, monkeypatch, seeded_all, a_token, f'USD {value:.2f}', 'customer.pdf', 'cost_value')

def test_breaker_trips_on_legacy_media_path(manager, monkeypatch, seeded_all, a_token):
    injected(manager, monkeypatch, seeded_all, a_token, '/media/x/y.png', 'customer.html', 'internal_key')

def test_pdf_metadata_clean(manager, seeded_all, a_token):
    response = manager.get(f'/quotes/{a_token}/customer.pdf')
    assert response.status_code == 200
    meta, dto = PdfReader(BytesIO(response.content)).metadata, dto_for(seeded_all, a_token)
    assert meta['/Title'] == dto['quote_no']
    assert all(meta[k] == (dto['seller']['company'].strip() or 'XL Lighting') for k in ('/Author','/Creator','/Producer'))
    text = '\n'.join(str(v) for v in meta.values())
    assert 'weasyprint' not in text.lower() and independent_scan(text, terms_for(seeded_all, seeded_all.model_ids[0])) == []

def test_zip_images_no_exif_and_safe_names(manager, seeded_all, a_token):
    response = manager.get(f'/quotes/{a_token}/images.zip')
    assert response.status_code == 200 and response.headers['content-type'] == 'application/zip'
    number = dto_for(seeded_all, a_token)['quote_no']
    assert response.headers['content-disposition'] == f'attachment; filename="{number}-images.zip"'
    with ZipFile(BytesIO(response.content)) as archive:
        assert archive.namelist() and len(archive.namelist()) == len(set(archive.namelist()))
        for name in archive.namelist():
            assert re.fullmatch(r'\d\d_[A-Za-z0-9-]+_(main|detail-\d)\.jpg', name)
            with Image.open(BytesIO(archive.read(name))) as image:
                image.load()
                assert image.format == 'JPEG' and image.mode == 'RGB' and max(image.size) <= 1600
                assert not image.getexif() and not {'exif','icc_profile','xmp','comment'} & set(image.info)

def test_unsafe_flagged_image_excluded(admin, manager, seeded_all, app, monkeypatch):
    candidates = [(mid, seeded_all.repository.images_for(mid, customer_safe_only=True)) for mid in seeded_all.model_ids]
    candidates = [(mid, images) for mid, images in candidates if len(images) >= 2]
    assert candidates, 'A real model with two registered safe images is required'
    mid, images = candidates[0]
    token = freeze_quote_for(manager, seeded_all, mid)
    uid = images[0]['asset_uid']
    assert admin.post(f'/api/cpq/assets/{uid}/flag', json={'customer_safe':False,'reason':'Synthetic exclusion test'}).status_code == 200
    api, calls = app.state.export_api, set()
    original = api.media.render
    def track(image_uid, asset, width):
        calls.add(image_uid)
        return original(image_uid, asset, width)
    monkeypatch.setattr(api.media, 'render', track)
    dto = exports.load_customer_dto(json.dumps(dto_for(seeded_all, token)))
    prepared = exports.prepare_images(dto, api.resolver())
    assert uid not in prepared[0]
    expected = exports.photos(dto.lines[0], prepared)
    response = manager.get(f'/quotes/{token}/images.zip')
    assert response.status_code == 200
    with ZipFile(BytesIO(response.content)) as archive:
        assert [archive.read(name) for name in archive.namelist()] == expected
    response = manager.get(f'/quotes/{token}/customer.html')
    assert response.status_code == 200
    parser = HTMLText()
    parser.feed(response.text)
    decoded = [base64.b64decode(v.split(',',1)[1]) for v in parser.images]
    assert all(data in decoded for data in expected) and uid not in calls
    second = freeze_quote_for(manager, seeded_all, mid)
    assert uid not in dto_for(seeded_all, second)['lines'][0]['image_refs']
    for image in images[1:]:
        assert admin.post(f"/api/cpq/assets/{image['asset_uid']}/flag", json={'customer_safe':False,'reason':'Synthetic exclusion test'}).status_code == 200
    response = manager.post('/api/cpq/quotes', json=ready(manager, seeded_all, mid))
    assert response.status_code == 409 and response.json()['error']['code'] == 'IMAGE_MISSING'

def test_export_code_never_reads_internal_snapshot():
    assert all('internal_snapshot' not in inspect.getsource(module) for module in (exports, routes_module))

def test_no_send_capability():
    root = Path(__file__).resolve().parents[2] / 'src/console'
    for directory in (root / 'cpq', root / 'routes'):
        assert directory.is_dir()
        for path in directory.rglob('*.py'):
            source = path.read_text(encoding='utf-8').lower()
            assert not any(v in source for v in ('smtplib','sendmail','graph.facebook','requests.post(','httpx.post(')), path
