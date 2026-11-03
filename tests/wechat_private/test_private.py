from copy import deepcopy
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sqlite3

import pytest


def callback(mid=1, *, peer='fictional-peer', outgoing=False, kind=1, content='虚构 ZX900 询问', at=1700000000):
    own = 'fictional-self'
    return dict(TypeName='AddMsg', Appid='fictional-app', Wxid=own, Data=dict(NewMsgId=mid,MsgType=kind,
        CreateTime=at, FromUserName={'string': own if outgoing else peer},
        ToUserName={'string': peer if outgoing else own}, Content={'string': content}))


def capture(env, payload):
    import wechat_private
    return wechat_private.capture(env.store, env.config, payload, env.now)


def seed_contacts(env, peer, name):
    from console.channels.wechat_private import digest
    with sqlite3.connect(env.contacts) as db:
        db.execute('CREATE TABLE IF NOT EXISTS contacts(account_sha256 TEXT,contact_id TEXT,remark TEXT,nickname TEXT,updated_at REAL)')
        db.execute('INSERT INTO contacts VALUES(?,?,?,?,?)', (digest('fictional-app\0fictional-self'),peer,name,'',1))


def adapter(env):
    from console.channels.wechat import WechatAdapter
    return WechatAdapter(env.path, archive_path=env.archive,contact_path=env.contacts,media_tokens=False)


def test_directions_same_storage_terminal_status_and_idempotence(env):
    assert capture(env, callback()) == 'inserted'
    assert capture(env, callback()) == 'duplicate'
    assert capture(env, callback(2, outgoing=True)) == 'inserted'
    rows = env.store.db.execute('SELECT direction,peer_id FROM wechat_private_messages ORDER BY message_id').fetchall()
    assert [tuple(r) for r in rows] == [('inbound','fictional-peer'),('outbound','fictional-peer')]
    assert {r[0] for r in env.store.db.execute('SELECT status FROM messages')} == {'collected_private'}
    assert env.store.db.execute('SELECT count(*) FROM inbound_routes').fetchone()[0] == 0
    conv, = adapter(env).list_conversations(env.now).values()
    assert not conv['is_group'] and conv['business_role'] == 'unknown' and conv['needs_classification']
    assert [m['role'] for m in conv['messages']] == ['buyer','seller']


@pytest.mark.parametrize('name,role', [('CUS001 虚构客户','customer'),('SUP002 虚构工厂','supplier'),
    ('SUP003 虚构物流','forwarder'),('SUP004 虚构包装','vendor'),('虚构名称','unknown'),('SUP001 CUS002 虚构','unknown')])
def test_existing_name_rules(env, name, role):
    seed_contacts(env,'fictional-peer',name)
    capture(env, callback())
    row = env.store.db.execute('SELECT business_role FROM wechat_private_contacts').fetchone()
    assert row[0] == role
    assert next(iter(adapter(env).list_conversations(env.now).values()))['business_role'] == role


def test_identity_mapping_overrides_name_and_refreshes_adapter_cache(env):
    from console.channels.wechat_private import account_key
    from console.inbox.model import make_key
    seed_contacts(env,'fictional-peer','SUP001 虚构供应商')
    capture(env,callback())
    a = adapter(env)
    conv, = a.list_conversations(env.now).values()
    assert conv['business_role'] == 'supplier'
    key = make_key('conversation','wechat',account_key('fictional-app','fictional-self'),'fictional-peer')
    with sqlite3.connect(env.archive) as db:
        db.executescript('CREATE TABLE parties(id INTEGER,kind TEXT,name TEXT); CREATE TABLE identities(conversation_key TEXT,party_id INTEGER);')
        db.execute("INSERT INTO parties VALUES(1,'customer','虚构已确认客户')")
        db.execute('INSERT INTO identities VALUES(?,1)',(key,))
    conv, = a.list_conversations(env.now).values()
    assert conv['business_role'] == 'customer' and conv['role_basis'] == 'confirmed_identity'


