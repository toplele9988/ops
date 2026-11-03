import io
import json
import logging
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from console.channels import AlibabaAdapter, WechatAdapter, WhatsAppAdapter, WebsiteAdapter, UnifiedInbox
from console.channels.common import ChannelUnavailable, placeholder
from console.channels.whatsapp import CHAT_ID, NoRedirect, local_url
from console.inbox.model import WAITING_WINDOW_DAYS, decode_key, make_key, waiting_state
from console.inbox.store import read_database
from channel_fixtures import (NOW, SECRET, FakeWAHA, ali_message, fake_stat, seed_ali,
                              seed_config, seed_wx, wx_message)


class AdapterTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.ali, self.wx, self.config = (self.root / n for n in ('ali.sqlite3', 'wx.sqlite3', 'wa.json'))
        self.net = FakeWAHA()
        network = patch('socket.socket.connect', side_effect=AssertionError('network forbidden'))
        network.start()
        self.addCleanup(network.stop)

    def wa(self):
        return WhatsAppAdapter(self.config, self.net.fetch, lambda: self.net.tick, fake_stat, 731)

    def test_alibaba_wraps_dedup_and_keeps_identity_and_metadata(self):
        first = ali_message()
        seed_ali(self.ali, [first, first, ali_message(role='seller', age=10, mid='m2')])
        adapter = AlibabaAdapter(self.ali)
        conv, = adapter.list_conversations(NOW).values()
        self.assertEqual(decode_key(conv['key'], 'conversation'), ('alibaba', 'fictional-account', 'alice'))
        self.assertEqual((conv['n_messages'], conv['channel_label'], conv['counterpart_role']), (2, '阿里国际站', 'customer'))
        self.assertFalse(conv['waiting_reply'])
        self.assertFalse(conv['dormant'])
        self.assertIsInstance(conv['messages'], tuple)
        self.assertEqual(len(adapter.messages(conv['key'])), 2)

    def test_alibaba_cache_refreshes_mtime_and_deletion(self):
        seed_ali(self.ali)
        adapter = AlibabaAdapter(self.ali)
        first = adapter.list_conversations(NOW)
        self.assertIs(first, adapter.list_conversations(NOW + 1))
        seed_ali(self.ali, [ali_message(cid='bob', mid='m2')])
        self.assertEqual(len(adapter.list_conversations(NOW)), 2)
        self.ali.unlink()
        self.assertEqual(adapter.list_conversations(NOW), {})
        self.assertFalse(adapter.health(NOW)['ready'])

    def test_alibaba_missing_table_and_column_ready_false_health_damage_isolated(self):
        with sqlite3.connect(self.ali) as db:
            db.execute('CREATE TABLE unrelated(dummy TEXT)')
        adapter = AlibabaAdapter(self.ali)
        self.assertEqual(adapter.list_conversations(NOW), {})
        self.assertFalse(adapter.health(NOW)['ready'])
        with sqlite3.connect(self.ali) as db:
            db.execute('CREATE TABLE inbound(id INTEGER PRIMARY KEY)')
        self.assertFalse(adapter.health(NOW)['ready'])
        self.ali.write_text('fictional corrupted sqlite')
        self.assertFalse(adapter.health(NOW)['ready'])

    def test_wechat_roles_majority_own_sender_and_default_exclusions(self):
        rows = [wx_message(mid='m1'), wx_message(mid='m2', sender='fictional-own', age=10),
                wx_message(mid='m3', age=5), wx_message(group='fictional-study@chatroom', mid='m4'),
                wx_message(group='fictional-office@chatroom', mid='m5'),
                wx_message(group='fictional-unknown@chatroom', mid='m6')]
        seed_wx(self.wx, rows, dict(m1='supplier', m2='customer', m3='supplier', m4='learning', m5='internal'))
        adapter = WechatAdapter(self.wx)
        convs = adapter.list_conversations(NOW)
        self.assertEqual(len(convs), 2)
        supplier = next(c for c in convs.values() if c['counterpart_role'] == 'supplier')
        self.assertEqual([m['role'] for m in supplier['messages']], ['buyer', 'seller', 'buyer'])
        self.assertTrue(supplier['waiting_reply'])
        self.assertEqual(supplier['account'], '微信')
        self.assertTrue(all(m['sender_label'].startswith('成员 ') for m in supplier['messages']))
        self.assertEqual(adapter.health(NOW)['groups_by_role'], dict(supplier=1, learning=1, internal=1, unclassified=1))
        self.assertEqual(len(WechatAdapter(self.wx, include_roles=('learning', 'internal')).list_conversations(NOW)), 2)

    def test_wechat_names_precedence_discovered_room_and_member_fallback(self):
        rows = [wx_message(), wx_message(group='fictional-b@chatroom', mid='m2'), wx_message(group='fictional-c@chatroom', mid='m3')]
        seed_wx(self.wx, rows)
        with sqlite3.connect(self.wx) as db:
            db.execute('INSERT INTO onboarding_candidates VALUES(?,?)', (rows[0]['group_id'], '虚构客户群'))
            db.execute('INSERT INTO fictional_rooms VALUES(?,?)', (rows[0]['group_id'], '不应覆盖首选名称'))
            db.execute('INSERT INTO fictional_rooms VALUES(?,?)', (rows[1]['group_id'], '虚构另一个群'))
            db.execute('INSERT INTO fictional_contacts VALUES(?,?)', ('fictional-other', '虚构成员'))
        convs = WechatAdapter(self.wx).list_conversations(NOW)
        self.assertEqual({c['buyer_name'] for c in convs.values()}, {'虚构客户群', '虚构另一个群', '微信群 ·room'})
        self.assertTrue(all(m['sender_label'] == '虚构成员' for c in convs.values() for m in c['messages']))

    def test_attachment_placeholders_never_expose_paths(self):
        cases = [('image', '[图片]'), ('file', '[文件]'), ('video', '[视频]'), ('voice', '[语音]'), ('unknown', '[附件]'), (43, '[视频]')]
        rows = [wx_message(mid='m' + str(i), text='', attachments=[{'msg_type': kind, 'path': '/fictional/private/path'}])
                for i, (kind, _) in enumerate(cases)]
        # The helper accepts optional field overrides through dict composition.
        seed_wx(self.wx, rows)
        conv, = WechatAdapter(self.wx).list_conversations(NOW).values()
        self.assertEqual([m['text'] for m in conv['messages']], [label for _, label in cases])
        self.assertNotIn('/fictional/private', json.dumps(conv))
        self.assertEqual(placeholder([{'mimetype': 'image/png'}]), '[图片]')

    def test_wechat_missing_tables_columns_bad_rows_and_recovery(self):
        adapter = WechatAdapter(self.wx)
        self.assertFalse(adapter.health(NOW)['ready'])
        with sqlite3.connect(self.wx) as db:
            db.execute('CREATE TABLE messages(created_at REAL)')
        self.assertFalse(adapter.health(NOW)['ready'])
        self.wx.unlink()
        seed_wx(self.wx)
        self.assertTrue(adapter.health(NOW)['ready'])
        with sqlite3.connect(self.wx) as db:
            db.execute('INSERT INTO messages VALUES(?,?,?)', ('fictional-app', 'bad', '{bad'))
            db.execute('INSERT INTO messages VALUES(?,?,?)', ('fictional-app', 'bad2', '[]'))
        self.assertEqual(len(adapter.list_conversations(NOW)), 1)
        with sqlite3.connect(self.wx) as db:
            db.execute('ALTER TABLE message_scopes RENAME COLUMN scope_id TO absent')
        self.assertFalse(adapter.health(NOW)['ready'])

    def test_wechat_dedup_readonly_and_wal_cache_invalidation(self):
        row = wx_message()
        seed_wx(self.wx, [row, row])
        adapter = WechatAdapter(self.wx)
        before = self.wx.read_bytes(), self.wx.stat().st_mtime_ns
        with read_database(self.wx) as db:
            self.assertEqual(db.execute('PRAGMA query_only').fetchone()[0], 1)
            with self.assertRaises(sqlite3.OperationalError):
                db.execute('DELETE FROM messages')
        with patch.object(adapter, '_load', wraps=adapter._load) as load:
            first = adapter.list_conversations(NOW)
            self.assertEqual(next(iter(first.values()))['n_messages'], 1)
            self.assertIs(first, adapter.list_conversations(NOW + 1))
            self.assertEqual(load.call_count, 1)
            self.assertEqual((self.wx.read_bytes(), self.wx.stat().st_mtime_ns), before)
            db = sqlite3.connect(self.wx)
            try:
                db.execute('PRAGMA journal_mode=WAL')
                db.execute('INSERT INTO messages VALUES(?,?,?)', ('fictional-app', 'm2', json.dumps(wx_message(mid='m2'))))
                db.commit()
                self.assertEqual(next(iter(adapter.list_conversations(NOW).values()))['n_messages'], 2)
                self.assertEqual(load.call_count, 2)
            finally:
                db.close()

    def test_whatsapp_overview_history_roles_masks_and_exact_get_paths(self):
        seed_config(self.config)
        adapter = self.wa()
        convs = adapter.list_conversations(NOW)
        self.assertEqual(len(convs), 2)
        self.assertEqual({c['account'] for c in convs.values()}, {'WhatsApp · ****8888'})
        self.assertEqual({c['counterpart_role'] for c in convs.values()}, {'customer'})
        group = next(c for c in convs.values() if c['is_group'])
        self.assertEqual(group['messages'][0]['text'], '[附件]')
        self.assertFalse(group['waiting_reply'])
        personal = next(c for c in convs.values() if not c['is_group'])
        messages = adapter.messages(personal['key'])
        self.assertEqual([m['role'] for m in messages], ['buyer', 'seller'])
        self.assertTrue(all(m['sender_label'] is None for m in messages))
        urls = [call[1] for call in self.net.calls]
        self.assertIn('http://127.0.0.1:12345/api/fictional-session/chats/overview?limit=500', urls)
        self.assertIn('http://127.0.0.1:12345/api/fictional-session/chats/15550001111%40c.us/messages?limit=100&downloadMedia=false', urls)
        health = adapter.health(NOW)
        self.assertEqual((health['session_status'], health['chats_total'], health['chats_7d']), ('WORKING', 2, 2))

    def test_whatsapp_chat_id_validation_precedes_key_and_transport(self):
        seed_config(self.config)
        bad = ['1@c.us/../../bad', '1@c.us?evil', '1%2f@g.us', '1@evil', '@g.us', 'a+b@lid', '汉字@c.us', '1@c.us\n']
        self.net.chats += [dict(id=cid, lastMessage=self.net.chats[0]['lastMessage']) for cid in bad]
        adapter = self.wa()
        convs = adapter.list_conversations(NOW)
        self.assertEqual(len(convs), 2)
        self.assertTrue(CHAT_ID.fullmatch('Abc_1.2-3@lid'))
        self.assertTrue(all(CHAT_ID.fullmatch(decode_key(k, 'conversation')[-1]) for k in convs))
        count = len(self.net.calls)
        with self.assertRaises(ChannelUnavailable):
            adapter.messages(make_key('conversation', 'whatsapp', 'fake', bad[0]))
        self.assertEqual(len(self.net.calls), count)

    def test_whatsapp_cache_ttl_and_failures_are_cached(self):
        seed_config(self.config)
        adapter = self.wa()
        first = adapter.list_conversations(NOW)
        key = next(iter(first))
        adapter.messages(key)
        self.net.tick = 14.99
        adapter.messages(key)
        self.assertEqual(len(self.net.calls), 3)
        self.net.tick = 15
        adapter.messages(key)
        self.assertEqual(len(self.net.calls), 4)
        self.net.tick = 29.99
        self.assertIs(first, adapter.list_conversations(NOW + 30))
        self.assertEqual(len(self.net.calls), 4)
        self.net.tick = 30
        adapter.list_conversations(NOW + 30)
        self.assertEqual(len(self.net.calls), 6)
        self.net.tick = 60
        self.net.error = TimeoutError(SECRET)
        self.assertFalse(adapter.health(NOW)['ready'])
        count = len(self.net.calls)
        self.net.tick = 61
        self.assertFalse(adapter.health(NOW)['ready'])
        self.assertEqual(len(self.net.calls), count)
        self.net.tick = 90
        self.net.error = None
        self.assertTrue(adapter.health(NOW)['ready'])

    def test_whatsapp_placeholder_name_fallback_and_empty_chat_count(self):
        seed_config(self.config)
        self.net.chats[0]['name'] = None
        self.net.chats[1]['lastMessage']['media'] = {'mimetype': 'video/mp4', 'url': '/fictional/private/media'}
        self.net.chats.append(dict(id='99999@lid', name=None, lastMessage=None))
        adapter = self.wa()
        convs = adapter.list_conversations(NOW)
        self.assertEqual(len(convs), 2)
        self.assertEqual(adapter.health(NOW)['chats_total'], 3)
        self.assertIn('WhatsApp ·1111', {c['buyer_name'] for c in convs.values()})
        self.assertIn('[视频]', {m['text'] for c in convs.values() for m in c['messages']})
        self.assertNotIn('/fictional/private', json.dumps(convs))

    def test_whatsapp_missing_config_fields_and_nonlocal_target_never_fetch(self):
        for value in ({}, {'session': 'fake', 'waha_api_key': SECRET},
                      dict(session='fake', waha_api_key=SECRET, waha_url='http://example.invalid')):
            self.config.write_text(json.dumps(value))
            self.config.chmod(0o640)
            self.assertFalse(self.wa().health(NOW)['ready'])
        self.assertEqual(self.net.calls, [])

    def test_private_config_permissions_owner_group_symlink_and_missing(self):
        seed_config(self.config)
        for field, value in [('st_uid', 1), ('st_gid', 732), ('st_mode', 0o100644), ('st_mode', 0o100600)]:
            def invalid(path, field=field, value=value):
                info = fake_stat(path)
                setattr(info, field, value)
                return info
            with self.subTest(field=field, value=value):
                adapter = WhatsAppAdapter(self.config, self.net.fetch, stat_fn=invalid, group_gid=731)
                self.assertFalse(adapter.health(NOW)['ready'])
        link = self.root / 'symlink.json'
        link.symlink_to(self.config)
        self.assertFalse(WhatsAppAdapter(link, self.net.fetch, stat_fn=fake_stat, group_gid=731).health(NOW)['ready'])
        self.config.unlink()
        self.assertFalse(self.wa().health(NOW)['ready'])
        self.assertEqual(self.net.calls, [])

    def test_config_change_revokes_cached_access_and_recovers(self):
        seed_config(self.config)
        adapter = self.wa()
        adapter.list_conversations(NOW)
        self.config.chmod(0o644)
        self.assertFalse(adapter.health(NOW)['ready'])
        self.assertEqual(adapter.list_conversations(NOW), {})
        seed_config(self.config)
        self.assertTrue(adapter.health(NOW)['ready'])

    def test_whatsapp_401_timeout_secrets_not_in_results_logs_or_errors(self):
        seed_config(self.config)
        seed_ali(self.ali)
        seed_wx(self.wx)
        output = io.StringIO()
        handler = logging.StreamHandler(output)
        logging.getLogger().addHandler(handler)
        self.addCleanup(logging.getLogger().removeHandler, handler)
        for error in (HTTPError('http://fictional.invalid/' + SECRET, 401, SECRET, {}, None), TimeoutError(SECRET)):
            self.net.error = error
            adapter = self.wa()
            hub = UnifiedInbox([AlibabaAdapter(self.ali), WechatAdapter(self.wx), adapter])
            result = hub.summary(NOW)
            self.assertEqual(len(hub.read(NOW)['conversations']), 2)
            self.assertFalse(adapter.health(NOW)['ready'])
            self.assertTrue(result['ready'])
            self.assertNotIn(SECRET, json.dumps([result, adapter.health(NOW), hub.read(NOW)]))
        self.assertNotIn(SECRET, output.getvalue())

    def test_whatsapp_history_failure_invalidates_channel_only(self):
        seed_config(self.config)
        adapter = self.wa()
        key = next(iter(adapter.list_conversations(NOW)))
        self.net.error = TimeoutError(SECRET)
        with self.assertRaisesRegex(ChannelUnavailable, '^渠道暂时无法读取$') as error:
            adapter.messages(key)
        self.assertNotIn(SECRET, str(error.exception))
        self.assertFalse(adapter.health(NOW)['ready'])
        self.assertEqual(adapter.list_conversations(NOW), {})

    def test_loopback_url_and_redirect_policy_without_network(self):
        for url in ('https://localhost', 'http://example.invalid', 'http://127.0.0.1.evil',
                    'http://localhost@evil', 'http://user:secret@localhost', 'http://[::1]', 'http://localhost/#fragment'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                local_url(url)
        for url in ('http://localhost:3000', 'http://127.0.0.1:3000'):
            self.assertEqual(local_url(url), url)
        with self.assertRaises(ChannelUnavailable):
            NoRedirect().redirect_request(None, None, 302, '', {}, 'http://example.invalid')

    def test_website_reserved_never_creates_files(self):
        path = self.root / 'missing' / 'website.sqlite3'
        self.assertFalse(WebsiteAdapter(path).health(NOW)['ready'])
        self.assertFalse(path.parent.exists())
        self.assertEqual(WebsiteAdapter(self.root).list_conversations(NOW), {})

    def test_shared_waiting_window_boundaries_unknown_closing_and_dormant(self):
        self.assertEqual(WAITING_WINDOW_DAYS, 30)
        for age, waiting, dormant in [(30 * 86400, True, False), (30 * 86400 + .1, False, True), (10, True, False)]:
            rows = [dict(role='buyer', text='Fictional question?', sent_at=NOW - age),
                    dict(role='unknown', text='Observation', sent_at=NOW)]
            state, _ = waiting_state(rows, NOW)
            self.assertEqual((state['waiting_reply'], state['dormant']), (waiting, dormant))
        for closing in ('好的', '谢谢！', '收到', '👍', '嗯', 'thank you'):
            self.assertFalse(waiting_state([dict(role='buyer', text=closing, sent_at=NOW)], NOW)[0]['waiting_reply'])
        self.assertFalse(waiting_state([dict(role='seller', text='Question?', sent_at=NOW - 40 * 86400)], NOW)[0]['dormant'])

    def test_waiting_projection_refreshes_without_source_changes(self):
        seed_ali(self.ali, [ali_message(age=30 * 86400)])
        adapter = AlibabaAdapter(self.ali)
        first = next(iter(adapter.list_conversations(NOW).values()))
        self.assertTrue(first['waiting_reply'])
        later = next(iter(adapter.list_conversations(NOW + 1).values()))
        self.assertTrue(later['dormant'])
        self.assertFalse(later['waiting_reply'])

    def test_all_three_channels_use_same_thirty_day_window(self):
        seed_ali(self.ali, [ali_message(age=31 * 86400)])
        seed_wx(self.wx, [wx_message(age=31 * 86400)])
        seed_config(self.config)
        self.net.chats = [dict(id='15550001111@c.us', name='Fictional old customer', lastMessage=dict(
            body='Fictional old question?', fromMe=False, timestamp=NOW - 31 * 86400))]
        hub = UnifiedInbox([AlibabaAdapter(self.ali), WechatAdapter(self.wx), self.wa()])
        summary = hub.summary(NOW)
        self.assertEqual((summary['waiting_total'], summary['dormant_total']), (0, 3))

    def test_merged_cache_reuses_aggregation_and_updates_with_source_keys(self):
        seed_ali(self.ali)
        seed_wx(self.wx)
        hub = UnifiedInbox([AlibabaAdapter(self.ali), WechatAdapter(self.wx)])
        from console.channels import aggregate_customers
        with patch('console.channels.aggregate_customers', wraps=aggregate_customers) as aggregate:
            first = hub.read(NOW)
            self.assertIs(first, hub.read(NOW + 10))
            self.assertEqual(aggregate.call_count, 1)
            seed_ali(self.ali, [ali_message(cid='new', mid='new')])
            self.assertEqual(len(hub.read(NOW + 11)['conversations']), 3)
            self.assertEqual(aggregate.call_count, 2)

    def test_cold_twenty_thousand_fictional_rows_under_five_seconds(self):
        seed_ali(self.ali, [ali_message(cid='c' + str(i % 100), mid='m' + str(i), age=i) for i in range(10000)])
        seed_wx(self.wx, [wx_message(group='fictional-' + str(i % 100) + '@chatroom', mid='w' + str(i), age=i) for i in range(10000)])
        started = time.perf_counter()
        hub = UnifiedInbox([AlibabaAdapter(self.ali), WechatAdapter(self.wx)])
        snapshot = hub.read(NOW)
        elapsed = time.perf_counter() - started
        self.assertEqual(sum(c['n_messages'] for c in snapshot['conversations'].values()), 20000)
        self.assertLess(elapsed, 5)


class RealConfigShapeTests(unittest.TestCase):
    """The bridge's private config names the WAHA key `waha_api_key` and WAHA expects it in X-Api-Key.
    A first version assumed a field called `authorization`; this pins the real shape."""

    def test_adapter_requires_waha_api_key_and_sends_x_api_key(self):
        import channel_fixtures as fx
        source = open(fx.__file__, encoding='utf8').read()
        self.assertIn("waha_api_key=SECRET", source)
        self.assertIn("{'X-Api-Key': SECRET}", source)


class UnclassifiedGroupTests(unittest.TestCase):
    def test_unclassified_wechat_groups_never_count_as_waiting(self):
        from console.channels import UnifiedInbox

        class Fake:
            name, label, ready, cache_key = 'wechat', '微信', True, 1

            def __init__(self, role):
                self.role = role

            def list_conversations(self, now):
                return {'k': dict(key='k', channel='wechat', account='微信', buyer_name='虚构群', country='',
                                  counterpart_role=self.role, waiting_reply=True, waiting_hours=3.0, n_messages=1,
                                  first_seen=now, last_seen=now, source_url=None, dormant=False,
                                  messages=(dict(role='buyer', text='虚构', sent_at=now),))}

        for role, expected in (('unclassified', False), ('customer', True), ('supplier', True)):
            with self.subTest(role=role):
                inbox = UnifiedInbox([Fake(role)])
                conv = inbox.read(1000.0)['conversations']['k']
                self.assertEqual(conv['waiting_reply'], expected)


class Base64BodyTests(unittest.TestCase):
    def test_raw_base64_body_is_shown_as_placeholder_not_text(self):
        from console.channels.whatsapp import WhatsAppAdapter
        adapter = WhatsAppAdapter(config_path='/nonexistent')
        blob = '/9j/4AAQSkZJRgABAgAAAQABAAD/' + 'A' * 300
        for raw, expected in ((dict(body=blob, hasMedia=True, timestamp=1000, media={}), None),
                              (dict(body=blob, hasMedia=False, timestamp=1000), '[附件]')):
            message = adapter._message(raw)
            self.assertNotIn('A' * 50, message['text'])
            if expected:
                self.assertEqual(message['text'], expected)
        normal = adapter._message(dict(body='Hello, how much is it?', timestamp=1000))
        self.assertEqual(normal['text'], 'Hello, how much is it?')
