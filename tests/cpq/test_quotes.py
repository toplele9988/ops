'''Freeze tests use existing per-test database-copy fixtures; no new fixtures.'''
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from threading import Barrier
import inspect
import json
import re
import sqlite3
import pytest
import console.cpq.quotes as module
from console.cpq.quotes import QuoteService
from console.security.media import make_asset_uid


def _images(app,monkeypatch):
    # Synthetic metadata only; this batch does not decode or export images.
    repository = app.state.cpq_service.repository
    def images(mid,*,customer_safe_only=False):
        return [{'asset_uid':make_asset_uid(repository.media_uid_secret,'quote-test-not-for-prod:'+mid),'customer_safe':True}]
    monkeypatch.setattr(repository,'images_for',images)


def _payload(client,source):
    response = client.post('/api/cpq/calculate',json=source)
    assert response.status_code==200,response.text
    return deepcopy(source)|{'payment_terms_id':'TT_30_70','acks':response.json()['gate']['requires_ack']}


def _freeze(client,payload):
    response = client.post('/api/cpq/quotes',json=payload)
    assert response.status_code==201,response.text
    return response.json()


def _row(seeded,token):
    return seeded.store.read('SELECT * FROM cpq_quote WHERE quote_token=?',(token,))[0]


def test_quote_no_unique_under_concurrency(sales_cost,seeded,app,monkeypatch):
    _images(app,monkeypatch)
    payload,barrier = _payload(sales_cost,seeded.payload),Barrier(20)
    def freeze(_):
        barrier.wait(timeout=30)
        return _freeze(sales_cost,payload)
    with ThreadPoolExecutor(max_workers=20) as pool:
        results = list(pool.map(freeze,range(20)))
    assert len({r['quote_no'] for r in results})==20
    assert len({r['quote_token'] for r in results})==20
    assert sorted(int(r['quote_no'].split('-')[1]) for r in results)==list(range(1,21))
    assert all(r['quote_no'].startswith(f'Q{seeded.today:%y%m%d}-') for r in results)
    assert seeded.store.read('SELECT COUNT(*) AS n FROM cpq_quote')[0]['n']==20
    assert seeded.store.read('SELECT COUNT(*) AS n FROM cpq_public_sku')[0]['n']==1


def test_server_recomputes_on_freeze(sales_cost,admin,seeded,app,monkeypatch):
    _images(app,monkeypatch)
    payload = _payload(sales_cost,seeded.payload)
    for field in ('result','totals','line_amount'):
        bad = deepcopy(payload)
        if field=='line_amount':
            bad['lines'][0]['amount_usd']=0
        else:
            bad[field]={'price_usd':0}
        response = sales_cost.post('/api/cpq/quotes',json=bad)
        assert response.status_code==400,response.text
        assert response.json()['error']['code']=='CLIENT_RESULT_REJECTED'
    assert seeded.store.read('SELECT COUNT(*) AS n FROM cpq_quote')[0]['n']==0
    calculation = {k:v for k,v in payload.items() if k in module.CALC_FIELDS}
    expected = sales_cost.post('/api/cpq/calculate',json=calculation).json()
    quote = _freeze(sales_cost,payload)
    response = admin.get('/api/cpq/quotes/'+quote['quote_token'])
    assert response.status_code==200,response.text
    assert response.json()['result']==expected
    row = _row(seeded,quote['quote_token'])
    assert json.loads(row['internal_snapshot_json'])['result']==expected
    assert 'cost_usd' not in json.loads(row['result_json'])['totals']
    assert json.loads(row['customer_dto_json'])['totals']['total_amount']==expected['totals']['price_usd']


def test_draft_has_no_exports(sales,seeded,freight_heavy_payload,app,monkeypatch):
    monkeypatch.setattr(app.state.cpq_service.repository,'images_for',lambda *a,**k:[])
    quote = _freeze(sales,deepcopy(freight_heavy_payload)|{'target_margin':0.10,'draft':True,'acks':[]})
    assert quote['status']=='draft' and quote['exports'] is None
    assert not quote['gate']['can_export'] and quote['gate']['requires_override']
    assert 'FREIGHT_EXCEEDS_GOODS' in quote['gate']['requires_ack']
    assert _row(seeded,quote['quote_token'])['customer_dto_json'] is None
    assert [r['action'] for r in seeded.store.audit_log(quote['quote_token'])]==['QUOTE_FROZEN']


def test_final_has_exports_links(sales,seeded,app,monkeypatch):
    _images(app,monkeypatch)
    quote = _freeze(sales,_payload(sales,seeded.payload))
    token = quote['quote_token']
    assert re.fullmatch(r'q_[0-9a-f]{32}',token)
    assert re.fullmatch(r'Q[0-9]{6}-[0-9]{3,}',quote['quote_no'])
    assert quote['status']=='final' and quote['gate']['can_export']
    assert quote['exports']=={key:f'/quotes/{token}/{name}' for key,name in (
        ('pdf','customer.pdf'),('html','customer.html'),('whatsapp','whatsapp.txt'),('images_zip','images.zip'))}
    assert _row(seeded,token)['customer_dto_json'] is not None


