from datetime import date
import json
import socket
import subprocess
from types import SimpleNamespace

import pytest

from console.archive.service import Archive
from console.archive.store import Store
from console.listing.config import Config
from console.listing.service import Listing

TODAY = date(2030, 1, 10)


class Hub:
    def __init__(self):
        self.calls = []

    def search(self, query, *, purpose, top_k, families):
        self.calls.append((query, purpose, top_k, families))
        return dict(ready=True, mode='lexical', items=[
            dict(family='technical', source_type_label='公开技术参考', confidential=False, is_cost_price=False,
                 source_id='kb_public_fixture_001', snippet='The fictional X900 is recorded as a 120 W fixture.'),
            dict(family='supplier', source_type_label='供应商资料', confidential=True, is_cost_price=True,
                 source_id='kb_supplier_secret', snippet='FICTIONAL_FACTORY_SECRET costs 999.99 CNY.'),
            dict(family='technical', source_type_label='其他资料', confidential=True, is_cost_price=False,
                 source_id='kb_internal_technical', snippet='Fictional Buyer Alice asked for details.'),
            dict(family='technical', source_type_label='公开技术参考', confidential=False, is_cost_price=True,
                 source_id='kb_public_cost_labeled', snippet='Retail price 999.99 CNY.'),
        ])


def ai_answer(prompt=None):
    return '''语言：English
TITLE
120 W LED Flood Light X900
KEYWORDS
LED flood light; stage lighting; X900
SELLING_POINTS
- Power: 120 W
- Product size: 20 × 10 × 5 cm
- Carton size: 40 × 30 × 20 cm
- Gross weight: 5 kg
- Units per carton: 2
ATTRIBUTES
Model: X900
Category: LED Flood Light
Power: 120 W
Product size: 20 × 10 × 5 cm
Carton size: 40 × 30 × 20 cm
Gross weight: 5 kg
Units per carton: 2
MOQ: 2
Lead time: 10–15 days after order confirmation
Voltage: To be confirmed: voltage
Light source: To be confirmed: light source
Ingress protection: To be confirmed: ingress protection
Material: To be confirmed: material
Certifications: To be confirmed: certifications
DESCRIPTION
LED Flood Light, model X900. Power: 120 W. Product size: 20 × 10 × 5 cm.
CHINESE_REVIEW
给老板核对：型号 X900，功率 120 W。电压、光源、防护等级、材质和认证待补充。
=====待确认=====
请核对缺失属性'''


class Fixture:
    def __init__(self, tmp_path):
        self.path = tmp_path / 'fictional-listing.sqlite3'
        self.store = Store(self.path, target_version=9)
        self.archive = Archive(store=self.store, today=lambda: TODAY)
        self.hub = Hub()
        self.calls = []
        self.clock = lambda: 1894262400.0
        self.fx = lambda: dict(usd_cny='7.0000', source='fictional-cache', as_of=1894233600)
        self.owner = 'fictional-owner'
        self.supplier = self.archive.write('party_create', None,
            dict(kind='supplier', name='FICTIONAL_FACTORY_SECRET', short_name='FICTIONAL_FACTORY_SECRET'),
            self.owner)['id']
        self.product = self.archive.write('product_create', None, dict(
            supplier_id=str(self.supplier), name='Fictional Flood Light', model='X900',
            category='LED Flood Light', wattage='120', description=(
                'FICTIONAL_FACTORY_SECRET; Fictional Buyer Alice; internal cost 999.99 CNY'),
            product_l_cm='20', product_w_cm='10', product_h_cm='5',
            carton_l_cm='40', carton_w_cm='30', carton_h_cm='20', gross_kg='5', net_kg='4',
            units_per_carton='2'), self.owner)['id']
        self.archive.write('product_confirm', self.product, {}, self.owner)
        self.offer = self.archive.write('offer_create', self.product, dict(
            price='101.23', currency='CNY', unit='pcs', quoted_on='2030-01-01',
            valid_until='2030-02-20', moq_units='2', lead_days_min='10', lead_days_max='15',
            source_ref='fictional-cost-source'), self.owner)['id']
        with self.store.write() as db:
            db.execute("INSERT INTO product_sources(product_id,kind,ref,title,note) VALUES (?,?,?,?,?)",
                       (self.product, 'manual', 'fictional-source', 'Fictional internal title',
                        'Fictional Buyer Alice; FICTIONAL_FACTORY_SECRET'))
        self.runner = self.fake_runner
        self.config = Config(per_minute=36, per_hour=36, ai_timeout=2)
        self.service = self.make_service()

    def fake_runner(self, prompt):
        self.calls.append(prompt)
        return ai_answer(prompt)

    def failing_runner(self, prompt):
        self.calls.append(prompt)
        raise RuntimeError('fictional provider failure')

    def make_service(self, **kwargs):
        options = dict(store=self.store, runner=self.runner, hub=self.hub, clock=self.clock,
                       today=lambda: TODAY, fx_reader=self.fx, config=self.config,
                       brand_terms=('BLOCKED_BRAND_FIXTURE',))
        options.update(kwargs)
        return Listing(**options)

    def generate(self, **fields):
        payload = dict(product_id=str(self.product), channel='alibaba')
        payload.update(fields)
        return self.service.generate(payload, self.owner)

    def fw(self, operation, row_id=None, **fields):
        return self.service.write(operation, row_id, fields, self.owner)


@pytest.fixture
def env(tmp_path):
    return Fixture(tmp_path)


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError('listing tests must not access network or real AI')

    monkeypatch.setattr(socket.socket, 'connect', blocked)
    monkeypatch.setattr(socket, 'create_connection', blocked)
    original = subprocess.run

    def guarded(command, *args, **kwargs):
        if 'imgw-ai-task' in str(command) or 'ai-task' in str(command):
            return blocked()
        return original(command, *args, **kwargs)

    monkeypatch.setattr(subprocess, 'run', guarded)

