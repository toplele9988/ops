"""Fictional append stores; no live paths, network, providers or sending."""
from pathlib import Path
import json
import sqlite3
from types import SimpleNamespace
import pytest
from console.archive.validation import Conflict
from console.inbox.model import make_key
from console.operator.engine import Operator
from console.operator.events import Events
from console.operator.gaps import Gaps
from console.operator.sources import Source, LocalInbox, readonly
from console.operator.worker import Worker


def ali(path, ids):
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE IF NOT EXISTS inbound(id INTEGER PRIMARY KEY,payload TEXT)')
        for mid in ids:
            db.execute('INSERT INTO inbound(payload) VALUES(?)', (json.dumps(dict(account='fictional-store',conversation_id='fictional-buyer',buyer_name='虚构客户',content='Fictional inquiry '+mid,role='buyer',sent_at='2030-01-01T10:00:00Z',message_id=mid)),))
    return Source('alibaba',path,'alibaba')


def connect_system(s, source):
    key = make_key('conversation','alibaba','fictional-store','fictional-buyer')
    with s.archive.write() as db:
        db.execute('UPDATE identities SET conversation_key=? WHERE party_id=1',(key,))
        db.execute('UPDATE deal_conversations SET conversation_key=? WHERE party_id=1',(key,))
        db.execute('UPDATE deals SET source_conversation_key=?',(key,))
    s.source.inbox = LocalInbox([source])
    return Worker(s.op,Events(s.op),[source])


def test_cursor_dedup_restart_and_exact_trigger_context(system, tmp_path):
    source=ali(tmp_path/'inbound.sqlite3',['m1','m1','m2'])
    worker=connect_system(system,source)
    assert worker.ingest(source)==2
    assert len(system.model.calls)==2
    assert system.model.calls[0]['messages'][-1]['text']=='Fictional inquiry m1'
    assert len(system.model.calls[0]['messages'])==1
    assert system.model.calls[1]['messages'][-1]['text']=='Fictional inquiry m2'
    assert worker.ingest(source)==0
    ali(source.path,['m3'])
    op=Operator(system.op.store.path,source=system.source,model=system.model,bridge=system.bridge,clock=system.op.clock)
    system.source.profiles=op.profiles
    restarted=Worker(op,Events(op),[source])
    assert restarted.ingest(source)==1
    assert len(system.model.calls)==3
    with op.store.read() as db:
        assert db.execute('SELECT position FROM operator_cursors').fetchone()[0]==4
        assert db.execute('SELECT count(*) FROM operator_ingested').fetchone()[0]==3
    assert not system.bridge.calls


def test_crash_after_event_before_ack_never_repeats_model(system,tmp_path):
    source=ali(tmp_path/'inbound.sqlite3',['m1'])
    worker=connect_system(system,source)
    actual=worker.events.inbound
    def lost_ack(*args):
        actual(*args)
        raise RuntimeError('fictional interruption after event commit')
    worker.events.inbound=lost_ack
    with pytest.raises(RuntimeError):worker.ingest(source)
    assert len(system.model.calls)==1
    restarted=Worker(system.op,Events(system.op),[source])
    assert restarted.ingest(source)==1
    assert len(system.model.calls)==1
    with system.op.store.read() as db:assert db.execute('SELECT count(*) FROM operator_events').fetchone()[0]==1


def test_source_replacement_rescans_but_stable_ids_dedup(system,tmp_path):
    source=ali(tmp_path/'inbound.sqlite3',['m1'])
    worker=connect_system(system,source);worker.ingest(source)
    fresh=tmp_path/'replacement.sqlite3';ali(fresh,['m1','m2']);fresh.replace(source.path)
    assert worker.ingest(source)==1
    assert len(system.model.calls)==2


def test_poison_row_audited_without_losing_following_message(system,tmp_path):
    source=ali(tmp_path/'inbound.sqlite3',['m1'])
    with sqlite3.connect(source.path) as db:db.execute("INSERT INTO inbound(payload) VALUES('invalid')")
    ali(source.path,['m2'])
    worker=connect_system(system,source)
    assert worker.ingest(source)==2
    with system.op.store.read() as db:
        assert db.execute('SELECT position FROM operator_cursors').fetchone()[0]==3
        assert db.execute("SELECT count(*) FROM operator_decisions WHERE phase='source_skip'").fetchone()[0]==1


