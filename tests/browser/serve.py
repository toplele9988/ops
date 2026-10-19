"""Throwaway browser-test server: synthetic fx/lanes/packing on a COPY of the catalog snapshot."""
import os, shutil, sqlite3, sys, tempfile
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import uvicorn
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.routing import Mount
from starlette.staticfiles import StaticFiles
from console.cpq.models import Lane, ExpressBand
from console.cpq.store import CPQStore
from console.cpq.workdb import open_work, ensure_schema
from console.routes.strobe_routes import Settings, build_routes
from console.strobe.repository import STROBES_SQL, SUPPLIER_LEGAL_NAME

CSP = "default-src 'self'; img-src 'self' data:; script-src 'self'; style-src 'self'; frame-ancestors 'none'"
ROOT = Path(os.environ['SERVE_SRC'])
tmp = Path(tempfile.mkdtemp())
cat = tmp / 'catalog.sqlite3'
shutil.copy(os.environ['XL_DB_PATH'], cat)
work = tmp / 'work.sqlite3'
c = open_work(str(work)); ensure_schema(c); c.close()
today = date.today(); epoch = int(datetime.combine(today, datetime.min.time(), tzinfo=timezone.utc).timestamp())
conn = sqlite3.connect(cat); conn.row_factory = sqlite3.Row
with conn:
    for (t,) in conn.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name IN ('cat_supplier_offer','cat_fx_rate','cat_packing')").fetchall():
        conn.execute(f'DROP TRIGGER "{t}"')
    rows = conn.execute(STROBES_SQL, {'supplier': SUPPLIER_LEGAL_NAME, 'today': today.isoformat()}).fetchall()
    conn.execute('INSERT INTO cat_fx_rate(fx_id,cny_per_usd_micro,source,as_of,created_at) VALUES (1,7120000,"synthetic-browser-test",?,?)', (epoch, epoch))
    pid = 0
    for row in rows[:6]:
        ids = [r[0] for r in conn.execute('SELECT sm.supplier_model_id FROM cat_supplier_model sm JOIN cat_supplier s ON s.supplier_id=sm.supplier_id WHERE sm.factory_model_code=? AND s.legal_name=?', (row['model'], row['supplier']))]
        for mode, dims in [('carton', (600, 400, 300, 12000)), ('flightcase', (700, 500, 400, 20000))]:
            pid += 1
            conn.execute('INSERT INTO cat_packing(packing_id,supplier_model_id,packing,units_per_pack,l_mm,w_mm,h_mm,gross_g,created_at) VALUES (?,?,?,?,?,?,?,?,?)', (pid, ids[0], mode, 2, *dims, epoch))
        conn.execute('UPDATE cat_supplier_offer SET fc_upgrade_rmb_fen=18000, fc_units_per_case=2 WHERE supplier_model_id=?', (ids[0],))
conn.close()
settings = Settings(cat, Path(os.environ['MEDIA_ROOT']), tmp / 'cache', work, 'test-secret-not-for-prod', True)
store = CPQStore(settings.db_path, settings.work_db)
store.add_fx(7.12, today, 'synthetic-browser-test')
lane = Lane('TEST-SEA-LCL', 'sea', today + timedelta(days=30), inland_cny_per_cbm=70, inland_min_cny=70, origin_fixed_usd=30, lcl_rate_usd_per_wm=20, fcl_rate_usd={'20GP': 400, '40GP': 600, '40HQ': 700}, fcl_origin_fixed_usd_per_container=20, fcl_dest_fixed_usd_per_container=30, dest_fixed_usd=40, last_mile_usd=20)
for item in (lane, replace(lane, lane_id='TEST-FAR-DDP', lcl_rate_usd_per_wm=6000, dest_fixed_usd=1200), Lane('TEST-EXP', 'express', today + timedelta(days=30), volumetric_divisor=5000, increment_kg=0.5, bands=tuple(ExpressBand(b, r) for b, r in ((21, 5), (45, 4), (100, 3), (99999, 2))), min_charge_usd=10, ddp_fee_usd=20)):
    store.put_lane(item)
for aid, cost in [('HOOK-QR', 6.5), ('SAFETY-CABLE', 2)]:
    store.put_accessory({'accessory_id': aid, 'name_en': 'Synthetic ' + aid, 'cost_cny': cost})

class Csp:
    def __init__(self, app): self.app = app
    async def __call__(self, scope, receive, send):
        async def s(m):
            if m['type'] == 'http.response.start':
                m['headers'] = list(m.get('headers', [])) + [(b'content-security-policy', CSP.encode())]
            await send(m)
        await self.app(scope, receive, s)

app = Starlette(routes=build_routes(settings) + [Mount('/static', StaticFiles(directory=ROOT / 'static'))], middleware=[Middleware(Csp)])
print('READY', flush=True)
uvicorn.run(app, host='127.0.0.1', port=int(os.environ.get('PORT', '8799')), log_level='warning')
