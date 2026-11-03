"""Full offline audit over fictional schemas already used by channel tests."""
import asyncio
from contextlib import closing
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import time
from types import SimpleNamespace

import pytest
from starlette.responses import JSONResponse

from console import v3_audit_api
from console.archive.store import Store
from console.audit.common import LEVELS, read_db
from console.audit.service import Audit


def _fixtures(name):
    # Unique import names avoid collisions with other suites' test modules.
    path = Path(__file__).with_name(name + '.py')
    spec = importlib.util.spec_from_file_location('audit_integration_' + name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CHANNEL = _fixtures('test_channels')
WECHAT = _fixtures('test_wechat')
NOW = WECHAT.NOW


@pytest.fixture
def all_sources(tmp_path, monkeypatch):
    channel = CHANNEL.ctx.__wrapped__(tmp_path)
    wx = WECHAT.wx.__wrapped__(tmp_path, monkeypatch)
    channel.now = NOW
    WECHAT.configure(wx)
    WECHAT.message(wx)
    WECHAT.sql(wx.discovery_path, 'INSERT INTO identities VALUES(?,?,?,?)',
               (WECHAT.GROUP, 'group', NOW - 86400, NOW))
    for index in range(6):
        CHANNEL.incoming(channel, ident='fictional-initial-' + str(index),
            account='fictional-account-' + str(index), conversation_id='fictional-chat-' + str(index),
            buyer_name='Fictional Buyer ' + str(index), received=NOW - 60, sent_at=CHANNEL.stamp(NOW - 60))
        CHANNEL.heartbeat(channel, at=NOW - 60, account='fictional-account-' + str(index))
    CHANNEL.session(channel, at=NOW - 60)
    CHANNEL.wa_message(channel, received=NOW - 60)
    archive = tmp_path / 'archive.sqlite3'
    # Initialize v6 before taking snapshots so a POST tests only run persistence,
    # independently of migrations (covered by the migration/review suites).
    Store(archive, target_version=6).initialize()
    options = dict(archive_path=archive, inbound_path=channel.inbound_path, probe_path=channel.probe_path,
        whatsapp_path=channel.whatsapp_path, whatsapp_config_path=channel.whatsapp_config_path,
        wechat_path=wx.wechat_path, discovery_path=wx.discovery_path, wechat_config_path=wx.wechat_config_path,
        clock=lambda: NOW)
    return SimpleNamespace(service=Audit(**options), options=options, channel=channel, wx=wx,
                           archive=archive, root=tmp_path)


def hashes(root, *, exclude_archive=False):
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in root.iterdir()
            if p.is_file() and (not exclude_archive or not p.name.startswith('archive.sqlite3'))}


def test_complete_scan_all_checks_no_read_failures_and_no_source_writes(all_sources):
    before = hashes(all_sources.root)
    result = all_sources.service.scan()
    assert sum(map(len, all_sources.service.checks.values())) == 27
    assert result['total'] == 26
    assert result['skipped'] == ['archive.pending_prices']
    assert result['failed_count'] == 0
    assert result['summary'] == '0 个需要处理'
    assert result['counts'] == dict(high=0, medium=0, low=0, ok=26)
    assert len({row['key'] for row in result['items']}) == 26
    assert all(row['level'] == 'ok' for row in result['items'])
    assert hashes(all_sources.root) == before
    output = json.dumps(result, ensure_ascii=False)
    for secret in ('fake-probe-secret', 'fake-event-cookie', 'FAKE-WA-SECRET', WECHAT.SECRET,
                   WECHAT.GROUP, 'Fictional Buyer', 'fictional-account', 'fictional-message'):
        assert secret not in output


