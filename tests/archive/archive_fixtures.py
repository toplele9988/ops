"""Fictional fixtures; source columns match the inspected catalog contract."""
import json
from pathlib import Path
import sqlite3

from console.supplier_data.report import build_report

SUPPLIER = '广州虚构星岚舞台灯光设备有限公司（舞台灯光）'


def report_product(eid, model, text='', supplier=SUPPLIER):
    return dict(equipment_id=eid, supplier=supplier, model=model, fixture_type='beam', wattage=100,
                asset_id='fake-asset-' + eid, source_file='虚构参数表.xlsx', source_type='fictional',
                chunks=[dict(chunk_id='fake-chunk-' + eid, text=text)])


def report(path, entries=None):
    if entries is None:
        entries = [
            report_product('spec-a', 'FA-10', '外箱尺寸 50x40x30 cm，毛重 12 kg，每箱数量 2 台'),
            report_product('pack-b', 'FB-20', '外箱尺寸 60x40x30 cm，毛重 15 kg，每箱数量 1 台'),
            report_product('pack-conflict', 'FC-30', '外箱尺寸 50x40x30 cm，外箱尺寸 80x40x30 cm，毛重 12 kg'),
            report_product('fragment', '6.光束角度：120°', '外箱尺寸 50x40x30 cm，毛重 12 kg'),
            report_product('missing', 'FD-40'),
            report_product('unknown', 'FE-50', supplier='未注明供应商'),
        ]
    value = build_report(entries)
    Path(path).write_text(json.dumps(value, ensure_ascii=False), encoding='utf8')
    return value


def catalog(path):
    with sqlite3.connect(path) as db:
        db.executescript('''
        CREATE TABLE cat_supplier(supplier_id TEXT PRIMARY KEY,legal_name TEXT,created_at INTEGER);
        CREATE TABLE cat_supplier_model(supplier_model_id INTEGER PRIMARY KEY,supplier_id TEXT,factory_model_code TEXT,created_at INTEGER);
        CREATE TABLE cat_supplier_offer(offer_id INTEGER PRIMARY KEY,supplier_id TEXT,supplier_model_id INTEGER,
          quoted_on TEXT,valid_until TEXT,raw_cost_rmb_fen INTEGER,tax_basis TEXT,includes_flightcase INTEGER,
          fc_units_per_case INTEGER,fc_upgrade_rmb_fen INTEGER,moq_units INTEGER,lead_days_min INTEGER,
          lead_days_max INTEGER,branding TEXT,corrects_offer_id INTEGER,source_kind TEXT,source_ref TEXT,
          evidence TEXT,extractor TEXT,created_at INTEGER);
        CREATE TABLE equipment_specs(equipment_id TEXT PRIMARY KEY,asset_id TEXT,supplier TEXT,model TEXT,
          fixture_type TEXT,wattage REAL,exw_price REAL,description TEXT);
        CREATE TABLE multimedia_assets(asset_id TEXT PRIMARY KEY,filename TEXT,source_type TEXT);
        CREATE TABLE channel_role_registry(classification_id TEXT,channel_kind TEXT,source_scope TEXT,
          display_name TEXT,channel_role TEXT,observed_at REAL);
        ''')
        db.execute('INSERT INTO cat_supplier VALUES (?,?,?)', ('fake-supplier-a', SUPPLIER, 0))
        db.execute('INSERT INTO cat_supplier_model VALUES (1,?,?,0)', ('fake-supplier-a', 'FA-10'))
        db.execute('INSERT INTO cat_supplier_offer(offer_id,supplier_id,supplier_model_id,quoted_on,raw_cost_rmb_fen,tax_basis) VALUES (1,?,1,?,?,?)',
                   ('fake-supplier-a', '2030-01-01', 1234, '虚构含税'))
        db.execute('INSERT INTO equipment_specs VALUES (?,?,?,?,?,?,?,?)',
                   ('spec-a', 'fake-asset-spec-a', SUPPLIER, 'fa_10', 'beam', 100, 99.99, '虚构正文不能复制'))
        db.execute('INSERT INTO equipment_specs VALUES (?,?,?,?,?,?,?,?)',
                   ('spec-z', 'fake-asset-z', SUPPLIER, 'FZ-90', 'wash', 50, None, '虚构正文不能复制'))
        db.executemany('INSERT INTO multimedia_assets VALUES (?,?,?)', [
            ('fake-asset-spec-a', '/fictional/private/虚构参数表.xlsx', 'fictional'),
            ('fake-asset-z', '虚构产品.pdf', 'fictional')])
        db.executemany('INSERT INTO channel_role_registry VALUES (?,?,?,?,?,?)', [
            ('fake-reg-a', 'group', 'group:fake-a', '虚构星岚供应商群', 'CHANNEL_ROLE_SUPPLIER', 1),
            ('fake-reg-b', 'group', 'group:fake-b', '虚构星岚客户群', 'CHANNEL_ROLE_CUSTOMER', 1)])


def offer(identity=1, **values):
    return dict(id=identity, product_id=1, price_fen=1234, currency='CNY', unit='台',
                **({'status': 'confirmed', 'quoted_on': '2030-01-01', 'valid_until': '2030-02-01',
                    'corrects_offer_id': None} | values))
