"""Fictional quotation workflow; temporary databases and offline language runner only."""
from decimal import Decimal
import re
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from console.archive import validation as v
from console.deals.service import Deals
from console.extract import storage as extract_storage, validation as extract_validation
from console.inbox.model import make_key
from console.lang import Lang
from console.roles.common import fingerprint
from fixtures import Fixture


class BlockRunner:
    def __init__(self):
        self.calls = []
        self.add_amount = False

    def __call__(self, prompt):
        self.calls.append(prompt)
        blocks = re.findall(r'\[\[(\d+)\]\]\n(.*?)\n\[\[/\1\]\]', prompt, re.S)
        assert blocks, '假模型只接受编号翻译块'
        prefix = 'Cotización ficticia: ' if '语言代码 es。' in prompt else '虚构中文核对稿：'
        return '\n'.join('[[' + number + ']]\n' + prefix + source +
                         (' EUR 777.77' if self.add_amount else '') + '\n[[/' + number + ']]'
                         for number, source in blocks)


class WorkflowTests(Fixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        for target in ('socket.socket.connect', 'socket.create_connection', 'console.inbox.ai.official_runner'):
            blocker = patch(target, side_effect=AssertionError('测试只能使用临时库和假服务'))
            blocker.start()
            self.addCleanup(blocker.stop)

    def write(self, op, rid, child=None, **fields):
        return self.quotes.write(op, rid, fields, 'fictional-owner', child)

    def terms(self, rid, **changes):
        fields = dict(confirm='1', payment='30_70', delivery_scope='虚构交付范围')
        fields.update(changes)
        return self.write('terms', rid, **fields)

    def snapshot(self):
        with self.store.read() as db:
            return {table: [tuple(row) for row in db.execute('SELECT * FROM ' + table + ' ORDER BY id')]
                    for table in ('quote_rounds', 'quote_lines', 'quote_costs', 'quote_negotiations', 'deal_events', 'audit')}

    def shipping(self):
        did, rid, *_ = self.setup_round(freight=False)
        first = self.aw('party_create', kind='forwarder', name='虚构海运甲')
        second = self.aw('party_create', kind='forwarder', name='虚构海运乙')
        rates = [self.freight(forwarder_id=str(party), mode='sea', unit='cbm', price=price,
                              min_charge='0', service='虚构港到港', destination_country=country)
                 for party, price, country in ((first, '600', 'ZZ'), (second, '680', 'ZZ'), (second, '700', 'YY'))]
        self.terms(rid)
        return did, rid, rates

    def choose(self, rid, rate_id, **changes):
        fields = dict(confirm='1', freight_quote_id=str(rate_id), reason_type='other', reason='虚构交期符合要求')
        fields.update(changes)
        return self.write('freight_choose', rid, **fields)

    def vendor_offer(self, name, price):
        party = self.aw('party_create', kind='vendor', name=name)
        key = make_key('conversation', 'wechat', 'fictional-account', name)
        values = dict(kind='offer', conversation_key=key,
            source_message_refs=[dict(kind='message', id='derived:fictional-' + name,
                                     derived=True, conversation_key=key, sent_at=1893456000)],
            party_id=party, party_hint=name, product_text='虚构线缆-' + name, matched_product_id=None,
            price_fen=int(Decimal(price) * 100), currency='CNY', unit='pcs', moq_units=1,
            quoted_on='2029-12-01', excerpt='虚构线缆报价', confidence=.9, source='rules')
        values['input_fingerprint'] = fingerprint(values)
        with self.quotes.store.write() as db:
            row = extract_storage.save_suggestion(db, values, 'fictional-owner', 1893456000)[0]
        result = extract_storage.decide(self.quotes.store, 'accept', row['id'],
            extract_validation.fields('accept', dict(create_product='1', product_name='虚构线缆-' + name,
                                                     valid_until='2030-02-01')),
            'fictional-owner', 1893456000)
        return party, result['product_id'], result['offer_id']

    def native_round(self, *, locked=True):
        key = make_key('conversation', 'alibaba', 'fictional-account', 'fictional-customer')
        conv = dict(key=key, channel='alibaba', account='fictional-account', buyer_name='虚构买家', country='ZZ',
                    messages=[dict(role='buyer', text='Hola, necesito información de las lámparas.', sent_at=1893456000)])
        inbox = SimpleNamespace(detail=lambda wanted: conv if wanted == key else None)
        self.runner = BlockRunner()
        self.language = Lang(self.path.parent / 'fictional-language.sqlite3', inbox=inbox,
                             runner=self.runner, clock=lambda: 1893456000)
        self.quotes.lang = self.language
        self.deals = Deals(store=self.store, lookup=lambda wanted: conv if wanted == key else None,
                           clock=lambda: 1893456000)
        customer = self.aw('party_create', kind='customer', name='虚构西语买家', country='ZZ')
        did = self.deals.write('create', None, dict(title='虚构母语报价', customer_party_id=str(customer),
                                                  conversation_key=key), 'fictional-owner')['id']
        pid, _ = self.product(name='虚构 WX-300 DMX 灯具')
        self.deals.write('item', did, dict(product_id=str(pid), name='虚构 WX-300 DMX 灯具', qty='24'), 'fictional-owner')
        rid = self.qw('create', did)
        lid = self.detail(rid)['lines'][0]['id']
        self.qw('line_update', rid, lid, unit='台', sell_unit_price='250.00')
        self.qw('update', rid, incoterm='CIF', valid_until='2030-01-20', destination_country='ZZ')
        self.terms(rid)
        if locked:
            self.language.write('contact', dict(conversation_key=key, native_language='es', locked='1'), 'fictional-owner')
        return did, rid, lid, key

    def test_replace_two_forwarders_is_atomic_and_request_replay_is_idempotent(self):
        did, rid, rates = self.shipping()
        first = self.choose(rid, rates[0], request_key='fictional-first-01')
        fields = dict(selection_mode='replace', replace_cost_id=str(first['cost_id']), request_key='fictional-replace-01')
        chosen = self.choose(rid, rates[1], **fields)
        before = self.snapshot()
        again = self.choose(rid, rates[1], **fields)
        self.assertTrue(again['reused'])
        self.assertEqual(chosen['cost_id'], again['cost_id'])
        self.assertEqual(self.snapshot(), before)
        costs = self.detail(rid)['costs']
        self.assertEqual([(c['freight_quote_id'], c['amount']) for c in costs], [(rates[1], '81.60')])
        self.assertEqual(len(self.detail(rid)['negotiations']), 2)
        self.assertFalse(self.quotes.read('text', {'lang': 'zh'}, rid)['blocking'])

    def test_reusing_request_key_with_changed_content_conflicts_without_writes(self):
        _, rid, rates = self.shipping()
        self.choose(rid, rates[0], request_key='fictional-same-01')
        before = self.snapshot()
        with self.assertRaisesRegex(v.Conflict, '请求编号'):
            self.choose(rid, rates[1], request_key='fictional-same-01')
        self.assertEqual(self.snapshot(), before)

    def test_replacing_with_already_selected_rate_still_records_request_for_replay(self):
        _, rid, rates = self.shipping()
        first = self.choose(rid, rates[0], request_key='fictional-first-01')
        self.choose(rid, rates[1], selection_mode='append', segment='虚构追加运输段', request_key='fictional-append-01')
        fields = dict(selection_mode='replace', replace_cost_id=str(first['cost_id']), request_key='fictional-existing-01')
        chosen = self.choose(rid, rates[1], **fields)
        before = self.snapshot()
        replay = self.choose(rid, rates[1], **fields)
        self.assertEqual(replay['cost_id'], chosen['cost_id'])
        self.assertTrue(replay['reused'])
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(len(self.detail(rid)['costs']), 1)

    def test_replace_event_failure_rolls_back_old_selection_and_audit(self):
        _, rid, rates = self.shipping()
        first = self.choose(rid, rates[0], request_key='fictional-first-01')
        before = self.snapshot()
        with patch.object(self.quotes.deals, 'quote_event', side_effect=RuntimeError('虚构事件写入失败')):
            with self.assertRaisesRegex(RuntimeError, '虚构事件写入失败'):
                self.choose(rid, rates[1], selection_mode='replace', replace_cost_id=str(first['cost_id']),
                            request_key='fictional-rollback-01')
        self.assertEqual(self.snapshot(), before)

    def test_append_requires_segment_reason_and_request_then_replays_once(self):
        _, rid, rates = self.shipping()
        self.choose(rid, rates[0])
        with self.assertRaisesRegex(v.Conflict, '替换或追加'):
            self.choose(rid, rates[1])
        with self.assertRaisesRegex(v.BadRequest, '运输段'):
            self.choose(rid, rates[1], selection_mode='append', request_key='fictional-append-01')
        fields = dict(selection_mode='append', segment='虚构国内转运段', request_key='fictional-append-01')
        self.choose(rid, rates[1], **fields)
        before = self.snapshot()
        self.assertTrue(self.choose(rid, rates[1], **fields)['reused'])
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(len(self.detail(rid)['costs']), 2)

    def test_same_freight_rate_cannot_silently_reuse_a_different_segment(self):
        _, rid, rates = self.shipping()
        self.choose(rid, rates[0], segment='虚构原海运段', request_key='fictional-segment-01')
        before = self.snapshot()
        with self.assertRaises(v.BadRequest):
            self.choose(rid, rates[0], selection_mode='append', segment='虚构另一个运输段',
                        request_key='fictional-segment-02')
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.detail(rid)['costs'][0]['calc']['segment'], '虚构原海运段')

    def test_cross_country_old_cost_and_choice_reject_without_writes(self):
        _, rid, rates = self.shipping()
        before = self.snapshot()
        with self.assertRaisesRegex(v.BadRequest, '目的国'):
            self.qw('cost', rid, kind='freight', freight_quote_id=str(rates[2]))
        with self.assertRaisesRegex(v.Conflict, '目的国'):
            self.choose(rid, rates[2])
        self.assertEqual(self.snapshot(), before)

    def test_destination_change_blocks_send_and_recalc_preserves_cost(self):
        _, rid, rates = self.shipping()
        cost = self.choose(rid, rates[0])['cost_id']
        self.qw('update', rid, destination_country='YY')
        before = self.snapshot()
        self.assertIn('freight_destination_mismatch', [p['code'] for p in self.detail(rid)['blocking']])
        with self.assertRaisesRegex(v.BadRequest, '目的国'):
            self.qw('cost_recalc', rid, cost)
        with self.assertRaisesRegex(v.Conflict, '目的国'):
            self.qw('status', rid, action='send', confirm='1')
        self.assertEqual(self.snapshot(), before)

    def test_replace_rejects_other_transport_mode_and_old_cost_cannot_accumulate(self):
        _, rid, rates = self.shipping()
        first = self.choose(rid, rates[0])
        air = self.freight(service='虚构航空段')
        before = self.snapshot()
        with self.assertRaisesRegex(v.BadRequest, '运输方式'):
            self.choose(rid, air, selection_mode='replace', replace_cost_id=str(first['cost_id']),
                        request_key='fictional-mode-01')
        with self.assertRaisesRegex(v.Conflict, '替换或追加'):
            self.qw('cost', rid, kind='freight', freight_quote_id=str(rates[1]))
        self.assertEqual(self.snapshot(), before)

    def test_customer_expiry_cannot_outlast_product_freight_or_manual_cost(self):
        _, rid, *_ = self.setup_round()
        self.terms(rid)
        self.qw('cost', rid, kind='other', amount='12', source_note='虚构费用来源', valid_until='2030-01-15')
        self.qw('update', rid, valid_until='2030-02-02')
        codes = [p['code'] for p in self.detail(rid)['blocking']]
        self.assertIn('offer_ends_before_quote', codes)
        self.assertEqual(codes.count('cost_ends_before_quote'), 2)
        with self.assertRaisesRegex(v.Conflict, '有效期超过'):
            self.qw('status', rid, action='send', confirm='1')

    def test_ddp_warns_scope_and_foreign_freight_shows_original_currency_only(self):
        _, rid, *_ = self.setup_round()
        foreign = self.freight(channel='other', currency='USD', price='2.00', min_charge='0')
        option = next(item for item in self.quotes.read('freight_options', row_id=rid)['items'] if item['id'] == foreign)
        self.assertEqual((option['currency'], option['amount_in_quote_currency'], option['amount_cny']), ('USD', '48.00', None))
        self.assertFalse(option['calculable'])
        self.assertIn('ddp_scope_unverified', [p['code'] for p in self.detail(rid)['warnings']])
        before = self.snapshot()
        with self.assertRaises(v.BadRequest):
            self.qw('cost', rid, kind='freight', freight_quote_id=str(foreign))
        self.assertEqual(self.snapshot(), before)

    def test_vendor_requires_explicit_party_and_retains_accepted_source_snapshot(self):
        _, rid, *_ = self.setup_round()
        first, product, offer = self.vendor_offer('虚构配套甲', '2.50')
        second, _, other_offer = self.vendor_offer('虚构配套乙', '3.20')
        fields = dict(kind='vendor', source_offer_id=str(offer), quantity='24', valid_until='2030-02-01', source_note='虚构询价确认')
        with self.assertRaisesRegex(v.BadRequest, '具体配套'):
            self.qw('cost', rid, **fields)
        with self.assertRaisesRegex(v.BadRequest, '来源与所选单位'):
            self.qw('cost', rid, party_id=str(second), **fields)
        cid = self.qw('cost', rid, party_id=str(first), **fields)
        cost = next(cost for cost in self.detail(rid)['costs'] if cost['id'] == cid)
        self.assertEqual((cost['amount'], cost['calc']['source_offer']['id'], cost['calc']['source_offer']['party_id']), ('60.00', offer, first))
        self.assertIn('由抽取员建议，老板确认', cost['calc']['source_offer']['source_ref'])
        with self.store.write() as db:
            self.assertIsNone(db.execute('SELECT supplier_id FROM products WHERE id=?', (product,)).fetchone()[0])
            db.execute('UPDATE offers SET price_fen=9999 WHERE id=?', (offer,))
        self.assertEqual(next(c for c in self.detail(rid)['costs'] if c['id'] == cid), cost)
        next_round = self.qw('create', self.detail(rid)['deal_id'])
        copied = next(c for c in self.detail(next_round)['costs'] if c['kind'] == 'vendor')
        self.assertEqual(copied['calc'], cost['calc'])
        self.assertEqual({source['id'] for source in self.detail(rid)['cost_sources']} & {offer, other_offer}, {offer, other_offer})

    def test_accessory_cannot_be_both_product_and_vendor_cost(self):
        _, rid, *_ = self.setup_round()
        party, product, offer = self.vendor_offer('虚构配套甲', '2.50')
        line = self.qw('line', rid, product_id=str(product), qty='24', sell_unit_price='8')
        with self.assertRaisesRegex(v.BadRequest, '重复计入'):
            self.qw('cost', rid, kind='vendor', party_id=str(party), source_offer_id=str(offer), quantity='24',
                    valid_until='2030-02-01', source_note='虚构询价确认')
        self.qw('line_remove', rid, line)
        self.qw('cost', rid, kind='vendor', party_id=str(party), source_offer_id=str(offer), quantity='24',
                valid_until='2030-02-01', source_note='虚构询价确认')
        before = self.snapshot()
        with self.assertRaisesRegex(v.BadRequest, '已计入配套费用'):
            self.qw('line', rid, product_id=str(product), qty='24', sell_unit_price='8')
        self.assertEqual(self.snapshot(), before)

    def test_quantity_24_to_30_recalculates_products_freight_and_linked_vendor_manual_review(self):
        _, rid, _, _, lid, _ = self.setup_round()
        self.qw('line_update', rid, lid, qty='24', unit='pcs')
        party = self.aw('party_create', kind='vendor', name='虚构线缆配套商')
        linked = self.qw('cost', rid, kind='vendor', party_id=str(party), unit_price='2.50',
                         quantity_line_id=str(lid), unit='pcs', valid_until='2030-02-01', source_note='虚构每件线缆报价')
        manual = self.qw('cost', rid, kind='domestic', party_id=str(party), amount='12',
                         valid_until='2030-02-01', source_note='虚构拖车总价')
        self.terms(rid)
        before = self.detail(rid)
        self.assertEqual(before['totals']['cost_cny'], '3367.92')
        self.qw('line_update', rid, lid, qty='30')
        after = self.detail(rid)
        costs = {cost['id']: cost for cost in after['costs']}
        self.assertEqual(after['totals']['cost_cny'], '4206.90')
        self.assertEqual(after['totals']['revenue'], '2973.90')
        self.assertEqual(costs[linked]['amount'], '75.00')
        self.assertEqual(costs[linked]['calc']['quantity'], 30)
        self.assertTrue(costs[manual]['calc']['needs_review'])
        self.assertIn('manual_cost_review', [p['code'] for p in after['blocking']])
        self.qw('cost_review', rid, manual, confirm='1')
        self.assertNotIn('manual_cost_review', [p['code'] for p in self.detail(rid)['blocking']])
        self.qw('status', rid, action='send', confirm='1')
        with self.assertRaisesRegex(v.Conflict, '新建下一轮'):
            self.qw('line_update', rid, lid, qty='24')

    def test_new_round_relinks_quantity_priced_cost_to_its_copied_line(self):
        did, rid, _, _, lid, _ = self.setup_round()
        self.qw('line_update', rid, lid, qty='24')
        party = self.aw('party_create', kind='vendor', name='虚构跨轮配套商')
        cid = self.qw('cost', rid, kind='vendor', party_id=str(party), unit_price='2.50',
                      quantity_line_id=str(lid), unit='pcs', valid_until='2030-02-01', source_note='虚构按件计费')
        copied_round = self.qw('create', did)
        copied = self.detail(copied_round)
        new_line = copied['lines'][0]['id']
        new_cost = next(cost for cost in copied['costs'] if cost['kind'] == 'vendor')
        self.assertNotEqual(new_line, lid)
        self.assertEqual(new_cost['calc']['quantity_line_id'], new_line)
        self.assertEqual(new_cost['amount'], '60.00')
        self.qw('line_update', copied_round, new_line, qty='30')
        recalculated = next(cost for cost in self.detail(copied_round)['costs'] if cost['id'] == new_cost['id'])
        self.assertEqual((recalculated['amount'], recalculated['calc']['quantity']), ('75.00', 30))
        self.assertFalse(recalculated['calc']['needs_review'])
        original = self.detail(rid)
        self.assertEqual(original['lines'][0]['qty'], 24)
        self.assertEqual(next(cost for cost in original['costs'] if cost['id'] == cid)['amount'], '60.00')

    def test_terms_pi_and_review_bind_to_current_amount_scope_and_hash(self):
        did, _, *_ = self.setup_round()
        rid = self.qw('create', did)
        lid = self.detail(rid)['lines'][0]['id']
        self.qw('update', rid, valid_until='2030-01-30')
        original = self.quotes.read('text', {'lang': 'zh'}, rid)
        self.assertIn('terms_unconfirmed', [p['code'] for p in original['blocking']])
        self.terms(rid)
        preview = self.quotes.read('pi', row_id=rid)
        self.assertFalse(preview['ready'])
        self.assertEqual(preview['missing'], ['公司开票名称', '公司开票地址'])
        self.terms(rid, seller_name='虚构开票公司', seller_address='虚构示例路 1 号')
        doc = self.quotes.read('text', {'lang': 'zh'}, rid)
        preview = self.quotes.read('pi', row_id=rid)
        self.assertTrue(preview['ready'])
        self.assertIn('30% 定金，70% 余款在发货前付清', doc['text'])
        self.assertIn('虚构交付范围', doc['text'])
        self.assertIn('USD 297.39', doc['text'])
        self.assertIn('定金 30%：USD 89.22', preview['text'])
        self.assertIn('余款 70%：USD 208.17', preview['text'])
        self.write('review', rid, confirm='1', source_hash=doc['source_hash'], lang='zh', text=doc['text'])
        self.assertEqual(self.detail(rid)['review']['reviewed_by'], 'fictional-owner')
        with self.assertRaisesRegex(v.Conflict, '文本与本轮'):
            self.write('review', rid, confirm='1', source_hash=doc['source_hash'], lang='zh', text=doc['text'] + '虚构修改')
        self.qw('line_update', rid, lid, qty='30')
        self.assertIsNone(self.detail(rid)['review'])
        with self.assertRaisesRegex(v.Conflict, '文本与本轮'):
            self.write('review', rid, confirm='1', source_hash=doc['source_hash'], lang='zh', text=doc['text'])
        for secret in ('FICTIONAL-SECRET', '123.47', 'fictional-private'):
            self.assertNotIn(secret, doc['text'] + preview['text'])

    def test_review_history_keeps_both_versions_and_current_review_uses_new_hash(self):
        _, rid, _, _, lid, _ = self.setup_round()
        self.terms(rid)
        first = self.quotes.read('text', {'lang': 'zh'}, rid)
        self.write('review', rid, confirm='1', source_hash=first['source_hash'], lang='zh', text=first['text'])
        saved = self.detail(rid)['review']
        self.assertEqual(self.detail(rid)['review_history'], [saved])
        self.qw('line_update', rid, lid, qty='30')
        self.assertIsNone(self.detail(rid)['review'])
        self.assertEqual(self.detail(rid)['review_history'], [saved])
        second = self.quotes.read('text', {'lang': 'zh'}, rid)
        self.assertNotEqual(second['source_hash'], first['source_hash'])
        self.write('review', rid, confirm='1', source_hash=second['source_hash'], lang='zh', text=second['text'])
        current = self.detail(rid)
        self.assertEqual(current['review']['source_hash'], second['source_hash'])
        self.assertEqual(current['review']['text'], second['text'])
        self.assertEqual(len(current['review_history']), 2)
        self.assertEqual(current['review_history'][0], saved)
        self.assertEqual([row['source_hash'] for row in current['review_history']], [first['source_hash'], second['source_hash']])
        self.assertEqual(current['review_history'][0]['text'], first['text'])

    def test_native_uses_public_lang_lock_and_preserves_facts_and_cache(self):
        _, rid, lid, key = self.native_round()
        native = self.write('native', rid, conversation_key=key)
        self.assertEqual((native['native_language'], native['contact']['source'], native['contact']['locked']), ('es', 'manual', True))
        self.assertIn('Cotización ficticia', native['text'])
        for text in (native['text'], native['back_translation']):
            for literal in ('WX-300', 'DMX', '24 台', 'USD 250.00', 'USD 6000.00', '2030-01-20'):
                self.assertIn(literal, text)
            self.assertNotIn('QuoteLiteral-', text)
            self.assertNotIn('FICTIONAL-SECRET', text)
        calls = len(self.runner.calls)
        self.write('native', rid, conversation_key=key)
        self.assertEqual(len(self.runner.calls), calls)
        old_hash = native['source_hash']
        self.qw('line_update', rid, lid, qty='30')
        with self.assertRaisesRegex(v.Conflict, '有效母语稿'):
            self.quotes.read('text', {'lang': 'native'}, rid)
        changed = self.write('native', rid, conversation_key=key)
        self.assertNotEqual(changed['source_hash'], old_hash)
        self.assertGreater(len(self.runner.calls), calls)
        self.assertIn('30 台', changed['text'])
        self.assertIn('USD 7500.00', changed['text'])

    def test_unknown_language_and_non_customer_conversation_never_call_model(self):
        did, rid, _, key = self.native_round(locked=False)
        with self.assertRaisesRegex(v.BadRequest, '母语尚未确认'):
            self.write('native', rid, conversation_key=key)
        supplier = self.aw('party_create', kind='supplier', name='虚构错误收件人')
        other_key = make_key('conversation', 'wechat', 'fictional-account', 'fictional-supplier')
        self.deals.write('conversation', did, dict(conversation_key=other_key, party_id=str(supplier)), 'fictional-owner')
        with self.assertRaisesRegex(v.BadRequest, '客户身份'):
            self.write('native', rid, conversation_key=other_key)
        self.assertEqual(self.runner.calls, [])
        self.assertEqual(self.detail(rid)['status'], 'draft')

    def test_unknown_quantity_placeholder_cannot_pass_send_check(self):
        product, _ = self.product()
        did = self.deals.write('create', None, dict(title='虚构未知数量'), 'fictional-owner')['id']
        self.deals.write('item', did, dict(product_id=str(product), name='虚构未确认数量灯具'), 'fictional-owner')
        rid = self.qw('create', did)
        lid = self.detail(rid)['lines'][0]['id']
        self.qw('line_update', rid, lid, sell_unit_price='250')
        self.qw('update', rid, incoterm='CIF', destination_country='ZZ', valid_until='2030-01-20')
        self.terms(rid)
        self.assertIn('qty_unconfirmed', [p['code'] for p in self.detail(rid)['blocking']])
        with self.assertRaisesRegex(v.Conflict, '数量'):
            self.qw('status', rid, action='send', confirm='1')
        self.qw('line_update', rid, lid, qty='24')
        self.assertNotIn('qty_unconfirmed', [p['code'] for p in self.detail(rid)['blocking']])

    def test_unit_change_requires_review_and_cannot_assume_quantity_conversion(self):
        _, rid, _, _, lid, _ = self.setup_round()
        party = self.aw('party_create', kind='vendor', name='虚构按件计费配套商')
        cid = self.qw('cost', rid, kind='vendor', party_id=str(party), unit_price='2.50', unit='pcs',
                      quantity_line_id=str(lid), valid_until='2030-02-01', source_note='虚构按件配套报价')
        self.qw('line_update', rid, lid, unit='箱')
        cost = next(item for item in self.detail(rid)['costs'] if item['id'] == cid)
        self.assertEqual(cost['amount'], '7.50')
        self.assertTrue(cost['calc']['needs_review'])
        self.assertIn('manual_cost_review', [p['code'] for p in self.detail(rid)['blocking']])

    def test_native_added_money_fails_closed_and_does_not_publish_artifact(self):
        _, rid, _, key = self.native_round()
        self.runner.add_amount = True
        with self.assertRaisesRegex(v.BadRequest, '没有通过校验'):
            self.write('native', rid, conversation_key=key)
        with self.assertRaisesRegex(v.Conflict, '有效母语稿'):
            self.quotes.read('text', {'lang': 'native'}, rid)
        self.assertEqual(self.detail(rid)['status'], 'draft')

    def test_native_language_change_invalidates_old_artifact_and_non_chinese_back_blocks(self):
        _, rid, _, key = self.native_round()
        self.write('native', rid, conversation_key=key)
        self.language.write('contact', dict(conversation_key=key, native_language='de', locked='1'), 'fictional-owner')
        with self.assertRaisesRegex(v.Conflict, '锁定状态已改变'):
            self.quotes.read('text', {'lang': 'native'}, rid)
        self.language.write('settings', dict(my_language='de'), 'fictional-owner')
        calls = len(self.runner.calls)
        with self.assertRaisesRegex(v.BadRequest, '阅读语言设为简体中文'):
            self.write('native', rid, conversation_key=key)
        self.assertEqual(len(self.runner.calls), calls)

    def test_quote_change_during_translation_rejects_old_native_version(self):
        _, rid, lid, key = self.native_round()
        changed = []
        def runner(prompt):
            if not changed:
                self.qw('line_update', rid, lid, qty='30')
                changed.append(True)
            return self.runner(prompt)
        self.language.runner = runner
        with self.assertRaisesRegex(v.Conflict, '报价刚刚改变'):
            self.write('native', rid, conversation_key=key)
        with self.assertRaisesRegex(v.Conflict, '有效母语稿'):
            self.quotes.read('text', {'lang': 'native'}, rid)
        self.assertEqual(self.detail(rid)['lines'][0]['qty'], 30)
