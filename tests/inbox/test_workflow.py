"""W2 acceptance against temporary SQLite, fake channels and an injected AI runner."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from starlette.testclient import TestClient

from console.channels.common import conversation
from console.customers import aggregate_customers
from console.inbox.model import waiting_state, make_key
from console.inbox.workflow import Workflow
from console.inbox import drafts
from console.inspiration.enrich import RunnerOutput
from console.archive import validation as v
from console.v3_inbox_api import build

NOW = 1800000000


class Inbox:
    def __init__(self):
        self.rows = {}
        for channel, name, role in [('alibaba', '虚构客户甲', 'customer'), ('whatsapp', '虚构客户乙', 'customer'), ('wechat', '虚构供应商', 'supplier')]:
            c = conversation(channel, channel, 'fictional-account', 'fictional-' + channel, name,
                [dict(role='buyer', text='Can you confirm the carton size?', sent_at=NOW-7200)], role=role)
            state, _ = waiting_state(c['messages'], NOW)
            self.rows[c['key']] = dict(c, **state, can_send=False, copy_hint='演练')
        self.keys = list(self.rows)

    def read(self, now=None):
        return dict(ready=True, conversations=self.rows, customers=aggregate_customers(self.rows.values()))

    def detail(self, key, now=None):
        return self.rows.get(key)


@pytest.fixture(autouse=True)
def no_network():
    connect = sqlite3.connect
    def isolated_connect(database, *args, **kwargs):
        if str(database).startswith(('/opt/imgw/', '/var/lib/imgw/', 'file:/opt/imgw/', 'file:/var/lib/imgw/', '/srv/imgw-dev/')):
            raise AssertionError('Only temporary fictional databases are allowed')
        return connect(database, *args, **kwargs)
    with patch('sqlite3.connect', side_effect=isolated_connect), patch('socket.create_connection', side_effect=AssertionError('network forbidden')), \
         patch('socket.socket.connect', side_effect=AssertionError('network forbidden')), \
         patch('socket.getaddrinfo', side_effect=AssertionError('network forbidden')), \
         patch('urllib.request.urlopen', side_effect=AssertionError('network forbidden')):
        yield


@pytest.fixture
def workflow(tmp_path):
    inbox = Inbox()
    now = [NOW]
    w = Workflow(inbox, tmp_path/'outbox.sqlite3', tmp_path/'archive.sqlite3', lambda: now[0])
    w.test_now = now
    return w


def action(w, kind, request='fictional-request', **fields):
    return w.action(w.inbox.keys[0], dict(action=kind, request_id=request, **fields), '虚构操作员')


def test_done_persists_and_new_message_reopens(workflow):
    w = workflow
    action(w, 'done')
    reopened = Workflow(w.inbox, w.outbox.store.path, w.store.path, w.clock)
    assert reopened.context(w.inbox.keys[0])['workflow_state'] == 'done'
    c = w.inbox.rows[w.inbox.keys[0]]
    c['messages'] += (dict(role='buyer', text='New fictional question', sent_at=NOW),)
    c['last_seen'] = NOW
    c['n_messages'] += 1
    assert reopened.context(c['key'])['workflow_state'] == 'waiting'


def test_reminder_due_and_new_message_cancel_snooze(workflow):
    w = workflow
    due = datetime.fromtimestamp(NOW+600, timezone.utc).isoformat()
    action(w, 'remind', remind_at=due)
    assert w.context(w.inbox.keys[0])['workflow_state'] == 'replied'
    w.test_now[0] += 601
    assert w.context(w.inbox.keys[0])['reminder_due']
    assert w.context(w.inbox.keys[0])['workflow_state'] == 'waiting'
    for raw in ['2026-01-01T12:00', 'not-a-time', datetime.fromtimestamp(NOW-1, timezone.utc).isoformat()]:
        with pytest.raises(v.BadRequest):
            action(w, 'remind', request='fictional-invalid-' + str(len(raw)), remind_at=raw)


def test_customer_assign_and_deal_identity_are_consistent(workflow):
    w = workflow
    c = w.inbox.rows[w.inbox.keys[1]]
    key = make_key('customer', c['channel'], c['account'], c['buyer_name'])
    action(w, 'assign', customer_key=key)
    created = action(w, 'create_deal', 'fictional-create')
    context = w.context(w.inbox.keys[0])
    assert context['customer']['buyer_name'] == '虚构客户乙'
    assert context['party']['name'] == '虚构客户乙'
    assert w.deals.read('detail', row_id=created['id'])['customer']['name'] == '虚构客户乙'
    first = w.inbox.rows[w.inbox.keys[0]]
    first_key = make_key('customer', first['channel'], first['account'], first['buyer_name'])
    with pytest.raises(v.Conflict):
        action(w, 'assign', 'fictional-reassign', customer_key=first_key)
    action(w, 'assign', 'fictional-reassign', customer_key=first_key, confirm='1')
    with w.store.read() as db:
        owner = db.execute('SELECT party_id FROM identities WHERE conversation_key=?', (first['key'],)).fetchone()[0]
        assert db.execute('SELECT party_id FROM deal_conversations WHERE deal_id=?', (created['id'],)).fetchone()[0] == owner


def test_concurrent_create_and_link_are_idempotent(workflow):
    w = workflow
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: action(w, 'create_deal'), range(4)))
    assert len({r['id'] for r in results}) == 1
    assert action(w, 'create_deal', 'fictional-refresh')['id'] == results[0]['id']
    for request in ['fictional-link-1', 'fictional-link-2']:
        action(w, 'link_deal', request, deal_id=str(results[0]['id']))
    with w.store.read() as db:
        assert db.execute('SELECT count(*) FROM deals').fetchone()[0] == 1
        assert db.execute('SELECT count(*) FROM deal_conversations').fetchone()[0] == 1
    with pytest.raises(v.Conflict):
        action(w, 'done')


def test_missing_deal_rolls_back_receipt(workflow):
    w = workflow
    with pytest.raises(v.Missing):
        action(w, 'link_deal', deal_id='999')
    assert w.context(w.inbox.keys[0])['deals'] == []
    with w.store.read() as db:
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='inbox_action_receipts'").fetchone():
            assert db.execute('SELECT count(*) FROM inbox_action_receipts').fetchone()[0] == 0


def test_dry_run_queue_deduplicates_and_never_enters_live_outbox(workflow):
    w = workflow
    fields = dict(text='Fictional edited reply', dry_run='1', confirm='1')
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: action(w, 'send', **fields), range(4)))
    assert len({r['message']['id'] for r in results}) == 1
    assert results[0]['message']['dry_run'] == 1
    assert results[0]['message']['state'] == 'queued'
    with sqlite3.connect(w.outbox.store.path) as db:
        assert db.execute('SELECT count(*) FROM outbox').fetchone()[0] == 0
        assert db.execute('SELECT count(*) FROM inbox_dry_run_drafts').fetchone()[0] == 1
    with pytest.raises(v.Conflict):
        action(w, 'send', **{**fields, 'text': 'changed body'})
    for changes in [dict(confirm='0'), dict(dry_run='0'), dict(text='【待确认：价格】')]:
        with pytest.raises(v.BadRequest):
            action(w, 'send', 'fictional-rejected', **{**fields, **changes})
    assert w.context(w.inbox.keys[0])['workflow_state'] == 'waiting', 'drafts must not mark a customer replied'


def test_supplier_question_has_product_and_selected_question(workflow):
    w = workflow
    key, supplier = w.inbox.keys[0], w.inbox.keys[2]
    fields = dict(request_id='fictional-supplier-draft', supplier_key=supplier, product='虚构灯具 X')
    preview = w.supplier_draft(key, fields, '虚构操作员')
    assert preview == w.supplier_draft(key, fields, '虚构操作员')
    assert preview['message']['state'] == 'draft'
    assert '虚构灯具 X' in preview['message']['text']
    assert 'Can you confirm the carton size?' in preview['message']['text']
    queued = action(w, 'supplier_send', 'fictional-supplier-queue', supplier_key=supplier,
                    draft_id=str(preview['message']['id']), text='Edited supplier question', dry_run='1', confirm='1')
    assert queued['message']['conversation_key'] == supplier
    assert queued['message']['source_key'] == key
    assert len(w.context(supplier)['outbox']) == 2
    with pytest.raises(v.BadRequest):
        w.supplier_draft(key, {**fields, 'supplier_key': w.inbox.keys[1]}, '虚构操作员')
    with pytest.raises(v.BadRequest):
        w.supplier_draft(key, {**fields, 'product': ''}, '虚构操作员')


def test_supplier_uses_linked_deal_products(workflow):
    w = workflow
    deal = action(w, 'create_deal')
    w.deals.write('item', deal['id'], {'name': '虚构案内产品', 'qty': '2'}, '虚构操作员')
    result = w.supplier_draft(w.inbox.keys[0], dict(request_id='fictional-supplier-item', supplier_key=w.inbox.keys[2]), '虚构操作员')
    assert '虚构案内产品' in result['message']['text']


@pytest.fixture
def http(tmp_path, monkeypatch):
    inbox = Inbox()
    flags = dict(auth=True, recheck=True)
    class Gate(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            request.state.password_principal = SimpleNamespace(username='fictional-operator') if flags['auth'] else None
            request.state.password_recheck = lambda: flags['recheck']
            request.state.session = SimpleNamespace(authenticated=True)
            return await call_next(request)
    runner_calls = []
    def runner(prompt):
        runner_calls.append(prompt)
        return RunnerOutput(json.dumps(dict(draft='Fictional generated reply', language='en', needs_confirmation=[])), 'fictional-ai')
    monkeypatch.setattr(drafts, 'LIMITER', drafts.DraftLimiter())
    monkeypatch.setattr('console.v3_inbox_api.draft_context', lambda *a, **k: dict(text='', sources=[]))
    routes = build(SimpleNamespace(error_response=lambda r, s: JSONResponse({'error': '拒绝访问'}, s)),
                   tmp_path/'inbound.sqlite3', tmp_path/'drafts.sqlite3', inbox=inbox,
                   runner=runner, clock=lambda: NOW, outbox=SimpleNamespace(send_state=lambda c: dict(enabled=False, available=False, reason='演练')))
    app = Starlette(routes=routes)
    app.add_middleware(Gate)
    with TestClient(app) as client:
        yield client, inbox, flags, runner_calls


def test_http_actions_filtering_auth_and_failure_redaction(http, monkeypatch):
    client, inbox, flags, calls = http
    key = inbox.keys[0]
    url = '/api/v1/inbox/conversations/' + key
    assert client.post(url+'/ops/action', data=dict(action='done', request_id='fictional-http-done')).status_code == 200
    done = client.get('/api/v1/inbox/conversations?state=done').json()
    assert done['total'] == 1
    assert done['items'][0]['workflow_state'] == 'done'
    assert client.get('/api/v1/inbox/conversations?state=waiting').json()['total'] == 1
    assert client.get('/api/v1/inbox/conversations?state=done&state=all').status_code == 400
    assert client.get('/api/v1/inbox/conversations?state=invalid').status_code == 400
    flags['recheck'] = False
    assert client.post(url+'/ops/action', data=dict(action='done', request_id='fictional-http-noauth')).status_code == 403
    assert client.get(url+'/ops/context').status_code == 403
    flags['recheck'] = True
    with patch('console.inbox.workflow.Workflow.action', side_effect=RuntimeError('fictional-private-path')):
        r = client.post(url+'/ops/action', data=dict(action='done', request_id='fictional-http-fail'))
        assert r.status_code == 503
        assert 'fictional-private-path' not in r.text
        assert '人工核对' in r.text


def test_http_draft_idempotent_and_complete_messages(http):
    client, inbox, flags, calls = http
    key = inbox.keys[0]
    url = '/api/v1/inbox/conversations/' + key
    a = client.post(url+'/draft', data=dict(request_id='fictional-ai-request'))
    b = client.post(url+'/draft', data=dict(request_id='fictional-ai-request'))
    assert a.status_code == b.status_code == 200, (a.text, b.text)
    assert a.json()['draft']['id'] == b.json()['draft']['id']
    assert len(calls) == 1
    c = inbox.rows[key]
    c['messages'] = tuple(dict(role='buyer', text='X'*5000, sent_at=NOW-1000+i) for i in range(301))
    c['n_messages'] = 301
    result = client.get(url).json()
    assert len(result['messages']) == 301
    assert len(result['messages'][0]['text']) == 5000
    assert client.get(url+'/ops/context').status_code == 200


def test_explicit_migration_is_additive_and_read_only_gets_create_nothing(tmp_path, workflow):
    w = workflow
    assert not w.store.path.exists()
    assert not w.outbox.store.path.exists()
    w.context(w.inbox.keys[0])
    assert not w.store.path.exists()
    assert not w.outbox.store.path.exists()
    from console.inbox.migrate import migrate
    draft_path = tmp_path/'legacy-drafts.sqlite3'
    old = dict(text='Existing fictional draft', model='fictional-model', language='en', needs_confirmation=[])
    with sqlite3.connect(draft_path) as db:
        db.execute(drafts._SCHEMA)
        db.execute('INSERT INTO drafts(id,conversation_key,created_at,model,language,text,needs_json) VALUES(1,?,?,?,?,?,?)', (w.inbox.keys[0], NOW, old['model'], old['language'], old['text'], '[]'))
    saved = {'id': 1}
    migrate(w.store.path, w.outbox.store.path, draft_path)
    migrate(w.store.path, w.outbox.store.path, draft_path)
    assert drafts.read_drafts(draft_path)[0][w.inbox.keys[0]]['id'] == saved['id']
    with sqlite3.connect(w.outbox.store.path) as db:
        assert db.execute('SELECT count(*) FROM outbox').fetchone()[0] == 0
        assert db.execute('SELECT count(*) FROM inbox_dry_run_drafts').fetchone()[0] == 0


def test_done_uses_source_revision_even_when_detail_history_is_richer(workflow):
    w = workflow
    key = w.inbox.keys[0]
    original = w.inbox.detail
    def detail(key, now=None):
        conv = dict(original(key))
        conv['messages'] = (dict(role='seller', text='Older fictional message', sent_at=NOW-10000),) + conv['messages']
        conv['n_messages'] += 1
        return conv
    w.inbox.detail = detail
    action(w, 'done')
    assert w.context(key)['workflow_state'] == 'done'
    source = w.inbox.rows[key]
    assert w.project(source)['workflow_state'] == 'done'


def test_summary_revision_detects_new_messages_outside_newest_conversation(http):
    client, inbox, _, _ = http
    a = client.get('/api/v1/inbox/summary').json()['revision']
    conv = inbox.rows[inbox.keys[0]]
    conv['messages'] = (dict(conv['messages'][0], text='Different fictional customer question'),)
    b = client.get('/api/v1/inbox/summary').json()['revision']
    assert a != b
