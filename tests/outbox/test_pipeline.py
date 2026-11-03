"""Durable sends, concurrency, quotas, privacy and safe binding with fake HTTP."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import hashlib
import json
import os
import sqlite3
import stat
import threading
from unittest.mock import patch

from console.inbox.model import make_key
from console.outbox import Outbox, Store
from console.outbox.model import DISABLED, UNKNOWN, UNSUPPORTED
from outbox_fixtures import ACCOUNT, CHAT, NOW, SECRET, SESSION, IsolatedTest, form


class PipelineTests(IsolatedTest):
    def records(self, table='outbox'):
        if not self.path.exists():
            return []
        with closing(sqlite3.connect(self.path)) as db:
            db.row_factory = sqlite3.Row
            return [dict(r) for r in db.execute('SELECT * FROM ' + table)]

    def test_settings_default_off_and_only_whatsapp_can_be_enabled(self):
        channels = self.service.settings()['channels']
        self.assertEqual([c['channel'] for c in channels], ['whatsapp', 'alibaba', 'wechat', 'website'])
        self.assertTrue(channels[0]['available'])
        self.assertFalse(any(c['enabled'] for c in channels))
        self.assertFalse(self.path.exists())
        for channel, phrase in [('alibaba', 'Win10'), ('wechat', '单独授权'), ('website', '尚未开放'), ('email', '原渠道')]:
            result, code = self.service.set_settings(dict(channel=channel, enabled='1'))
            self.assertEqual(code, 400)
            self.assertIn(phrase, result['reason'])
        self.assertEqual(self.service.set_settings(dict(channel='whatsapp', enabled='true'))[1], 400)
        self.enable()
        self.assertTrue(Store(self.path).enabled())
        self.assertTrue(self.service.settings()['channels'][0]['enabled'])
        self.assertEqual(self.service.set_settings(dict(channel='whatsapp', enabled='0'))[1], 200)
        self.assertFalse(Store(self.path).enabled())
        self.assertEqual(len(self.records('audit')), 7)
        self.assertEqual(self.records(), [])
        self.assertEqual(self.net.calls, [])

    def test_validation_order_and_all_refusals_never_insert_or_call_gateway(self):
        # Channel rejection must precede both the switch and confirmation validation.
        for channel in ('alibaba', 'wechat', 'website'):
            payload, code = self.service.send(self.hub.key(channel), {})
            self.assertEqual((code, payload['error']), (400, UNSUPPORTED))
        self.assertEqual(self.send(confirm='0')[0]['error'], DISABLED)
        self.enable()
        for bad in ('!!!', '', 'a', self.key + '=', 'x' * 9000):
            self.assertEqual(self.service.send(bad, form())[1], 400)
        for fields, phrase in [
            ({'confirm': ''}, '确认发送'), ({'confirm': '0'}, '确认发送'),
            ({'text': ''}, '1–4000'), ({'text': ' \n '}, '1–4000'),
            ({'text': '字' * 4001}, '1–4000'), ({'text': '\ud800'}, '1–4000'),
            ({'text': '虚构【待确认：价格】'}, '草稿里还有【待确认】，请先改掉再发送'),
            ({'text': '虚构【待确认未闭合'}, '草稿里还有【待确认】'),
            ({'request_id': ''}, '请求标识必填'), ({'request_id': 'short'}, '8–80'),
            ({'request_id': 'a' * 81}, '8–80'), ({'request_id': 'fictional_id'}, '8–80'),
            ({'request_id': '../private'}, '8–80'), ({'confirm_new_recipient': 'yes'}, '0 或 1'),
            ({'draft_id': '0'}, '草稿标识'), ({'draft_id': '1.5'}, '草稿标识'),
            ({'draft_id': '-1'}, '草稿标识'), ({'draft_id': '9' * 19}, '草稿标识'),
        ]:
            with self.subTest(fields=str(fields)[:80]):
                payload, code = self.send(**fields)
                self.assertEqual(code, 400)
                self.assertIn(phrase, payload['error'])
        no_confirm = form()
        del no_confirm['confirm']
        self.assertEqual(self.service.send(self.key, no_confirm)[1], 400)
        missing_id = form()
        del missing_id['request_id']
        self.assertEqual(self.service.send(self.key, missing_id)[1], 400)
        missing = make_key('conversation', 'whatsapp', 'fictional-account', 'fictional-missing@c.us')
        self.assertEqual(self.service.send(missing, form())[1], 404)
        malformed = make_key('conversation', 'whatsapp', 'fictional-account', 'fictional.dot@c.us')
        self.assertEqual(self.service.send(malformed, form())[1], 400)
        self.assertEqual(self.records(), [])
        self.assertEqual(self.net.calls, [])
        self.assertGreater(len(self.records('audit')), 20)

    def test_success_committed_sending_before_transport_hashes_and_dedup(self):
        self.enable()
        def before_send(packet):
            record, = self.records()
            self.assertEqual(record['state'], 'sending')
            self.assertEqual(record['idempotency_key'], packet['idempotency_key'])
        self.net.before_send = before_send
        first, code = self.send(draft_id='42')
        self.assertEqual(code, 200)
        self.assertEqual(first['message']['state'], 'sent')
        self.assertEqual(first['message']['state_text'], '已发出')
        self.assertFalse(first['needs_new_recipient_confirmation'])
        record, = self.records()
        self.assertEqual(record['text'], '虚构回复，请核对资料')
        self.assertEqual(record['draft_id'], 42)
        self.assertEqual(record['text_sha'], hashlib.sha256(record['text'].encode()).hexdigest())
        self.assertEqual(record['chat_ref'], hashlib.sha256(CHAT.encode()).hexdigest()[:16])
        expected = 'ops-' + hashlib.sha256(('fictional-request-01' + self.key).encode()).hexdigest()[:40]
        packet = self.net.sends[0][2]
        self.assertEqual(packet, dict(idempotency_key=expected, session=SESSION, account_id=ACCOUNT,
            chat_id=CHAT, actor='owner_explicit', text=record['text'], auto_send=False))
        # A replay uses the original record, even if valid text has changed.
        repeated, code = self.send(text='修改过的虚构文字')
        self.assertEqual((repeated, code), (first, 200))
        recreated = Outbox(self.path, bridge=self.bridge, inbox=self.hub, clock=lambda: self.now)
        self.assertEqual(recreated.send(self.key, form())[0], first)
        self.assertEqual(len(self.net.calls), 2)
        self.assertEqual(len(self.records()), 1)

    def test_concurrent_same_request_has_one_record_and_one_network_send(self):
        self.enable()
        ready, release = threading.Event(), threading.Event()
        def blocked(_):
            ready.set()
            self.assertTrue(release.wait(10))
        self.net.before_send = blocked
        with ThreadPoolExecutor(max_workers=10) as pool:
            first = pool.submit(self.send)
            self.assertTrue(ready.wait(10))
            services = [Outbox(self.path, bridge=self.bridge, inbox=self.hub, clock=lambda: self.now) for _ in range(8)]
            futures = [pool.submit(s.send, self.key, form()) for s in services]
            duplicates = [f.result(timeout=10) for f in futures]
            self.assertEqual({r[0]['message']['state'] for r in duplicates}, {'sending'})
            release.set()
            final, _ = first.result(timeout=10)
        self.assertEqual({r[0]['message']['id'] for r in duplicates}, {final['message']['id']})
        self.assertEqual(len(self.records()), 1)
        self.assertEqual(len(self.net.sends), 1)
        self.assertEqual(len(self.net.calls), 2)

    def test_same_request_cannot_target_another_conversation(self):
        self.enable()
        self.send()
        other = self.hub.add(chat='fictional-other@c.us')
        result, code = self.service.send(other, form())
        self.assertEqual(code, 409)
        self.assertIn('其它会话', result['error'])
        self.assertEqual(len(self.net.sends), 1)

    def test_replay_returns_durable_result_even_if_inbox_source_is_unavailable(self):
        self.enable()
        first, _ = self.send()
        with patch.object(self.hub, 'read', side_effect=AssertionError('replay must not refresh any remote source')):
            repeated, code = self.send()
        self.assertEqual((repeated, code), (first, 200))
        self.assertEqual(len(self.net.calls), 2)

    def test_known_refusals_are_failed_and_unknown_codes_are_uncertain(self):
        self.enable()
        cases = [('recipient_not_bound', '这位联系人还没有绑定', True),
                 ('send_kind_invalid', '内容不符合要求', False), ('text_invalid', '内容不符合要求', False),
                 ('api_auth_failed', '发送服务认证失败，请联系管理员', False),
                 ('account_mismatch', '发送没有成功', False)]
        for i, (error, phrase, needs) in enumerate(cases):
            self.net.answers = [(409 if needs else 401 if error == 'api_auth_failed' else 400, {'error': error})]
            payload, code = self.send(request_id=f'fictional-failed-{i}')
            self.assertEqual((code, payload['message']['state']), (200, 'failed'))
            self.assertEqual(payload['error_text'], phrase)
            self.assertEqual(payload['needs_new_recipient_confirmation'], needs)
        self.assertEqual(len(self.net.sends), 5)
        self.assertEqual(self.net.binds, [])

    def test_unknown_never_retries_old_request_new_request_is_explicit_attempt(self):
        self.enable()
        self.net.answers = [TimeoutError(SECRET + CHAT)]
        first, code = self.send()
        self.assertEqual((code, first['message']['state']), (200, 'unknown'))
        self.assertEqual(first['message']['state_text'], UNKNOWN)
        self.assertIn('不要重复发送', first['error_text'])
        self.assertEqual(self.send()[0], first)
        self.assertEqual(len(self.net.sends), 1)
        second, code = self.send(request_id='fictional-new-after-manual-check')
        self.assertEqual(second['message']['state'], 'sent')
        self.assertNotEqual(second['message']['id'], first['message']['id'])
        self.assertEqual(len(self.net.sends), 2)

    def test_unconfirmed_recipient_does_not_bind_or_retry_even_on_replay_with_confirmation(self):
        self.enable()
        self.net.answers = [(409, {'error': 'recipient_not_bound'})]
        first, _ = self.send()
        again, _ = self.send(confirm_new_recipient='1')
        self.assertEqual(again, first)
        self.assertTrue(again['needs_new_recipient_confirmation'])
        self.assertEqual(self.net.binds, [])
        self.assertEqual(len(self.net.sends), 1)

    def test_confirmed_new_recipient_binds_once_then_resends_identical_packet_once(self):
        self.enable()
        self.net.answers = [(409, {'error': 'recipient_not_bound'})]
        payload, code = self.send(confirm_new_recipient='1')
        self.assertEqual((code, payload['message']['state']), (200, 'sent'))
        self.assertEqual(len(self.net.binds), 1)
        self.assertEqual(len(self.net.sends), 2)
        self.assertEqual(self.net.sends[0][2], self.net.sends[1][2])
        self.assertEqual(self.net.binds[0][2], dict(actor='owner_explicit', confirmed=True, session=SESSION,
            account_id=ACCOUNT, chat_id=CHAT, reason='后台首次发送，用户已确认'))
        self.assertFalse(payload['needs_new_recipient_confirmation'])
        self.send(confirm_new_recipient='1')
        self.assertEqual(len(self.net.calls), 4)
        actions = [r['action'] for r in self.records('audit')]
        self.assertEqual(actions.count('send_attempt'), 2)
        self.assertEqual(actions.count('recipient_bind_attempt'), 1)
        self.assertEqual(actions.count('recipient_bind_result'), 1)

    def test_limiter_durable_conversation_window_and_replays_bypass_full_quota(self):
        self.enable()
        for i in range(5):
            self.assertEqual(self.send(request_id=f'fictional-quota-{i}')[1], 200)
        payload, code = self.send(request_id='fictional-quota-six')
        self.assertEqual(code, 429)
        self.assertEqual(payload['retry_after'], 60)
        self.assertEqual(self.send(request_id='fictional-quota-0')[0]['message']['id'], 1)
        self.assertEqual(len(self.net.sends), 5)
        self.now += 59
        self.assertEqual(self.send(request_id='fictional-quota-six')[0]['retry_after'], 1)
        self.now += 1
        self.assertEqual(self.send(request_id='fictional-quota-six')[1], 200)
        self.assertEqual(len(self.records()), 6)

    def test_global_hour_quota_counts_failures_and_uncertain_attempts_across_instances(self):
        self.enable()
        self.net.answers = [TimeoutError('fictional timeout')] * 60
        for i in range(60):
            key = self.hub.add(chat=f'fictional-customer-{i}@c.us')
            self.assertEqual(self.service.send(key, form(request_id=f'fictional-global-{i}'))[1], 200)
        another = Outbox(self.path, bridge=self.bridge, inbox=self.hub, clock=lambda: self.now)
        payload, code = another.send(self.key, form(request_id='fictional-global-extra'))
        self.assertEqual((code, payload['retry_after']), (429, 3600))
        self.now += 3600
        self.assertEqual(another.send(self.key, form(request_id='fictional-global-extra'))[1], 200)
        self.assertEqual(len(self.net.sends), 61)

    def test_parallel_distinct_requests_cannot_exceed_quota(self):
        self.enable()
        with ThreadPoolExecutor(max_workers=10) as pool:
            outcomes = list(pool.map(lambda i: self.send(request_id=f'fictional-parallel-{i}'), range(10)))
        self.assertEqual(sum(code == 200 for _, code in outcomes), 5)
        self.assertEqual(sum(code == 429 for _, code in outcomes), 5)
        self.assertEqual(len(self.records()), 5)
        self.assertEqual(len(self.net.sends), 5)

    def test_settings_rechecked_atomically_at_admission(self):
        self.enable()
        self.service.store.set_enabled(False, NOW)
        row, new, code, retry = self.service.store.reserve(self.key, CHAT, '虚构', 'fictional-race', None, NOW)
        self.assertEqual((row, new, code, retry), (None, False, 'disabled', 0))
        self.assertEqual(self.records(), [])

    def test_audit_safe_codes_no_secret_or_plain_chat_and_storage_private_wal(self):
        self.enable()
        original = os.umask(0o022)
        self.addCleanup(os.umask, original)
        self.net.answers = [(400, {'error': SECRET + CHAT})]
        payload, _ = self.send()
        self.assertEqual(payload['message']['state'], 'unknown')
        current = os.umask(0o022)
        self.assertEqual(current, 0o022)
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute('PRAGMA journal_mode').fetchone()[0], 'wal')
            with self.assertRaises(sqlite3.IntegrityError), db:
                db.execute("UPDATE outbox SET state='retry'")
            with self.assertRaises(sqlite3.IntegrityError), db:
                db.execute('INSERT INTO outbox SELECT * FROM outbox')
        for item in (payload, self.records('audit'), self.records()):
            rendered = json.dumps(item, ensure_ascii=False)
            for value in (SECRET, CHAT, ACCOUNT, 'fictional-waha-key', 'fictional-ignored-private'):
                self.assertNotIn(value, rendered)

    def test_recent_limits_order_and_public_fields(self):
        self.assertEqual(self.service.recent(), {'items': []})
        self.assertFalse(self.path.parent.exists())
        self.enable()
        # Spread synthetic sends over time, within both quotas.
        for i in range(55):
            self.now += 61
            self.send(request_id=f'fictional-list-{i}')
        global_rows = self.service.recent()['items']
        rows = self.service.recent(self.key)['items']
        self.assertEqual((len(global_rows), len(rows)), (50, 30))
        self.assertEqual([r['id'] for r in rows], list(range(55, 25, -1)))
        for row in global_rows:
            self.assertEqual(set(row), {'id', 'conversation_key', 'channel', 'text', 'state',
                                        'state_text', 'error_text', 'created_at'})
        self.assertEqual(self.service.recent(self.hub.key('alibaba'))['items'], [])

    def test_crash_left_sending_is_never_reissued(self):
        self.enable()
        row, new, _, _ = self.service.store.reserve(self.key, CHAT, '虚构', 'fictional-request-01', None, NOW)
        payload, code = self.send()
        self.assertEqual((code, payload['message']['id'], payload['message']['state']), (200, row['id'], 'sending'))
        self.assertEqual(self.net.calls, [])

    def test_wal_and_shared_memory_files_are_private_during_active_write(self):
        self.enable()
        with self.service.store._write() as db:
            db.execute("INSERT INTO audit(at,action) VALUES(?,?)", (NOW, 'fictional-permissions'))
            for suffix in ('', '-wal', '-shm'):
                path = type(self.path)(str(self.path) + suffix)
                self.assertTrue(path.exists())
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_unique_idempotency_conflict_returns_existing_without_sending(self):
        self.enable()
        # Force a synthetic hash collision to exercise the second UNIQUE constraint.
        with patch('console.outbox.store.sha', return_value='a' * 64):
            first, code = self.send()
            second, repeated_code = self.send(request_id='fictional-collision')
        self.assertEqual((first, code), (second, repeated_code))
        self.assertEqual(len(self.records()), 1)
        self.assertEqual(len(self.net.sends), 1)

    def test_private_store_rejects_symlink_without_touching_target(self):
        target = self.root / 'fictional-target'
        target.write_text('fictional private data')
        linked = self.root / 'outbox-link.sqlite3'
        linked.symlink_to(target)
        with self.assertRaises(OSError):
            Store(linked).set_enabled(True, NOW)
        self.assertEqual(target.read_text(), 'fictional private data')
        self.assertEqual(self.net.calls, [])