@pytest.mark.parametrize('change', ['wrong_app','wrong_own','wrong_recipient','group','official','system','owner'])
def test_account_system_group_and_owner_gates(env, change):
    p=callback()
    if change=='wrong_app': p['Appid']='other-app'
    if change=='wrong_own': p['Wxid']='other-self'
    if change=='wrong_recipient': p['Data']['ToUserName']['string']='other-self'
    if change=='group': p['Data']['FromUserName']['string']='fictional-room@chatroom'
    if change=='official': p['Data']['FromUserName']['string']='gh_fictional'
    if change=='system': p['Data']['FromUserName']['string']='filehelper'
    if change=='owner': p['Data']['FromUserName']['string']='fictional-operator'
    assert capture(env,p) is None
    assert env.store.db.execute('SELECT count(*) FROM messages').fetchone()[0] == 0


def test_unsigned_id_variants_conflicts_and_unsupported_types(env):
    assert capture(env,callback(-1)) == 'inserted'
    assert capture(env,callback('18446744073709551615')) == 'duplicate'
    assert capture(env,callback('18446744073709551615',content='虚构变更正文')) == 'conflict'
    assert capture(env,callback(7,kind=9999,content='not executed')) == 'inserted'
    assert env.store.db.execute('SELECT count(*) FROM messages').fetchone()[0]==2
    assert '待解析' in json.loads(env.store.db.execute("SELECT payload FROM messages WHERE message_id='7'").fetchone()[0])['text']


def test_receiver_hooks_retain_both_directions_and_do_not_dispatch_private(env, monkeypatch):
    import api_groupbot
    r = api_groupbot.Runner.__new__(api_groupbot.Runner)
    r.store=env.store; r.now=lambda:env.now; r.last_ingest_error=None
    calls=[]
    r._ingest_message=lambda *args:calls.append(args) or 202
    assert r.ingest(callback(),env.config)==200
    assert r.ingest(callback(2,outgoing=True),env.config)==200
    assert calls==[]
    assert env.store.db.execute('SELECT count(*) FROM received_callback_archive').fetchone()[0]==1
    assert env.store.db.execute('SELECT count(*) FROM outbound_callback_archive').fetchone()[0]==1
    assert r.ingest(callback(3,peer='fictional-room@chatroom'),env.config)==202
    assert len(calls)==1


def test_storage_failure_retries_after_durable_raw_archive(env, monkeypatch):
    import api_groupbot, wechat_private
    r=api_groupbot.Runner.__new__(api_groupbot.Runner);r.store=env.store;r.now=lambda:env.now
    original=wechat_private.capture
    monkeypatch.setattr(wechat_private,'capture',lambda *a,**kw:(_ for _ in ()).throw(sqlite3.OperationalError()))
    assert r.ingest(callback(),env.config)==503
    assert env.store.db.execute('SELECT count(*) FROM received_callback_archive').fetchone()[0]==1
    monkeypatch.setattr(wechat_private,'capture',original)
    assert r.ingest(callback(),env.config)==200
    assert env.store.db.execute('SELECT count(*) FROM messages').fetchone()[0]==1


def test_replay_bounded_immutable_and_restart_safe(env):
    import receive_only, outbound_callback, wechat_private
    for i in range(1,5):
        receive_only.capture(env.store,env.config,callback(i),env.now)
    outbound_callback.intercept(env.store,env.config,callback(9,outgoing=True),env.now,
                                validate_config=receive_only.validate)
    first=wechat_private.replay(env.store,env.config,limit=2,now=env.now)
    assert first['inserted']==2 and first['scanned']==2
    assert wechat_private.replay(env.store,env.config,limit=100,now=env.now)['inserted']==3
    assert wechat_private.replay(env.store,env.config,now=env.now)['scanned']==0
    assert {r[0] for r in env.store.db.execute('SELECT processing_state FROM received_callback_archive')}=={'retained_only'}


