from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from console.archive import validation as v
from console.archive.service import Archive
from console.archive.store import Store
from console.deals.service import Deals
from console.deals.stages import KEYS
from console.inbox.model import make_key
from test_migration import fixture

NOW = 2_000_000_000
KEY = make_key('conversation', 'alibaba', 'fictional-account', 'fictional-conversation')
KEY2 = make_key('conversation', 'wechat', 'fictional-account', 'fictional-group')


class DealServiceTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / 'archive.sqlite3'
        self.store = Store(self.path)
        self.archive = Archive(store=self.store)
        self.now = NOW
        self.messages = {}
        self.deals = Deals(store=self.store, lookup=self.messages.get, clock=lambda: self.now)
        guard = patch('socket.socket.connect', side_effect=AssertionError('tests must be offline'))
        guard.start()
        self.addCleanup(guard.stop)

    def party(self, kind='customer', name='虚构客户'):
        return self.archive.write('party_create', None, dict(kind=kind, name=name), 'fictional-owner')['id']

    def write(self, op, did=None, child=None, **fields):
        return self.deals.write(op, did, fields, 'fictional-owner', child)['id']

    def detail(self, did):
        return self.deals.read('detail', row_id=did)

    def confirm_payment(self, did):
        from console.fulfil.service import Fulfil
        service = Fulfil(store=self.store, deals=self.deals, clock=lambda: self.now)
        pid = service.write('payment_create', did, dict(kind='deposit', amount_fen='1200',
            currency='USD', received_on='2030-01-01', method='wire'), 'fictional-owner')['id']
        service.write('payment_confirm', pid, {'confirm': '1'}, 'fictional-owner')

    def test_missing_and_v1_reads_never_migrate(self):
        self.assertEqual(self.deals.read('summary')['open'], 0)
        self.assertFalse(self.path.exists())
        with sqlite3.connect(self.path) as db:
            fixture(db)
        before = self.path.read_bytes()
        self.assertEqual(self.deals.read('list')['total'], 0)
        self.assertIsNone(self.deals.read('identity_lookup', {'key': KEY}))
        self.assertEqual(before, self.path.read_bytes())

    def test_create_from_conversation_reuse_party_multiple_deals_and_duplicate_link(self):
        a = self.write('create', conversation_key=KEY, party_kind='customer', party_name='虚构客户')
        b = self.write('create', conversation_key=KEY2, party_kind='customer', party_name='虚构客户')
        pid = self.detail(a)['customer']['id']
        self.assertEqual(self.detail(b)['customer']['id'], pid)
        self.assertEqual(self.detail(a)['title'], '虚构客户的案子')
        c = self.write('create', conversation_key=KEY)
        self.assertEqual(self.detail(c)['customer']['id'], pid)
        before = self.detail(a)
        self.write('conversation', a, conversation_key=KEY)
        self.assertEqual(before, self.detail(a))
        self.assertEqual(len(self.deals.read('by_conversation', {'key': KEY})['deals']), 2)
        other = self.party('vendor', '虚构配套方')
        with self.assertRaises(v.Conflict):
            self.write('conversation', a, conversation_key=KEY, party_id=str(other))
        self.assertEqual(len(self.detail(a)['participants']), 1)

    def test_explicit_customer_and_participant_removal_only_affects_one_deal(self):
        customer, supplier = self.party(), self.party('supplier', '虚构供应商')
        a = self.write('create', customer_party_id=str(customer))
        b = self.write('create')
        self.write('conversation', a, conversation_key=KEY, party_id=str(supplier))
        self.write('conversation', b, conversation_key=KEY)
        target = next(p for p in self.detail(a)['participants'] if p['party_id'] == supplier)
        self.write('participant_remove', a, target['id'])
        self.assertEqual(self.detail(a)['conversations'], [])
        self.assertEqual(len(self.detail(b)['conversations']), 1)
        self.assertEqual(self.deals.read('identity_lookup', {'key': KEY})['party']['id'], supplier)
        with self.assertRaises(v.Missing):
            self.write('participant_remove', a, self.detail(b)['participants'][0]['id'])

    def test_identity_transfer_confirmation_and_global_links(self):
        customer, vendor = self.party(), self.party('vendor', '虚构验货方')
        did = self.write('create', conversation_key=KEY)
        self.write('identity', conversation_key=KEY, party_id=str(customer))
        self.assertEqual(self.detail(did)['conversations'][0]['party_id'], customer)
        with self.assertRaises(v.Conflict):
            self.write('identity', conversation_key=KEY, party_id=str(vendor))
        self.write('identity', conversation_key=KEY, party_id=str(vendor), confirm='1')
        self.assertEqual(self.detail(did)['conversations'][0]['party_id'], vendor)
        self.assertEqual(self.detail(did)['unlinked_participants'], 1)
        self.assertEqual(len(self.detail(did)['participants']), 2)

    def test_templates_reentry_does_not_reopen_and_terminal_stops_new_auto(self):
        did = self.write('create', stage='sourcing')
        tasks = self.detail(did)['tasks']['sourcing']
        self.assertEqual(len(tasks), 3)
        self.write('task_status', tasks[0]['id'], status='done')
        self.write('update', did, stage='inquiry')
        self.write('update', did, stage='sourcing')
        self.assertEqual(len(self.detail(did)['tasks']['sourcing']), 3)
        self.assertEqual(self.detail(did)['tasks']['sourcing'][0]['status'], 'done')
        self.confirm_payment(did)
        d = self.detail(did)
        self.assertEqual(d['status'], 'won')
        self.assertIsNotNone(d['won_at'])
        self.assertEqual(d['tasks']['paid'], [])
        won_at = d['won_at']
        self.now += 10
        self.write('update', did, stage='inspection')
        self.assertEqual(self.detail(did)['won_at'], won_at)
        self.assertEqual(self.detail(did)['tasks']['inspection'], [])
        self.write('task', did, title='虚构备货待办')
        self.assertEqual(len(self.detail(did)['tasks']['inspection']), 1)
        self.write('update', did, stage='quoting')
        self.assertEqual((self.detail(did)['status'], self.detail(did)['won_at']), ('open', None))
        with self.assertRaises(v.BadRequest):
            self.write('update', did, status='lost')
        self.write('update', did, status='lost', lost_reason='虚构预算原因')
        self.assertIsNotNone(self.detail(did)['closed_at'])
        with self.assertRaises(v.BadRequest):
            self.write('update', did, status='lost')
        self.write('update', did, stage='negotiating')
        self.assertEqual(self.detail(did)['tasks']['negotiating'], [])
        self.write('update', did, status='open')
        self.assertIsNone(self.detail(did)['closed_at'])

    def test_create_and_update_paid_or_later_require_payment_confirmation(self):
        for stage in KEYS[5:]:
            with self.subTest(stage=stage), self.assertRaises(v.BadRequest):
                self.write('create', stage=stage)
        did = self.write('create', stage='awaiting_payment')
        before = self.detail(did)
        for stage in KEYS[5:]:
            for fields in (dict(stage=stage), dict(stage=stage, status='won')):
                with self.subTest(fields=fields), self.assertRaises(v.BadRequest):
                    self.write('update', did, **fields)
        with self.assertRaises(v.BadRequest):
            self.write('update', did, status='won')
        self.assertEqual(before, self.detail(did))
        self.confirm_payment(did)
        won_at = self.detail(did)['won_at']
        for stage in KEYS[6:]:
            self.now += 10
            self.write('update', did, stage=stage)
            self.assertEqual((self.detail(did)['status'], self.detail(did)['won_at']), ('won', won_at))

    def test_historical_paid_without_ledger_is_preserved_and_can_progress(self):
        did = self.write('create')
        with self.store.write() as db:
            db.execute("UPDATE deals SET stage='paid',status='won',won_at=? WHERE id=?", ('2029-12-01', did))
        self.write('update', did, notes='虚构历史记录待核对')
        self.write('update', did, stage='inspection')
        self.assertEqual((self.detail(did)['status'], self.detail(did)['won_at']), ('won', '2029-12-01'))
        with self.store.read() as db:
            self.assertNotIn('deal_payments', {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")})
            self.assertEqual([r[0] for r in db.execute('SELECT version FROM schema_migrations')], [2])

    def test_money_items_tasks_events_and_audit_redaction(self):
        did = self.write('create', title='fictional-private-title')
        self.write('update', did, value='12.34', currency='USD', notes='fictional-private-note', next_action='虚构跟进', next_action_due='2033-05-18')
        iid = self.write('item', did, name='fictional-private-item', target_price='0.3', qty='2', currency='CNY')
        tid = self.write('task', did, title='fictional-private-task', due='2033-05-18T12:30:00+08:00')
        self.write('task_status', tid, status='done')
        self.write('event', did, note='fictional-private-event')
        d = self.detail(did)
        self.assertEqual(d['value'], '12.34')
        self.assertEqual(d['items'][0]['target_price'], '0.30')
        self.assertNotIn('value_fen', d)
        self.assertNotIn('target_price_fen', d['items'][0])
        task = next(t for t in d['tasks']['inquiry'] if t['id'] == tid)
        self.assertEqual(task['due'], '2033-05-18T04:30:00+00:00')
        self.assertIsNotNone(task['done_at'])
        self.write('task_status', tid, status='open')
        self.assertIsNone(next(t for t in self.detail(did)['tasks']['inquiry'] if t['id'] == tid)['done_at'])
        self.write('item_remove', did, iid)
        self.write('update', did, value='')
        self.assertIsNone(self.detail(did)['value'])
        with self.store.read() as db:
            audits = [dict(r) for r in db.execute('SELECT * FROM audit')]
        raw = json.dumps([{k: value for k, value in a.items() if k != 'at'} for a in audits])
        for secret in ('fictional-private', '12.34', '1234', '0.30'):
            self.assertNotIn(secret, raw)
        for a in audits:
            self.assertEqual(set(json.loads(a['detail'])), {'fields'})

    def test_summary_waiting_attention_stale_filter_pagination_and_activity(self):
        pid = self.party()
        a = self.write('create', title='虚构甲', customer_party_id=str(pid), conversation_key=KEY)
        b = self.write('create', title='虚构乙')
        self.write('task', b, title='虚构到期待办', due='2030-01-01')
        self.now += 8*86400
        self.messages[KEY] = dict(last_role='buyer', last_at=self.now, channel_label='虚构渠道', title='虚构会话')
        summary = self.deals.read('summary')
        self.assertEqual((summary['open'], summary['waiting_on_us'], summary['due_tasks'], summary['stale']), (2, 1, 1, 1))
        self.assertEqual(self.deals.read('list', dict(waiting='us'))['total'], 2)
        self.assertEqual(self.deals.read('list', dict(party=pid))['total'], 1)
        self.assertEqual(self.deals.read('list', dict(q='客户'))['total'], 1)
        self.assertEqual(self.deals.read('list', dict(q='%'))['total'], 0)
        self.assertEqual(self.deals.read('list', dict(page=2, page_size=1))['items'][0]['id'], b)
        self.assertEqual(self.detail(a)['last_activity_at'], self.now)
        self.messages[KEY] = dict(last_role='seller', last_at=self.now - 49*3600)
        self.assertEqual(self.deals.read('list', dict(waiting='them'))['total'], 1)
        self.assertEqual(self.deals.read('summary')['overdue'], 1)
        self.write('update', a, status='paused')
        self.assertEqual(self.deals.read('list')['total'], 1)
        self.assertEqual(self.deals.read('list', dict(status='paused'))['total'], 1)
        self.assertEqual(self.deals.read('summary')['paused'], 1)

    def test_rollback_party_identity_participants_and_audits(self):
        self.store.initialize()
        with self.store.read() as db:
            before = db.execute('SELECT count(*) FROM audit').fetchone()[0]
        with patch.object(self.deals, '_templates', side_effect=RuntimeError('fictional')), self.assertRaises(RuntimeError):
            self.write('create', conversation_key=KEY, party_kind='customer', party_name='虚构回滚客户')
        with self.store.read() as db:
            for table in ('parties', 'identities', 'deals', 'deal_participants', 'deal_conversations', 'deal_events'):
                self.assertEqual(db.execute('SELECT count(*) FROM ' + table).fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT count(*) FROM audit').fetchone()[0], before)

    def test_events_last_100_reverse_order_and_stage_progress(self):
        did = self.write('create', stage='quoting')
        for i in range(103):
            self.write('event', did, note='虚构记录 ' + str(i))
        d = self.detail(did)
        self.assertEqual(len(d['events']), 100)
        self.assertEqual(d['events'][0]['summary'], '虚构记录 102')
        self.assertEqual([s['key'] for s in d['stages'] if s['done']], ['inquiry', 'sourcing'])

    def test_party_kinds_preserve_foreign_names_and_country(self):
        for kind in ('customer', 'vendor'):
            pid = self.party(kind, '　广州 Fictional 有限公司　')
            self.archive.write('party_update', pid, dict(country='ZZ'), 'fictional-owner')
            p = self.archive.read('parties', row_id=pid)
            self.assertEqual(p['name'], '广州 Fictional 有限公司')
            self.assertEqual(p['short_name'], p['name'])
            self.assertEqual(p['country'], 'ZZ')

    def test_stale_seven_day_boundary_and_combined_transition_conflicts(self):
        did = self.write('create')
        self.now += 7*86400-1
        self.assertEqual(self.deals.read('summary')['stale'], 0)
        self.now += 1
        self.assertEqual(self.deals.read('summary')['stale'], 1)
        with self.assertRaises(v.BadRequest):
            self.write('update', did, stage='paid', status='open')
        self.assertEqual(self.detail(did)['stage'], 'inquiry')
        self.confirm_payment(did)
        with self.assertRaises(v.BadRequest):
            self.write('update', did, stage='inquiry', status='lost', lost_reason='虚构原因')
        self.assertEqual(self.detail(did)['stage'], 'paid')
