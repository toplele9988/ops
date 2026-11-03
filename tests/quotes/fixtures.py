"""All fixtures are fictional; tests never read live FX or business databases."""
from datetime import date
from pathlib import Path
import tempfile
from console.archive.service import Archive
from console.archive.store import Store
from console.deals.service import Deals
from console.quotes.service import Quotes


class Fixture:
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='fictional-quotes-')
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'archive.sqlite3'
        self.fx_path = Path(self.tmp.name) / 'fx.json'
        self.fx_path.write_text('{"rate":"7.123456","source":"fictional-cache","source_updated":"2030-01-01 08:00","fetched_at":1893456000.125}')
        self.today = date(2030, 1, 1)
        self.store = Store(self.path)
        self.archive = Archive(store=self.store, today=lambda:self.today)
        self.deals = Deals(store=self.store, lookup=lambda key:None, clock=lambda:1893456000)
        self.quotes = Quotes(store=self.store, today=lambda:self.today, fx_path=self.fx_path, clock=lambda:1893456000)

    def aw(self, op, row=None, **fields):
        return self.archive.write(op, row, fields, 'fictional-owner')['id']

    def qw(self, op, row=None, child=None, **fields):
        return self.quotes.write(op, row, fields, 'fictional-owner', child)['id']

    def product(self, **packing):
        supplier = self.aw('party_create', kind='supplier', name='FICTIONAL-SECRET-SUPPLIER')
        values = dict(name='Fictional Lamp', supplier_id=str(supplier), units_per_carton='2', carton_l_cm='50',
                      carton_w_cm='40', carton_h_cm='30', gross_kg='8')
        values.update(packing)
        product = self.aw('product_create', **values)
        offer = self.aw('offer_create', product, price='123.47', currency='CNY', unit='pcs',
                        quoted_on='2029-12-01', valid_until='2030-02-01', lead_days_min='8', lead_days_max='12')
        return product, offer

    def freight(self, **changes):
        with self.store.read() as db:
            row = db.execute("SELECT id FROM parties WHERE kind='forwarder'").fetchone()
        party = row[0] if row else self.aw('party_create', kind='forwarder', name='FICTIONAL-SECRET-FORWARDER')
        values = dict(forwarder_id=str(party), origin='Fictional port', destination_country='ZZ', mode='air',
                      service='FICTIONAL-SECRET-RATE', unit='kg', price='2.31', currency='CNY', min_charge='71.29',
                      quoted_on='2029-12-01', valid_until='2030-02-01')
        values.update(changes)
        return self.aw('freight_create', **values)

    def setup_round(self, *, freight=True):
        product, offer = self.product()
        did = self.deals.write('create', None, {'title':'Fictional deal'}, 'fictional-owner')['id']
        self.deals.write('item', did, dict(name='Fictional Lamp', product_id=str(product), qty='3'), 'fictional-owner')
        rid = self.qw('create', did)
        line = self.detail(rid)['lines'][0]['id']
        self.qw('line_update', rid, line, sell_unit_price='99.13')
        self.qw('update', rid, valid_until='2030-01-30', destination_country='ZZ')
        self.qw('terms', rid, confirm='1', payment='30_70', delivery_scope='虚构交付范围：按本轮约定交付')
        cost = self.qw('cost', rid, kind='freight', freight_quote_id=str(self.freight())) if freight else None
        return did, rid, product, offer, line, cost

    def detail(self, rid):
        return self.quotes.read('detail', row_id=rid)
