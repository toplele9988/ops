"""Fictional dossier fixtures. Every SQLite path and all network I/O are guarded."""
import hashlib
import importlib.util
import json
from pathlib import Path
import socket
import sqlite3
import sys
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / 'src'))
import console
console.__path__.append('/opt/imgw/wechat/console')
sys.dont_write_bytecode = True


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    original = sqlite3.connect
    def connect(path, *a, **kw):
        assert str(path).startswith((str(tmp_path) + '/', tmp_path.as_uri() + '/')), 'only fictional temporary stores allowed'
        return original(path, *a, **kw)
    def blocked(*a, **kw):
        raise AssertionError('dossier tests cannot connect to a network')
    monkeypatch.setattr(sqlite3, 'connect', connect)
    monkeypatch.setattr(socket.socket, 'connect', blocked)
    monkeypatch.setattr(socket, 'create_connection', blocked)


def load_importer():
    # Exact canonical importer, in an independent module namespace.
    spec = importlib.util.spec_from_file_location('dossier_intake_prepare', REPO / 'tools/dossier/prepare.py')
    prepare = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(prepare)
    prior = sys.modules.get('prepare')
    sys.modules['prepare'] = prepare
    try:
        spec = importlib.util.spec_from_file_location('dossier_import', REPO / 'tools/dossier/import_candidate.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        if prior is not None:
            sys.modules['prepare'] = prior
        else:
            del sys.modules['prepare']


@pytest.fixture
def env(tmp_path):
    from console.dossiers import Dossiers
    from console.dossiers.drafts import Drafts, migrate
    from console.dossiers.sources import Sources
    importer = load_importer()
    candidate, target = tmp_path / 'candidate.sqlite3', tmp_path / 'dossiers.sqlite3'
    db = sqlite3.connect(candidate)
    db.executescript((REPO / 'tools/dossier/schema.sql').read_text())
    def insert(table, **row):
        db.execute('INSERT INTO '+table+' ('+','.join(row)+') VALUES ('+','.join('?' for _ in row)+')',tuple(row.values()))
    insert('metadata', key='schema_revision',value='dossier-v1')
    for sid,name,typ in [('S1','虚构甲灯具','产品'),('S2','虚构乙灯具','产品'),('S3','虚构运达物流','物流')]:
        insert('supplier',supplier_id=sid,name=name,supplier_type=typ,categories_json='["虚构洗墙灯"]',contacts_json='[]',reliability_notes='虚构验收，未经复核',aliases_json='[]',field_sources_json='{}')
    nas = tmp_path / 'nas';nas.mkdir()
    # Valid 1px PNG; all image bytes and names are synthetic.
    import base64
    image = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aZWQAAAAASUVORK5CYII=')
    (nas / 'fictional.png').write_bytes(image)
    file_hash=hashlib.sha256(image).hexdigest()
    payload=json.dumps(dict(text='虚构规格原文，不对应任何真实业务',group_id='fictional-group@chatroom'),ensure_ascii=False)
    wx=tmp_path / 'wechat.sqlite3'
    with sqlite3.connect(wx) as w:
        w.executescript('CREATE TABLE messages(app_id TEXT,message_id TEXT,group_id TEXT,created_at REAL,payload TEXT);CREATE TABLE message_scopes(app_id TEXT,message_id TEXT,business_role TEXT);CREATE TABLE company_media(app_id TEXT,message_id TEXT,ordinal INTEGER,archive_status TEXT,archive_sha256 TEXT);CREATE TABLE company_note_assets(app_id TEXT,message_id TEXT,parent_ordinal INTEGER,ordinal INTEGER,archive_status TEXT,archive_sha256 TEXT);')
        w.execute('INSERT INTO messages VALUES (?,?,?,?,?)',('fictional-app','fictional-msg','fictional-group@chatroom',1900000000,payload))
        w.execute('INSERT INTO message_scopes VALUES (?,?,?)',('fictional-app','fictional-msg','supplier'))
        w.execute('INSERT INTO company_media VALUES (?,?,?,?,?)',('fictional-app','fictional-msg',0,'ready',file_hash))
        w.execute('INSERT INTO company_note_assets VALUES (?,?,?,?,?,?)',('fictional-app','fictional-msg',0,1,'ready',file_hash))
    blob=tmp_path / 'blobs';blob.mkdir();(blob / (file_hash+'.blob')).write_bytes(image)
    for sid,kind,key,content,fh,parent,coverage in [
        ('SM','message',json.dumps(['fictional-app','fictional-msg']),hashlib.sha256(payload.encode()).hexdigest(),None,None,{}),
        ('SF','nas_file','fictional.png',file_hash,file_hash,None,{}),
        ('SA','message_attachment',json.dumps(['fictional-app','fictional-msg',None,0]),file_hash,file_hash,'SM',{'format':'png','name':'虚构图片.png'}),
        ('SN','note_asset',json.dumps(['fictional-app','fictional-msg',0,1]),file_hash,file_hash,'SM',{'format':'png','name':'虚构笔记图.png'})]:
        insert('source',source_id=sid,kind=kind,external_key=key,content_sha256=content,file_sha256=fh,parent_source_id=parent,locator='page:2:cell:B7',source_at='2030-03-17',observed_at='2030-03-18',scope='supplier',direction='incoming',parse_state='read',extract_state='reviewed',failure_reason=None,coverage_json=json.dumps(coverage),private_ref='/never/expose/private.json')
    for pid,signature in [('P1','config-alpha'),('P2','config-beta')]:
        insert('product',product_id=pid,product_family='fictional-family',category='虚构洗墙灯',canonical_model='DEMO-X',display_name='虚构洗墙灯',variant_label=signature,config_json=json.dumps({'功率':'虚构数值','配置':signature}),config_signature=signature,evidence_state='待核实：虚构证据',field_sources_json='{}')
        for supplier in (['S1','S2'] if pid=='P1' else ['S1']):
            insert('supplier_product',supplier_id=supplier,product_id=pid,model_aliases_json='["DEMO-X"]',evidence_json='[]')
        insert('evidence',evidence_id='E'+pid,entity_kind='product',entity_id=pid,field_path='config.功率',source_id='SF',locator='page:2:cell:B7',excerpt='虚构参数片段',bbox_json=None,method='fictional',evidence_state='待核实')
    for oid,supplier,amount,kind in [('O1','S1','123.4500','unit_price'),('O2','S2','120.00000001','unit_price'),('O3','S1',None,'configuration_surcharge')]:
        insert('offer',offer_id=oid,supplier_id=supplier,product_id='P1',amount_decimal=amount,amount_raw=amount or '约若干',currency=None,tax_basis='unknown',tax_rate_text=None,unit=None,price_kind=kind,quote_state='observed',moq_text=None,quantity_condition=None,lead_time_text=None,packing_json='{}',conditions_json='{}',quoted_on=None,quoted_on_basis='unknown',document_period='2030-03',valid_until=None,supersedes_offer_id=None,source_id='SM',locator='text',field_sources_json='{}',dedup_key=oid)
        insert('evidence',evidence_id='E'+oid,entity_kind='offer',entity_id=oid,field_path='amount_decimal',source_id='SM',locator='text',excerpt='虚构报价片段',bbox_json=None,method='fictional',evidence_state='待核实')
    for i in range(1,36):
        insert('gap',gap_id=f'G{i:03}',supplier_id='S1' if i<34 else 'S2',product_id='P1',offer_id=None,gap_type='power_conflict' if i==1 else 'quote_terms',priority=1 if i==1 else 8,impact='虚构影响：配置与采购成本需核对',ask_text=f'请补虚构资料{i}',source_refs_json='["SM","SF"]',state='open')
    db.commit();db.close()
    with sqlite3.connect(target) as t:
        for table in ('parties','products','offers'):
            t.execute('CREATE TABLE '+table+'(id INTEGER PRIMARY KEY,value TEXT)')
            t.execute('INSERT INTO '+table+" VALUES(1,'fictional-marker')")
    importer.import_candidate(candidate,target,True)
    key=tmp_path / 'key';key.write_bytes(b'fictional-media-key-32-bytes-long!')
    sources=Sources(config=dict(nas_root=str(nas),wechat_db=str(wx),blob_root=str(blob),media_key=str(key)))
    service=Dossiers(target,resolver=sources)
    draftpath=tmp_path / 'drafts.sqlite3';migrate(draftpath)
    return SimpleNamespace(root=tmp_path,candidate=candidate,target=target,service=service,sources=sources,drafts=Drafts(service,draftpath),importer=importer,wx=wx,nas=nas,image=image,key=key)
