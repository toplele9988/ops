import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest
from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import PlainTextResponse
from starlette.testclient import TestClient


def test_import_replay_and_legacy(env):
    before=env.target.read_bytes()
    result=env.importer.import_candidate(env.candidate,env.target,True)
    assert not any(result['inserted'].values())
    assert env.target.read_bytes()==before
    with sqlite3.connect(env.target) as db:
        for table in ('parties','products','offers'):
            assert db.execute('SELECT * FROM '+table).fetchall()==[(1,'fictional-marker')]


def test_import_conflict_atomic_and_dry_run(env):
    missing=env.root / 'absent.sqlite3'
    env.importer.import_candidate(env.candidate,missing)
    assert not missing.exists()
    with sqlite3.connect(env.candidate) as db:
        db.execute("UPDATE supplier SET name='虚构变更' WHERE supplier_id='S2'")
    before=env.target.read_bytes()
    with pytest.raises(ValueError,match='immutable_key_conflict'):
        env.importer.import_candidate(env.candidate,env.target,True)
    assert env.target.read_bytes()==before


def test_projections_decimal_unknown_and_no_cross_supplier_price(env):
    from console.dossiers.service import family_key
    rows=env.service.suppliers()['items']
    assert rows[0]['latest_quote'] is None
    s=env.service.supplier('S1')
    assert len(s['products'])==2
    assert all(o['supplier_id']=='S1' for p in s['products'] for o in p['offers'])
    f=env.service.family(family_key('fictional-family'))
    assert len(f['suppliers'])==2 and len(f['configurations'])==2
    amounts=[o['amount_decimal'] for p in f['configurations'] for o in p['offers']]
    assert '123.4500' in amounts and '120.00000001' in amounts and None in amounts
    assert all(o['currency'] is None and o['tax_basis']=='unknown' and o['valid_until'] is None for p in f['configurations'] for o in p['offers'])
    assert env.service.families()['items'][0]['thumbnail_source_id']=='SF'
    assert env.service.gaps({'supplier':'S1','priority':'1','type':'power_conflict'})['total']==1
    assert len(env.service.queue_card()['items'])==5


def test_readonly_does_not_create_or_change(env):
    from console.dossiers.store import readonly
    from console.dossiers import Dossiers
    before=env.target.read_bytes()
    env.service.families();env.service.supplier('S1');env.service.gaps()
    with readonly(env.target) as db:
        with pytest.raises(sqlite3.OperationalError):
            db.execute('DELETE FROM sd_supplier')
    assert env.target.read_bytes()==before
    path=env.root/'missing.sqlite3'
    with pytest.raises(ValueError):Dossiers(path).gaps()
    assert not path.exists()


def test_sources_message_nas_attachment_note_and_tamper(env):
    message=env.service.source('SM')
    assert message['conversation_url'].startswith('/app/inbox?')
    assert message['message_at']==1900000000 and '虚构规格原文' in message['original_text']
    assert 'private_ref' not in message and 'external_key' not in message
    for sid in ('SF','SA','SN'):
        source=env.service.source(sid)
        token=source['media_url'].rsplit('/',1)[1]
        assert env.sources.media(token,env.service)==(env.image,'image/png')
        with pytest.raises(ValueError):env.sources.media(token+'x',env.service)
    (env.nas/'fictional.png').write_bytes(b'changed')
    assert env.service.source('SF')['available'] is False
    with sqlite3.connect(env.wx) as db:
        db.execute("UPDATE message_scopes SET business_role='customer'")
    assert env.service.source('SM')['available'] is False
    assert env.service.source('SN')['available'] is False


def test_source_path_escape_symlink_and_changed_message(env):
    row=env.service.source_row('SF')
    row['external_key']='../outside.png'
    with pytest.raises(ValueError):env.sources.locate(row)
    (env.nas/'link.png').symlink_to(env.nas/'fictional.png')
    row['external_key']='link.png'
    with pytest.raises(OSError):env.sources.locate(row)
    with sqlite3.connect(env.wx) as db:db.execute("UPDATE messages SET payload='{}'")
    assert env.service.source('SM')['available'] is False


def test_private_message_conversation_uses_account_binding(env):
    import hashlib
    from urllib.parse import parse_qs,urlparse
    from console.inbox.model import decode_key
    payload=json.dumps(dict(text='虚构私聊规格',group_id='fictional-peer',own_wxid='fictional-self'))
    with sqlite3.connect(env.wx) as db:
        db.execute('CREATE TABLE wechat_private_messages(app_id TEXT,message_id TEXT,own_wxid TEXT,peer_id TEXT)')
        db.execute('INSERT INTO wechat_private_messages VALUES(?,?,?,?)',('fictional-app','fictional-msg','fictional-self','fictional-peer'))
        db.execute('UPDATE messages SET group_id=?,payload=?',('fictional-peer',payload))
    with sqlite3.connect(env.target) as db:db.execute('UPDATE sd_source SET content_sha256=? WHERE source_id=?',(hashlib.sha256(payload.encode()).hexdigest(),'SM'))
    source=env.service.source('SM');assert source['available']
    key=parse_qs(urlparse(source['conversation_url']).query)['conversation'][0]
    parts=decode_key(key,'conversation')
    assert parts[1].startswith('微信私聊:') and parts[2]=='fictional-peer'


