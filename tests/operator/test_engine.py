import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from copy import deepcopy
import pytest
from console.archive.validation import BadRequest, Conflict
from console.operator.facts import guard
from console.operator.store import dumps
from conftest import switch, enable


def action(rows, kind):
    return next(a for a in rows if a['kind']==kind)


def test_inquiry_parallel_and_reply_continuation(system):
    s=system
    first=s.op.handle(s.event())
    assert {a['kind'] for a in first} >= {'ask_supplier','ask_forwarder','reply_customer'}
    assert all(a['state']=='dry_run' for a in first)
    assert not s.bridge.calls
    s.supplier()
    second=s.op.handle(s.event('supplier-message','supplier_reply','supplier'))
    assert any(a['kind']=='ask_forwarder' for a in second)
    assert not any(a['kind']=='quote_customer' for a in second)
    s.forwarder()
    third=s.op.handle(s.event('freight-message','forwarder_reply','forwarder'))
    quote=action(third,'quote_customer')
    assert quote['level']==3 and quote['state']=='dry_run'
    text='\n'.join(quote['data']['parts'])
    assert 'USD' in text and 'Fictional lamp' in text
    assert '100.00' not in text and 'cost' not in text and 'CNY' not in text
    calc=quote['data']['calculation']['result']
    assert not calc['blocking'] and calc['totals']['profit_cny'] is not None
    assert s.op.handle(s.event('freight-message','forwarder_reply','forwarder'))==third
    records=s.op.read()['decisions']
    assert any(r['phase']=='decision' and r['data']['context']['offers'] for r in records)
    assert s.model.calls[0]['purpose']=='customer_reply'
    assert not {'offers','freight_quotes','parties'} & s.model.calls[0].keys()


@pytest.mark.parametrize('bits',range(8))
def test_three_switch_intersection(system,bits):
    s=system
    switch(s.op,'master','enabled');switch(s.op,'master','dry_run',False)
    switch(s.op,'levels','1',bool(bits&1))
    switch(s.op,'types','1:supplier_product',bool(bits&2))
    switch(s.op,'contacts','1:'+s.keys['supplier'],bool(bits&4))
    row=action(s.op.handle(s.event()),'ask_supplier')
    assert row['state']==('ready' if bits==7 else 'pending')
    s.op.dispatch(row['id'])
    assert len(s.bridge.calls)==(1 if bits==7 else 0)
    assert all(c['channel']=='wechat' for c in s.bridge.calls)


def test_dry_run_is_never_released_on_switch_enable(system):
    s=system
    row=action(s.op.handle(s.event()),'ask_supplier')
    enable(s)
    assert s.op.dispatch(row['id'])['state']=='dry_run'
    assert not s.bridge.calls
    result=s.op.write('approve',row['id'],dict(operation_key='approve-test'),'fictional-owner')
    assert result['action']['state']=='scheduled'
    assert len(s.bridge.calls)==1


def test_rehearsal_cannot_be_overridden_by_approval(system):
    row=action(system.op.handle(system.event()),'ask_supplier')
    with pytest.raises(Conflict):
        system.op.write('approve',row['id'],dict(operation_key='approve-test'),'fictional-owner')
    assert not system.bridge.calls


def test_night_queues_until_recipient_morning_and_stop(system):
    s=system;enable(s)
    s.now[0]=datetime.fromisoformat('2030-01-01T23:00:00+00:00').timestamp()
    row=action(s.op.handle(s.event()),'ask_supplier')
    assert row['state']=='scheduled' and row['reason']=='recipient_night'
    assert datetime.fromtimestamp(row['due_at'],timezone.utc).hour==8
    s.op.tick();assert not s.bridge.calls
    s.now[0]=row['due_at']
    s.op.tick();assert len(s.bridge.calls)==1
    switch(s.op,'master','enabled',False)
    s.now[0]+=60;s.op.tick();assert len(s.bridge.calls)==1
    assert action(s.op.read()['actions'],'ask_supplier')['state']=='dry_run'


@pytest.mark.parametrize('quota,wait', [('hourly_limit',3600),('daily_limit',86400)])
def test_rolling_limits_and_intervals(system,quota,wait):
    s=system;enable(s)
    with s.op.store.write() as db:
        p=json.loads(db.execute('SELECT value FROM operator_profiles WHERE conversation_key=?',(s.keys['supplier'],)).fetchone()[0]);p[quota]=1
        db.execute('UPDATE operator_profiles SET value=? WHERE conversation_key=?',(dumps(p),s.keys['supplier']))
    row=action(s.op.handle(s.event()),'ask_supplier')
    assert s.op.dispatch(row['id'])['state']=='scheduled'
    s.now[0]+=29
    assert s.op.dispatch(row['id'])['state']=='scheduled'
    assert len(s.bridge.calls)==1
    s.now[0]+=2
    row=s.op.dispatch(row['id'])
    assert row['reason']=='rate_or_interval' and row['due_at']>=s.now[0]+wait-32
    assert len(s.bridge.calls)==1


