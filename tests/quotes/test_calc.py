from copy import deepcopy
from datetime import date
from decimal import Decimal
import unittest
from console.quotes.calc import compute
from console.quotes.freight import calculate, PACKING
from console.quotes.money import money

TODAY = date(2030,1,1)
ROUND = dict(fx_x10000=70000, currency='USD', target_margin_bp=1500, incoterm='EXW', valid_until='2030-02-01')
LINE = dict(id=1, name='Fictional', qty=2, offer_id=1, cost_unit_fen=700, cost_currency='CNY',
            cost_valid_until='2030-02-01', cost_validity='valid', sell_unit_minor=200)
COST = dict(kind='domestic', amount_fen=100, manual=1, valid_until='2030-02-01', source_note='fictional')


class CalcTests(unittest.TestCase):
    def test_table_driven_financial_cases_and_currencies(self):
        cases = [
            ('normal', {}, {}, '28.00','14.00','50.00%', False, False),
            ('inverted', {}, {'sell_unit_minor':50}, '7.00','-7.00','-100.00%', True, True),
            ('below', {}, {'sell_unit_minor':110}, '15.40','1.40','9.09%', False, True),
            ('eur', {'currency':'EUR','fx_x10000':80000}, {}, '32.00','18.00','56.25%', False, False),
            ('gbp', {'currency':'GBP','fx_x10000':90000}, {}, '36.00','22.00','61.11%', False, False),
            ('cny', {'currency':'CNY','fx_x10000':10000}, {'cost_unit_fen':10}, '4.00','3.80','95.00%', False, False),
            ('half', {'fx_x10000':15000}, {'qty':1,'sell_unit_minor':1,'cost_unit_fen':1}, '0.02','0.01','50.00%', False, False),
            ('target0', {'target_margin_bp':0}, {'sell_unit_minor':100}, '14.00','0.00','0.00%', False, False),
        ]
        for name, r, l, revenue, profit, margin, inverted, below in cases:
            with self.subTest(name=name):
                result = compute(ROUND | r, [LINE | l], [], TODAY)
                self.assertEqual((result['totals']['revenue_cny'],result['totals']['profit_cny'],result['totals']['margin']), (revenue,profit,margin))
                self.assertEqual((result['inverted'],result['below_target']), (inverted,below))

    def test_table_driven_blocking_and_warnings(self):
        cases = [
            ({'fx_x10000':None},{},[], 'fx_missing',False),
            ({'valid_until':None},{},[], 'quote_no_expiry',False),
            ({'valid_until':'2029-12-31'},{},[], 'quote_expired',False),
            ({'incoterm':'DDP'},{},[], 'ddp_no_freight',False),
            ({'incoterm':'FOB'},{},[], 'fob_no_domestic',True),
            ({},{'offer_id':None},[], 'offer_unusable',False),
            ({},{'cost_validity':'suggested'},[], 'offer_unusable',False),
            ({},{'cost_currency':'USD'},[], 'cost_currency_invalid',False),
            ({},{'cost_valid_until':None},[], 'offer_no_expiry',False),
            ({},{'cost_valid_until':'2029-12-31'},[], 'offer_expired',False),
            ({},{'cost_valid_until':'2030-01-01'},[], 'offer_expiring',True),
            ({},{'sell_unit_minor':None},[], 'sell_price_missing',False),
            ({},{'qty_confirmed':0},[], 'qty_unconfirmed',False),
            ({},{},[COST | {'valid_until':None}], 'cost_no_expiry',False),
            ({},{},[COST | {'valid_until':'2029-12-31'}], 'cost_expired',False),
            ({},{},[COST | {'valid_until':'2030-01-07'}], 'cost_expiring',True),
            ({},{},[COST | {'source_note':''}], 'manual_source_missing',False),
            ({},{},[COST | {'amount_fen':None}], 'cost_amount_missing',False),
            ({},{},[dict(kind='freight', manual=0, validity='suggested', amount_fen=None, valid_until='2030-02-01')], 'freight_unusable',False),
            ({},{'sell_unit_minor':0},[], 'revenue_zero',False),
        ]
        for r,l,c,code,warning in cases:
            with self.subTest(code=code):
                result = compute(ROUND | r, [LINE | l], c, TODAY)
                self.assertIn(code, [p['code'] for p in result['warnings' if warning else 'blocking']])
                self.assertTrue(all(any('\u4e00' <= char <= '\u9fff' for char in p['message']) for p in result['blocking']+result['warnings']))
        self.assertIn('lines_missing', [p['code'] for p in compute(ROUND, [], [], TODAY)['blocking']])
        result = compute(ROUND, [LINE | {'cost_valid_until':'2030-01-08'}], [], TODAY)
        self.assertFalse(result['warnings'])

    def test_quantity_allocation_exact_tail_and_minimum_attainable(self):
        lines = [LINE | {'id':1, 'qty':1, 'cost_unit_fen':100}, LINE | {'id':2, 'qty':3, 'cost_unit_fen':500}]
        result = compute(ROUND, lines, [COST | {'amount_fen':101}], TODAY)
        self.assertEqual([p['allocated_fee_cny'] for p in result['per_line']], ['0.25','0.76'])
        self.assertEqual([p['landed_cost_cny'] for p in result['per_line']], ['1.25','15.76'])
        self.assertEqual(result['totals']['cost_cny'], '17.01')
        for part,line in zip(result['per_line'], lines):
            landed = int(Decimal(part['landed_cost_cny']) * 100)
            minimum = part['min_price_for_target_minor']
            from console.quotes.money import rounded
            def meets(p):
                revenue = rounded(Decimal(p) * line['qty'] * ROUND['fx_x10000'] / 10000)
                return (revenue-landed)*10000 >= revenue*1500
            self.assertTrue(meets(minimum))
            self.assertFalse(meets(minimum-1))
        tail = compute(ROUND, [LINE | {'qty':1}]*4, [COST | {'amount_fen':2}], TODAY)
        self.assertEqual([p['allocated_fee_cny'] for p in tail['per_line']], ['0.01','0.00','0.01','0.00'])

    def test_pure_no_mutation_missing_revenue_and_decimal_sum(self):
        args = (deepcopy(ROUND), [deepcopy(LINE)], [deepcopy(COST)])
        before = deepcopy(args)
        compute(*args, TODAY)
        self.assertEqual(args,before)
        r = compute(ROUND, [LINE | {'cost_unit_fen':10,'qty':1}, LINE | {'cost_unit_fen':20,'qty':1}], [], TODAY)
        self.assertEqual(r['totals']['cost_cny'],'0.30')
        self.assertIsNone(compute(ROUND | {'fx_x10000':None}, [LINE], [], TODAY)['totals']['profit_cny'])
        self.assertEqual(money(-1),'-0.01')

    def test_incomplete_cost_does_not_present_fake_profit_or_minimum(self):
        for lines,costs in [([LINE | {'cost_unit_fen':None}],[]),([LINE],[COST | {'amount_fen':None}]),
                            ([LINE | {'qty_confirmed':0}],[])]:
            result=compute(ROUND,lines,costs,TODAY)
            self.assertFalse(result['totals']['cost_complete'])
            self.assertIsNone(result['totals']['profit_cny'])
            self.assertIsNone(result['totals']['margin'])
            self.assertIsNone(result['per_line'][0]['min_price_for_target_minor'])