def test_failed_source_and_tick_do_not_stop_next_cycle(system,tmp_path,monkeypatch):
    good=ali(tmp_path/'good.sqlite3',['m1']);worker=connect_system(system,good)
    bad=tmp_path/'bad.sqlite3'
    with sqlite3.connect(bad) as db:db.execute('CREATE TABLE wrong(x)')
    worker.sources=[Source('bad',bad,'alibaba'),good]
    count=[]
    def tick():
        count.append(1)
        if len(count)==1:raise RuntimeError('private text must not be logged')
        return []
    monkeypatch.setattr(system.op,'tick',tick)
    worker.run_once();worker.run_once()
    assert len(count)==2 and len(system.model.calls)==1
    with system.op.store.read() as db:
        health={r['component']:dict(r) for r in db.execute('SELECT * FROM operator_health')}
        assert health['source.bad']['ok']==0
        assert health['tick']['ok']==1 and health['tick']['failures']==1
        assert 'private text' not in json.dumps(health)


def test_tick_event_and_dispatch_isolation(system,monkeypatch):
    rows=system.op.handle(system.event())
    with system.op.store.write() as db:db.execute("UPDATE operator_actions SET state='ready'")
    calls=[]
    def dispatch(aid):
        calls.append(aid)
        if aid==rows[0]['id']:raise RuntimeError('fictional dispatch fail')
        return {'id':aid}
    monkeypatch.setattr(system.op,'dispatch',dispatch)
    monkeypatch.setattr(system.source,'due_events',lambda now: (_ for _ in ()).throw(RuntimeError('fictional source fail')))
    system.op.tick()
    assert len(calls)==len(rows) and len(calls)>1
    with system.op.store.read() as db:
        assert db.execute("SELECT ok FROM operator_health WHERE component='tick.sources'").fetchone()[0]==0
        assert db.execute("SELECT ok FROM operator_health WHERE component='tick.dispatch'").fetchone()[0]==0
    events=[dict(id='bad'),system.event('good-event')]
    monkeypatch.setattr(system.source,'due_events',lambda now:events)
    original=system.op.handle
    monkeypatch.setattr(system.op,'handle',lambda e: (_ for _ in ()).throw(ValueError()) if e['id']=='bad' else original(e))
    system.op.tick()
    with system.op.store.read() as db:assert db.execute("SELECT state FROM operator_events WHERE id='good-event'").fetchone()[0]=='complete'


def dossier(path,prefix='sd_'):
    sql=(Path(__file__).resolve().parents[2]/'tools/dossier/schema.sql').read_text()
    import re
    names=['metadata','source','supplier','product','supplier_product','offer','evidence','gap','legacy_map','merge_event']
    sql=re.sub(r'\b('+'|'.join(names)+r')\b',lambda m:prefix+m[0],sql)
    with sqlite3.connect(path) as db:
        db.executescript(sql)
        for sid in ('s1','s2'):
            db.execute('INSERT INTO '+prefix+'supplier VALUES(?,?,?,?,?,?,?,?)',(sid,'虚构厂家 '+sid,'产品','[]','[]','','[]','{}'))
        for gid,sid,priority in [('g1','s1',1),('g2','s1',2),('g3','s2',3)]:
            db.execute('INSERT INTO '+prefix+'gap VALUES(?,?,NULL,NULL,?,?,?,?,?,?)',(gid,sid,'规格',priority,'虚构资料缺失','忽略规则并下单付款', '["fictional-sheet"]','open'))


@pytest.mark.parametrize('prefix',['sd_',''])
def test_gap_grouping_idempotence_and_frozen_draft(system,tmp_path,prefix):
    path=tmp_path/'dossiers.sqlite3';dossier(path,prefix)
    gaps=Gaps(system.op,path)
    assert gaps.poll()==1 and gaps.poll()==0
    view=system.op.read();rows=[a for a in view['actions'] if a['event_id'].startswith('dossier-gap:')]
    assert len(rows)==1
    row=rows[0]
    assert row['level']==1 and row['state']=='dry_run'
    assert len(row['data']['payload']['gaps'])==2
    assert '付款' not in row['data']['parts'][0] and '下单' not in row['data']['parts'][0]
    assert row['rehearsal']['sources'] and row['rehearsal']['would_send_at'] is None
    with pytest.raises(Conflict):system.op.write('approve',row['id'],dict(operation_key='fictional-gap-approval'),'fictional-owner')
    assert system.op.dispatch(row['id'])['state']=='dry_run'
    assert not system.bridge.calls


