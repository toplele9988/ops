"""A1/A2a fixtures and synthetic data confined to per-test catalog copies."""
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
from datetime import date,datetime,timedelta,timezone
from pathlib import Path
from types import SimpleNamespace
import os
import sqlite3
import sys

TEST_MODE = os.environ.get("CPQ_TEST_MODE")
PDF_MODE = os.environ.get("CPQ_TEST_PDF", "real")
if TEST_MODE not in ("synthetic", "existing"):
    raise RuntimeError("Set CPQ_TEST_MODE=synthetic or CPQ_TEST_MODE=existing explicitly")
if PDF_MODE not in ("real", "skip"):
    raise RuntimeError("Set CPQ_TEST_PDF to real or skip")
if PDF_MODE == "skip":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "support"))
import pytest
from PIL import Image
from starlette.applications import Starlette
from starlette.testclient import TestClient
from console.cpq.models import GateContext,Lane,ExpressBand
from console.cpq.store import CPQStore
from console.cpq.workdb import open_work,ensure_schema
from console.routes.strobe_routes import Settings,build_routes
from console.security.media import MediaAsset,make_asset_uid
from console.strobe.repository import STROBES_SQL
import console.strobe.repository as _strobe_repository
import console.cpq.store as _cpq_store

# Synthetic tests only: isolate supplier identity without changing production code.
if TEST_MODE == 'synthetic' or os.environ.get('CPQ_TEST_FIXTURE_SUPPLIER') == '1':
    SUPPLIER_LEGAL_NAME = 'Synthetic Fixture Supplier'
    _strobe_repository.SUPPLIER_LEGAL_NAME = SUPPLIER_LEGAL_NAME
    _cpq_store.SUPPLIER_LEGAL_NAME = SUPPLIER_LEGAL_NAME
else:
    SUPPLIER_LEGAL_NAME = _strobe_repository.SUPPLIER_LEGAL_NAME


@pytest.fixture
def engine_ctx():
    return GateContext(images_ok={'MODEL-1':True})


def backup(source,destination):
    src = sqlite3.connect(Path(source).expanduser().absolute().as_uri()+'?mode=ro',uri=True)
    dst = sqlite3.connect(destination)
    try:
        src.execute('PRAGMA query_only=ON')
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    return destination


@pytest.fixture(scope='session')
def snapshot_db(tmp_path_factory):
    if TEST_MODE == 'existing':
        source = os.environ.get('XL_DB_PATH')
        if not source or not os.environ.get('MEDIA_ROOT'):
            raise RuntimeError('existing mode requires XL_DB_PATH and MEDIA_ROOT')
        return backup(source,tmp_path_factory.mktemp('cpq-explicit-existing')/'catalog.sqlite3')
    from tests.cpq.fixture_catalog import create_catalog
    return create_catalog(tmp_path_factory.mktemp('synthetic-cpq-catalog'),SUPPLIER_LEGAL_NAME)


@pytest.fixture
def catalog_copy(snapshot_db,tmp_path):
    return backup(snapshot_db,tmp_path/'catalog-test.sqlite3')


@pytest.fixture
def work_db(tmp_path):
    path = tmp_path/'cpq_work.sqlite3'
    conn = open_work(path)
    try:
        ensure_schema(conn)
    finally:
        conn.close()
    return path


@pytest.fixture
def settings(catalog_copy,work_db,tmp_path,snapshot_db):
    media_root = snapshot_db.parent/'media' if TEST_MODE == 'synthetic' else Path(os.environ['MEDIA_ROOT'])
    return Settings(catalog_copy,media_root,tmp_path/'media-cache',work_db,'test-secret-not-for-prod',True)


@pytest.fixture
def app(settings):
    routes = build_routes(settings)
    result = Starlette(routes=routes)
    result.state.strobe_api = routes[0].endpoint.__self__
    if TEST_MODE == 'synthetic':
        from jinja2 import ChoiceLoader, FileSystemLoader
        templates = Path(__file__).resolve().parents[1]/'support'/'templates'
        result.state.strobe_api.templates.loader = ChoiceLoader([FileSystemLoader(templates), result.state.strobe_api.templates.loader])
    result.state.strobe_repository = result.state.strobe_api.repository
    result.state.cpq_service = next(r for r in routes if r.name=='cpq_context').endpoint.__self__.service
    return result


@contextmanager
def _client(application,role=None):
    headers = {} if role is None else {'X-Test-User':f'test-{role}','X-Test-Roles':role}
    with TestClient(application,headers=headers) as client:
        yield client


def role_fixture(role):
    @pytest.fixture(name=role or 'anon')
    def fixture(app):
        with _client(app,role) as client:
            yield client
    return fixture


anon = role_fixture(None)
viewer = role_fixture('viewer')
sales = role_fixture('sales')
sales_cost = role_fixture('sales_cost')
manager = role_fixture('manager')
admin = role_fixture('admin')


@pytest.fixture
def a_valid_uid(viewer):
    response = viewer.get('/api/strobes')
    assert response.status_code==200,response.text
    images = [im for item in response.json()['items'] for im in item['images']]
    assert images,'Snapshot has no customer-safe registered strobe images'
    return images[0]['asset_uid']


@pytest.fixture
def planted_symlink_asset(app,tmp_path,monkeypatch):
    root = tmp_path/'isolated-media'
    root.mkdir()
    external = tmp_path/'outside.png'
    Image.new('RGB',(8,8),'red').save(external)
    link = root/'escape.png'
    link.symlink_to(external)
    api = app.state.strobe_api
    monkeypatch.setattr(api.media,'root',root)
    repository = api.repository
    repository._refresh_assets()
    asset_id = 'asset:'+'f'*64
    uid = make_asset_uid(repository.media_uid_secret,asset_id)
    monkeypatch.setitem(repository._asset_index,uid,MediaAsset(asset_id,'equipment:test-symlink',str(link)))
    monkeypatch.setattr(repository,'_refresh_assets',lambda:None)
    return uid