def test_public_sku_persisted_and_stable(sales_cost,seeded,app,monkeypatch):
    _images(app,monkeypatch)
    payload = _payload(sales_cost,seeded.payload)
    first = _freeze(sales_cost,payload)
    sku = json.loads(_row(seeded,first['quote_token'])['customer_dto_json'])['lines'][0]['sku']
    assert seeded.store.public_sku(seeded.model_ids[0])==sku
    second = _freeze(sales_cost,payload)
    assert json.loads(_row(seeded,second['quote_token'])['customer_dto_json'])['lines'][0]['sku']==sku
    assert seeded.store.read('SELECT COUNT(*) AS n FROM cpq_public_sku')[0]['n']==1


def test_internal_snapshot_requires_admin(sales_cost,manager,admin,seeded,app,monkeypatch):
    _images(app,monkeypatch)
    quote = _freeze(sales_cost,_payload(sales_cost,seeded.payload))
    path = '/api/cpq/quotes/'+quote['quote_token']
    assert manager.get(path).status_code==403
    response = admin.get(path)
    assert response.status_code==200,response.text
    assert 'supplier' in json.dumps(response.json()['internal_snapshot']).lower()
    assert response.json()['created_by']=='test-sales_cost'


def test_get_public_row_never_selects_internal_snapshot(sales_cost,seeded,app,monkeypatch):
    source = inspect.getsource(QuoteService.get_public_row)
    assert 'internal_snapshot' not in source and 'SELECT *' not in source.upper()
    _images(app,monkeypatch)
    quote = _freeze(sales_cost,_payload(sales_cost,seeded.payload))
    api = next(r for r in app.routes if r.name=='cpq_quotes').endpoint.__self__
    assert set(api.quotes.get_public_row(quote['quote_token']))=={'status','customer_dto_json','quote_no','valid_until'}


def test_audit_rows_have_no_cost_values(sales_cost,seeded,app,monkeypatch):
    _images(app,monkeypatch)
    _freeze(sales_cost,_payload(sales_cost,seeded.payload))
    rows = seeded.store.read('SELECT * FROM cpq_audit_log')
    assert any(r['action']=='QUOTE_FROZEN' for r in rows)
    text = json.dumps(rows,ensure_ascii=False)
    for offer in seeded.store.offers(seeded.model_ids[0],seeded.today):
        if offer['exw_cny'] is not None:
            assert str(offer['exw_cny']) not in text
            assert format(offer['exw_cny'],'.2f') not in text
        assert offer['supplier_name'] not in text
    assert all(json.loads(r['detail_json'])=={'status':'final'} for r in rows)


def test_leak_blocked_on_freeze(sales_cost,seeded,app,monkeypatch):
    _images(app,monkeypatch)
    supplier = seeded.store.offers(seeded.model_ids[0],seeded.today)[0]['supplier_name']
    original = module.build_customer_dto
    def contaminated(snapshot):
        dto = original(snapshot)
        return replace(dto,seller=replace(dto.seller,company=supplier))
    monkeypatch.setattr(module,'build_customer_dto',contaminated)
    before = seeded.store.read('SELECT COUNT(*) AS n FROM cpq_quote')[0]['n']
    response = sales_cost.post('/api/cpq/quotes',json=_payload(sales_cost,seeded.payload))
    assert response.status_code==422,response.text
    assert response.json()['error']['code']=='LEAK_BLOCKED'
    assert 'supplier_name' in response.json()['error']['details']['categories']
    assert supplier not in response.text
    assert seeded.store.read('SELECT COUNT(*) AS n FROM cpq_quote')[0]['n']==before
    assert seeded.store.read('SELECT COUNT(*) AS n FROM cpq_public_sku')[0]['n']==0
    audits = seeded.store.read('SELECT * FROM cpq_audit_log')
    assert [r['action'] for r in audits]==['LEAK_BLOCKED']
    assert supplier not in json.dumps(audits,ensure_ascii=False)
    assert set(json.loads(audits[0]['detail_json']))=={'categories'}


@pytest.mark.parametrize('earliest',['pricing','lane','offer','null_offer'])
def test_valid_until_is_minimum(sales_cost,seeded,app,monkeypatch,earliest):
    _images(app,monkeypatch)
    window = app.state.cpq_service.config['pricing']['quote_validity_days']
    assert window>2
    lane_days,offer_days = {'pricing':(window+1,window+2),'lane':(window-2,window+1),'offer':(window+1,window-2),'null_offer':(window+1,None)}[earliest]
    lane_until = seeded.today+timedelta(days=lane_days)
    offer_until = None if offer_days is None else (seeded.today+timedelta(days=offer_days)).isoformat()
    seeded.store.put_lane(replace(seeded.store.lane('TEST-SEA-LCL'),valid_until=lane_until))
    with sqlite3.connect(seeded.settings.db_path) as conn:
        for smid in seeded.supplier_models[seeded.model_ids[0]]:
            conn.execute('UPDATE cat_supplier_offer SET valid_until=? WHERE supplier_model_id=?',(offer_until,smid))
    quote = _freeze(sales_cost,_payload(sales_cost,seeded.payload))
    dates = [(seeded.today+timedelta(days=window)).isoformat(),lane_until.isoformat()]
    if offer_until:
        dates.append(offer_until)
    assert quote['valid_until']==min(dates)
    assert _row(seeded,quote['quote_token'])['valid_until']==min(dates)
