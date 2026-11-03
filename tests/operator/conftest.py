"""Only fictional parties/messages/prices, all databases under pytest tmp_path."""
from copy import deepcopy
from datetime import datetime, timezone
import json
from types import SimpleNamespace
import pytest
from console.archive.store import Store as ArchiveStore, insert
from console.inbox.model import make_key
from console.operator.bridge import FakeBridge
from console.operator.context import ArchiveContext
from console.operator.engine import Operator
from console.operator.store import dumps

STAMP = '2030-01-01T10:00:00+00:00'
KEYS = {name: make_key('conversation', channel, 'fictional-account', name)
        for name, channel in (('customer','whatsapp'),('supplier','wechat'),('forwarder','wechat'))}


class Model:
    def __init__(self):
        self.intent, self.calls, self.fact_ids = 'inquiry', [], []

    def decide(self, context, **kwargs):
        self.calls.append(deepcopy(context))
        return dict(plan=dict(intent=self.intent, fact_ids=self.fact_ids),
                    model=dict(provider='fake', requested_model='auto', actual_model='fictional-qwen', task_id='fake-task'))


class Inbox:
    def __init__(self):
        self.messages = {k:[dict(role='buyer',text='Fictional inquiry, please quote.',sent_at=1893492000)] for k in KEYS.values()}

    def detail(self, key, now):
        return dict(key=key, messages=self.messages.get(key, []))


@pytest.fixture
def system(tmp_path):
    archive = ArchiveStore(tmp_path/'archive.sqlite3', target_version=9)
    with archive.write() as db:
        for pid, kind in enumerate(('customer','supplier','forwarder'),1):
            insert(db,'parties',dict(id=pid,kind=kind,name='Fictional '+kind,status='active',created_at=STAMP,updated_at=STAMP), 'fixture')
            insert(db,'identities',dict(party_id=pid,conversation_key=KEYS[kind],channel='whatsapp' if pid==1 else 'wechat',created_at=STAMP,created_by='fixture'), 'fixture')
        insert(db,'deals',dict(id=1,title='Fictional lights',customer_party_id=1,source_conversation_key=KEYS['customer'],
                             stage='inquiry',status='open',created_at=STAMP,updated_at=STAMP,stage_changed_at=STAMP), 'fixture')
        for pid, kind in enumerate(('customer','supplier','forwarder'),1):
            insert(db,'deal_participants',dict(deal_id=1,party_id=pid,role=kind,created_at=STAMP),'fixture')
            insert(db,'deal_conversations',dict(deal_id=1,party_id=pid,conversation_key=KEYS[kind],created_at=STAMP),'fixture')
        insert(db,'products',dict(id=1,supplier_id=2,name='Fictional lamp',status='confirmed',units_per_carton=2,
            carton_l_cm=30,carton_w_cm=30,carton_h_cm=30,gross_kg=4,
            field_state_json=dumps({k:dict(state='confirmed') for k in ('units_per_carton','carton_l_cm','carton_w_cm','carton_h_cm','gross_kg')}),
            created_at=STAMP,updated_at=STAMP),'fixture')
        insert(db,'product_sources',dict(product_id=1,kind='manual',ref='fictional-product-sheet'),'fixture')
        insert(db,'deal_items',dict(id=1,deal_id=1,product_id=1,name='Fictional lamp',qty=4),'fixture')
        db.execute('CREATE TABLE deal_operation_context (deal_id INTEGER PRIMARY KEY,destination_country TEXT NOT NULL)')
        db.execute("INSERT INTO deal_operation_context VALUES(1,'ZZ')")
    inbox, model, bridge = Inbox(), Model(), FakeBridge()
    now = [datetime.fromisoformat(STAMP).timestamp()]
    policy = dict(status='confirmed',source_ref='fictional-approved-pricing-policy',fx_source='fictional-approved-fx',
        fx_x10000=70000,target_margin_bp=2000,currency='USD',incoterm='DDP',valid_until='2030-01-15',
        delivery_scope='fictional door delivery including taxes',terms_confirmed=True,
        freight_route=dict(forwarder_id=3,origin='Fictional origin',destination_country='ZZ',mode='air',service='Fictional DDP',unit='kg'))
    source = ArchiveContext(archive.path,inbox,quote_policy=lambda deal: deepcopy(policy))
    op = Operator(tmp_path/'operator.sqlite3',source=source,model=model,bridge=bridge,clock=lambda:now[0])
    source.profiles = op.profiles
    for kind, key in KEYS.items():
        op.write('profile',None,dict(operation_key='profile-'+kind,conversation_key=key,
            type={'customer':'customer_wholesale','supplier':'supplier_product','forwarder':'supplier_logistics'}[kind],
            timezone='UTC',language='en',tone='professional',hourly_limit='6',daily_limit='20',interval_seconds='30'),'fictional-owner')
    state = SimpleNamespace(op=op,archive=archive,source=source,inbox=inbox,model=model,bridge=bridge,now=now,policy=policy,keys=KEYS)
    def supplier(expiry='2030-01-15'):
        with archive.write() as db:
            insert(db,'offers',dict(product_id=1,price_fen=10000,currency='CNY',status='confirmed',source_ref='fictional-supplier-message',
                quoted_on='2029-12-01',valid_until=expiry,created_at=STAMP,updated_at=STAMP),'fixture')
    def forwarder(expiry='2030-01-15'):
        with archive.write() as db:
            insert(db,'freight_quotes',dict(forwarder_id=3,origin='Fictional origin',destination_country='ZZ',mode='air',service='Fictional DDP',
                unit='kg',price_fen=200,currency='CNY',status='confirmed',source_ref='fictional-forwarder-message',quoted_on='2030-01-01',
                valid_until=expiry,created_at=STAMP,updated_at=STAMP),'fixture')
    state.supplier, state.forwarder = supplier, forwarder
    state.event = lambda eid='event-1',kind='inbound',party='customer': dict(id=eid,kind=kind,conversation_key=KEYS[party],deal_id=1)
    return state


def switch(op, scope, key, enabled=True):
    import uuid
    return op.write('settings',None,dict(scope=scope,key=key,enabled='1' if enabled else '0',operation_key=str(uuid.uuid4())),'fictional-owner')


def enable(system, level=1, party='supplier'):
    op=system.op
    switch(op,'master','enabled')
    switch(op,'master','dry_run',False)
    switch(op,'levels',str(level))
    typ={'supplier':'supplier_product','forwarder':'supplier_logistics','customer':'customer_wholesale'}[party]
    switch(op,'types',f'{level}:{typ}')
    switch(op,'contacts',f'{level}:{system.keys[party]}')


@pytest.fixture(autouse=True)
def forbid_external_io(monkeypatch):
    import socket
    import subprocess
    def blocked(*args, **kwargs):
        raise AssertionError('operator isolation forbids real network/model calls')
    monkeypatch.setattr(socket.socket, 'connect', blocked)
    monkeypatch.setattr(socket, 'create_connection', blocked)
    monkeypatch.setattr(subprocess, 'run', blocked)
    import sqlite3
    from pathlib import Path
    connect = sqlite3.connect
    def isolated_database(path, *args, **kwargs):
        raw = str(path)
        assert raw == ':memory:' or raw.startswith(('/tmp/', 'file:///tmp/', 'file:/tmp/')), 'operator tests must use temporary SQLite only'
        return connect(path, *args, **kwargs)
    monkeypatch.setattr(sqlite3, 'connect', isolated_database)