def test_replay_skips_owner_invalid_and_handles_message_already_live(env):
    import receive_only, wechat_private
    for p in (callback(),callback(2,peer='fictional-operator'),callback(0)):
        receive_only.capture(env.store,env.config,p,env.now)
    capture(env,callback())
    assert wechat_private.replay(env.store,env.config,now=env.now)['scanned']==2
    assert wechat_private.replay(env.store,env.config,now=env.now)['scanned']==0


def test_media_uses_real_company_download_archive_and_extraction_pipeline(env, monkeypatch):
    import api_groupbot, company_materials, media_archive
    seed_contacts(env,'fictional-peer','SUP001 虚构工厂')
    xml='<msg><appmsg><title>fictional.txt</title><type>6</type><appattach><totallen>18</totallen><attachid>fictional-attach</attachid><fileext>txt</fileext></appattach></appmsg></msg>'
    capture(env,callback(1,kind=49,content=xml))
    monkeypatch.setattr(company_materials,'_settings',lambda c:c['company_knowledge'])
    # The actual archive writer, claim, source checks and commit all run.
    calls=[]
    class Client:
        def __init__(self,**kwargs): pass
        def download_attachment(self,app,descriptor):
            calls.append((app,descriptor['kind']))
            return b'fictional document'
    monkeypatch.setattr(company_materials,'_extract',lambda *a:dict(status='ready',text='虚构 ZX900 文件正文',format='txt',truncated=False))
    result=company_materials.collect_one(env.store,env.config,env.root/'config.json',now=env.now,client_factory=Client)
    assert result['claimed']==1 and result['ready']==1, result
    row=env.store.db.execute('SELECT * FROM company_media').fetchone()
    assert row['archive_status']=='ready' and row['text']=='虚构 ZX900 文件正文'
    assert (env.workspace/'media_archive'/(row['archive_sha256']+'.blob')).read_bytes()==b'fictional document'
    assert calls==[('fictional-app','file')]
    assert company_materials.collect_one(env.store,env.config,env.root/'config.json',now=env.now,client_factory=Client)['claimed']==0


def test_media_binding_revoked_on_account_change(env, monkeypatch):
    import company_materials, wechat_private
    capture(env,callback())
    company_materials.initialize(env.store)
    row=dict(env.store.db.execute('SELECT m.*,s.profile_id,s.scope_id,s.own_wxid,s.business_role FROM messages m JOIN message_scopes s USING(app_id,message_id)').fetchone())
    payload=json.loads(row['payload'])
    assert wechat_private.media_auth(env.store.db,row,payload,env.config,env.now)
    changed=deepcopy(env.config);changed['owner_control']['own_wxid']='fictional-other'
    assert wechat_private.media_auth(env.store.db,row,payload,changed,env.now) is None


def test_knowledge_hub_search_reads_private_and_never_customer_reply(env, candidate):
    from console.channels.wechat_private import knowledge_hits
    from console.kb.search import Search
    seed_contacts(env,'fictional-peer','SUP001 虚构工厂')
    capture(env,callback())
    # Test real Search entry point with an unavailable catalog, so private data
    # must come from the new read-only supplement rather than a fake index.
    class Index:
        def snapshot(self): return None
    search=Search(Index(),embedder=None,private_path=env.path)
    # Isolated paths are explicit; no default archive/contact DB is accessed.
    import console.kb.search as search_module
    original=search_module.knowledge_hits
    search_module.knowledge_hits=lambda p,q,u,k,f:original(p,q,u,k,f,archive_path=env.archive,contact_path=env.contacts)
    try:
        assert search.search('ZX900','owner')['items']
        assert search.search('ZX900','product_facts')['items']
        assert search.search('ZX900','customer_reply',conversation_scope='other-customer')['items']==[]
        assert search.search('ZX900','customer_reply',conversation_scope='same-customer')['items']==[]
    finally:
        search_module.knowledge_hits=original


