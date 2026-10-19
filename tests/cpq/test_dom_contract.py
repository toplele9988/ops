from copy import deepcopy
from html.parser import HTMLParser
import json
import re

VOID = {'area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr'}
PRIVATE = re.compile(r'(?:^|[_-])(cost|exw|supplier|offer|margin)(?:[_-]|$)', re.I)
# These are structural identifiers explicitly required by the DOM contract.
ID_ALLOWLIST = {'cpq-margin','cpq-margin-value','cpq-pricing-mode','cpq-cost-chart','cpq-cost-basis'}
PUBLIC = {'model_id','internal_sku','product_name','ip_rating','pixel_segments','power_w','voltage','net_weight_kg','certifications','dmx_modes','control_protocols','light_source','dimensions_cm','carton','flight_case','spec_completeness','images'}

class Node:
    def __init__(self, tag, attrs, parent=None):
        self.tag, self.attrs, self.parent = tag, dict(attrs), parent
        self.text, self.direct = '', []

class Doc(HTMLParser):
    def __init__(self, text):
        super().__init__(convert_charrefs=True)
        self.nodes = []
        self.stack = [Node('document', [])]
        self.feed(text)
    def handle_starttag(self, tag, attrs):
        node = Node(tag, attrs, self.stack[-1])
        self.nodes.append(node)
        if tag not in VOID:
            self.stack.append(node)
    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID:
            self.handle_endtag(tag)
    def handle_endtag(self, tag):
        for i in range(len(self.stack)-1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                break
    def handle_data(self, data):
        self.stack[-1].direct.append(data)
        for node in self.stack:
            node.text += data
    def select(self, selector):
        parts = selector.split()
        def matches(node, part):
            tag = re.match(r'^[a-z][a-z0-9-]*', part)
            if tag and tag.group() != node.tag:
                return False
            for value in re.findall(r'#([\w-]+)', part):
                if node.attrs.get('id') != value:
                    return False
            for value in re.findall(r'\.([\w-]+)', part):
                if value not in node.attrs.get('class', '').split():
                    return False
            for expression in re.findall(r'\[([^]]+)\]', part):
                key, sep, value = expression.partition('=')
                if key not in node.attrs or (sep and node.attrs[key] != value.strip('\"\'')):
                    return False
            return True
        found = []
        for node in self.nodes:
            if not matches(node, parts[-1]):
                continue
            parent, valid = node.parent, True
            for part in reversed(parts[:-1]):
                while parent is not None and not matches(parent, part):
                    parent = parent.parent
                if parent is None:
                    valid = False
                    break
                parent = parent.parent
            if valid:
                found.append(node)
        return found

def page(viewer):
    response = viewer.get('/strobes')
    assert response.status_code == 200, response.text
    return response.text, Doc(response.text)

def test_static_dom_contract(viewer):
    _, doc = page(viewer)
    selectors = [
        '#sg-root', '#sg-data', '#sg-search', '#sg-filters [data-filter][data-value]', '#sg-sort',
        '#sg-view-toggle [data-view=grid]', '#sg-view-toggle [data-view=table]',
        '#sg-grid .sg-card[data-model-id]', '.sg-card .sg-img[data-asset-uid]', '.sg-add[data-model-id]',
        '#sg-table tbody tr[data-model-id]', '#sg-lightbox', '#sg-lb-stage img', '#sg-lb-thumbs', '#sg-lb-counter', '#sg-lb-specs',
        '#cpq-drawer', '#cpq-lines [data-line-model-id]', '.cpq-qty', '.cpq-pack [data-mode=carton]', '.cpq-pack [data-mode=flightcase]',
        '#cpq-mode [data-mode=sea_lcl]', '#cpq-mode [data-mode=sea_fcl]', '#cpq-mode [data-mode=express]',
        '#cpq-incoterm [data-incoterm=FOB]', '#cpq-incoterm [data-incoterm=CIF]', '#cpq-incoterm [data-incoterm=DAP]', '#cpq-incoterm [data-incoterm=DDP]',
        '#cpq-lane', '#cpq-margin[type=range]', '#cpq-margin-value', '#cpq-pricing-mode', '#cpq-result', '#cpq-cost-chart', '#cpq-freight-ratio', '#cpq-verify',
        '#cpq-alerts', '#cpq-ack', '#cpq-ack input[data-ack-code]', '#cpq-override-reason', '#cpq-cost-basis[data-masked=true]',
    ]
    for selector in selectors:
        assert doc.select(selector), selector
    for action in ('save','pdf','whatsapp','zip','letter'):
        nodes = doc.select(f'#cpq-actions [data-action={action}]')
        assert len(nodes) == 1
        assert 'disabled' in nodes[0].attrs and nodes[0].attrs.get('title')
    assert doc.select('#sg-root')[0].attrs['data-view'] == 'grid'
    dialog = doc.select('#sg-lightbox')[0]
    assert dialog.attrs['role'] == 'dialog' and dialog.attrs['aria-modal'] == 'true'
    slider = doc.select('#cpq-margin')[0]
    assert (slider.attrs['min'], slider.attrs['max'], slider.attrs['step']) == ('-10','60','0.5')
    ids = [n.attrs['id'] for n in doc.nodes if 'id' in n.attrs]
    assert len(ids) == len(set(ids))

def test_gallery_matches_api(viewer):
    _, doc = page(viewer)
    response = viewer.get('/api/strobes')
    assert response.status_code == 200
    api = response.json()
    data_node = doc.select('#sg-data')[0]
    assert data_node.tag == 'script' and data_node.attrs['type'] == 'application/json'
    data = json.loads(data_node.text)
    cards = doc.select('#sg-grid .sg-card[data-model-id]')
    rows = doc.select('#sg-table tbody tr[data-model-id]')
    assert len(cards) == len(rows) == len(data) == api['count'] == len(api['items'])
    by_id = {i['model_id']: i for i in api['items']}
    assert {n.attrs['data-model-id'] for n in cards} == set(by_id)
    assert {n.attrs['data-model-id'] for n in rows} == set(by_id)
    for item in data:
        assert set(item) == PUBLIC
        for key, value in item.items():
            assert value == by_id[item['model_id']][key], key
    for card in cards:
        a = card.attrs
        item = by_id[a['data-model-id']]
        assert a['data-ip'] == (item['ip_rating'] or '')
        dual = item['light_source']['dual']
        assert a['data-dual'] == ('' if dual is None else str(dual).lower())
        for attribute, field in [('data-segments','pixel_segments'),('data-power','power_w')]:
            if item[field] is None:
                assert a[attribute] == ''
            else:
                assert float(a[attribute]) == item[field]
        modes = [v for v in (item['dmx_modes'] or []) if isinstance(v, (int,float)) and v > 0]
        assert (float(a['data-dmx-max']) if a['data-dmx-max'] else None) == (max(modes) if modes else None)
        source = item['flight_case']['source']
        assert a['data-fc'] == ('' if not source else 'false' if source == 'none' else 'true')


def check_private(value):
    if isinstance(value, dict):
        for key, child in value.items():
            assert not PRIVATE.search(key), key
            assert 'factory_model' not in key
            check_private(child)
    elif isinstance(value, list):
        for child in value:
            check_private(child)

def test_no_private_catalog_fields(viewer):
    html, doc = page(viewer)
    assert '广州凤舞灯光有限公司' not in html and '凤舞' not in html
    for node in doc.nodes:
        if node.tag == 'script' and node.attrs.get('type') == 'application/json':
            check_private(json.loads(node.text))
        for key, value in node.attrs.items():
            # data-* names are the declared DOM interface, not serialized business keys.
            if key.startswith('data-'):
                continue
            assert not PRIVATE.search(key), key
            if key in ('id','name') and value not in ID_ALLOWLIST:
                assert not PRIVATE.search(value or ''), value
        if any('毛利' in text for text in node.direct):
            ancestor = node
            while ancestor and ancestor.attrs.get('id') != 'cpq-drawer':
                ancestor = ancestor.parent
            assert ancestor is not None
    assert doc.select('#cpq-cost-basis')[0].text.strip() == '●●●●●'


def test_public_projection_excludes_nested_private_fields(viewer, app, monkeypatch):
    repository = app.state.strobe_repository
    items = deepcopy(repository.list_strobes())
    assert items
    items[0]['raw_cost_cny'] = 'PRIVATE_SENTINEL'
    items[0]['supplier_name'] = 'PRIVATE_SENTINEL'
    items[0]['light_source']['supplier'] = 'PRIVATE_SENTINEL'
    items[0]['carton']['cost'] = 'PRIVATE_SENTINEL'
    monkeypatch.setattr(repository, 'list_strobes', lambda: items)
    html, doc = page(viewer)
    assert 'PRIVATE_SENTINEL' not in html
    check_private(json.loads(doc.select('#sg-data')[0].text))


def test_page_requires_authentication(anon):
    response = anon.get('/strobes')
    assert response.status_code == 401
    assert response.json()['error']['code'] == 'UNAUTHENTICATED'