@pytest.fixture
def seeded(settings):
    # All prices, rates and physical measurements here are synthetic test data.
    today = date.today()
    epoch = int(datetime.combine(today,datetime.min.time(),tzinfo=timezone.utc).timestamp())
    conn = sqlite3.connect(settings.db_path)
    conn.row_factory = sqlite3.Row
    mids,smids = [],{}
    try:
        with conn:
            rows = conn.execute(STROBES_SQL,{'supplier':SUPPLIER_LEGAL_NAME,'today':today.isoformat()}).fetchall()[:3]
            assert len(rows)==3,'Snapshot needs three accepted strobe models'
            for (trigger,) in conn.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name IN ('cat_supplier_offer','cat_fx_rate','cat_packing')").fetchall():
                conn.execute('DROP TRIGGER "%s"' % trigger.replace('"','""'))  # throwaway per-test copy only
            conn.execute('DELETE FROM cat_fx_rate')
            conn.execute('INSERT INTO cat_fx_rate(fx_id,cny_per_usd_micro,source,as_of,created_at) VALUES (?,?,?,?,?)',(1,7120000,'synthetic-test',epoch,epoch))
            conn.execute('DELETE FROM cat_packing')
            pid = 0
            for row in rows:
                mid = row['equipment_id']
                mids.append(mid)
                ids = [r[0] for r in conn.execute('SELECT sm.supplier_model_id FROM cat_supplier_model sm JOIN cat_supplier s ON s.supplier_id=sm.supplier_id WHERE sm.factory_model_code=? AND s.legal_name=? ORDER BY sm.supplier_model_id',(row['model'],row['supplier']))]
                assert ids
                smids[mid] = ids
                for smid in ids:
                    conn.execute('UPDATE cat_supplier_offer SET raw_cost_rmb_fen=?,fc_upgrade_rmb_fen=?,fc_units_per_case=2,valid_until=? WHERE supplier_model_id=?',(100000,18000,(today+timedelta(days=30)).isoformat(),smid))
                for mode,dims in [('carton',(600,400,300,12000)),('flightcase',(700,500,400,20000))]:
                    pid += 1
                    conn.execute('INSERT INTO cat_packing(packing_id,supplier_model_id,packing,units_per_pack,l_mm,w_mm,h_mm,gross_g,created_at) VALUES (?,?,?,?,?,?,?,?,?)',(pid,ids[0],mode,2,*dims,epoch))
    finally:
        conn.close()
    store = CPQStore(settings.db_path,settings.work_db)
    store.add_fx(7.12,today,'synthetic-test')
    lane = Lane('TEST-SEA-LCL','sea',today+timedelta(days=30),inland_cny_per_cbm=70,inland_min_cny=70,origin_fixed_usd=30,lcl_rate_usd_per_wm=20,fcl_rate_usd={'20GP':400,'40GP':600,'40HQ':700},fcl_origin_fixed_usd_per_container=20,fcl_dest_fixed_usd_per_container=30,dest_fixed_usd=40,last_mile_usd=20)
    for item in (lane,replace(lane,lane_id='TEST-SEA-FCL'),replace(lane,lane_id='TEST-FAR-DDP',lcl_rate_usd_per_wm=6000,dest_fixed_usd=1200),Lane('TEST-EXP','express',today+timedelta(days=30),volumetric_divisor=5000,increment_kg=0.5,bands=tuple(ExpressBand(bound,rate) for bound,rate in ((21,5),(45,4),(100,3),(99999,2))),min_charge_usd=10,ddp_fee_usd=20)):
        store.put_lane(item)
    for aid,cost in [('HOOK-QR',6.5),('SAFETY-CABLE',2),('LINK-CABLE',3)]:
        store.put_accessory({'accessory_id':aid,'name_en':'Synthetic '+aid,'cost_cny':cost})
    payload = {'lines':[{'model_id':mids[0],'qty':40,'packing_mode':'carton','packing_override':None,'accessories':[{'accessory_id':'HOOK-QR','qty_per_unit':2}],'unit_price_usd':None}],'shipping':{'mode':'sea_lcl','incoterm':'DDP','lane_id':'TEST-SEA-LCL'},'target_margin':0.18,'pricing_mode':'margin'}
    return SimpleNamespace(settings=settings,store=store,today=today,epoch=epoch,model_ids=mids,supplier_models=smids,payload=payload)


@pytest.fixture
def freight_heavy_payload(seeded):
    payload = deepcopy(seeded.payload)
    payload['shipping']['lane_id'] = 'TEST-FAR-DDP'
    payload['lines'][0]['qty'] = 2
    return payload


def pytest_collection_modifyitems(items):
    pdf_modules = {'test_exports.py', 'test_export_leaks.py', 'test_letter.py'}
    asset_tests = {'test_no_external_urls_in_templates_and_static', 'test_rendered_page_has_no_external_refs'}
    for item in items:
        if PDF_MODE == 'skip' and item.path.name in pdf_modules:
            item.add_marker(pytest.mark.skip(reason='real PDF renderer is not available; import-only double'))
        if TEST_MODE == 'synthetic' and item.name in asset_tests:
            item.add_marker(pytest.mark.skip(reason='actual vendor assets are not included in synthetic template'))
