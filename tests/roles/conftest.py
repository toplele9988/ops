from datetime import datetime, timezone
from types import SimpleNamespace
import socket
import subprocess

import pytest

from console.archive.service import Archive
from console.archive.store import Store
from console.deals.service import Deals
from console.deals.rules import paid
from console.inbox.ai import RunnerOutput
from console.inbox.model import make_key
from console.quotes.service import Quotes
from console.roles import Roles
from console.roles.common import Config


class Clock:
    def __init__(self):
        self.now = datetime(2030, 1, 10, 8, tzinfo=timezone.utc).timestamp()

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class Inbox:
    def __init__(self):
        self.conversations, self.missing, self.calls = {}, set(), []
        self.ready = True

    def read(self, now=None):
        return dict(ready=self.ready, conversations={k: c for k, c in self.conversations.items() if k not in self.missing})

    def detail(self, key, now=None):
        self.calls.append(key)
        return self.read(now)['conversations'].get(key)


class Hub:
    def __init__(self):
        self.calls = []
        self.index = SimpleNamespace(conversation_scope=lambda *args: None)

    def background(self):
        return '虚构公司背景；价格和交期须人工核对'

    def context(self, query, purpose, **kwargs):
        self.calls.append((query, purpose, kwargs))
        return dict(text='虚构公开知识：灯具需核对电压。', sources=[dict(source_id='fictional-chunk-1', title='虚构资料', type='技术资料', date='2030-01-01')])


def answer(text, model='fictional-model'):
    return RunnerOutput('语言：中文\n' + text + '\n=====待确认=====\n请人工核对', model)


class Env:
    def __init__(self, tmp_path):
        self.path = tmp_path / 'archive.sqlite3'
        self.clock, self.inbox, self.hub = Clock(), Inbox(), Hub()
        self.store = Store(self.path)
        self.archive = Archive(store=self.store, today=lambda: datetime.fromtimestamp(self.clock(), timezone.utc).date())
        self.deals = Deals(store=self.store, inbox=self.inbox, clock=self.clock)
        self.fx = tmp_path / 'fx.json'
        self.fx.write_text('{"rate":"7.0","source":"fictional-cache","fetched_at":1894233600}')
        self.quotes = Quotes(store=self.store, clock=self.clock, today=lambda: datetime.fromtimestamp(self.clock(), timezone.utc).date(), fx_path=self.fx)
        self.roles = self.service()
        self.counter = 0

    def service(self, **kwargs):
        options = dict(store=self.store, inbox=self.inbox, clock=self.clock, hub=self.hub,
                       config=Config(timezone='UTC', per_minute=600, per_hour=600), runner=None)
        options.update(kwargs)
        return Roles(**options)

    def aw(self, op, row_id=None, **fields):
        return self.archive.write(op, row_id, fields, 'fictional-owner')['id']

    def dw(self, op, row_id=None, **fields):
        return self.deals.write(op, row_id, fields, 'fictional-owner')['id']

    def qw(self, op, row_id, child_id=None, **fields):
        return self.quotes.write(op, row_id, fields, 'fictional-owner', child_id)['id']

    def conv(self, *, name='虚构 Hasan', text='Please confirm ZX Beam shipping', role='buyer', age=0, channel='alibaba', counterpart='customer', key=None):
        self.counter += 1
        key = key or make_key('conversation', channel, 'fictional-account', 'fictional-conversation-' + str(self.counter))
        at = self.clock() - age
        conv = dict(key=key, channel=channel, channel_label='虚构渠道', account='fictional-account', buyer_name=name,
                    counterpart_role=counterpart, is_group=channel == 'wechat', country='ZZ', first_seen=at,
                    last_seen=at, n_messages=1, messages=[dict(role=role, text=text, sent_at=at, message_id='fictional-message-' + str(self.counter))])
        self.inbox.conversations[key] = conv
        return key

    def message(self, key, text='虚构新问题', role='buyer', at=None):
        conv = self.inbox.conversations[key]
        when = self.clock() if at is None else at
        conv['messages'].append(dict(role=role, text=text, sent_at=when))
        conv['messages'].sort(key=lambda m: m['sent_at'])
        conv.update(last_seen=max(m['sent_at'] for m in conv['messages']), n_messages=len(conv['messages']))

    def deal(self, *, key=None, stage='inquiry', title='虚构灯具案子'):
        fields = dict(title=title, stage='inquiry' if paid(stage) else stage)
        if key:
            fields.update(conversation_key=key, party_kind='customer', party_name=self.inbox.conversations[key]['buyer_name'])
        did = self.dw('create', **fields)
        if paid(stage):
            self.historical_paid(did, stage=stage)
        return did

    def historical_paid(self, did, *, stage='paid'):
        # Explicit legacy fixture without a ledger, preserving quote fallback tests.
        stamp = datetime.fromtimestamp(self.clock(), timezone.utc).isoformat(timespec='microseconds')
        with self.store.write() as db:
            db.execute("UPDATE deals SET stage=?,status='won',won_at=?,stage_changed_at=?,updated_at=? WHERE id=?",
                       (stage, stamp, stamp, stamp, did))

    def good_round(self, *, key=None, stage='quoting', amount='20.00', currency='USD'):
        self.counter += 1
        supplier = self.aw('party_create', kind='supplier', name='FICTIONAL-SECRET-FACTORY-' + str(self.counter))
        product = self.aw('product_create', supplier_id=str(supplier), name='ZX Beam Lamp ' + str(self.counter),
                          units_per_carton='2', carton_l_cm='40', carton_w_cm='30', carton_h_cm='20', gross_kg='5')
        offer = self.aw('offer_create', product, price='13.47', currency='CNY', unit='pcs', quoted_on='2029-12-01', valid_until='2030-02-20')
        did = self.deal(key=key, stage=stage)
        self.dw('item', did, name='ZX Beam Lamp', product_id=str(product), qty='2')
        rid = self.qw('create', did)
        self.qw('update', rid, incoterm='EXW', currency=currency, fx_rate='7.0', valid_until='2030-02-20',
                destination_country='Brazil')
        line = self.quotes.read('detail', row_id=rid)['lines'][0]['id']
        self.qw('line_update', rid, line, sell_unit_price=amount)
        cost = self.qw('cost', rid, kind='freight', manual='1', amount='10.00', source_note='虚构运价', valid_until='2030-02-20',
                       destination_country='Brazil', mode='sea')
        # Since R2-B a usable quote needs the round's payment terms to be confirmed first.
        self.qw('terms', rid, confirm='1', payment='30_70', delivery_scope='虚构交付范围：按本轮约定交付')
        return did, rid, line, cost, product, offer


@pytest.fixture(autouse=True)
def offline(monkeypatch, tmp_path):
    from console.dossiers import store as dossier_store
    monkeypatch.setattr(dossier_store, 'DB_PATH', tmp_path / 'disabled-dossiers.sqlite3')
    def blocked(*args, **kwargs):
        raise AssertionError('roles tests must never access network or real AI')
    monkeypatch.setattr(socket.socket, 'connect', blocked)
    monkeypatch.setattr(socket, 'create_connection', blocked)
    original = subprocess.run
    def guarded(command, *args, **kwargs):
        if 'imgw-ai-task' in str(command) or 'ai-task' in str(command):
            return blocked()
        return original(command, *args, **kwargs)
    monkeypatch.setattr(subprocess, 'run', guarded)


@pytest.fixture
def env(tmp_path):
    return Env(tmp_path)