def test_complete_findings_sort_and_summary_are_check_counts(all_sources):
    CHANNEL.incoming(all_sources.channel, ident='fictional-attachment', received=NOW - 10,
        sent_at=CHANNEL.stamp(NOW - 10), content='[图片]')
    all_sources.channel.whatsapp_config_path.write_text(json.dumps(dict(auto_send=True,
        token='FAKE-SECRET-IN-INTEGRATION')))
    with closing(sqlite3.connect(all_sources.archive)) as db, db:
        db.execute("INSERT INTO deals(title,created_at,updated_at,stage_changed_at) VALUES (?,?,?,?)",
                   ('虚构无会话案子', '2030-01-01', '2030-01-01', '2030-01-01'))
    result = all_sources.service.scan()
    assert result['failed_count'] == 0
    items = result['items']
    assert items == sorted(items, key=lambda row: (LEVELS[row['level']], row['channel'], row['key']))
    count = sum(row['level'] != 'ok' for row in items)
    assert count == result['attention_count'] == 3
    assert result['summary'] == '3 个需要处理'
    assert sum(result['counts'].values()) == result['total']


def test_post_full_run_only_persists_role_output_in_archive(all_sources):
    gate = SimpleNamespace(error_response=lambda request, code: JSONResponse({'error': 'denied'}, status_code=code))
    route = next(route for route in v3_audit_api.build(gate, service=all_sources.service)
                 if route.path == '/api/v1/audit/run')
    before = hashes(all_sources.root, exclude_archive=True)
    with read_db(all_sources.archive) as db:
        business_tables = ('parties', 'products', 'offers', 'freight_quotes', 'deals',
                           'deal_participants', 'deal_conversations', 'triage_suggestions')
        business_before = {table: db.execute('SELECT count(*) FROM ' + table).fetchone()[0]
                           for table in business_tables}
    async def body():
        return b''
    request = SimpleNamespace(headers={'content-length': '0', 'content-type': 'application/x-www-form-urlencoded'},
        body=body, query_params={}, path_params={}, state=SimpleNamespace(form={},
            password_principal=SimpleNamespace(username='fictional-owner'),
            session=SimpleNamespace(authenticated=True), password_recheck=lambda: True))
    response = asyncio.run(route.endpoint(request))
    assert response.status_code == 200
    result = json.loads(response.body)
    assert result['failed_count'] == 0 and result['run']['role'] == 'audit'
    assert result['run']['source'] == 'rules' and result['run']['model'] is None
    assert response.headers['cache-control'] == 'no-store'
    assert hashes(all_sources.root, exclude_archive=True) == before
    with read_db(all_sources.archive) as db:
        assert db.execute('SELECT count(*) FROM role_runs').fetchone()[0] == 1
        assert db.execute('SELECT count(*) FROM role_ai_reservations').fetchone()[0] == 0
        assert {table: db.execute('SELECT count(*) FROM ' + table).fetchone()[0]
                for table in business_tables} == business_before


def test_twenty_thousand_messages_per_channel_complete_under_three_seconds(all_sources):
    # No payloads enter Python during the audited read. Fixture generation is
    # outside the timed region, and a new service guarantees a cold audit cache.
    count = 20000
    with closing(sqlite3.connect(all_sources.channel.inbound_path)) as db, db:
        db.executemany('INSERT INTO inbound VALUES(?,?,?)', (
            ('fictional-large-' + str(index), json.dumps(dict(account='fictional-account-' + str(index % 6),
                conversation_id='fictional-chat-' + str(index % 6), buyer_name='Fictional Buyer ' + str(index % 6),
                role='buyer', content='fictional-private-body-never-returned',
                sent_at=CHANNEL.stamp(NOW - 120), history=False)), NOW - 120)
            for index in range(count)))
    with closing(sqlite3.connect(all_sources.wx.wechat_path)) as db, db:
        db.executemany('INSERT INTO messages VALUES(?,?,?,?,?)', (
            ('fictional-app', 'fictional-large-' + str(index), WECHAT.GROUP, NOW - 120,
             'fictional-private-body-never-returned') for index in range(count)))
        db.executemany('INSERT INTO message_scopes VALUES(?,?,?,?)', (
            ('fictional-app', 'fictional-large-' + str(index), WECHAT.GROUP, 'supplier')
            for index in range(count)))
    service = Audit(**all_sources.options)
    start = time.monotonic()
    result = service.scan()
    elapsed = time.monotonic() - start
    assert elapsed < 3, elapsed
    assert result['failed_count'] == 0, [(item['key'], item.get('error')) for item in result['items'] if item.get('read_failed')]
    assert result['total'] == 26 and result['attention_count'] == 0
