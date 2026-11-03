"""Fictional inquiries through real ASGI routes; no sockets or external runner."""
from datetime import date
import socket
from types import SimpleNamespace
from urllib.parse import parse_qs
from unittest.mock import Mock

import pytest
from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from starlette.testclient import TestClient

from console import v3_archive_api, v3_deals_api, v3_deal_workflow_api
from console.archive.service import Archive
from console.archive.store import Store
from console.deals.demands import Demands
from console.deals.followups import Followups
from console.deals.offers import Offers
from console.deals.service import Deals
from console.inbox.model import make_key
from console.lang import Lang

NOW = 2_000_000_000.0
INQUIRIES = (
    ('需要24台LX-100摇头灯，电压：220V；目的地：西班牙；配套要求：航空箱。', 24),
    ('Necesito 30 unidades de LX-100 moving head; destino: Madrid, España; voltaje: 220V; accesorios: flight cases.', 30),
)


@pytest.fixture
def integrated(tmp_path, monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError('集成测试禁止网络、真实AI或渠道发送')
    monkeypatch.setattr(socket.socket, 'connect', blocked)
    monkeypatch.setattr('console.inbox.ai.official_runner', blocked)
    now = [NOW]
    conversations = {}
    inbox = SimpleNamespace(read=lambda now=None: dict(ready=True, conversations=conversations),
                            detail=lambda key, now=None: conversations.get(key))
    store = Store(tmp_path / 'archive.sqlite3', target_version=9)
    store.initialize()
    archive = Archive(store=store, today=lambda: date(2033, 5, 18))
    deals = Deals(store=store, inbox=inbox, clock=lambda: now[0])
    offers = Offers(store=store, path=tmp_path / 'offers.sqlite3', inbox=inbox,
                    today=lambda: date(2033, 5, 18), clock=lambda: now[0])
    demands = Demands(store=store, path=tmp_path / 'demands.sqlite3', inbox=inbox, clock=lambda: now[0])
    translations = []
    def translate(prompt):
        translations.append(prompt)
        text = 'Confirme el embalaje.' if '语言代码 es。' in prompt else '请确认包装。'
        return '[[1]]\n' + text + '\n[[/1]]'
    lang = Lang(tmp_path / 'lang.sqlite3', inbox=inbox, runner=translate, clock=lambda: now[0])
    runner = Mock(return_value='语言：中文\n请确认包装。\n=====待确认=====\n人工核对产品规格及交期')
    followups = Followups(tmp_path / 'followups.sqlite3', store=store, inbox=inbox, lang=lang,
                          runner=runner, clock=lambda: now[0], demands=demands)
    gate = SimpleNamespace(error_response=lambda request, code: JSONResponse({'error': '请先认证'}, status_code=code))
    routes = v3_archive_api.build(gate, service=archive) + v3_deals_api.build(gate, service=deals)
    routes += v3_deal_workflow_api.build(gate, offers_service=offers, demands_service=demands, followups_service=followups)
    app = Starlette(routes=routes)
    async def host(request, call_next):
        request.state.password_principal = SimpleNamespace(username='fictional-owner')
        request.state.password_recheck = lambda: True
        request.state.session = SimpleNamespace(authenticated=True)
        if request.method == 'POST':
            form = parse_qs((await request.body()).decode('utf8'), keep_blank_values=True)
            if form.get('csrf') != ['fictional-csrf']:
                return JSONResponse({'error': '表单校验失败，请重新操作'}, status_code=403)
            request.state.form = form
        return await call_next(request)
    app.add_middleware(BaseHTTPMiddleware, dispatch=host)
    with TestClient(app) as http:
        yield SimpleNamespace(http=http, store=store, deals=deals, archive=archive, demands=demands,
            offers=offers, followups=followups, runner=runner, lang=lang, translations=translations,
            conversations=conversations, now=now)


def post(env, path, **fields):
    response = env.http.post('/api/v1/' + path, data=dict(csrf='fictional-csrf', **fields))
    assert response.status_code == 200, response.text
    assert response.headers['cache-control'] == 'no-store'
    return response.json()


def get(env, path):
    response = env.http.get('/api/v1/' + path)
    assert response.status_code == 200, response.text
    assert response.headers['x-content-type-options'] == 'nosniff'
    return response.json()


def conversation(env, party, suffix, *, text, role='buyer'):
    key = make_key('conversation', 'alibaba', 'fictional-account', suffix)
    env.conversations[key] = dict(key=key, channel='alibaba', channel_label='阿里国际站',
        buyer_name=party, messages=[dict(role=role, text=text, sent_at=NOW - 72 * 3600,
                                        message_id='fictional-message-' + suffix)])
    return key


@pytest.mark.parametrize('inquiry,quantity', INQUIRIES)
def test_inquiry_confirm_compare_two_suppliers_and_native_followup_over_http(integrated, inquiry, quantity):
    e = integrated
    customer = post(e, 'archive/parties', kind='customer', name='虚构买家 CUS-101')['id']
    suppliers = [post(e, 'archive/parties', kind='supplier', name='虚构工厂' + name)['id']
                 for name in ('甲 SUP-201', '乙 SUP-202')]
    source = conversation(e, '虚构买家 CUS-101', 'customer', text=inquiry)
    supplier_keys = [conversation(e, '虚构供应方 SUP-' + str(201 + i), 'supplier-' + str(i),
                                 text='请确认报价、包装和交期', role='seller') for i in range(2)]
    did = post(e, 'deals', title='虚构多供应商案子', customer_party_id=str(customer), conversation_key=source)['id']
    for supplier, key in zip(suppliers, supplier_keys):
        post(e, f'deals/{did}/conversations', party_id=str(supplier), conversation_key=key)
    assert get(e, f'deals/{did}/demands')['items'] == []
    assert get(e, f'deals/{did}')['items'] == []
    scanned = post(e, f'deals/{did}/demands/scan')
    assert scanned['added'] == 1
    candidate = scanned['items'][0]
    assert candidate['qty'] == quantity and candidate['source']['text'] == inquiry
    assert get(e, f'deals/{did}')['items'] == []
    fields = {key: str(candidate[key] or '') for key in ('name', 'qty', 'destination', 'specifications', 'accessories')}
    accepted = post(e, f'deals/{did}/demands/{candidate["id"]}/accept', **fields)
    repeated = post(e, f'deals/{did}/demands/{candidate["id"]}/accept', **fields)
    assert repeated['reused'] and repeated['accepted_item_id'] == accepted['accepted_item_id']
    item = get(e, f'deals/{did}')['items'][0]
    assert item['qty'] == quantity and item['product_id'] is None and item['target_price'] is None
    for label in ('目的地：', '规格：220V', '配套要求：'):
        assert label in item['note']
    offer_ids = []
    for supplier, price in zip(suppliers, ('100.00', '90.00')):
        product = post(e, 'archive/products', supplier_id=str(supplier), name='虚构LX-100摇头灯', model='LX-100',
                       wattage='100', units_per_carton='2')['id']
        offer_ids.append(post(e, f'archive/products/{product}/offers', price=price, currency='CNY', unit='台',
            quoted_on='2033-05-01', valid_until='2033-06-01', moq_units='12', lead_days_min='7',
            lead_days_max='10', source_ref='fictional-supplier-offer', includes_flightcase='0')['id'])
    comparison = get(e, f'deals/{did}/offers')
    assert comparison['customer']['codes'] == ['CUS101']
    options = comparison['items'][0]['options']
    assert [o['supplier']['codes'] for o in options] == [['SUP201'], ['SUP202']]
    assert [o['price'] for o in options] == ['100.00', '90.00']
    assert all(o['requires_spec_confirmation'] for o in options)
    choose_path = f'/api/v1/deals/{did}/items/{item["id"]}/offer'
    assert e.http.post(choose_path, data=dict(csrf='fictional-csrf', offer_id=str(offer_ids[1]), reason='虚构较优交期')).status_code == 400
    chosen = post(e, f'deals/{did}/items/{item["id"]}/offer', offer_id=str(offer_ids[1]),
                  reason='已人工核对虚构规格与包装，选择交期可满足的方案', confirm_specs='1')
    assert chosen['offer_id'] == offer_ids[1]
    selected = get(e, f'deals/{did}/offers')['items'][0]['selection']
    assert selected['offer_id'] == offer_ids[1] and selected['reason'].startswith('已人工核对')
    waiting = post(e, f'deals/{did}/waiting', party_id=str(suppliers[1]), conversation_key=supplier_keys[1],
                   title='核对本案包装与交期', since=str(NOW - 72 * 3600), item_id=str(item['id']))['id']
    post(e, f'deals/{did}/items', name='FICTIONAL_OTHER_PRODUCT_NEVER_INCLUDE', qty='999')
    post(e, f'deals/{did}/update', notes='FICTIONAL_INTERNAL_COST_NEVER_INCLUDE')
    second = conversation(e, '同一虚构供应方的另一会话', 'supplier-other-topic', text='另一件事已经办好')
    post(e, f'deals/{did}/conversations', party_id=str(suppliers[1]), conversation_key=second)
    before = get(e, f'deals/{did}/waiting')['items'][0]
    assert before['waited_hours'] == 72
    e.lang.write('contact', dict(conversation_key=supplier_keys[1], native_language='es', locked='1'), 'fictional-owner')
    draft = post(e, f'deals/{did}/waiting/{waiting}/draft', supplement='请核对本案包装')
    assert draft['draft_only'] and draft['native_language'] == 'es'
    assert draft['text'] == 'Confirme el embalaje.' and draft['back_translation'] == '请确认包装。'
    assert draft['validation']['ok'] and draft['contact']['locked']
    prompt = e.runner.call_args.args[0]
    assert str(quantity) in prompt and candidate['name'] in prompt
    for field in ('destination', 'specifications', 'accessories'):
        assert candidate[field] in prompt
    assert 'FICTIONAL_OTHER_PRODUCT_NEVER_INCLUDE' not in prompt
    assert 'FICTIONAL_INTERNAL_COST_NEVER_INCLUDE' not in prompt
    assert e.runner.call_count == 1 and len(e.translations) == 2
    assert get(e, f'deals/{did}/waiting')['items'][0]['last_reminded_at'] is None
    post(e, f'deals/{did}/waiting/{waiting}/remind', request_id='fictional-manual-reminder')
    e.now[0] += 3600
    after = get(e, f'deals/{did}/waiting')['items'][0]
    assert after['since'] == before['since'] and after['waited_hours'] == 73
    assert after['last_reminded_at'] == NOW
    general = post(e, f'deals/{did}/waiting', party_id=str(suppliers[1]), conversation_key=supplier_keys[1],
                   title='核对虚构资质资料')['id']
    post(e, f'deals/{did}/waiting/{general}/draft')
    assert candidate['name'] not in e.runner.call_args.args[0]
    assert 'FICTIONAL_OTHER_PRODUCT_NEVER_INCLUDE' not in e.runner.call_args.args[0]
    with e.store.read() as db:
        assert db.execute('SELECT max(version) FROM schema_migrations').fetchone()[0] == 9
        assert db.execute('SELECT count(*) FROM deal_items WHERE deal_id=?', (did,)).fetchone()[0] == 2
        assert db.execute("SELECT count(*) FROM sqlite_master WHERE name LIKE '%outbox%'").fetchone()[0] == 0


def test_host_csrf_stops_entire_workflow_before_candidate_writes(integrated):
    e = integrated
    response = e.http.post('/api/v1/deals/1/demands/scan', data={})
    assert response.status_code == 403 and '表单校验失败' in response.json()['error']
    assert not e.demands.store.path.exists()
    assert not e.offers.path.exists() and not e.followups.store.path.exists()