@pytest.mark.parametrize('outcome',['unknown','failed'])
def test_unknown_and_failed_never_retry_even_new_event(system,outcome):
    s=system;enable(s);s.bridge.outcome=outcome
    row=action(s.op.handle(s.event()),'ask_supplier')
    assert s.op.dispatch(row['id'])['state']==outcome
    for _ in range(3):s.op.dispatch(row['id'])
    s.now[0]+=31
    other=action(s.op.handle(s.event('new-message')),'ask_supplier')
    assert s.op.dispatch(other['id'])['state']=='duplicate'
    assert len(s.bridge.calls)==1


def test_duplicate_event_and_concurrent_send_have_at_most_one_bridge_call(system):
    s=system;enable(s)
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _:s.op.handle(s.event()),range(2)))
    assert len(s.model.calls)==1
    row=action(s.op.read()['actions'],'ask_supplier')
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _:s.op.dispatch(row['id']),range(2)))
    assert len(s.bridge.calls)<=1


@pytest.mark.parametrize('expiry',[None,'2029-12-31'])
def test_missing_expired_cost_triggers_new_request(system,expiry):
    s=system;s.supplier(expiry);s.forwarder()
    rows=s.op.handle(s.event('supplier-price','supplier_reply','supplier'))
    assert any(a['kind']=='ask_supplier' for a in rows)
    assert not any(a['kind']=='quote_customer' for a in rows)


def test_quote_rechecked_on_send(system):
    s=system;s.supplier();s.forwarder();enable(s,3,'customer')
    row=action(s.op.handle(s.event()),'quote_customer')
    s.now[0]=datetime.fromisoformat('2030-01-16T10:00:00+00:00').timestamp()
    assert s.op.dispatch(row['id'])['state']=='blocked'
    assert not s.bridge.calls


def test_unsourced_numbers_promises_and_leaks_blocked(system):
    s=system
    row=action(s.op.handle(s.event()),'reply_customer');ctx=row['data']['context']
    for malicious in ('Price USD 999.', 'Delivery guaranteed tomorrow.', 'Another buyer paid USD 1.', '成本价 CNY 100'):
        data=deepcopy(row['data']);data['parts'].append(malicious)
        assert guard(data,ctx,s.op.day())==['unsourced_content']
    bad=dict(id='secret',kind='cost',text='100',status='confirmed',source_ref='secret',observed_at='2030-01-01',
             valid_until='2030-02-01',public=False,is_cost_price=True,confidential=True)
    ctx['facts'].append(bad);data=deepcopy(row['data']);data['fact_ids']=['secret'];data['parts'].append('100')
    assert guard(data,ctx,s.op.day())==['source_unusable']
    with pytest.raises(BadRequest):
        s.op.write('edit',row['id'],dict(operation_key='edit-failure',text='Price USD 999.'),'fictional-owner')


def test_alibaba_draft_only_and_payment_always_manual(system):
    s=system;enable(s,3,'customer')
    s.model.intent='payment_report'
    row=action(s.op.handle(s.event()),'confirm_payment')
    assert row['state']=='blocked'
    with pytest.raises(BadRequest):s.op.write('approve',row['id'],dict(operation_key='payment-approve'),'fictional-owner')
    assert not s.bridge.calls and not s.bridge.commands
    from console.operator.policy import evaluate
    settings=s.op.read()['settings'];settings['levels']['2']=True
    st,reason,_=evaluate(settings,dict(kind='reply_customer',contact_key='ali'),dict(type='customer_wholesale',channel='alibaba'),s.now[0],approved=True)
    assert (st,reason)==('pending','channel_unavailable')


def test_source_identity_mismatch_stops_send(system):
    s=system;enable(s)
    row=action(s.op.handle(s.event()),'ask_supplier')
    with s.archive.write() as db:db.execute("UPDATE parties SET status='paused' WHERE id=2")
    assert s.op.dispatch(row['id'])['state']=='blocked'
    assert not s.bridge.calls


def test_read_does_not_create_runtime_database(tmp_path):
    from console.operator.engine import Operator
    op=Operator(tmp_path/'absent.sqlite3',source=None)
    assert op.read()['settings']['enabled'] is False
    assert not (tmp_path/'absent.sqlite3').exists()