class FreightTests(unittest.TestCase):
    def setUp(self):
        self.line = dict(qty=3, units_per_carton=2, carton_l_cm=50, carton_w_cm=40, carton_h_cm=30, gross_kg=8)
        self.quote = dict(unit='kg', mode='air', price_fen=200, min_charge_fen=0)

    def test_units_modes_minimum_and_chargeable(self):
        for mode,unit,expected in [('air','kg',4800),('express','kg',4800),('sea','kg',3200),('rail','kg',3200),
                                    ('sea','cbm',24),('air','ticket',200),('sea','container',200)]:
            with self.subTest(mode=mode,unit=unit):
                result = calculate([self.line], self.quote | dict(mode=mode,unit=unit))
                self.assertEqual(result['amount_fen'],expected)
                self.assertEqual(result['cartons'],2)
                self.assertEqual(Decimal(result['cbm']),Decimal('0.12'))
                self.assertEqual(Decimal(result['volumetric_kg']),24)
        self.assertEqual(calculate([self.line], self.quote | {'min_charge_fen':9999})['amount_fen'],9999)
        self.assertEqual(calculate([self.line | {'gross_kg':20}],self.quote)['amount_fen'],8000)

    def test_all_packaging_fields_mandatory_even_flat_rate_and_rounding(self):
        for field in PACKING:
            for unit in ('kg','cbm','ticket','container'):
                with self.subTest(field=field, unit=unit):
                    result = calculate([self.line | {field:None}], self.quote | {'unit':unit})
                    self.assertIsNone(result['amount_fen'])
                    self.assertEqual(result['blocking'][0]['code'],'freight_packing_missing')
        r = calculate([self.line | {'qty':1,'gross_kg':0.15}], self.quote | {'mode':'sea','price_fen':10})
        self.assertEqual(r['amount_fen'],2)
        self.assertEqual(calculate([self.line | {'qty_confirmed':0}],self.quote)['blocking'][0]['code'],'freight_packing_missing')

class BoundaryTests(unittest.TestCase):
    def test_minimum_exhaustive_small_integer_prices(self):
        from console.quotes.money import rounded
        for fx in (1,1500,71234,99999):
            for qty in (1,3):
                for cost in (0,1,13,100):
                    for target in (0,1500,9000):
                        with self.subTest(fx=fx,qty=qty,cost=cost,target=target):
                            r=compute(ROUND | dict(fx_x10000=fx,target_margin_bp=target),
                                [LINE | dict(qty=qty,cost_unit_fen=cost)],[],TODAY)
                            price=r['per_line'][0]['min_price_for_target_minor']
                            def meets(p):
                                sale=rounded(Decimal(p)*qty*fx/10000)
                                return sale>0 and (sale-cost*qty)*10000>=sale*target
                            self.assertTrue(meets(price))
                            self.assertFalse(meets(price-1))
