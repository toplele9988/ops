from datetime import date
import json
import unittest
from unittest.mock import patch
from console.archive import validation as v
from console.archive.store import Store, insert
from console.quotes.fx import read_fx
from fixtures import Fixture


class ServiceTests(Fixture, unittest.TestCase):
    def test_freight_options_arithmetic_channel_recommendations_and_cost_match(self):
        did,rid,*_ = self.setup_round(freight=False)
        rates = [
            self.freight(price='2.31', min_charge='0', transit_days_min='8', transit_days_max='12', channel='forwarder'),
            self.freight(price='3.00', min_charge='0', transit_days_min='2', transit_days_max='4', channel='platform_alibaba'),
            self.freight(price='2.50', min_charge='0', transit_days_min='5', transit_days_max='7', channel='other'),
        ]
        comparison = self.quotes.read('freight_options', row_id=rid)
        by_id = {item['id']: item for item in comparison['items']}
        self.assertEqual([by_id[rate]['amount_cny'] for rate in rates], ['55.44', '72.00', '60.00'])
        self.assertEqual(by_id[rates[0]]['recommendations'], ['cheapest'])
        self.assertEqual(by_id[rates[1]]['recommendations'], ['fastest'])
        self.assertEqual(by_id[rates[2]]['recommendations'], ['balanced'])
        self.assertEqual(by_id[rates[1]]['channel'], 'platform_alibaba')
        self.assertEqual(by_id[rates[1]]['billing_unit'], 'kg')
        self.assertEqual(by_id[rates[1]]['billing_quantity'], '24.000')
        self.assertEqual(by_id[rates[1]]['cbm'], '0.120')
        for index, rate_id in enumerate(rates):
            self.qw('freight_choose', rid, confirm='1', freight_quote_id=str(rate_id), selection_mode='append', request_key='fictional-append-' + str(index), segment='虚构运输段 ' + str(index), reason='人工核对测试计价')
        costs = {cost['freight_quote_id']: cost['amount'] for cost in self.detail(rid)['costs']}
        self.assertEqual(costs, {rate_id: by_id[rate_id]['amount_cny'] for rate_id in rates})

    def test_freight_options_incomplete_packing_reason_and_unspecified_destination(self):
        did,rid,*_ = self.setup_round(freight=False)
        rate_id = self.freight(transit_days_min='3', transit_days_max='5')
        self.qw('line_update', rid, self.detail(rid)['lines'][0]['id'], qty='3')
        with self.store.write() as db:
            db.execute('UPDATE quote_lines SET carton_l_cm=NULL WHERE round_id=?', (rid,))
            db.execute('UPDATE quote_rounds SET destination_country=NULL WHERE id=?', (rid,))
        comparison = self.quotes.read('freight_options', row_id=rid)
        item = next(item for item in comparison['items'] if item['id'] == rate_id)
        self.assertTrue(comparison['destination_unspecified'])
        self.assertIn('所有目的国', comparison['destination_notice'])
        self.assertFalse(item['calculable'])
        self.assertIn('第1条产品缺少外箱长', item['calculation_reasons'])
        self.assertEqual(item['amount_cny'], None)
        self.assertEqual(item['recommendations'], ['fastest'])

    def test_freight_options_exclude_expired_and_no_expiry_and_expose_expiry_days(self):
        did,rid,*_ = self.setup_round(freight=False)
        self.freight(channel='forwarder', quoted_on='2029-12-01', valid_until='2029-12-31')
        self.freight(channel='platform_alibaba', quoted_on='2029-12-01', valid_until='')
        today_rate = self.freight(channel='other', quoted_on='2029-12-01', valid_until='2030-01-01')
        items = self.quotes.read('freight_options', row_id=rid)['items']
        self.assertEqual([item['id'] for item in items], [today_rate])
        self.assertEqual(items[0]['validity'], 'expiring')
        self.assertEqual(items[0]['remaining_validity_days'], 0)

    def test_freight_compare_filters_current_rates_by_country_and_mode(self):
        self.setup_round(freight=False)
        air = self.freight(channel='platform_alibaba')
        sea = self.freight(channel='other', mode='sea', unit='cbm')
        other_country = self.freight(channel='forwarder', destination_country='YY')
        result = self.quotes.read('freight_compare', {'destination_country': 'ZZ', 'mode': 'air'})
        self.assertEqual([item['id'] for item in result['items']], [air])
        self.assertEqual(result['items'][0]['channel'], 'platform_alibaba')
        all_rows = self.quotes.read('freight_compare')
        self.assertEqual({item['id'] for item in all_rows['items']}, {air, sea, other_country})

    def test_freight_compare_reads_archive_without_quote_round_tables(self):
        path = self.path.parent / 'fictional-legacy-freight.sqlite3'
        store = Store(path, target_version=2)
        with store.write() as db:
            forwarder = insert(db, 'parties', dict(kind='forwarder', name='虚构旧结构货代',
                created_at='2029-12-01', updated_at='2029-12-01'), 'fictional-owner')
            freight_id = insert(db, 'freight_quotes', dict(forwarder_id=forwarder, origin='虚构起运地',
                destination_country='ZZ', destination_detail=None, mode='air', service='虚构旧服务', unit='kg',
                price_fen=250, currency='CNY', min_charge_fen=1000, transit_days_min=3,
                transit_days_max=5, quoted_on='2029-12-01', valid_until='2030-02-01', source_ref=None,
                notes=None, status='confirmed', created_at='2029-12-01', updated_at='2029-12-01'),
                'fictional-owner')
        compare = self.quotes.__class__(path, today=lambda:self.today, fx_path=self.fx_path)
        result = compare.read('freight_compare')
        self.assertEqual([item['id'] for item in result['items']], [freight_id])
        self.assertEqual(result['items'][0]['channel'], 'forwarder')
        with store.read() as db:
            self.assertIsNone(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='quote_rounds'").fetchone())

    def test_default_copy_currency_price_and_fx(self):
        did,rid,pid,oid,lid,cid = self.setup_round()
        d = self.detail(rid)
        self.assertEqual((d['round_no'],d['currency'],d['incoterm'],d['fx_rate']), (1,'USD','DDP','7.1235'))
        self.assertEqual(d['lines'][0]['cost_unit'], '123.47')
        self.assertEqual(d['lines'][0]['sell_unit_price'], '99.13')
        self.assertEqual(d['costs'][0]['amount'],'71.29')
        self.assertEqual(d['blocking'],[])
        self.qw('status',rid,action='send',confirm='1')
        new = self.qw('create',did)
        self.assertEqual(self.detail(rid)['status'],'superseded')
        nd = self.detail(new)
        self.assertEqual((nd['round_no'],nd['copied_from_round_id'],nd['status']), (2,rid,'draft'))
        self.assertEqual(nd['lines'][0]['cost_unit'],'123.47')
        self.assertEqual(nd['costs'][0]['amount'],'71.29')
        self.assertIsNone(nd['valid_until'])
        self.assertEqual(nd['negotiations'],[])
        self.assertEqual(len(self.quotes.read('list',row_id=did)['items']),2)
        self.qw('update',new,currency='EUR')
        self.assertIsNone(self.detail(new)['fx_rate'])
        self.assertIsNone(self.detail(new)['lines'][0]['sell_unit_price'])
        self.qw('update',new,fx_rate='8.1234',fx_source='fictional manual')
        self.assertEqual(self.detail(new)['fx_rate'],'8.1234')
        self.assertEqual(self.detail(new)['fx_source'],'fictional manual')

    def test_offer_reference_status_validity_future_current_and_currency(self):
        did,rid,pid,oid,lid,_ = self.setup_round(freight=False)
        for field,value,allowed in [('status','suggested',False),('status','rejected',False),
            ('valid_until',None,False),('valid_until','2029-12-31',False),('quoted_on','2030-01-02',False),
            ('currency','USD',False),('valid_until','2030-01-07',True)]:
            with self.subTest(field=field,value=value):
                with self.store.write() as db:
                    db.execute('UPDATE offers SET '+field+'=? WHERE id=?',(value,oid))
                if allowed:
                    self.qw('line_update',rid,lid,offer_id=str(oid))
                    self.assertIn('offer_expiring',[p['code'] for p in self.detail(rid)['warnings']])
                else:
                    with self.assertRaises(v.BadRequest):
                        self.qw('line_update',rid,lid,offer_id=str(oid))
                    missing = self.qw('line',rid,product_id=str(pid),qty='1')
                    self.assertIsNone(next(l for l in self.detail(rid)['lines'] if l['id']==missing)['offer_id'])
                    self.qw('line_remove',rid,missing)
                with self.store.write() as db:
                    db.execute("UPDATE offers SET status='confirmed',currency='CNY',quoted_on='2029-12-01',valid_until='2030-02-01' WHERE id=?",(oid,))
        newer = self.aw('offer_create',pid,price='900',unit='pcs',quoted_on='2030-01-01')
        with self.assertRaises(v.BadRequest):
            self.qw('line_update',rid,lid,offer_id=str(oid))
        self.qw('line_update',rid,lid,product_id=str(pid))
        self.assertIsNone(self.detail(rid)['lines'][0]['offer_id'])

    def test_snapshot_source_changes_send_today_and_recalc(self):
        did,rid,pid,oid,lid,cid = self.setup_round()
        before = self.detail(rid)
        freight_id = before['costs'][0]['freight_quote_id']
        with self.store.write() as db:
            db.execute('UPDATE offers SET price_fen=999999,lead_days_min=99,lead_days_max=100 WHERE id=?',(oid,))
            db.execute('UPDATE products SET gross_kg=100 WHERE id=?',(pid,))
            db.execute('UPDATE freight_quotes SET price_fen=999 WHERE id=?',(freight_id,))
        after = self.detail(rid)
        self.assertEqual(before['lines'],after['lines'])
        self.assertEqual(before['costs'],after['costs'])
        self.today = date(2030,2,2)
        self.qw('update',rid,valid_until='2030-03-01')
        with self.assertRaises(v.Conflict):
            self.qw('status',rid,action='send',confirm='1')
        self.assertIn('offer_expired',[p['code'] for p in self.detail(rid)['blocking']])
        self.assertIn('cost_expired',[p['code'] for p in self.detail(rid)['blocking']])
        self.today = date(2030,1,1)
        self.qw('cost_recalc',rid,cid)
        self.assertEqual(self.detail(rid)['costs'][0]['amount'],'1998.00')
        self.assertEqual(self.detail(rid)['lines'][0]['cost_unit'],'123.47')
        self.qw('line_update',rid,lid,qty='5')
        self.assertEqual(self.detail(rid)['costs'][0]['amount'],'2997.00')

    def test_freight_references_current_packaging_manual_and_cost_types(self):
        did,rid,pid,oid,lid,cid = self.setup_round()
        freight_id=self.detail(rid)['costs'][0]['freight_quote_id']
        for field,value in [('status','suggested'),('status','rejected'),('valid_until',None),('valid_until','2029-12-31'),('currency','USD')]:
            with self.subTest(field=field,value=value):
                with self.store.write() as db:
                    db.execute('UPDATE freight_quotes SET '+field+'=? WHERE id=?',(value,freight_id))
                with self.assertRaises(v.BadRequest):
                    self.qw('cost',rid,kind='freight',freight_quote_id=str(freight_id))
                with self.store.write() as db:
                    db.execute("UPDATE freight_quotes SET status='confirmed',valid_until='2030-02-01',currency='CNY' WHERE id=?",(freight_id,))
        with self.store.write() as db:
            db.execute('UPDATE products SET units_per_carton=NULL WHERE id=?',(pid,))
        self.qw('cost_recalc',rid,cid)
        self.assertIsNone(self.detail(rid)['costs'][0]['amount'])
        self.assertIn('freight_packing_missing',[p['code'] for p in self.detail(rid)['blocking']])
        self.qw('cost_remove',rid,cid)
        vendor=self.aw('party_create',kind='vendor',name='虚构配套单位')
        for kind in ('freight','vendor','domestic','other'):
            values=dict(kind=kind,manual='1',amount='0.10',valid_until='2030-01-07',source_note='fictional-source')
            if kind in ('vendor','domestic'):
                values['party_id']=str(vendor)
            if kind == 'freight':
                values.update(destination_country='ZZ',mode='air')
            for missing in ('amount','valid_until','source_note'):
                with self.assertRaises(v.BadRequest):
                    self.qw('cost',rid,**{k:value for k,value in values.items() if k!=missing})
            self.qw('cost',rid,**values)
        self.assertNotIn('freight_packing_missing',[p['code'] for p in self.detail(rid)['blocking']])
        self.assertIn('cost_expiring',[p['code'] for p in self.detail(rid)['warnings']])

    def test_state_machine_lock_notes_accept_stage_and_no_won(self):
        did,rid,pid,oid,lid,cid=self.setup_round()
        with self.assertRaises(v.BadRequest):
            self.qw('status',rid,action='send')
        with self.assertRaises(v.Conflict):
            self.qw('status',rid,action='accept')
        self.qw('status',rid,action='send',confirm='1')
        for op,child,fields in [('line',None,dict(name='Fictional',qty='1')),('line_update',lid,dict(qty='5')),
            ('line_remove',lid,{}),('cost',None,dict(kind='other',amount='1',source_note='fictional',valid_until='2030-02-01')),
            ('cost_remove',cid,{}),('cost_recalc',cid,{}),('update',None,dict(fx_rate='8'))]:
            with self.subTest(op=op), self.assertRaisesRegex(v.Conflict,'这一轮已发出，请新建下一轮'):
                self.qw(op,rid,child,**fields)
        self.qw('update',rid,notes='fictional-private-note')
        self.qw('status',rid,action='accept')
        d=self.deals.read('detail',row_id=did)
        self.assertEqual((d['stage'],d['status'],d['won_at']),('awaiting_payment','open',None))
        self.assertEqual(len(d['tasks']['awaiting_payment']),3)
        self.assertTrue(any(e['summary']=='报价第 1 轮已发出' for e in d['events']))
        self.assertTrue(any(e['summary']=='报价第 1 轮已接受' for e in d['events']))
        with self.assertRaises(v.Conflict):
            self.qw('line_remove',rid,lid)
        self.qw('negotiation',rid,side='customer',note='fictional after acceptance')
        new=self.qw('create',did)
        self.assertEqual(self.detail(rid)['status'],'accepted')
        self.qw('update',new,valid_until='2030-01-30')
        self.qw('terms',new,confirm='1',payment='30_70',delivery_scope='虚构交付范围')
        self.qw('status',new,action='send',confirm='1')
        self.qw('status',new,action='reject')
        self.assertEqual(self.detail(new)['status'],'rejected')
        with self.assertRaises(v.Conflict):
            self.qw('status',new,action='accept')

    def test_accept_never_regresses_later_stage(self):
        did,rid,*_=self.setup_round()
        # Historical won record without a ledger; acceptance must leave it intact.
        with self.store.write() as db:
            db.execute("UPDATE deals SET stage='inspection',status='won',won_at='2029-12-01' WHERE id=?", (did,))
        self.qw('status',rid,action='send',confirm='1')
        self.qw('status',rid,action='accept')
        self.assertEqual(self.deals.read('detail',row_id=did)['stage'],'inspection')

    def test_negotiations_events_and_value_free_audit_and_money(self):
        did,rid,pid,oid,lid,cid=self.setup_round()
        for side in ('customer','supplier','forwarder','vendor','us'):
            self.qw('negotiation',rid,side=side,note='fictional-private-negotiation',price='98.76',currency='EUR')
        for amount in ('0.1','0.2'):
            self.qw('cost',rid,kind='other',amount=amount,valid_until='2030-02-01',source_note='fictional-private-source')
        d=self.detail(rid)
        self.assertEqual([c['amount'] for c in d['costs']][-2:],['0.10','0.20'])
        self.assertEqual(d['totals']['cost_cny'],'442.00')
        self.assertEqual(len(d['negotiations']),5)
        self.assertTrue(all(n['price']=='98.76' and n['who']=='fictional-owner' for n in d['negotiations']))
        with self.store.read() as db:
            events=db.execute("SELECT count(*) FROM deal_events WHERE summary LIKE '%还价与回应%'").fetchone()[0]
            audits=[dict(r) for r in db.execute('SELECT * FROM audit')]
        self.assertEqual(events,5)
        audit_text=json.dumps([{k:value for k,value in row.items() if k!='at'} for row in audits])
        for secret in ('fictional-private','98.76','9876','123.47','12347','71.29','7129','99.13','9913','FICTIONAL-SECRET'):
            self.assertNotIn(secret,audit_text)
        self.assertTrue(all(set(json.loads(a['detail']))=={'fields'} for a in audits))
        for bad in ('0.1+0.2','0.001','1e3','NaN','-1',0.1):
            with self.assertRaises(v.BadRequest):
                self.qw('line_update',rid,lid,sell_unit_price=bad)

    def test_text_allowlist_placeholders_and_not_ready(self):
        did,rid,pid,oid,lid,cid=self.setup_round()
        self.qw('update',rid,notes='FICTIONAL-SECRET-SUPPLIER profit 123.47')
        self.qw('line_update',rid,lid,note='FICTIONAL-SECRET-RATE 71.29')
        for lang in ('en','zh'):
            response=self.quotes.read('text',{'lang':lang},rid)
            text=response['text']
            self.assertTrue(response['ready'])
            for forbidden in ('123.47','12347','71.29','7129','2.31','231','FICTIONAL-SECRET','cost','profit','利润','供应商','成本','运价'):
                self.assertNotIn(forbidden,text)
            for expected in ('Fictional Lamp','99.13','297.39','DDP','ZZ','2030-01-30','30%','70%','8–12'):
                self.assertIn(expected,text)
            self.assertEqual(response,self.quotes.read('text',{'lang':lang},rid))
        self.qw('line',rid,name='Fictional extra',qty='1')
        text=self.quotes.read('text',{},rid)
        self.assertFalse(text['ready'])
        self.assertTrue(text['blocking'])
        self.assertIn('【待确认：交期】',text['text'])

    def test_missing_cache_never_guessed_and_invalid_fx(self):
        for data in ('{}','not json','{"rate":"NaN"}','{"rate":true}','{"rate":"0","source":"fictional"}'):
            self.fx_path.write_text(data)
            self.assertIsNone(read_fx(self.fx_path)['usd_cny'])
        self.fx_path.unlink()
        did,rid,*_=self.setup_round()
        self.assertIsNone(self.detail(rid)['fx_rate'])
        self.assertIn('fx_missing',[p['code'] for p in self.detail(rid)['blocking']])
        for fx in ('0','-1','nan','1e3','1.12345'):
            with self.assertRaises(v.BadRequest):
                self.qw('update',rid,fx_rate=fx)
        self.qw('update',rid,fx_rate='7.1234')
        self.assertEqual(self.detail(rid)['fx_rate'],'7.1234')

    def test_unknown_quantity_child_ownership_and_transaction_rollback(self):
        did=self.deals.write('create',None,{},'fictional-owner')['id']
        self.deals.write('item',did,dict(name='Fictional unknown quantity'),'fictional-owner')
        rid=self.qw('create',did)
        self.assertIn('qty_unconfirmed',[p['code'] for p in self.detail(rid)['blocking']])
        lid=self.detail(rid)['lines'][0]['id']
        rid2=self.qw('create',did)
        with self.assertRaises(v.Missing):
            self.qw('line_remove',rid2,lid)
        with self.store.read() as db:
            before=db.execute('SELECT count(*) FROM audit').fetchone()[0]
        with patch.object(self.quotes.deals,'quote_event',side_effect=RuntimeError('fictional')), self.assertRaises(RuntimeError):
            self.qw('negotiation',rid,side='us',note='fictional rollback')
        self.assertEqual(self.detail(rid)['negotiations'],[])
        with self.store.read() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM audit').fetchone()[0],before)

    def test_send_checks_each_blocker_and_warnings_are_permitted(self):
        did,rid,pid,oid,lid,cid=self.setup_round()
        cases=[('fx_x10000',None),('valid_until',None),('valid_until','2029-12-31')]
        for key,value in cases:
            with self.subTest(field=key,value=value):
                with self.store.write() as db:
                    old=db.execute('SELECT '+key+' FROM quote_rounds WHERE id=?',(rid,)).fetchone()[0]
                    db.execute('UPDATE quote_rounds SET '+key+'=? WHERE id=?',(value,rid))
                with self.assertRaises(v.Conflict):
                    self.qw('status',rid,action='send',confirm='1')
                with self.store.write() as db:
                    db.execute('UPDATE quote_rounds SET '+key+'=? WHERE id=?',(old,rid))
        self.qw('line_update',rid,lid,sell_unit_price='')
        with self.assertRaises(v.Conflict):
            self.qw('status',rid,action='send',confirm='1')
        self.qw('line_update',rid,lid,sell_unit_price='0.01')
        with self.assertRaises(v.Conflict):
            self.qw('status',rid,action='send',confirm='1')
        self.qw('cost_remove',rid,cid)
        self.qw('line_update',rid,lid,sell_unit_price='19.00')
        with self.assertRaises(v.Conflict):
            self.qw('status',rid,action='send',confirm='1')
        self.qw('update',rid,incoterm='FOB')
        self.today=date(2030,1,28)
        warnings={p['code'] for p in self.detail(rid)['warnings']}
        self.assertTrue({'offer_expiring','below_target','fob_no_domestic'} <= warnings)
        self.assertFalse(self.detail(rid)['blocking'])
        self.qw('status',rid,action='send',confirm='1')

    def test_recalc_selects_current_rate_and_duplicate_rounds_are_serialized(self):
        from concurrent.futures import ThreadPoolExecutor
        did,rid,pid,oid,lid,cid=self.setup_round()
        new_rate=self.freight(price='10.00',quoted_on='2030-01-01')
        self.qw('cost_recalc',rid,cid)
        self.assertEqual(self.detail(rid)['costs'][0]['freight_quote_id'],new_rate)
        self.assertEqual(self.detail(rid)['costs'][0]['amount'],'240.00')
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures=[pool.submit(self.qw,'create',did) for _ in range(2)]
            ids=[f.result() for f in futures]
        self.assertEqual(len(set(ids)),2)
        self.assertEqual([r['round_no'] for r in self.quotes.read('list',row_id=did)['items']],[1,2,3])
