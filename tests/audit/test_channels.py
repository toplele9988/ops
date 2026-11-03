"""Fictional local databases only; these checks never contact a service."""
from datetime import datetime, timezone
import hashlib
import json
import sqlite3
import time
from types import SimpleNamespace

import pytest

from console.audit import alibaba, whatsapp
from console.audit.common import query_budget

NOW = datetime(2030, 1, 20, tzinfo=timezone.utc).timestamp()
DAY = 86400


def stamp(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat()


def write(path, statement, values=()):
    with sqlite3.connect(path) as db:
        db.execute(statement, values)


def incoming(ctx, ident='m1', **values):
    payload = dict(account='fictional-account', conversation_id='fictional-conversation',
        buyer_name='Fictional Buyer', role='buyer', content='fictional message', sent_at=stamp(NOW - 60), history=False)
    payload.update(values.pop('payload', {}))
    received = values.pop('received', NOW - 60)
    payload.update(values)
    write(ctx.inbound_path, 'INSERT INTO inbound VALUES(?,?,?)', (ident, json.dumps(payload), received))
    ctx.memo.clear()


def heartbeat(ctx, at=NOW - 60, version='0.3.5.4', account='fictional-account'):
    write(ctx.probe_path, 'INSERT OR REPLACE INTO probe_state VALUES(?,?,?)',
          (account, json.dumps(dict(version=version, state='running', token='fake-probe-secret')), at))


def session(ctx, status='WORKING', at=NOW - 60, ident='status1'):
    write(ctx.whatsapp_path, 'INSERT INTO events VALUES(?,?,?,?)',
          (ident, 'session.status', json.dumps(dict(payload=dict(status=status), cookie='fake-event-cookie')), at))


def wa_message(ctx, ident='wa1', received=NOW - 60, direction='inbound', media=0):
    write(ctx.whatsapp_path, 'INSERT INTO messages VALUES(?,?,?,?,?)',
          (ident, direction, received, media, 'fictional message never selected'))


@pytest.fixture
def ctx(tmp_path):
    context = SimpleNamespace(now=NOW, memo={}, session_status=None,
        inbound_path=tmp_path / 'inbound.sqlite3', probe_path=tmp_path / 'probe.sqlite3',
        whatsapp_path=tmp_path / 'whatsapp.sqlite3', whatsapp_config_path=tmp_path / 'whatsapp.json')
    write(context.inbound_path, 'CREATE TABLE inbound(id TEXT PRIMARY KEY,payload TEXT NOT NULL,created_at REAL NOT NULL)')
    write(context.probe_path, 'CREATE TABLE probe_state(account TEXT PRIMARY KEY,snapshot_json TEXT NOT NULL,received_at REAL NOT NULL)')
    with sqlite3.connect(context.whatsapp_path) as db:
        db.executescript('''
        CREATE TABLE events(event_key TEXT PRIMARY KEY,event_type TEXT NOT NULL,raw_json TEXT NOT NULL,received REAL NOT NULL);
        CREATE TABLE messages(message_key TEXT PRIMARY KEY,direction TEXT NOT NULL,received REAL NOT NULL,has_media INTEGER NOT NULL,content TEXT NOT NULL);
        CREATE TABLE media_jobs(message_key TEXT PRIMARY KEY,status TEXT NOT NULL,metadata_json TEXT NOT NULL,updated REAL NOT NULL);
        ''')
    context.whatsapp_config_path.write_text(json.dumps(dict(auto_send=False, waha_api_key='FAKE-WA-SECRET-123',
        token='FAKE-TOKEN-123', cookie='FAKE-COOKIE-123', nested=dict(secret='FAKE-NESTED-123'))))
    return context


def test_alibaba_account_coverage_trigger_and_clear(ctx):
    incoming(ctx)
    result = alibaba.check_accounts(ctx)
    assert (result['level'], result['count'], result['accounts_with_data']) == ('low', 5, 1)
    assert '另外 5 个子账号计划在自动发品和 AI 运营跑通后启用' in result['meaning']
    for index in range(1, 6):
        incoming(ctx, str(index), account='fictional-' + str(index))
    result = alibaba.check_accounts(ctx)
    assert result['level'] == 'ok' and result['count'] == 0
    assert len(result['samples']) == 5


def test_alibaba_main_without_data_is_high_but_planned_roles_are_not(ctx):
    result = alibaba.check_accounts(ctx)
    assert result['level'] == 'high'
    assert '主账号没有有效消息' in result['meaning']
    ctx.plan = {'whatsapp': 'not_started', 'alibaba_accounts': {'main': 'planned', 'others': 'planned'},
                'wechat': 'operating'}
    result = alibaba.check_accounts(ctx)
    assert result['level'] == 'low' and result['count'] == 6
    assert '目前 0 个有消息' in result['meaning']
    ctx.plan['alibaba_accounts']['main'] = 'not_started'
    assert alibaba.check_accounts(ctx)['level'] == 'low'


def test_alibaba_planned_account_probe_is_ignored_but_main_stale_probe_is_high(ctx):
    incoming(ctx)
    heartbeat(ctx, at=NOW - 60, account='fictional-account')
    heartbeat(ctx, at=NOW - 8 * 3600, account='fictional-planned-child')
    result = alibaba.check_probe(ctx)
    assert result['level'] == 'ok' and result['count'] == 0
    heartbeat(ctx, at=NOW - 8 * 3600, account='fictional-account')
    result = alibaba.check_probe(ctx)
    assert result['level'] == 'high' and result['count'] == 1
    assert '主账号心跳超过 6 小时' in result['meaning']


def test_alibaba_probe_not_operating_is_planned_and_does_not_alarm(ctx):
    for status in ('planned', 'not_started'):
        ctx.plan = {'whatsapp': 'not_started', 'alibaba_accounts': {'main': status, 'others': 'planned'},
                    'wechat': 'operating'}
        result = alibaba.check_probe(ctx)
        assert result['level'] == 'ok' and result['count'] == 0
        assert '尚未开始运营' in result['meaning']


@pytest.mark.parametrize('at,level', [(NOW - 21600, 'ok'), (NOW - 21601, 'high'), (NOW + 1, 'high')])
def test_alibaba_probe_threshold(ctx, at, level):
    heartbeat(ctx, at)
    result = alibaba.check_probe(ctx)
    assert result['level'] == level
    assert result['script_version'] == '0.3.5.4'
    assert 'fake-probe-secret' not in json.dumps(result)


def test_alibaba_empty_probe_and_invalid_version(ctx):
    assert alibaba.check_probe(ctx)['level'] == 'high'
    heartbeat(ctx, version='https://fictional.invalid?token=SECRET')
    result = alibaba.check_probe(ctx)
    assert result['script_version'] is None
    assert 'SECRET' not in json.dumps(result)


@pytest.mark.parametrize('age,level', [(7 * DAY, 'ok'), (7 * DAY + 1, 'medium')])
def test_alibaba_latest_account_message(ctx, age, level):
    incoming(ctx, sent_at=stamp(NOW - age))
    result = alibaba.check_latest_messages(ctx)
    assert result['level'] == level
    assert result['accounts'][0]['last_message_at'] == NOW - age


def test_alibaba_no_messages_is_not_healthy(ctx):
    assert alibaba.check_latest_messages(ctx)['level'] == 'medium'


@pytest.mark.parametrize('body', ['[图片]', '[文件] fictional.pdf', '[附件]', '[视频]', '[语音]', 'fictional [图片]'])
def test_alibaba_attachment_placeholder(ctx, body):
    incoming(ctx, content=body)
    result = alibaba.check_attachments(ctx)
    assert result['count'] == 1 and result['level'] == 'medium'
    assert body not in json.dumps(result)


def test_alibaba_no_attachment_and_invalid_json(ctx):
    incoming(ctx)
    write(ctx.inbound_path, 'INSERT INTO inbound VALUES(?,?,?)', ('bad-json', '{token=secret', NOW))
    result = alibaba.check_attachments(ctx)
    assert result['count'] == 0 and result['level'] == 'ok'
    assert alibaba.check_accounts(ctx)['accounts_with_data'] == 1


def test_alibaba_gap_anomaly_and_new_message_clear(ctx):
    for index in range(4):
        incoming(ctx, str(index), received=NOW - 2 * DAY + index * 3600)
    result = alibaba.check_buyer_gap(ctx)
    assert result['level'] == 'high' and result['count'] == 1
    assert result['baseline_average_seconds'] == 3600
    assert result['threshold_seconds'] == 21600
    incoming(ctx, 'history', received=NOW - 10, history=True)
    assert alibaba.check_buyer_gap(ctx)['level'] == 'high'
    incoming(ctx, 'seller', received=NOW - 10, role='seller')
    assert alibaba.check_buyer_gap(ctx)['level'] == 'high'
    incoming(ctx, 'live', received=NOW - 10)
    assert alibaba.check_buyer_gap(ctx)['level'] == 'ok'


def test_alibaba_insufficient_gap_baseline_not_false_alarm(ctx):
    incoming(ctx, received=NOW - 10 * DAY)
    result = alibaba.check_buyer_gap(ctx)
    assert result['level'] == 'ok' and not result['baseline_sufficient']
    assert result['threshold_seconds'] is None


def test_alibaba_duplicate_buyer_and_rename(ctx):
    incoming(ctx, 'one', conversation_id='first')
    incoming(ctx, 'two', conversation_id='second', buyer_name=' fictional buyer ')
    result = alibaba.check_duplicate_buyers(ctx)
    assert result['count'] == 1 and result['extra_conversations'] == 1
    assert 'fictional buyer' not in json.dumps(result).lower()
    incoming(ctx, 'three', conversation_id='second', buyer_name='New Fictional Buyer', sent_at=stamp(NOW))
    assert alibaba.check_duplicate_buyers(ctx)['count'] == 0


def test_alibaba_same_name_across_accounts_not_duplicate(ctx):
    incoming(ctx, 'one')
    incoming(ctx, 'two', account='another-fictional-account')
    assert alibaba.check_duplicate_buyers(ctx)['level'] == 'ok'


@pytest.mark.parametrize('status,expected', [('WORKING', 'ok'), ('STOPPED', 'high'), ('SCAN_QR_CODE', 'high'),
    ('FAILED', 'high'), ('token=SECRET', 'high')])
def test_whatsapp_session_states(ctx, status, expected):
    session(ctx, status)
    result = whatsapp.check_session(ctx)
    assert result['level'] == ('ok' if expected == 'ok' else 'medium')
    if status != 'WORKING':
        assert '还没登录' in result['meaning']
        assert '准备开始运营时' in result['remedy']
    assert '系统后台 → 渠道接入 → WhatsApp 扫码登录' in result['remedy']
    assert 'SECRET' not in json.dumps(result)


def test_whatsapp_session_stale_and_missing(ctx):
    assert whatsapp.check_session(ctx)['level'] == 'medium'
    session(ctx, at=NOW - 21601)
    assert whatsapp.check_session(ctx)['level'] == 'medium'
    session(ctx, at=NOW - 21600, ident='new-status')
    assert whatsapp.check_session(ctx)['level'] == 'ok'
    ctx.session_status = lambda: 'STOPPED'
    assert whatsapp.check_session(ctx)['level'] == 'medium'
    ctx.session_status = lambda: dict(status='WORKING', received_at=NOW)
    assert whatsapp.check_session(ctx)['level'] == 'ok'


def test_whatsapp_operating_session_problem_remains_high(ctx):
    ctx.plan = {'whatsapp': 'operating', 'alibaba_accounts': {'main': 'operating', 'others': 'planned'},
                'wechat': 'operating'}
    ctx.session_status = lambda: 'STOPPED'
    result = whatsapp.check_session(ctx)
    assert result['level'] == 'high'
    assert '收件可能中断' in result['meaning']


def test_whatsapp_planned_session_is_medium_until_operations_start(ctx):
    ctx.plan = {'whatsapp': 'planned', 'alibaba_accounts': {'main': 'operating', 'others': 'planned'},
                'wechat': 'operating'}
    ctx.session_status = lambda: 'STOPPED'
    result = whatsapp.check_session(ctx)
    assert result['level'] == 'medium'
    assert '准备开始运营时' in result['remedy']


def test_whatsapp_recent_message_only_inbound(ctx):
    result = whatsapp.check_latest_message(ctx)
    assert result['level'] == 'ok'
    assert result['count'] == 0 and result['has_ever_received'] is False
    assert '尚未开始运营，暂无数据' in result['meaning']
    wa_message(ctx, 'old', received=NOW - 7 * DAY - 1)
    wa_message(ctx, 'out', received=NOW, direction='outbound')
    result = whatsapp.check_latest_message(ctx)
    assert result['level'] == 'medium' and result['has_ever_received'] is True
    wa_message(ctx, 'new', received=NOW - 7 * DAY)
    result = whatsapp.check_latest_message(ctx)
    assert result['level'] == 'ok' and result['last_received_at'] == NOW - 7 * DAY


def test_whatsapp_operating_without_recent_messages_still_reports(ctx):
    ctx.plan = {'whatsapp': 'operating', 'alibaba_accounts': {'main': 'operating', 'others': 'planned'},
                'wechat': 'operating'}
    assert whatsapp.check_latest_message(ctx)['level'] == 'medium'
    wa_message(ctx, 'old-operating-message', received=NOW - 8 * DAY)
    result = whatsapp.check_latest_message(ctx)
    assert result['level'] == 'medium' and result['has_ever_received'] is True


def test_whatsapp_planned_with_no_received_history_is_ok(ctx):
    ctx.plan = {'whatsapp': 'planned', 'alibaba_accounts': {'main': 'operating', 'others': 'planned'},
                'wechat': 'operating'}
    result = whatsapp.check_latest_message(ctx)
    assert result['level'] == 'ok' and result['has_ever_received'] is False
    assert '尚未开始运营，暂无数据' in result['meaning']


def test_whatsapp_media_download_pending_vs_archived(ctx):
    wa_message(ctx)
    assert whatsapp.check_media(ctx)['level'] == 'ok'
    for index, status in enumerate(('pending_media_url', 'deferred', 'archived')):
        ident = 'media-' + str(index)
        wa_message(ctx, ident, media=1)
        write(ctx.whatsapp_path, 'INSERT INTO media_jobs VALUES(?,?,?,?)',
              (ident, status, '{"token":"FAKE-MEDIA-SECRET"}', NOW))
    wa_message(ctx, 'missing-job', media=1)
    result = whatsapp.check_media(ctx)
    assert result['count'] == 3 and result['level'] == 'medium'
    assert 'FAKE-MEDIA-SECRET' not in json.dumps(result)
    write(ctx.whatsapp_path, "UPDATE media_jobs SET status='archived'")
    write(ctx.whatsapp_path, 'INSERT INTO media_jobs VALUES(?,?,?,?)', ('missing-job', 'archived', '{}', NOW))
    assert whatsapp.check_media(ctx)['level'] == 'ok'


def test_whatsapp_media_without_job_table_counts_unprocessed(ctx):
    write(ctx.whatsapp_path, 'DROP TABLE media_jobs')
    wa_message(ctx, media=1)
    assert whatsapp.check_media(ctx)['count'] == 1


@pytest.mark.parametrize('received', ['token=FAKE-RECEIVED-SECRET', float('inf')])
def test_whatsapp_media_samples_allow_only_finite_timestamps(ctx, received):
    wa_message(ctx, media=1, received=received)
    result = whatsapp.check_media(ctx)
    assert result['count'] == 1
    assert result['samples'][0]['received_at'] is None
    assert 'FAKE-RECEIVED-SECRET' not in json.dumps(result, allow_nan=False)


def test_whatsapp_auto_send_boolean_and_no_config_secret_leak(ctx):
    result = whatsapp.check_auto_send(ctx)
    assert result['level'] == 'ok' and result['auto_send'] is False
    assert 'FAKE-' not in json.dumps(result)
    ctx.whatsapp_config_path.write_text(json.dumps(dict(auto_send=True, waha_api_key='FAKE-ON-SECRET')))
    result = whatsapp.check_auto_send(ctx)
    assert result['level'] == 'high' and result['count'] == 1
    assert 'FAKE-ON-SECRET' not in json.dumps(result)


@pytest.mark.parametrize('value', ['false', 0, None, {}, []])
def test_whatsapp_auto_send_rejects_non_boolean(ctx, value):
    ctx.whatsapp_config_path.write_text(json.dumps(dict(auto_send=value)))
    with pytest.raises(ValueError):
        whatsapp.check_auto_send(ctx)


def test_whatsapp_config_symlink_rejected(ctx, tmp_path):
    alias = tmp_path / 'alias.json'
    alias.symlink_to(ctx.whatsapp_config_path)
    ctx.whatsapp_config_path = alias
    with pytest.raises(OSError):
        whatsapp.check_auto_send(ctx)


def test_channel_checks_read_only_and_samples_bounded(ctx):
    for index in range(8):
        incoming(ctx, 'message-' + str(index), content='[图片]', conversation_id='conv-' + str(index))
        wa_message(ctx, 'media-' + str(index), media=1)
    heartbeat(ctx)
    session(ctx)
    paths = (ctx.inbound_path, ctx.probe_path, ctx.whatsapp_path, ctx.whatsapp_config_path)
    before = [hashlib.sha256(path.read_bytes()).digest() for path in paths]
    for _, _, check in alibaba.CHECKS + whatsapp.CHECKS:
        result = check(ctx)
        assert len(result['samples']) <= 5
        rendered = json.dumps(result)
        for secret in ('fictional-account', 'fictional-conversation', 'Fictional Buyer', 'FAKE-', 'fictional message', 'fake-probe-secret'):
            assert secret not in rendered
    assert before == [hashlib.sha256(path.read_bytes()).digest() for path in paths]


@pytest.mark.parametrize('module,path', [(alibaba, 'inbound_path'), (whatsapp, 'whatsapp_path')])
def test_missing_source_raises_for_runner_isolation(ctx, tmp_path, module, path):
    setattr(ctx, path, tmp_path / 'does-not-exist.sqlite3')
    check = module.check_accounts if module is alibaba else module.check_latest_message
    with pytest.raises(FileNotFoundError):
        check(ctx)
    assert not getattr(ctx, path).exists()


def test_alibaba_100k_rows_aggregate_once_within_three_seconds(ctx):
    payload = dict(account='fictional-account', buyer_name='Fictional Buyer', role='buyer',
                   content='fictional', sent_at=stamp(NOW - 60), history=False)
    with sqlite3.connect(ctx.inbound_path) as db:
        db.executemany('INSERT INTO inbound VALUES(?,?,?)', ((str(index), json.dumps(dict(payload,
            conversation_id='fictional-' + str(index % 1000))), NOW - 1000 - index) for index in range(100000)))
    heartbeat(ctx)
    start = time.monotonic()
    with query_budget(2.8):
        results = [check(ctx) for _, _, check in alibaba.CHECKS]
    assert time.monotonic() - start < 3
    assert results[0]['accounts_with_data'] == 1
    assert results[-1]['extra_conversations'] == 999
    assert len(ctx.memo) == 1


def test_bad_probe_and_bad_config_are_isolated_and_errors_hide_secrets(ctx, tmp_path):
    from console.audit.service import Audit
    incoming(ctx)
    session(ctx)
    wa_message(ctx)
    ctx.probe_path.write_text('FAKE-BROKEN-DB-SECRET')
    ctx.whatsapp_config_path.write_text('{"auto_send":false,"token":"FAKE-JSON-SECRET"')
    service = Audit(tmp_path / 'archive.sqlite3', inbound_path=ctx.inbound_path,
        probe_path=ctx.probe_path, whatsapp_path=ctx.whatsapp_path,
        whatsapp_config_path=ctx.whatsapp_config_path, clock=lambda: NOW,
        checks=dict(alibaba=alibaba.CHECKS, whatsapp=whatsapp.CHECKS))
    result = service.scan()
    failed = {item['key'] for item in result['items'] if item.get('read_failed')}
    assert failed == {'alibaba.probe', 'whatsapp.auto_send'}
    assert result['total'] == 10
    assert 'FAKE-' not in json.dumps(result)
