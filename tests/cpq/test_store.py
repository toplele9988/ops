from datetime import timedelta
import sqlite3
import pytest
from console.cpq.seed import seed_work_db
from console.strobe.repository import open_catalog


def test_fx_latest_and_fallback(seeded):
    with sqlite3.connect(seeded.settings.db_path) as conn:
        for row in [(2,8000000,seeded.epoch),(3,9000000,seeded.epoch-1)]:
            conn.execute('INSERT INTO cat_fx_rate(fx_id,cny_per_usd_micro,source,as_of,created_at) VALUES (?,?,?,?,?)',(row[0],row[1],'synthetic-test',row[2],seeded.epoch))
    assert seeded.store.fx(seeded.today)['usd_cny']==8
    assert seeded.store.fx(seeded.today+timedelta(days=3))['age_days']==3
    seeded.store.add_fx(9,seeded.today)
    assert seeded.store.fx(seeded.today)['usd_cny']==8
    with sqlite3.connect(seeded.settings.db_path) as conn:
        conn.execute('DELETE FROM cat_fx_rate')
    assert seeded.store.fx(seeded.today)['usd_cny']==9
    with sqlite3.connect(seeded.settings.work_db) as conn:
        conn.execute('DELETE FROM cpq_fx_rate')
    assert seeded.store.fx(seeded.today) is None


def test_packing_conversion_and_latest(seeded):
    mid = seeded.model_ids[0]
    profiles = seeded.store.packing(mid)
    assert profiles['carton']['l_cm']==60 and profiles['carton']['gw_kg']==12
    assert profiles['flight_case']['l_cm']==70 and profiles['flight_case']['gw_kg']==20
    assert 'flightcase' not in profiles
    with sqlite3.connect(seeded.settings.db_path) as conn:
        conn.execute('INSERT INTO cat_packing(packing_id,supplier_model_id,packing,units_per_pack,l_mm,w_mm,h_mm,gross_g,created_at) VALUES (?,?,?,?,?,?,?,?,?)',(999,seeded.supplier_models[mid][0],'carton',2,900,400,300,13000,seeded.epoch))
    assert seeded.store.packing(mid)['carton']['l_cm']==90


def test_work_packing_override_wins(seeded):
    mid = seeded.model_ids[0]
    seeded.store.put_packing(mid,'carton',dict(seeded.store.packing(mid)['carton'],l_cm=111))
    assert seeded.store.packing(mid)['carton']['l_cm']==111
    with open_catalog(seeded.settings.db_path) as conn:
        assert conn.execute('SELECT MAX(l_mm) FROM cat_packing WHERE packing=?',('carton',)).fetchone()[0]==600
        with pytest.raises(sqlite3.OperationalError):
            conn.execute('DELETE FROM cat_fx_rate')


def test_case_cost_source_order(seeded):
    mid = seeded.model_ids[0]
    offers = seeded.store.offers(mid,seeded.today)
    assert seeded.store.case_cost(mid,offers)==180
    profile = dict(seeded.store.packing(mid)['flight_case'])
    for value,expected in ((250,250),(0,0),(None,180)):
        profile['case_cost_cny']=value
        seeded.store.put_packing(mid,'flight_case',profile)
        assert seeded.store.case_cost(mid,offers)==expected
    assert seeded.store.case_cost(mid,[]) is None


def test_work_annotations(seeded):
    store,mid = seeded.store,seeded.model_ids[0]
    store.put_public_sku(mid,'TEST-SKU')
    assert store.public_sku(mid)=='TEST-SKU'
    store.put_asset_flag('synthetic-asset',False,reason='test',actor='test')
    assert store.asset_flags()['synthetic-asset'] is False
    record = store.append_audit('test',actor='test',quote_token='test-token',detail={'model_id':mid})
    rows = store.audit_log('test-token')
    assert rows[0]['id']==record and rows[0]['detail']=={'model_id':mid}


def test_seed_idempotent_and_warns(work_db,capsys):
    for _ in range(2):
        result = seed_work_db(work_db,fx=7.12,as_of='2026-10-19')
        assert result['unpriced_lanes']==['SEA-DEHAM','EXP-DHL-DE']
    assert capsys.readouterr().err.count('WARNING:')==4
    with sqlite3.connect(work_db) as conn:
        assert conn.execute('SELECT COUNT(*) FROM cpq_fx_rate').fetchone()[0]==1
        assert conn.execute('SELECT COUNT(*) FROM cpq_freight_lane').fetchone()[0]==2
        assert conn.execute('SELECT COUNT(*) FROM cpq_accessory').fetchone()[0]==3