def test_draft_snapshots_all_filtered_pages_idempotent_and_readonly_core(env):
    before=env.target.read_bytes()
    a=env.drafts.create('S1',{},'fictional-operator')
    b=env.drafts.create('S1',{},'fictional-operator')
    assert a==b and a['state']=='draft'
    assert len(json.loads(a['gap_ids_json']))==33
    assert json.loads(a['source_refs_json'])[0]['content_sha256']
    one=env.drafts.create('S1',{'priority':'1'},'fictional-operator')
    assert len(json.loads(one['gap_ids_json']))==1
    assert len(env.drafts.list('S1')['items'])==2
    assert env.target.read_bytes()==before
    assert env.drafts.path.stat().st_mode & 0o777==0o600


def test_draft_migration_replays_and_rejects_unknown_schema(env):
    from console.dossiers.drafts import migrate
    migrate(env.drafts.path)
    path=env.root/'incompatible-drafts.sqlite3'
    with sqlite3.connect(path) as db:db.execute('CREATE TABLE sd_question_draft(wrong TEXT)')
    with pytest.raises(ValueError,match='incompatible_draft_schema'):migrate(path)


def client(env, *, authenticated=True, recheck=True):
    from console.v3_dossiers_api import build
    class Gate:
        def error_response(self,request,code):return PlainTextResponse('forbidden',code)
    app=Starlette(routes=build(Gate(),service=env.service,drafts=env.drafts))
    async def auth(request,call_next):
        request.state.password_principal=SimpleNamespace(username='fictional-operator') if authenticated else None
        request.state.session=SimpleNamespace(authenticated=True,csrf='fictional-csrf')
        request.state.password_recheck=lambda:recheck
        return await call_next(request)
    app.add_middleware(BaseHTTPMiddleware,dispatch=auth)
    return TestClient(app)


@pytest.mark.parametrize('path',['suppliers','suppliers/S1','families','gaps','sources/SM','suppliers/S1/drafts'])
def test_api_auth_recheck_no_store(env,path):
    with client(env) as c:
        r=c.get('/api/v1/dossiers/'+path)
        assert r.status_code==200 and r.headers['cache-control']=='no-store'
    with client(env,authenticated=False) as c:assert c.get('/api/v1/dossiers/'+path).status_code==403
    with client(env,recheck=False) as c:assert c.get('/api/v1/dossiers/'+path).status_code==403


def test_api_validation_csrf_and_media(env):
    with client(env) as c:
        for suffix in ('gaps?page=0','gaps?page_size=999','gaps?supplier=S1&supplier=S2','suppliers?path=bad','gaps?page=1.5'):
            r=c.get('/api/v1/dossiers/'+suffix)
            assert r.status_code==400 and r.headers['cache-control']=='no-store'
        assert c.get('/api/v1/dossiers/suppliers/absent').status_code==404
        url='/api/v1/dossiers/suppliers/S1/drafts'
        assert c.post(url,data={'priority':'1'}).status_code==403
        assert c.post(url,data={'priority':'1'},headers={'x-csrf-token':'bad'}).status_code==403
        r=c.post(url,data={'priority':'1'},headers={'x-csrf-token':'fictional-csrf'})
        assert r.status_code==200 and r.json()['state']=='draft' and r.headers['cache-control']=='no-store'
        media=c.get('/api/v1/dossiers/sources/SF').json()['media_url']
        r=c.get(media)
        assert r.status_code==200 and r.content==env.image and r.headers['cache-control']=='no-store'
        assert c.get(media+'tampered').status_code==404


def test_queue_card_addition_isolated_and_fails_separately(env):
    from console.ops_queue import OpsQueue
    class Overview:
        def read(self):return dict(channels=[])
    class Pipeline:
        def read(self):return dict(failed_jobs={'count':0})
    queue=OpsQueue(paths={'archive':env.target},overview=Overview(),pipeline=Pipeline(),dossiers=env.service)
    value=queue.read()
    assert value['supplier_dossiers']['ready'] and value['supplier_dossiers']['total']==35
    env.target.unlink()
    value=queue.read()
    assert value['supplier_dossiers']==dict(ready=False,total=None,items=[])
    assert any('供应商缺资料' in x for x in value['unavailable'])
