import unittest

from console.archive.validation import BadRequest
from console.deals.rules import transition, pending_templates
from console.deals.stages import KEYS, templates, progress
from console.deals.waiting import evaluate, inbox_lookup

NOW = 2_000_000_000


class RulesTests(unittest.TestCase):
    def test_stage_order_templates_and_idempotence(self):
        self.assertEqual(len(KEYS), 11)
        for stage in KEYS:
            with self.subTest(stage=stage):
                ts = templates(stage)
                self.assertEqual([t['template_key'] for t in ts], [f'{stage}:{i}' for i in range(1, len(ts)+1)])
                self.assertTrue(all(t['auto'] == 1 for t in ts))
                self.assertEqual(pending_templates(stage, 'open', set()), ts)
                self.assertEqual(pending_templates(stage, 'open', {t['template_key'] for t in ts}), [])
                for status in ('won', 'lost'):
                    self.assertEqual(pending_templates(stage, status, set()), ts if status == 'won' and KEYS.index(stage) >= KEYS.index('paid') else [])
                self.assertEqual(sum(s['current'] for s in progress(stage)), 1)

    def test_payment_table_and_rollback(self):
        for stage in KEYS:
            with self.subTest(stage=stage):
                old = dict(stage='inquiry', status='open', won_at=None)
                won = KEYS.index(stage) >= KEYS.index('paid')
                if won:
                    with self.assertRaises(BadRequest):
                        transition(old, dict(stage=stage), 'stamp', creating=True)
                    with self.assertRaises(BadRequest):
                        transition(old, dict(stage=stage), 'stamp')
                    changes, events = transition(old, dict(stage=stage), 'stamp', payment_confirmed=True)
                else:
                    changes, events = transition(old, dict(stage=stage), 'stamp', creating=True)
                self.assertEqual(changes.get('status', 'open'), 'won' if won else 'open')
                self.assertEqual(changes.get('won_at'), 'stamp' if won else None)
                self.assertEqual(sum(k == 'system' for k, _ in events), int(won))
        old = dict(stage='shipped', status='won', won_at='before')
        changes, events = transition(old, dict(stage='quoting'), 'stamp')
        self.assertEqual((changes['status'], changes['won_at']), ('open', None))
        self.assertIn(('stage', '阶段：已发货 → 报价'), events)
        with self.assertRaises(BadRequest):
            transition(dict(stage='inquiry', status='open'), dict(status='lost'), 'stamp')
        changes, _ = transition(dict(stage='inquiry', status='open'), dict(status='lost', lost_reason='虚构原因'), 'stamp')
        self.assertEqual(changes['closed_at'], 'stamp')
        with self.assertRaises(BadRequest):
            transition(dict(stage='inquiry', status='open'), dict(status='won'), 'stamp')

    def test_paid_progress_keeps_historical_won_time_without_creating_payment(self):
        for won_at in ('before', None):
            old = dict(stage='paid', status='won', won_at=won_at)
            for stage in KEYS[6:]:
                changes, events = transition(old, dict(stage=stage), 'stamp')
                self.assertNotIn('won_at', changes)
                self.assertNotIn('status', changes)
                self.assertFalse(any(kind == 'system' for kind, _ in events))


class WaitingTests(unittest.TestCase):
    def test_role_latest_missing_and_boundary_table(self):
        participant = [dict(party_id=1, role='supplier')]
        cases = [([], {}, 'none', False), (['a'], {}, 'none', False),
            (['a'], {'a': ('buyer', NOW)}, 'us', False),
            (['a'], {'a': ('seller', NOW - 48*3600)}, 'them', False),
            (['a'], {'a': ('seller', NOW - 48*3600 - 1)}, 'them', True),
            (['a', 'b'], {'a': ('seller', NOW-200000), 'b': ('buyer', NOW-1)}, 'us', False),
            (['a', 'b'], {'a': ('buyer', NOW-200000), 'b': ('seller', NOW-1)}, 'them', False),
            (['a'], {'a': ('unknown', NOW)}, 'none', False)]
        for keys, data, state, overdue in cases:
            with self.subTest(keys=keys, data=data):
                links = [dict(id=i, party_id=1, conversation_key=k) for i, k in enumerate(keys)]
                def lookup(k):
                    return dict(last_role=data[k][0], last_at=data[k][1]) if k in data else None
                result = evaluate(participant, links, [], lookup, NOW)
                self.assertEqual(result['participants'][0]['state'], state)
                # A different conversation must not hide an unanswered item.
                any_us = any(role == 'buyer' for role, _ in data.values())
                any_overdue = any(role == 'seller' and NOW - at > 48*3600 for role, at in data.values())
                self.assertEqual(result['waiting_on_us'], any_us)
                self.assertEqual(result['overdue'], ['supplier'] if any_overdue else [])
                self.assertEqual(result['needs_attention'], any_us or any_overdue)
                self.assertEqual(result['unlinked_participants'], int(not keys))

    def test_role_dedup_due_task_and_injected_threshold(self):
        ps = [dict(party_id=i, role=r) for i, r in enumerate(('supplier', 'supplier', 'forwarder', 'customer'))]
        cs = [dict(id=i, party_id=i, conversation_key=str(i)) for i in range(3)]
        result = evaluate(ps, cs, [], lambda k: dict(last_role='seller', last_at=NOW-3601), NOW, overdue_hours=1)
        self.assertEqual(result['waiting_on'], ['supplier', 'forwarder'])
        self.assertEqual(result['overdue'], ['supplier', 'forwarder'])
        self.assertEqual(result['unlinked_participants'], 1)
        for status in ('open', 'done', 'skipped'):
            for due in (NOW-1, NOW, NOW+1, None):
                result = evaluate([], [], [dict(status=status, due=due)], lambda k: None, NOW)
                self.assertEqual(result['needs_attention'], status == 'open' and due is not None and due <= NOW)

    def test_inbox_lookup_snapshot_once_no_bodies_and_failure(self):
        class Fake:
            calls = 0
            def read(self, now):
                self.calls += 1
                return {'conversations': {'a': dict(messages=[dict(role='buyer', sent_at=NOW, text='fictional secret')], buyer_name='虚构', channel_label='虚构渠道')}}
        fake = Fake()
        lookup = inbox_lookup(fake, NOW)
        self.assertEqual(lookup('a')['last_role'], 'buyer')
        self.assertNotIn('text', lookup('a'))
        self.assertIsNone(lookup('missing'))
        self.assertEqual(fake.calls, 1)
        class Failed:
            def read(self, now):
                raise RuntimeError('fictional secret')
        self.assertIsNone(inbox_lookup(Failed(), NOW)('a'))
        result = evaluate([dict(party_id=1, role='vendor')], [dict(id=1, party_id=1, conversation_key='a')], [],
                          lambda k: (_ for _ in ()).throw(RuntimeError('fictional')), NOW)
        self.assertEqual(result['participants'][0]['state'], 'none')
