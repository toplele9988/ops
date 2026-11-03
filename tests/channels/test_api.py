"""Authenticated adapter APIs with synthetic databases, credentials and GET transport."""
import asyncio
import json
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import importlib.util
import unittest
from unittest.mock import Mock, patch

from console.channels import build_inbox
from console.inbox import ai, drafts
from console.channels.probe import CHECKLIST, diagnose, read_probe
from channel_fixtures import (NOW, SECRET, FakeWAHA, ali_message, fake_stat, request,
                              seed_ali, seed_config, seed_probe, seed_wx, wx_message)

try:
    from console import v3_inbox_api as inbox_api, v3_customers_api as customers_api, v3_channels_api as channels_api
except ModuleNotFoundError as exc:
    if exc.name != 'starlette' and not (exc.name or '').startswith('starlette.'):
        raise
    inbox_api = customers_api = channels_api = None


@unittest.skipIf(inbox_api is None, 'starlette not installed')
class UnifiedApiTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.ali, self.wx, self.config, self.probe, self.draft_path = (
            self.root / n for n in ('ali.sqlite3', 'wx.sqlite3', 'config.json', 'probe.sqlite3', 'drafts.sqlite3'))
        seed_ali(self.ali, [ali_message(), ali_message(cid='old', mid='old', age=31 * 86400)])
        seed_wx(self.wx, [wx_message(group='fictional-client@chatroom', mid='client'),
            wx_message(group='fictional-supplier@chatroom', mid='supplier'),
            wx_message(group='fictional-other@chatroom', mid='other'),
            wx_message(group='fictional-study@chatroom', mid='study')],
            dict(client='customer', supplier='supplier', study='learning'))
        with sqlite3.connect(self.wx) as db:
            db.execute('INSERT INTO onboarding_candidates VALUES(?,?)', ('fictional-client@chatroom', '虚构客户群'))
        seed_config(self.config)
        self.net = FakeWAHA()
        self.hub = build_inbox(self.ali, wechat_path=self.wx, whatsapp_config_path=self.config,
                              fetch=self.net.fetch, clock=lambda: self.net.tick, stat_fn=fake_stat, group_gid=731)
        self.app = SimpleNamespace(error_response=lambda req, code: code)
        self.runner = Mock(return_value=json.dumps(dict(draft='虚构草稿【待确认：规格】', language='中文', needs_confirmation=['规格'])) )
        routes = (inbox_api.build(self.app, self.ali, self.draft_path, self.runner, inbox=self.hub)
                  + customers_api.build(self.app, self.ali, inbox=self.hub)
                  + channels_api.build(self.app, self.ali, inbox=self.hub, probe_path=self.probe))
        self.handlers = {r.name: r.endpoint for r in routes}
        for replacement in (patch('time.time', return_value=NOW),
                            patch.object(drafts, 'LIMITER', drafts.DraftLimiter()),
                            patch('socket.socket.connect', side_effect=AssertionError('network forbidden'))):
            replacement.start()
            self.addCleanup(replacement.stop)

    def call(self, name='inbox_conversations', **options):
        return asyncio.run(self.handlers[name](request(**options)))

    def data(self, name='inbox_conversations', **options):
        result = self.call(name, **options)
        self.assertEqual(result.status_code, 200, result.body.decode())
        self.assertEqual(result.headers['cache-control'], 'no-store')
        return json.loads(result.body)

    def find(self, channel, role=None):
        return next(c for c in self.data(query={'role': 'all'})['items']
                    if c['channel'] == channel and (role is None or c['counterpart_role'] == role))

    def test_list_channel_role_filters_default_and_fields(self):
        default = self.data()
        self.assertEqual(default['total'], 6)
        self.assertNotIn('supplier', {c['counterpart_role'] for c in default['items']})
        self.assertEqual(self.data(query={'role': 'all'})['total'], 7)
        self.assertEqual(self.data(query={'role': 'supplier'})['total'], 1)
        self.assertEqual(self.data(query={'channel': 'wechat'})['total'], 2)
        self.assertEqual(self.data(query={'channel': 'whatsapp'})['total'], 2)
        self.assertEqual(self.data(query={'channel': 'alibaba'})['total'], 2)
        for item in default['items']:
            self.assertTrue({'key', 'channel', 'account', 'channel_label', 'counterpart_role', 'dormant',
                             'buyer_name', 'last', 'n_messages', 'waiting_reply', 'waiting_hours', 'has_draft'} <= item.keys())
            self.assertNotIn('15559998888', item['account'])
            self.assertNotIn('fictional-own', item['account'])
        self.assertEqual(self.data(query={'channel': 'wechat', 'role': 'supplier', 'state': 'waiting'})['total'], 1)

    def test_bad_role_and_repeated_role_rejected(self):
        from starlette.datastructures import QueryParams
        for query in ({'role': 'learning'}, {'role': 'Customer'}, QueryParams('role=all&role=supplier')):
            self.assertEqual(self.call(query=query).status_code, 400)

    def test_summary_preserves_account_stats_and_separates_supplier_dormant(self):
        summary = self.data('inbox_summary')
        self.assertEqual((summary['waiting_total'], summary['supplier_waiting'], summary['dormant_total']), (3, 1, 1))  # unclassified WeChat groups no longer count as waiting
        self.assertEqual(summary['new_24h'], 6)
        by_channel = {c['channel']: c for c in summary['by_channel']}
        self.assertEqual(set(by_channel), {'alibaba', 'wechat', 'whatsapp'})
        self.assertEqual([by_channel[c]['conversations'] for c in ('alibaba', 'wechat', 'whatsapp')], [2, 3, 2])
        self.assertEqual(by_channel['wechat']['waiting'], 1)
        self.assertTrue(all(c['ready'] for c in by_channel.values()))
        self.assertEqual(sum(c['conversations'] for c in summary['channels']), 7)

    def test_detail_sender_labels_copy_hint_and_send_disabled_for_all_channels(self):
        rows = self.data(query={'role': 'all'})['items']
        for row in rows:
            detail = self.data('inbox_detail', key=row['key'])
            conv = detail['conversation']
            self.assertFalse(conv['can_send'])
            self.assertIn('复制', conv['copy_hint'])
            self.assertEqual(conv['counterpart_role'], row['counterpart_role'])
            for message in detail['messages']:
                self.assertIn('sender_label', message)
                if row['channel'] == 'wechat':
                    self.assertTrue(message['sender_label'].startswith('成员 '))
                elif row['channel'] == 'alibaba':
                    self.assertIsNone(message['sender_label'])

    def test_supplier_and_whatsapp_drafts_use_role_wording_and_never_send(self):
        supplier = self.find('wechat', 'supplier')
        result = self.data('inbox_draft', key=supplier['key'])
        self.assertIn('draft', result)
        prompt = self.runner.call_args.args[0]
        self.assertIn('如果是微信供应商群，对方是供应商：写采购沟通，不是销售回复', prompt)
        self.assertIn('请起草采购/核对资料的跟进', prompt)
        self.assertIn('供应商：', prompt)
        customer = self.find('whatsapp')
        self.data('inbox_draft', key=customer['key'])
        prompt = self.runner.call_args.args[0]
        self.assertIn('请起草销售回复', prompt)
        self.assertIn('客户使用的语言', prompt)
        self.assertTrue(all(call[0] == 'GET' and 'send' not in call[1] for call in self.net.calls))
        self.assertEqual(self.data('inbox_summary')['draft_new'], 2)

    def test_customers_multichannel_names_and_group_exclusions(self):
        result = self.data('customers_list')
        self.assertEqual(result['total'], 4)
        self.assertEqual({c['channel'] for c in result['items']}, {'alibaba', 'wechat', 'whatsapp'})
        wx, = self.data('customers_list', query={'channel': 'wechat'})['items']
        self.assertEqual(wx['buyer_name'], '虚构客户群')
        self.assertEqual(self.data('customers_list', query={'channel': 'whatsapp'})['total'], 1)
        self.assertEqual(self.data('customers_detail', key=wx['key'])['n_conversations'], 1)

    def test_unreachable_whatsapp_does_not_hide_other_channels_or_echo_secrets(self):
        self.net.error = TimeoutError(SECRET)
        rows = self.data()
        self.assertEqual(rows['total'], 4)
        summary = self.data('inbox_summary')
        self.assertFalse(next(c for c in summary['by_channel'] if c['channel'] == 'whatsapp')['ready'])
        health = self.data('system_channels')
        self.assertEqual(len(health['channels']), 4)
        self.assertNotIn(SECRET, json.dumps([rows, summary, health]))
        self.assertNotIn('fictional-ignored', json.dumps(health))

    def test_health_four_channels_operational_fields_and_role_counts(self):
        result = self.data('system_channels')
        channels = {c['channel']: c for c in result['channels']}
        self.assertEqual(set(channels), {'alibaba', 'wechat', 'whatsapp', 'website'})
        for channel in channels.values():
            self.assertIn(channel['state'], ('ok', 'quiet', 'stale'))
            self.assertTrue(channel['summary'])
            self.assertIn('accounts', channel)
        self.assertEqual(channels['wechat']['groups_by_role'], dict(customer=1, supplier=1, unclassified=1, learning=1))
        self.assertEqual(channels['wechat']['messages_24h'], 4)
        self.assertEqual(channels['whatsapp']['accounts'][0]['name'], 'WhatsApp 主号')
        self.assertEqual(channels['whatsapp']['session_status'], 'WORKING')
        self.assertEqual(channels['whatsapp']['plan'], 'not_started')
        self.assertEqual(channels['wechat']['plan'], 'operating')
        self.assertFalse(channels['website']['ready'])
        self.assertEqual(channels['website']['label'], '独立站（预留）')
        self.assertTrue(any('6 个账号只有 1 个有数据' in n for n in result['notes']))
        self.assertTrue(any('主账号正常采集；另外 5 个子账号计划在自动发品和 AI 运营跑通后启用，目前没有数据是正常的' in n
                            for n in result['notes']))
        self.assertNotIn('其余账号的询盘不会出现在系统里', json.dumps(result, ensure_ascii=False))

    def test_health_nonworking_session_status(self):
        self.net.status = 'SCAN_QR_CODE'
        wa = next(c for c in self.data('system_channels')['channels'] if c['channel'] == 'whatsapp')
        self.assertFalse(wa['ready'])
        self.assertEqual(wa['session_status'], 'SCAN_QR_CODE')
        self.assertEqual(wa['summary'], '还没开始运营')
        self.assertEqual(wa['diagnosis'], '还没开始运营：准备开始时到 系统后台 → 渠道接入 扫码登录')
        self.assertEqual(wa['state'], 'quiet')
        self.assertEqual(wa['checklist'], [])
        self.assertNotIn('收件可能中断', json.dumps(wa, ensure_ascii=False))

    def test_logged_in_without_chats_waits_for_first_contacts(self):
        self.net.chats = []
        wa = next(c for c in self.data('system_channels')['channels'] if c['channel'] == 'whatsapp')
        self.assertTrue(wa['ready'])
        self.assertEqual(wa['summary'], '已登录，等待第一批联系人和会话')
        self.assertEqual(wa['diagnosis'], '已登录，等待第一批联系人和会话')
        self.assertEqual(wa['state'], 'quiet')
        self.assertNotIn('收件可能中断', json.dumps(wa, ensure_ascii=False))

    def make_ali_quiet(self):
        with sqlite3.connect(self.ali) as db:
            db.execute('DELETE FROM inbound')
        seed_ali(self.ali, [ali_message(age=13 * 3600)])

    def test_probe_interrupted_heartbeat_diagnosis_and_checklist(self):
        self.make_ali_quiet()
        seed_probe(self.probe, age=7 * 3600)
        before = self.probe.read_bytes(), self.probe.stat().st_mtime_ns
        ali = next(c for c in self.data('system_channels')['channels'] if c['channel'] == 'alibaba')
        self.assertEqual(ali['diagnosis'], '阿里已 13 小时没有收到消息，探针心跳也已中断，请到 Win10 上检查')
        self.assertEqual(ali['checklist'], CHECKLIST)
        self.assertEqual(ali['state'], 'stale')
        self.assertEqual(ali['probe']['script_version'], 'fictional-1.2')
        self.assertEqual(ali['probe']['status'], 'collecting')
        self.assertNotIn('fictional-private-error', json.dumps(ali))
        self.assertEqual((self.probe.read_bytes(), self.probe.stat().st_mtime_ns), before)

    def test_probe_healthy_heartbeat_quiet_messages(self):
        self.make_ali_quiet()
        seed_probe(self.probe)
        ali = next(c for c in self.data('system_channels')['channels'] if c['channel'] == 'alibaba')
        self.assertIsNone(ali['diagnosis'])
        self.assertEqual(ali['checklist'], [])
        self.assertEqual(ali['state'], 'quiet')
        self.assertIn('心跳正常', ali['summary'])

    def test_probe_absent_or_wrong_schema_is_null_without_creating_file(self):
        self.make_ali_quiet()
        ali = next(c for c in self.data('system_channels')['channels'] if c['channel'] == 'alibaba')
        self.assertIsNone(ali['probe'])
        self.assertIsNone(ali['diagnosis'])
        self.assertFalse(self.probe.exists())
        with sqlite3.connect(self.probe) as db:
            db.execute('CREATE TABLE unrelated(dummy TEXT)')
        self.assertIsNone(read_probe(self.probe))

    def test_stale_main_probe_is_a_warning_even_when_messages_are_recent(self):
        seed_probe(self.probe, age=7 * 3600)
        ali = next(c for c in self.data('system_channels')['channels'] if c['channel'] == 'alibaba')
        self.assertIn('主账号探针心跳已超过 6 小时', ali['diagnosis'])
        self.assertEqual(ali['state'], 'stale')
        self.assertEqual(ali['checklist'], CHECKLIST)

    def test_planned_child_heartbeat_does_not_mask_stale_main_probe(self):
        seed_probe(self.probe, age=7 * 3600, account='fictional-account')
        seed_probe(self.probe, age=60, account='fictional-planned-child')
        ali = next(c for c in self.data('system_channels')['channels'] if c['channel'] == 'alibaba')
        self.assertIn('主账号探针心跳已超过 6 小时', ali['diagnosis'])
        self.assertEqual(ali['state'], 'stale')

    def test_recheck_and_auth_remain_enforced_on_new_channel_reads_and_drafts(self):
        supplier = self.find('wechat', 'supplier')
        for name in self.handlers:
            self.assertEqual(self.call(name, key=supplier['key'], authenticated=False), 403)
            self.assertEqual(self.call(name, key=supplier['key'], recheck=False), 403)
        self.runner.assert_not_called()

    def test_whatsapp_detail_timeout_fixed_503_and_other_channels_survive(self):
        wa = self.find('whatsapp')
        self.net.error = TimeoutError(SECRET)
        response = self.call('inbox_detail', key=wa['key'])
        self.assertEqual(response.status_code, 503)
        self.assertNotIn(SECRET, response.body.decode())
        self.assertTrue(self.data('inbox_summary')['ready'])
        self.assertEqual(self.data(query={'channel': 'wechat'})['total'], 2)

    def test_build_keywords_share_sources_without_explicit_hub(self):
        routes = customers_api.build(self.app, self.ali, wechat_path=self.wx,
            whatsapp_config_path=self.config, fetch=self.net.fetch, clock=lambda: self.net.tick,
            stat_fn=fake_stat, group_gid=731)
        result = json.loads(asyncio.run(routes[0].endpoint(request())).body)
        self.assertEqual(result['total'], 4)


