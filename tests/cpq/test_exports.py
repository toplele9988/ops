from copy import deepcopy
from html.parser import HTMLParser
from io import BytesIO
import json
import re
import pytest
from pypdf import PdfReader
from starlette.applications import Starlette
from console.routes.export_routes import build_export_routes
from console.routes.strobe_routes import build_routes
FORMATS = ('customer.pdf', 'customer.html', 'whatsapp.txt', 'images.zip')

@pytest.fixture
def app(settings):
    routes = build_routes(settings)
    paths = {r.path for r in routes}
    routes.extend(r for r in build_export_routes(settings) if r.path not in paths)
    result = Starlette(routes=routes)
    result.state.strobe_api = next(r for r in routes if r.name == 'strobe_list').endpoint.__self__
    result.state.strobe_repository = result.state.strobe_api.repository
    result.state.cpq_service = next(r for r in routes if r.name == 'cpq_context').endpoint.__self__.service
    return result

@pytest.fixture
def quotation(manager, seeded, app):
    candidates = [mid for mid in seeded.model_ids if (item := app.state.cpq_service.repository.get_strobe(mid))['dmx_modes'] and item['power_w'] and item['images']]
    assert candidates, 'Real seeded data must provide DMX, Power and images'
    payload = deepcopy(seeded.payload)
    payload['lines'][0]['model_id'] = candidates[0]
    payload['target_margin'] = 0.20
    calculated = manager.post('/api/cpq/calculate', json=payload)
    assert calculated.status_code == 200, calculated.text
    # Synthetic delivery interval for this test only.
    payload.update(acks=calculated.json()['gate']['requires_ack'], payment_terms_id='TT_30_70', lead_time_days={candidates[0]: '15-20'})
    response = manager.post('/api/cpq/quotes', json=payload)
    assert response.status_code == 201, response.text
    quote = response.json()
    dto = json.loads(seeded.store.read('SELECT customer_dto_json FROM cpq_quote WHERE quote_token=?', (quote['quote_token'],))[0]['customer_dto_json'])
    return quote, dto

class Images(HTMLParser):
    def __init__(self):
        super().__init__()
        self.images, self.tags = [], []
    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        if tag == 'img':
            self.images.append(dict(attrs).get('src', ''))

def test_pdf_five_in_one(manager, quotation):
    quote, dto = quotation
    response = manager.get(f"/quotes/{quote['quote_token']}/customer.pdf")
    assert response.status_code == 200, response.text[:200]
    assert response.headers['content-disposition'] == f"attachment; filename=\"{dto['quote_no']}.pdf\""
    reader = PdfReader(BytesIO(response.content))
    text = ' '.join('\n'.join(p.extract_text() or '' for p in reader.pages).split())
    for line in dto['lines']:
        assert line['sku'] in text and line['lead_time'] in text
        assert f"USD {line['unit_price']:,.2f}" in text and f"USD {line['amount']:,.2f}" in text
    assert all(label in text for label in ('DMX', 'Power', 'CBM'))
    assert format(dto['lines'][0]['packing']['cbm'], 'g') in text
    assert f"USD {dto['totals']['total_amount']:,.2f}" in text
    assert dto['incoterm'] in text and dto['terms']['payment'] in text
    assert sum(len(p.images) for p in reader.pages) >= len(dto['lines'])
    assert re.search(r'Page\s+1\s*/\s*\d+', text)

def test_html_self_contained(manager, quotation):
    response = manager.get(f"/quotes/{quotation[0]['quote_token']}/customer.html")
    assert response.status_code == 200
    assert 'src="/' not in response.text and 'href="/' not in response.text and 'http' not in response.text.lower()
    parser = Images()
    parser.feed(response.text)
    assert parser.images and all(v.startswith('data:image/') for v in parser.images)
    assert not set(parser.tags) & {'script', 'iframe', 'object', 'embed', 'link'}

def test_whatsapp_format(manager, quotation):
    quote, dto = quotation
    response = manager.get(f"/quotes/{quote['quote_token']}/whatsapp.txt")
    assert response.status_code == 200 and response.headers['content-type'] == 'text/plain; charset=utf-8'
    text = response.text
    assert text.startswith('*Quotation ') and '(Product photos sent separately)' in text
    assert not re.search(r'<[^>]+>', text) and all(len(v) <= 4000 for v in text.splitlines())
    for i, line in enumerate(dto['lines']):
        start = text.index(f"*{line['line_no']}) {line['sku']}*")
        end = text.index(f"*{dto['lines'][i + 1]['line_no']}) ", start + 1) if i + 1 < len(dto['lines']) else text.index('*Total:', start)
        block = text[start:end]
        assert 6 <= len([v for v in block.splitlines() if v.strip()]) <= 10
        assert f"USD {line['unit_price']:,.2f}" in block and f"USD {line['amount']:,.2f}" in block

def test_export_blocked_when_gate_fails(manager, seeded):
    response = manager.post('/api/cpq/quotes', json=deepcopy(seeded.payload) | {'draft': True, 'acks': []})
    assert response.status_code == 201, response.text
    quote = response.json()
    assert quote['exports'] is None
    for fmt in FORMATS:
        result = manager.get(f"/quotes/{quote['quote_token']}/{fmt}")
        assert result.status_code == 409 and result.json()['error']['code'] == 'QUOTE_NOT_FINAL'
        for token in ('q_' + '0' * 32, 'invalid', 'q_' + 'A' * 32):
            assert manager.get(f'/quotes/{token}/{fmt}').status_code == 404

def test_exports_require_auth(anon, quotation):
    for fmt in FORMATS:
        assert anon.get(f"/quotes/{quotation[0]['quote_token']}/{fmt}").status_code == 401

def test_asset_flag_requires_admin(manager, admin, a_valid_uid, app):
    path = f'/api/cpq/assets/{a_valid_uid}/flag'
    body = {'customer_safe': False, 'reason': 'Synthetic visibility test'}
    assert manager.post(path, json=body).status_code == 403
    assert admin.post(path, json=body).status_code == 200
    asset = app.state.strobe_repository.asset_by_uid(a_valid_uid)
    store = app.state.cpq_service.store
    assert store.asset_flags()[asset.asset_id] is False
    assert any(r['action'] == 'ASSET_FLAG' and r['actor'] == 'test-admin' for r in store.audit_log())
    assert admin.post('/api/cpq/assets/a_0000000000000000/flag', json=body).status_code == 404

def test_asset_flag_validation(admin, a_valid_uid):
    path = f'/api/cpq/assets/{a_valid_uid}/flag'
    for body in ({'customer_safe': 1, 'reason': ''}, {'customer_safe': False, 'reason': 'x' * 201}, {'customer_safe': False}):
        assert admin.post(path, json=body).status_code == 400
    headers = {'content-type': 'application/json'}
    assert admin.post(path, content=b' ' * 8193, headers=headers).status_code == 413
    assert admin.post(path, content=(p for p in (b' ' * 4096, b' ' * 4097)), headers=headers).status_code == 413
