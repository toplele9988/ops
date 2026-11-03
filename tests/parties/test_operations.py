"""Fictional data only. Every DB under pytest tmp_path; no live adapters."""
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4
import json
import sqlite3
import threading

import pytest
from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from starlette.testclient import TestClient

from console.archive import validation as v
from console.archive.store import Store, configure, migrate, insert
from console.customers.operations import Parties, parameters
from console.inbox.model import make_key
from console.v3_customers_api import build_parties

NOW = datetime(2026, 11, 2, tzinfo=timezone.utc).timestamp()


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def deny(*a, **kw):
        raise AssertionError('network prohibited')
    monkeypatch.setattr('socket.socket.connect', deny)


class Inbox:
    def __init__(self):
        self.conversations = {}
        for i, channel in enumerate(('alibaba', 'whatsapp', 'wechat', 'wechat'), 1):
            key = make_key('conversation', channel, 'fictional-account', f'fictional-{i}')
            self.conversations[key] = dict(key=key, buyer_name=f'虚构单位{i}', channel=channel,
                last_seen=NOW - i * 86400, waiting_reply=i == 1,
                messages=[dict(role='buyer', text=f'虚构消息{i}', sent_at=NOW - i * 86400),
                          dict(role='seller', text=f'虚构回复{i}', sent_at=NOW - i * 86400 + 1)])
    def read(self):
        return dict(conversations=self.conversations)
    def detail(self, key):
        return self.conversations[key]


@pytest.fixture
def env(tmp_path):
    inbox = Inbox()
    service = Parties(tmp_path / 'fictional.sqlite3', inbox=inbox, clock=lambda: NOW)
    return service, list(inbox.conversations)


def write(s, op, pid=None, token=None, **f):
    return s.write(op, pid, dict(f, request_id=token or str(uuid4())), 'fictional-owner')


def create(s, name='虚构客户', kind='customer', **fields):
    return write(s, 'create', name=name, kind=kind, **fields)['id']


def detail(s, pid):
    return s.read('detail', row_id=pid)


def listing(s, **params):
    return s.read('list', parameters(params))


def test_read_never_creates_or_migrates_db(env):
    s, _ = env
    assert listing(s)['items'] == []
    assert not s.store.path.exists()
    with sqlite3.connect(s.store.path) as db:
        configure(db)
        migrate(db)
    before = s.store.path.read_bytes()
    assert listing(s)['items'] == []
    assert s.store.path.read_bytes() == before


@pytest.mark.parametrize('kind', v.PARTY_KINDS)
def test_create_link_update_persist_idempotently(env, kind):
    s, keys = env
    token = str(uuid4())
    fields = dict(name='虚构测试单位', kind=kind, country='虚构国', conversation_key=keys[0])
    a = write(s, 'create', token=token, **fields)
    assert write(s, 'create', token=token, **fields) == a
    pid = a['id']
    write(s, 'update', pid, notes='虚构内部备注', tags='重点,采购', next_followup='2026-11-08')
    write(s, 'link', pid, conversation_key=keys[1])
    reopened = Parties(s.store.path, inbox=s.inbox, clock=lambda: NOW)
    p = detail(reopened, pid)
    assert p['country'] == '虚构国' and p['tags'] == '重点,采购'
    assert p['next_followup'] == '2026-11-08'
    assert {i['channel'] for i in p['identities']} == {'alibaba', 'whatsapp'}
    assert len([e for e in p['timeline'] if e.get('conversation_key')]) == 4
    assert p['todo_count'] == 1
    with s.store.read() as db:
        assert db.execute('SELECT COUNT(*) FROM parties').fetchone()[0] == 1
        assert db.execute('PRAGMA foreign_key_check').fetchall() == []
        assert '虚构内部备注' not in str([tuple(r) for r in db.execute('SELECT detail FROM audit')])


def test_failed_link_rolls_back_create_and_idempotency(env):
    s, keys = env
    first = create(s, conversation_key=keys[0])
    with pytest.raises(v.Conflict):
        create(s, name='虚构另一个客户', conversation_key=keys[0])
    with s.store.read() as db:
        assert db.execute('SELECT COUNT(*) FROM parties').fetchone()[0] == 1
    assert detail(s, first)['identities'][0]['key'] == keys[0]