class PureHealthAndPromptTests(unittest.TestCase):
    def test_diagnosis_strict_six_hour_boundary(self):
        self.assertEqual(diagnose(NOW - 21600, {'last_heartbeat_at': NOW - 21601}, NOW), (None, []))
        self.assertEqual(diagnose(NOW - 21601, {'last_heartbeat_at': NOW - 21600}, NOW), (None, []))
        self.assertEqual(diagnose(NOW - 21601, None, NOW), (None, []))
        self.assertIsNotNone(diagnose(NOW - 21601, {'last_heartbeat_at': NOW - 21601}, NOW)[0])

    def test_customer_supplier_prompt_wording(self):
        messages = [dict(role='buyer', text='虚构问题', sent_at=NOW)]
        self.assertIn('买家：', ai.make_prompt(messages))
        self.assertIn('客户：', ai.make_prompt(messages, 'whatsapp', 'customer'))
        self.assertIn('供应商：', ai.make_prompt(messages, 'wechat', 'supplier'))


@unittest.skipIf(__import__('importlib').util.find_spec('starlette') is None, 'starlette not installed')
class WhatsAppDisconnectedTests(unittest.TestCase):
    def test_a_failed_session_is_explained_not_just_unavailable(self):
        from console.v3_channels_api import whatsapp_disconnected
        info = whatsapp_disconnected('FAILED')
        self.assertIn('FAILED', info['summary'])
        self.assertIn('重新扫码', info['diagnosis'])
        self.assertIn('需要重新扫码登录，去系统后台 → 渠道接入 → WhatsApp', info['diagnosis'])
        self.assertNotIn('还没接进后台', json.dumps(info, ensure_ascii=False))
        self.assertGreaterEqual(len(info['checklist']), 3)
        self.assertEqual(whatsapp_disconnected('WORKING'), {})
        self.assertEqual(whatsapp_disconnected(None), {})