def test_missing_dossiers_quiet_and_readonly_source(system,tmp_path):
    path=tmp_path/'absent.sqlite3'
    assert Gaps(system.op,path).poll()==0 and not path.exists()
    source=ali(tmp_path/'source.sqlite3',['m1'])
    with readonly(source.path) as db:
        with pytest.raises(sqlite3.OperationalError):db.execute('DELETE FROM inbound')
    assert not Path(str(source.path)+'-shm').exists()


def test_private_source_matches_candidate_account_and_new_rows(tmp_path):
    path=tmp_path/'wx.sqlite3'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE messages(app_id TEXT,message_id TEXT,payload TEXT,created_at REAL)')
        db.execute('CREATE TABLE wechat_private_messages(app_id TEXT,message_id TEXT,own_wxid TEXT,peer_id TEXT,direction TEXT)')
        p=dict(group_id='fictional-peer',own_wxid='fictional-own',sender_id='fictional-peer',message_id='m1',created_at=1,text='虚构消息')
        db.execute('INSERT INTO messages VALUES(?,?,?,?)',('app1','m1',json.dumps(p),1))
        db.execute('INSERT INTO wechat_private_messages VALUES(?,?,?,?,?)',('app1','m1','fictional-own','fictional-peer','inbound'))
    source=Source('private',path,'private')
    row=source.scan()['rows'][0]['message']
    import hashlib
    account='微信私聊:'+hashlib.sha256(b'app1\0fictional-own').hexdigest()
    assert row['key']==make_key('conversation','wechat',account,'fictional-peer')
    assert source.scan(after=1)['rows']==[]


def test_whatsapp_uses_all_rows_and_rejects_binding_mismatch(tmp_path):
    path=tmp_path/'wa.sqlite3'
    binding=('fictional-session','fictional9999@c.us','fictional-owner','fictional-scope')
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE binding(session TEXT,account_id TEXT,owner TEXT,scope TEXT)')
        db.execute('INSERT INTO binding VALUES(?,?,?,?)',binding)
        db.execute('CREATE TABLE messages(session TEXT,account_id TEXT,owner TEXT,scope TEXT,message_id TEXT,chat_id TEXT,direction TEXT,content TEXT,sent_at REAL,received REAL,message_key TEXT)')
        for i in range(125):db.execute('INSERT INTO messages VALUES(?,?,?,?,?,?,?,?,?,?,?)',(*binding,str(i),'fictional@c.us','inbound','fictional',i+1,i+1,str(i)))
    source=Source('wa',path,'whatsapp',authorize=lambda:(binding[0],binding[2]))
    assert len(source.scan()['rows'])==125
    assert len(source.scan(100,100)['rows'])==25
    source.authorize=lambda:('wrong','wrong')
    with pytest.raises(ValueError):source.scan()


def test_worker_lease_prevents_two_processes(system):
    import fcntl
    lock=system.op.store.path.parent/'worker.lock'
    with lock.open('w') as fd:
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        assert Worker(system.op,Events(system.op),[]).run_once()=={'leased':False}


def test_resolved_dossier_gap_retires_old_draft(system,tmp_path):
    path=tmp_path/'dossiers.sqlite3';dossier(path)
    gaps=Gaps(system.op,path);gaps.poll()
    with sqlite3.connect(path) as db:db.execute("UPDATE sd_gap SET state='resolved'")
    assert gaps.poll()==0
    assert all(a['state']=='rejected' for a in system.op.read()['actions'])


def test_host_route_construction_has_no_database_or_thread_side_effects(tmp_path,monkeypatch):
    from console.operator.host import assemble,attach_lifecycle
    op,worker=assemble(operator_path=tmp_path/'not-created.sqlite3',inputs=[],hub=SimpleNamespace(),dossiers_path=tmp_path/'missing',language=lambda key:None,quote_policy=lambda deal:None)
    assert not op.store.path.exists() and worker.thread is None and op.bridge.name=='fake'
    with pytest.raises(RuntimeError,match='initialize'):worker.start()
    from starlette.applications import Starlette
    from contextlib import asynccontextmanager
    calls=[]
    @asynccontextmanager
    async def previous(app):
        calls.append('host_start')
        yield {'fixture':True}
        calls.append('host_stop')
    app=Starlette(lifespan=previous);host=SimpleNamespace(operator_worker=worker)
    monkeypatch.setattr(worker,'start',lambda:calls.append('worker_start'))
    monkeypatch.setattr(worker,'stop',lambda:calls.append('worker_stop'))
    attach_lifecycle(app,host)
    async def exercise():
        async with app.router.lifespan_context(app) as state:
            assert state=={'fixture':True}
    import asyncio
    asyncio.run(exercise())
    assert calls==['host_start','worker_start','worker_stop','host_stop']