def test_request_conflict_does_not_mutate(env):
    s, _ = env
    token = 'fictional-request'
    pid = write(s, 'create', token=token, name='虚构客户', kind='customer')['id']
    with pytest.raises(v.Conflict):
        write(s, 'create', token=token, name='变更内容', kind='customer')
    with pytest.raises(v.Conflict):
        s.write('create', None, dict(name='虚构客户', kind='customer', request_id=token), 'another-owner')
    assert detail(s, pid)['name'] == '虚构客户'


def test_filters_sort_and_deal_side_effects(env):
    s, keys = env
    a = create(s, name='A虚构客户', country='虚构国甲', conversation_key=keys[0])
    b = create(s, name='B虚构客户', country='虚构国乙', conversation_key=keys[1])
    c = create(s, name='C未联系客户')
    write(s, 'update', b, tags='重点')
    token = str(uuid4())
    deal = write(s, 'deal', a, token=token, title='虚构采购案子')
    assert write(s, 'deal', a, token=token, title='虚构采购案子') == deal
    assert [r['id'] for r in listing(s, country='虚构国甲', channel='alibaba', contact='7', open='yes')['items']] == [a]
    assert [r['id'] for r in listing(s, q='重点')['items']] == [b]
    assert len(listing(s, contact='never')['items']) == 1
    assert listing(s, contact='stale')['total'] == 0
    assert listing(s, open='no')['total'] == 2
    assert listing(s, sort='tasks')['items'][0]['id'] == a
    assert listing(s, sort='recent')['items'][0]['id'] == a
    assert [r['name'] for r in listing(s, sort='name')['items']] == ['A虚构客户', 'B虚构客户', 'C未联系客户']
    assert listing(s, sort='oldest')['items'][0]['name'] == 'C未联系客户'
    write(s, 'update', a, next_followup='2026-11-10')
    write(s, 'update', b, next_followup='2026-11-05')
    assert [r['id'] for r in listing(s, sort='followup')['items'][:2]] == [b, a]
    assert listing(s, contact='30')['total'] == 2
    assert listing(s, page='2', page_size='2')['total'] == 3
    assert len(listing(s, page='2', page_size='2')['items']) == 1
    with s.store.read() as db:
        did = deal['deal_id']
        assert db.execute('SELECT customer_party_id FROM deals WHERE id=?', (did,)).fetchone()[0] == a
        assert db.execute('SELECT COUNT(*) FROM deal_tasks WHERE deal_id=?', (did,)).fetchone()[0] > 0
        assert db.execute('SELECT conversation_key FROM deal_conversations WHERE deal_id=?', (did,)).fetchone()[0] == keys[0]


def test_merge_reversible_no_data_loss_and_no_chains(env):
    s, keys = env
    a = create(s, name='虚构主客户', conversation_key=keys[0])
    b = create(s, name='虚构重复客户', conversation_key=keys[1])
    c = create(s, name='虚构第三客户')
    write(s, 'update', b, notes='保留原备注', tags='原标签')
    deal = write(s, 'deal', b, title='原案子')
    with pytest.raises(v.BadRequest):
        write(s, 'merge', a, source_id=str(b))
    merge = write(s, 'merge', a, source_id=str(b), confirm='1')
    p = detail(s, a)
    assert len(p['identities']) == 2 and len(p['deals']) == 1
    assert p['merged_profiles'][0]['notes'] == '保留原备注'
    assert listing(s)['total'] == 2
    with pytest.raises(v.Conflict):
        write(s, 'merge', c, source_id=str(a), confirm='1')
    with pytest.raises(v.Conflict):
        write(s, 'update', b, notes='不可改已合并来源')
    write(s, 'undo', a, merge_id=str(merge['merge_id']), confirm='1')
    assert len(detail(s, a)['identities']) == 1
    assert len(detail(s, b)['identities']) == 1
    assert detail(s, b)['notes'] == '保留原备注'
    assert detail(s, a)['merges'][0]['undone_at']
    with s.store.read() as db:
        assert db.execute('SELECT customer_party_id FROM deals WHERE id=?', (deal['deal_id'],)).fetchone()[0] == b
        assert db.execute('SELECT party_id FROM identities WHERE conversation_key=?', (keys[1],)).fetchone()[0] == b


