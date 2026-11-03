"""Cross-module invariants using actual SQLite, fictional channels and no network."""
from types import SimpleNamespace
import pytest
from test_workflow import Inbox, NOW, no_network  # shared offline guard, not service mocks
from console.archive.store import Store
from console.customers.operations import Parties
from console.deals.service import Deals
from console.inbox.workflow import Workflow
from console.ops_queue import OpsQueue
from console.ops_queue.migrate import migrate
from console.v3_ops_drafts_api import read_drafts


def test_merge_inbox_deals_undo_and_new_records_keep_historical_foreign_keys(tmp_path):
    inbox = Inbox()
    path = tmp_path / 'archive.sqlite3'
    parties = Parties(path, inbox=inbox, clock=lambda: NOW)
    def write(op, pid=None, **f):
        return parties.write(op, pid, dict(request_id='r11-' + str(len(calls)), **f), 'fictional-owner')
    calls = []
    def call(op, pid=None, **f):
        calls.append(op)
        return write(op, pid, **f)
    a = call('create', name='虚构保留客户', kind='customer', country='GB', conversation_key=inbox.keys[0])['id']
    b = call('create', name='虚构重复客户', kind='customer', country='US', conversation_key=inbox.keys[1])['id']
    call('update', a, timezone='Europe/London')
    w = Workflow(inbox, tmp_path/'outbox.sqlite3', path, lambda: NOW)
    did = w.action(inbox.keys[1], dict(action='create_deal', request_id='r11-create-old'), 'fictional-owner')['id']
    assert w.deals.read('detail', row_id=did)['customer']['id'] == b
    merge = call('merge', a, source_id=str(b), confirm='1')['merge_id']
    context = w.context(inbox.keys[1])
    assert context['party']['id'] == a
    assert context['customer']['buyer_name'] == '虚构保留客户'
    assert context['customer']['n_conversations'] == 2
    assert context['party']['timezone'] == 'Europe/London'
    assert w.project(inbox.rows[inbox.keys[1]])['customer_name'] == '虚构保留客户'
    assert w.deals.read('detail', row_id=did)['customer']['id'] == a
    assert did in [d['id'] for d in w.deals.read('list', {'party': a})['items']]
    newer = w.deals.write('create', None, dict(customer_party_id=str(b), title='虚构合并后新案子'), 'fictional-owner')['id']
    assert w.deals.read('detail', row_id=newer)['customer']['id'] == a
    # Explicit assignment to the displayed owner is not permission to rewrite history.
    w.action(inbox.keys[1], dict(action='assign', customer_party_id=str(a), confirm='1', request_id='r11-assign'), 'fictional-owner')
    with w.store.read() as db:
        assert db.execute('SELECT party_id FROM identities WHERE conversation_key=?', (inbox.keys[1],)).fetchone()[0] == b
        assert db.execute('SELECT customer_party_id FROM deals WHERE id=?', (did,)).fetchone()[0] == b
    call('undo', a, merge_id=str(merge), confirm='1')
    assert w.context(inbox.keys[1])['party']['id'] == b
    assert w.deals.read('detail', row_id=did)['customer']['id'] == b
    assert w.deals.read('detail', row_id=newer)['customer']['id'] == a
    with pytest.raises(Exception, match='IANA'):
        call('update', a, timezone='Mars/Imaginary')


def test_drafts_remain_unsendable_and_queue_requires_explicit_done(tmp_path):
    inbox = Inbox()
    archive, outbox, drafts = [tmp_path / (n + '.sqlite3') for n in ('archive', 'outbox', 'drafts')]
    migrate(archive, outbox, drafts); migrate(archive, outbox, drafts)
    parties = Parties(archive, inbox=inbox, clock=lambda: NOW)
    pid = parties.write('create', None, dict(request_id='r11-party', name='虚构客户', kind='customer', conversation_key=inbox.keys[0]), 'fictional-owner')['id']
    p = parties.write('draft', pid, dict(request_id='r11-party-draft', purpose='followup', conversation_key=inbox.keys[0]), 'fictional-owner')
    parties.write('review', pid, dict(request_id='r11-party-review', draft_id=str(p['draft_id']), text='虚构人工核对稿', confirm='1'), 'fictional-owner')
    w = Workflow(inbox, outbox, archive, lambda: NOW)
    healthy = SimpleNamespace(read=lambda: dict(channels=[], failed_jobs=dict(count=0)))
    queue = OpsQueue(paths={'archive': archive}, inbox=inbox, overview=healthy, pipeline=healthy, clock=lambda: NOW)
    before = queue.read()['counts']['buyer_reply']
    w.action(inbox.keys[0], dict(action='send', request_id='r11-dry-send', text='Fictional response', confirm='1', dry_run='1'), 'fictional-owner')
    assert queue.read()['counts']['buyer_reply'] == before
    rows = read_drafts(archive, outbox)['items']
    assert {r['source'] for r in rows} == {'inbox', 'parties'}
    assert all(r['dry_run'] for r in rows)
    assert any(r['reviewed_at'] for r in rows if r['source'] == 'parties')
    w.action(inbox.keys[0], dict(action='done', request_id='r11-done'), 'fictional-owner')
    assert queue.read()['counts']['buyer_reply'] == before - 1
    c = inbox.rows[inbox.keys[0]]
    c['messages'] += (dict(role='buyer', text='Fictional new question', sent_at=NOW),)
    c['last_seen'] = NOW
    assert queue.read()['counts']['buyer_reply'] == before
    from console.inbox.store import read_database
    with read_database(outbox) as db:
        assert db.execute('SELECT COUNT(*) FROM outbox').fetchone()[0] == 0


def test_global_drafts_route_is_read_only_authenticated_and_rechecks(tmp_path):
    import asyncio
    from starlette.datastructures import QueryParams
    from starlette.responses import JSONResponse
    from console.v3_ops_drafts_api import build
    gate = SimpleNamespace(error_response=lambda r, code: JSONResponse({'error': 'denied'}, code))
    routes = build(gate, tmp_path/'archive.sqlite3', tmp_path/'outbox.sqlite3')
    assert len(routes) == 1 and routes[0].methods == {'GET', 'HEAD'}
    def request(auth=True, recheck=True, query=''):
        return SimpleNamespace(query_params=QueryParams(query), state=SimpleNamespace(password_principal=SimpleNamespace(username='fictional-owner') if auth else None, password_recheck=lambda: recheck, session=SimpleNamespace(authenticated=auth)))
    for req, code in [(request(auth=False),403),(request(recheck=False),403),(request(query='unexpected=1'),400),(request(),200)]:
        response = asyncio.run(routes[0].endpoint(req))
        assert response.status_code == code
        assert response.headers['cache-control'] == 'no-store'
    assert not list(tmp_path.iterdir()), 'GET never creates or migrates a database'
