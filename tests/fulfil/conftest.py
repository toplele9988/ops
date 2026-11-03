"""Every E1 fixture is synthetic; network and real AI execution are forbidden."""
from datetime import date
import socket
import subprocess
from types import SimpleNamespace

import pytest

from console.archive.service import Archive
from console.archive.store import Store, insert
from console.deals.service import Deals
from console.deals.rules import paid
from console.fulfil.service import Fulfil


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError('fulfil tests must never use networks or real AI')
    monkeypatch.setattr(socket.socket, 'connect', blocked)
    monkeypatch.setattr(socket, 'create_connection', blocked)
    original = subprocess.Popen
    def guarded(command, *args, **kwargs):
        if 'ai-task' in str(command):
            blocked()
        return original(command, *args, **kwargs)
    monkeypatch.setattr(subprocess, 'Popen', guarded)
    monkeypatch.setattr('console.inbox.ai.official_runner', blocked)


@pytest.fixture
def fulfil_env(tmp_path):
    store = Store(tmp_path / 'fictional-e1.sqlite3', target_version=8)
    clock = lambda: 1893456000
    today = lambda: date(2030, 1, 1)
    archive = Archive(store=store, today=today)
    deals = Deals(store=store, lookup=lambda key: None, clock=clock)
    service = Fulfil(store=store, deals=deals, clock=clock, today=today)
    def aw(op, row_id=None, **fields):
        return archive.write(op, row_id, fields, 'fictional-owner')['id']
    customer = aw('party_create', kind='customer', name='虚构客户灯塔')
    supplier = aw('party_create', kind='supplier', name='虚构供应商星河')
    forwarder = aw('party_create', kind='forwarder', name='虚构货代远洋')
    product = aw('product_create', name='虚构灯', supplier_id=str(supplier))
    def deal(stage='paid', title='虚构灯案子'):
        did = deals.write('create', None, dict(title=title, customer_party_id=str(customer),
                          stage='inquiry' if paid(stage) else stage), 'fictional-owner')['id']
        if paid(stage):
            # Explicit legacy fixture: old won deals may have no payment ledger.
            # New payment flows below start at inquiry and use payment_confirm.
            with store.write() as db:
                db.execute("UPDATE deals SET stage=?,status='won',won_at=created_at WHERE id=?", (stage, did))
        deals.write('item', did, dict(name='虚构灯', product_id=str(product), qty='2', target_price='99.99',
                                      note='内部成本 secret'), 'fictional-owner')
        return did
    did = deal()
    def write(op, rid=did, child=None, **fields):
        return service.write(op, rid, fields, 'fictional-owner', child)
    def quote(target=did, currency='USD'):
        at = '2030-01-01T00:00:00+00:00'
        with store.write() as db:
            n = db.execute('SELECT COALESCE(MAX(round_no),0)+1 FROM quote_rounds WHERE deal_id=?', (target,)).fetchone()[0]
            rid = insert(db, 'quote_rounds', dict(deal_id=target, round_no=n, status='accepted', currency=currency,
                         created_at=at, updated_at=at, notes='报价内部成本999'), 'fictional-owner')
            lid = insert(db, 'quote_lines', dict(round_id=rid, product_id=product, name='虚构灯', qty=2,
                         sell_unit_minor=3500, cost_unit_fen=1379, note='内部利润4321'), 'fictional-owner')
        return rid, lid
    return SimpleNamespace(store=store, deals=deals, service=service, customer=customer, supplier=supplier,
                           forwarder=forwarder, product=product, did=did, deal=deal, write=write, quote=quote)