def test_draft_review_never_sends_and_requires_owned_conversation(env):
    s, keys = env
    a = create(s, conversation_key=keys[0])
    with pytest.raises(v.Conflict):
        write(s, 'draft', a, purpose='followup', conversation_key=keys[1])
    token = str(uuid4())
    draft = write(s, 'draft', a, token=token, purpose='followup', conversation_key=keys[0])
    assert write(s, 'draft', a, token=token, purpose='followup', conversation_key=keys[0]) == draft
    assert draft['dry_run'] and draft['state'] == 'draft'
    with pytest.raises(v.BadRequest):
        write(s, 'review', a, draft_id=str(draft['draft_id']), text='虚构核对内容')
    write(s, 'review', a, draft_id=str(draft['draft_id']), text='虚构核对内容', confirm='1')
    d = detail(s, a)['drafts'][0]
    assert d['text'] == '虚构核对内容' and d['reviewed_at'] and d['state'] == 'draft' and d['dry_run'] == 1
    with s.store.read() as db:
        assert db.execute('SELECT COUNT(*) FROM parties_outbox').fetchone()[0] == 1
    with pytest.raises(sqlite3.IntegrityError):
        with s.store.write() as db:
            db.execute("UPDATE parties_outbox SET state='sent'")


def test_supplier_packing_and_offer(env):
    s, keys = env
    pid = create(s, kind='supplier', conversation_key=keys[2])
    with s.store.write() as db:
        product = insert(db, 'products', dict(supplier_id=pid, name='虚构灯', carton_l_cm=40, carton_w_cm=30, carton_h_cm=20,
            gross_kg=5, units_per_carton=2, created_at=s.stamp(), updated_at=s.stamp()), 'fixture')
        incomplete = insert(db, 'products', dict(supplier_id=pid, name='虚构缺资料产品', created_at=s.stamp(), updated_at=s.stamp()), 'fixture')
        insert(db, 'offers', dict(product_id=product, price_fen=12345, currency='CNY', quoted_on='2026-10-01', valid_until='2026-10-30', status='confirmed', created_at=s.stamp(), updated_at=s.stamp()), 'fixture')
    assert detail(s, pid)['products'][0]['current_offer']['price'] == '123.45'
    assert len(detail(s, pid)['pending_questions']) == 4
    with pytest.raises(v.Conflict):
        write(s, 'verify', pid, product_id=str(incomplete), confirm='1')
    write(s, 'verify', pid, product_id=str(product), confirm='1')
    assert detail(s, pid)['products'][0]['verified']
    for purpose in ('inquiry', 'materials'):
        assert write(s, 'draft', pid, purpose=purpose, conversation_key=keys[2])['dry_run']
    with pytest.raises(v.BadRequest):
        write(s, 'draft', pid, purpose='followup', conversation_key=keys[2])


@pytest.mark.parametrize('fields', [dict(name=''), dict(name='a'*201), dict(country='b'*101), dict(next_followup='2026-02-31'), dict(tags='x'*301), dict(arbitrary='x')])
def test_bad_writes_are_atomic(env, fields):
    s, _ = env
    pid = create(s)
    before = detail(s, pid)
    with pytest.raises(v.BadRequest):
        write(s, 'update', pid, **fields)
    assert detail(s, pid) == before


@pytest.mark.parametrize('query', [{'page':'0'}, {'page_size':'101'}, {'sort':'bad'}, {'contact':'bad'}, {'open':'bad'}, {'kind':'bad'}, {'unexpected':'x'}])
def test_invalid_queries(query):
    with pytest.raises(v.BadRequest):
        parameters(query)


def test_concurrent_request_creates_once(env):
    s, _ = env
    results, errors = [], []
    def attempt():
        try:
            results.append(write(s, 'create', token='same-request', name='虚构并发客户', kind='customer'))
        except Exception as e:
            errors.append(e)
    threads = [threading.Thread(target=attempt) for _ in range(5)]
    for t in threads: t.start()
    for t in threads: t.join()
    assert not errors
    assert len({r['id'] for r in results}) == 1
    assert listing(s)['total'] == 1