def test_ops_local_projection_sees_private_with_same_conversation_key(env):
    from console.ops_queue.inbox import wechat
    capture(env,callback())
    assert set(wechat(env.path,env.now))==set(adapter(env).list_conversations(env.now))


def test_group_projection_unchanged_and_private_accounts_separate(env):
    from console.channels import UnifiedInbox
    group=dict(app_id='fictional-app',own_wxid='fictional-self',group_id='fictional-room@chatroom',sender_id='fictional-member',
               message_id='group-1',created_at=env.now-20,text='虚构群消息',at_ids=[],attachments=[])
    with env.store.transaction() as db:
        db.execute("INSERT INTO messages VALUES(?,?,?,?,?,?,'collected_silent','',?)",('fictional-app','group-1',group['group_id'],group['sender_id'],group['created_at'],json.dumps(group),env.now))
        db.execute("INSERT INTO message_scopes VALUES('fictional-app','group-1','profile','group-scope','fictional-self','customer')")
    before=adapter(env).list_conversations(env.now)
    capture(env,callback())
    after=adapter(env).list_conversations(env.now)
    assert all(after[k]==v for k,v in before.items())
    unified=UnifiedInbox([adapter(env)]).read(env.now)
    private=next(c for c in unified['conversations'].values() if not c['is_group'])
    assert private['needs_classification'] and not private['waiting_reply']


def test_pending_file_completion_reuses_existing_completion_claim(env, monkeypatch):
    import company_materials
    pending='<msg><appmsg><title>fictional.txt</title><type>74</type></appmsg></msg>'
    complete='<msg><appmsg><title>fictional.txt</title><type>6</type><appattach><attachid>fictional-id</attachid><fileext>txt</fileext></appattach></appmsg></msg>'
    assert capture(env,callback(1,kind=49,content=pending))=='inserted'
    company_materials.initialize(env.store)
    assert capture(env,callback(1,kind=49,content=complete))=='upgraded'
    monkeypatch.setattr(company_materials,'_settings',lambda c:c['company_knowledge'])
    claimed=company_materials._claim_media_completion(env.store.db,env.config,env.now)
    assert claimed is not None
    assert claimed[2]['appmsg_type']==6
    assert env.store.db.execute('SELECT count(*) FROM messages').fetchone()[0]==1


@pytest.mark.parametrize('kind,content,label', [
    (3,'<msg><img aeskey="fictional-key" cdnmidimgurl="fictional-ref"/></msg>','image'),
    (34,'<msg><voicemsg voiceformat="4" length="5" voicelength="1000" bufid="1" clientmsgid="fictional-voice"/></msg>','voice'),
    (49,'<msg><appmsg><type>24</type><title>虚构笔记</title><recorditem>&lt;recordinfo&gt;&lt;datalist count="1"&gt;&lt;dataitem datatype="1"&gt;&lt;datadesc&gt;虚构正文&lt;/datadesc&gt;&lt;/dataitem&gt;&lt;/datalist&gt;&lt;/recordinfo&gt;</recorditem></appmsg></msg>','note')])
def test_media_descriptors_use_existing_normalizer(env,kind,content,label):
    import wtapi_events
    from dataclasses import asdict
    p=callback(1,kind=kind,content=content)
    native=wtapi_events.normalize_private_message(p) or wtapi_events.normalize_owner_media_message(p)
    assert native is not None
    assert capture(env,p)=='inserted'
    stored=json.loads(env.store.db.execute('SELECT payload FROM messages').fetchone()[0])
    assert stored['attachments']==json.loads(json.dumps(asdict(native)['attachments']))