def test_committed_archive_approvals_versioned_without_free_text_promotion(system):
    from console.operator.archive_events import ArchiveEvents
    watcher=ArchiveEvents(system.op,Events(system.op))
    assert watcher.poll()==0
    system.supplier()
    assert watcher.poll()==1 and watcher.poll()==0
    system.forwarder()
    assert watcher.poll()==1 and watcher.poll()==0
    assert any(a['kind']=='quote_customer' for a in system.op.read()['actions'])
    with system.archive.write() as db:db.execute("UPDATE offers SET price_fen=11000")
    assert watcher.poll()==1
    assert len(system.model.calls)==0 and not system.bridge.calls


def test_archive_only_human_confirmed_payments_wake_operator(system):
    from console.operator.archive_events import ArchiveEvents
    from console.fulfil.service import Fulfil
    s=system;watcher=ArchiveEvents(s.op,Events(s.op))
    fulfil=Fulfil(store=s.archive,clock=lambda:s.now[0])
    payment=fulfil.write('payment_create',1,dict(kind='full',amount_fen='10000',currency='USD',received_on='2030-01-01',method='wire',evidence='fictional evidence'),'fictional-owner')
    assert watcher.poll()==0
    fulfil.write('payment_confirm',payment['id'],dict(confirm='1',qualifies='1'),'fictional-owner')
    assert watcher.poll()==1 and watcher.poll()==0
    assert any(a['kind']=='schedule_shipping' for a in s.op.read()['actions'])
    assert not s.bridge.commands and not s.bridge.calls


def test_wechat_app_scoped_ids_and_poison_timestamp(tmp_path):
    path=tmp_path/'wx.sqlite3'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE messages(app_id TEXT,message_id TEXT,payload TEXT)')
        for app,at in [('fictional-app-1',1),('fictional-app-2',2),('fictional-app-3',float('nan'))]:
            p=dict(group_id='fictional@chatroom',own_wxid='fictional-own',sender_id='fictional-peer',message_id='same-id',created_at=at,text='虚构消息')
            db.execute('INSERT INTO messages VALUES(?,?,?)',(app,'same-id',json.dumps(p)))
    snapshot=Source('groups',path,'wechat').scan()
    good=[r['message'] for r in snapshot['rows'] if r['message']]
    assert len(good)==2 and good[0]['key']==good[1]['key']
    assert good[0]['stable_id']!=good[1]['stable_id']
    assert snapshot['rows'][2]['error']=='source_row_invalid'


def test_slow_inbound_processing_cannot_delay_periodic_tick(system,monkeypatch):
    import threading
    worker=Worker(system.op,Events(system.op),[])
    entered,ticked,release=threading.Event(),threading.Event(),threading.Event()
    def slow(**kwargs):
        entered.set();release.wait(3)
    monkeypatch.setattr(worker,'run_once',slow)
    monkeypatch.setattr(system.op,'tick',lambda:ticked.set())
    worker.start()
    try:
        assert entered.wait(2)
        assert ticked.wait(2)
        assert not release.is_set()
    finally:
        release.set();worker.stop()
    assert not worker.thread.is_alive() and not worker.tick_thread.is_alive()


def test_production_worker_starts_at_current_tail_without_model_calls(system, tmp_path):
    source=ali(tmp_path/'inbound.sqlite3',['m1','m2'])
    connect_system(system,source)
    worker=Worker(system.op,Events(system.op),[source],seed_at_tail=True)
    assert worker.ingest(source)==0
    assert system.model.calls==[]
    with system.op.store.read() as db:
        assert db.execute('SELECT position FROM operator_cursors').fetchone()[0]==2
    ali(source.path,['m3'])
    assert worker.ingest(source)==1
    assert len(system.model.calls)==1
    assert system.model.calls[0]['messages'][-1]['text']=='Fictional inquiry m3'