class Gates(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        request.state.password_principal = SimpleNamespace(username='fictional-owner') if request.headers.get('x-auth') == '1' else None
        request.state.session = SimpleNamespace(authenticated=True)
        request.state.password_recheck = lambda: request.headers.get('x-recheck') == '1'
        # Model the required host CSRF gate, including cached form compatibility.
        if request.method == 'POST' and request.headers.get('x-csrf-token') != 'fictional-csrf':
            return JSONResponse({'error':'CSRF'}, status_code=403)
        return await call_next(request)


def test_http_gates_validation_and_side_effects(env):
    s, keys = env
    gate = SimpleNamespace(error_response=lambda r, code: JSONResponse({'error':'请登录'}, status_code=code))
    app = Starlette(routes=build_parties(gate, service=s), middleware=[])
    app.add_middleware(Gates)
    with TestClient(app) as client:
        headers = {'x-auth':'1', 'x-recheck':'1', 'x-csrf-token':'fictional-csrf'}
        assert client.get('/api/v1/parties-ops').status_code == 403
        assert client.post('/api/v1/parties-ops', headers={'x-auth':'1','x-recheck':'1'}, data={}).status_code == 403
        assert client.post('/api/v1/parties-ops', headers={**headers, 'x-recheck':'0'}, data={}).status_code == 403
        assert not s.store.path.exists()
        fields = dict(name='虚构HTTP客户', kind='customer', request_id='http-create', conversation_key=keys[0])
        created = client.post('/api/v1/parties-ops', headers=headers, data=fields)
        assert created.status_code == 200
        pid = created.json()['id']
        assert client.post('/api/v1/parties-ops', headers=headers, data=fields).json() == created.json()
        assert client.get(f'/api/v1/parties-ops/{pid}', headers=headers).json()['identities'][0]['key'] == keys[0]
        assert client.get('/api/v1/parties-ops?kind=customer&kind=supplier', headers=headers).status_code == 400
        assert client.post(f'/api/v1/parties-ops/{pid}/update', headers={**headers, 'content-type':'application/x-www-form-urlencoded'}, content='name=a&name=b').status_code == 400
        assert client.post(f'/api/v1/parties-ops/{pid}/update', headers=headers, json={}).status_code == 400
        response = client.get('/api/v1/parties-ops', headers=headers)
        assert response.json()['total'] == 1 and response.headers['cache-control'] == 'no-store'
        assert client.get('/api/v1/parties-ops/999', headers=headers).status_code == 404
        assert client.get('/api/v1/parties-ops/conversations', headers=headers).json()['items'][0]['party_id'] == pid


def test_supplier_terms_persist_and_review_requires_ownership(env):
    s, _ = env
    pid = create(s, kind='supplier')
    other = create(s, name='虚构另一家工厂', kind='supplier')
    token = str(uuid4())
    result = write(s, 'term', pid, token=token, field='lead_time', value='虚构交期15天')
    assert write(s, 'term', pid, token=token, field='lead_time', value='虚构交期15天') == result
    assert detail(s, pid)['terms'][0]['value'] == '虚构交期15天'
    with s.store.write() as db:
        tid = insert(db, 'terms', dict(party_id=pid, field='moq', value='虚构起订量2件', status='suggested', updated_at=s.stamp()), 'fixture')
    with pytest.raises(v.Conflict):
        write(s, 'term-status', other, term_id=str(tid), action='confirm')
    write(s, 'term-status', pid, term_id=str(tid), action='confirm')
    assert detail(s, pid)['terms'][1]['status'] == 'confirmed'
    assert len(detail(s, pid)['terms']) == 2


def test_explicit_identity_takes_priority_over_stale_legacy_ref(env):
    s, keys = env
    owner = create(s, conversation_key=keys[0])
    stale = create(s, name='虚构旧资料', kind='supplier')
    with s.store.write() as db:
        db.execute('UPDATE parties SET conversation_ref=? WHERE id=?', (keys[0], stale))
    assert detail(s, stale)['identities'] == []
    assert detail(s, stale)['last_contact'] is None
    assert len(detail(s, owner)['identities']) == 1
    with pytest.raises(v.Conflict):
        write(s, 'draft', stale, purpose='inquiry', conversation_key=keys[0])


def test_merged_customer_case_carries_all_channels_without_transferring_identity(env):
    s, keys = env
    a = create(s, name='虚构主客户', conversation_key=keys[0])
    b = create(s, name='虚构重复客户', conversation_key=keys[1])
    write(s, 'merge', a, source_id=str(b), confirm='1')
    did = write(s, 'deal', a, title='虚构跨渠道采购')['deal_id']
    with s.store.read() as db:
        links = {r['conversation_key']: r['party_id'] for r in db.execute('SELECT * FROM deal_conversations WHERE deal_id=?', (did,))}
        assert links == {keys[0]: a, keys[1]: b}
        assert db.execute('SELECT party_id FROM identities WHERE conversation_key=?', (keys[1],)).fetchone()[0] == b
        assert db.execute('SELECT customer_party_id FROM deals WHERE id=?', (did,)).fetchone()[0] == a