def test_note_inline_extraction_and_child_registration_share_company_pipeline(env,monkeypatch):
    import company_materials
    record='<recordinfo><datalist count="2"><dataitem datatype="1"><datadesc>虚构 ZX900 笔记正文</datadesc></dataitem><dataitem datatype="8" dataid="fictional-child"><datatitle>fictional.txt</datatitle><fileext>txt</fileext><datasize>18</datasize><cdndataurl>fictional-ticket</cdndataurl><cdndatakey>11111111111111111111111111111111</cdndatakey></dataitem></datalist></recordinfo>'
    import html
    xml='<msg><appmsg><type>24</type><title>虚构笔记</title><recorditem>'+html.escape(record)+'</recorditem></appmsg></msg>'
    capture(env,callback(1,kind=49,content=xml))
    monkeypatch.setattr(company_materials,'_settings',lambda c:c['company_knowledge'])
    result=company_materials.collect_one(env.store,env.config,env.root/'config.json',now=env.now,
                                       client_factory=lambda **kw:pytest.fail('inline note must not download its body'))
    assert result['claimed']==1 and result['ready']==1,result
    row=env.store.db.execute('SELECT * FROM company_media').fetchone()
    assert row['format']=='wechat_note_inline' and 'ZX900' in row['text']
    assert env.store.db.execute('SELECT count(*) FROM company_note_assets').fetchone()[0]>=1
    import company_note_assets
    calls=[]
    class Client:
        def __init__(self,**kwargs): pass
        def download_note_asset(self,*args):
            calls.append(args)
            return b'fictional document'
    monkeypatch.setattr(company_note_assets,'_extract',lambda *args:dict(status='ready',text='虚构子文件正文',format='txt',truncated=False))
    child=company_note_assets.collect_one(env.store,env.config,env.root/'config.json',now=env.now,client_factory=Client)
    assert child['claimed']==1 and child['ready']==1,child
    assert len(calls)==1
    saved=env.store.db.execute('SELECT archive_status,text FROM company_note_assets').fetchone()
    assert tuple(saved)==('ready','虚构子文件正文')


def test_customer_and_supplier_page_real_party_timeline_reads_private(env):
    from console.channels import UnifiedInbox
    from console.customers.operations import Parties
    from console.archive.store import Store
    capture(env,callback())
    capture(env,callback(2,outgoing=True))
    inbox=UnifiedInbox([adapter(env)])
    # Pin the read clock for list/detail, as the real page calls both without arguments.
    class Inbox:
        def read(self,*args):return inbox.read(env.now)
        def detail(self,key,*args):return inbox.detail(key,env.now)
    party_store=Store(env.archive,target_version=9)
    parties=Parties(store=party_store,inbox=Inbox(),clock=lambda:env.now)
    parties.initialize()
    key=next(iter(inbox.read(env.now)['conversations']))
    for kind in ('supplier','customer'):
        created=parties.write('create',None,dict(name='虚构'+kind,kind=kind,conversation_key=key,
                                               request_id='fictional-request-'+kind),'fictional-operator')
        result=parties.read('detail',row_id=created['id'])
        chat=[row for row in result['timeline'] if row['channel']=='wechat']
        assert {row['role'] for row in chat}=={'buyer','seller'}
        assert len(chat)==2
        # Release the fictional mapping so the next case can bind the same chat.
        with party_store.write() as db:
            db.execute('DELETE FROM identities')
            db.execute('UPDATE parties SET conversation_ref=NULL')


def test_ops_unknown_card_is_present_without_model_scan(env):
    from console.channels import UnifiedInbox
    from console.ops_queue.service import OpsQueue
    capture(env,callback())
    class Empty:
        def read(self):return dict(channels=[],sources=[])
    q=OpsQueue(paths={'archive':env.archive,'wechat':env.path},inbox=UnifiedInbox([adapter(env)]),
               overview=Empty(),pipeline=Empty(),clock=lambda:env.now)
    result=q.read()
    ownership=[i for i in result['items'] if i['kind']=='ownership']
    assert len(ownership)==1 and ownership[0]['label']=='待你确认归属'
