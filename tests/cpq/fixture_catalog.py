"""Create a synthetic-only fixture; never opens a production catalog."""
from datetime import date
from pathlib import Path
import argparse, json, sqlite3
from PIL import Image


def create_catalog(root, supplier='Synthetic Fixture Supplier'):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    path, media = root / 'catalog.sqlite3', root / 'media'
    if path.exists() or media.exists():
        raise FileExistsError('synthetic_fixture_destination_must_be_empty')
    media.mkdir()
    with sqlite3.connect(path) as db:
        db.executescript('''
        CREATE TABLE equipment_specs (equipment_id TEXT PRIMARY KEY,supplier TEXT,model TEXT,fixture_type TEXT,wattage REAL,ip_rating TEXT,extraction_snapshot_json TEXT);
        CREATE TABLE cat_supplier (supplier_id TEXT PRIMARY KEY,legal_name TEXT);
        CREATE TABLE cat_supplier_model (supplier_model_id TEXT PRIMARY KEY,supplier_id TEXT,factory_model_code TEXT);
        CREATE TABLE cat_supplier_offer (offer_id TEXT PRIMARY KEY,supplier_id TEXT,supplier_model_id TEXT,quoted_on TEXT,valid_until TEXT,raw_cost_rmb_fen INTEGER,tax_basis TEXT,includes_flightcase INTEGER,fc_upgrade_rmb_fen INTEGER,fc_units_per_case INTEGER);
        CREATE TABLE cat_review (review_id INTEGER PRIMARY KEY,subject_kind TEXT,subject_id TEXT,decision TEXT);
        CREATE TABLE cat_fx_rate (fx_id INTEGER PRIMARY KEY,cny_per_usd_micro INTEGER,source TEXT,as_of INTEGER,created_at INTEGER);
        CREATE TABLE cat_packing (packing_id INTEGER PRIMARY KEY,supplier_model_id TEXT,packing TEXT,units_per_pack INTEGER,l_mm INTEGER,w_mm INTEGER,h_mm INTEGER,gross_g INTEGER,created_at INTEGER);
        CREATE TABLE multimedia_assets (asset_id TEXT PRIMARY KEY,nas_rel_path TEXT,source_type TEXT,extraction_snapshot_json TEXT);
        ''')
        db.execute('INSERT INTO cat_supplier VALUES (?,?)', ('synthetic-supplier',supplier))
        for i in range(1,31):
            smid, model = f'supplier-model:fixture-{i}', f'fixture-model-{i}'
            db.execute('INSERT INTO cat_supplier_model VALUES(?,?,?)', (smid,'synthetic-supplier',model))
            if i > 3: continue
            mid, oid = f'equipment:fixture-{i}', f'fixture-offer-{i}'
            db.execute('INSERT INTO equipment_specs VALUES(?,?,?,?,?,?,?)', (mid,supplier,model,'strobe',100+i,'IP65','{}'))
            db.execute('INSERT INTO cat_supplier_offer VALUES(?,?,?,?,?,?,?,?,?,?)', (oid,'synthetic-supplier',smid,date.today().isoformat(),None,100000,None,0,18000,2))
            db.execute('INSERT INTO cat_review VALUES(?,?,?,?)', (i,'offer',oid,'accept'))
            name=f'fixture-{i}.png'
            Image.new('RGB',(500,100),(i*30,40,80)).save(media/name)
            db.execute('INSERT INTO multimedia_assets VALUES(?,?,?,?)', (f'asset:fixture-{i}',name,'supplier_image',json.dumps({'equipment_id':mid,'local_path':str(media/name)})))
    return path

if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--directory',required=True)
    args=parser.parse_args()
    print(create_catalog(args.directory))
