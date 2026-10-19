'''DTO tests use supplied public product data and explicit quotation test vectors.'''
from copy import deepcopy
from dataclasses import FrozenInstanceError, asdict
import inspect
import json

import pytest

from console.cpq import dto as dto_module
from console.cpq.dto import CustomerQuoteDTO, build_customer_dto, dataclass_field_tree, round2


EXPECTED_DTO_FIELDS = {
    'quote_no': None,
    'issue_date': None,
    'valid_until': None,
    'currency': None,
    'seller': {
        'company': None, 'address': None, 'contact': None,
        'email': None, 'whatsapp': None, 'website': None,
    },
    'buyer': {'company': None, 'contact': None, 'country': None},
    'incoterm': None,
    'named_place': None,
    'shipping_mode_label': None,
    'lines': [{
        'line_no': None,
        'sku': None,
        'product_name': None,
        'highlights': None,
        'specs': [{'label': None, 'value': None}],
        'packing': {
            'type': None,
            'units_per_pkg': None,
            'pkg_size_cm': None,
            'pkg_gw_kg': None,
            'packages': None,
            'cbm': None,
            'gw_kg': None,
        },
        'accessories_included': None,
        'image_refs': None,
        'qty': None,
        'unit_price': None,
        'amount': None,
        'lead_time': None,
    }],
    'totals': {'total_amount': None, 'packages': None, 'cbm': None, 'gw_kg': None},
    'terms': {
        'payment': None, 'lead_time': None, 'warranty': None,
        'price_includes': None, 'validity': None,
    },
}

# Public fields copied from the FIRST real item in the supplied
# sample_strobes.json. Keeping this literal makes the test independent of
# where the dispatcher stores its attachments. Unknown measurements stay unknown.
SAMPLE_PUBLIC_ITEM = {
    'ip_rating': None,
    'pixel_segments': None,
    'dmx_modes': [6, 16, 42],
    'control_protocols': ['DMX512', 'RDM', 'Auto', 'Master/Slave'],
    'power_w': 500.0,
    'voltage': None,
    'net_weight_kg': None,
    'dimensions_cm': {'l': None, 'w': None, 'h': None},
    'carton': {
        'source': 'none', 'units_per_pkg': None,
        'l_cm': None, 'w_cm': None, 'h_cm': None, 'gw_kg': None,
    },
    'flight_case': {
        'source': 'none', 'units_per_pkg': None,
        'l_cm': None, 'w_cm': None, 'h_cm': None, 'gw_kg': None,
    },
    'certifications': None,
    'internal_sku': 'XL-STB-001',
    'product_name': '500W Strobe',
    'light_source': {
        'rgb_count': 768, 'white_count': None, 'led_type': None,
        'dual': None, 'label': '768 RGB',
    },
}
SAMPLE_IMAGE = {
    'asset_uid': 'a_0de1cd16fa7d694a',
    'role': 'main',
    'customer_safe': True,
    'thumb_url': '/media/a/a_0de1cd16fa7d694a?w=640',
    'large_url': '/media/a/a_0de1cd16fa7d694a?w=1280',
    'orig_url': '/media/a/a_0de1cd16fa7d694a',
}


def _snapshot():
    # Quote values are test vectors from the specification, not an offer for
    # the real sample product. In particular, no missing packing is invented.
    return {
        'quote_no': 'Q261019-001',
        'issue_date': '2026-10-19',
        'valid_until': '2026-11-03',
        'seller': {'company': '', 'address': '', 'contact': '', 'email': '', 'whatsapp': '', 'website': ''},
        'buyer': {'company': '', 'contact': '', 'country': ''},
        'shipping': {'mode': 'sea_lcl', 'incoterm': 'DDP', 'named_place': 'Hamburg, Germany'},
        'terms': {'payment': '', 'lead_time': '', 'warranty': '', 'price_includes': '', 'validity': ''},
        'lines': [{
            'item': deepcopy(SAMPLE_PUBLIC_ITEM),
            'qty': 40,
            'packing_mode': 'carton',
            'accessories': [
                {'name_en': 'Quick-release clamp', 'qty_per_unit': 2},
                {'name_en': 'Safety cable', 'qty_per_unit': 1},
            ],
            'lead_time': '15–20 days',
            'result': {'packages': 20, 'cbm': 7.68, 'gw_kg': 780.0, 'unit_price_usd': 186.5, 'amount_usd': 7460.0},
            'image_uids': [SAMPLE_IMAGE['asset_uid']],
        }],
        'totals': {'price_usd': 7460.0, 'packages': 20, 'cbm': 7.68, 'gw_kg': 780.0},
    }


def test_customer_dto_whitelist():
    assert dataclass_field_tree(CustomerQuoteDTO) == EXPECTED_DTO_FIELDS
    snapshot = _snapshot()
    customer = build_customer_dto(snapshot)
    nodes = (
        customer, customer.seller, customer.buyer, customer.lines[0],
        customer.lines[0].packing, customer.lines[0].specs[0], customer.totals, customer.terms,
    )
    for node in nodes:
        assert type(node).__dataclass_params__.frozen
        assert not hasattr(node, '__dict__')
    with pytest.raises(FrozenInstanceError):
        customer.currency = 'EUR'
    snapshot['lines'][0]['item']['internal_sku'] = 'CHANGED'
    snapshot['lines'][0]['image_uids'].clear()
    assert customer.lines[0].sku == SAMPLE_PUBLIC_ITEM['internal_sku']
    assert customer.lines[0].image_refs == (SAMPLE_IMAGE['asset_uid'],)
    assert customer.lines[0].accessories_included == ('2 × Quick-release clamp', '1 × Safety cable')
    for mode, label in (('sea_lcl', 'Sea freight (LCL)'), ('sea_fcl', 'Sea freight (FCL)'), ('express', 'Express courier')):
        snapshot = _snapshot()
        snapshot['shipping']['mode'] = mode
        assert build_customer_dto(snapshot).shipping_mode_label == label
    for mode, label in (('carton', 'Carton'), ('flight_case', 'Flight case')):
        snapshot = _snapshot()
        snapshot['lines'][0]['packing_mode'] = mode
        assert build_customer_dto(snapshot).lines[0].packing.type == label


def test_build_dto_is_explicit():
    source = inspect.getsource(dto_module)
    for forbidden in ('asdict(', '**item', '**line', '**snapshot', '__dict__', 'vars('):
        assert forbidden not in source


def test_dto_generates_english_highlights():
    snapshot = _snapshot()
    customer = build_customer_dto(snapshot)
    assert customer == build_customer_dto(snapshot)
    line = customer.lines[0]
    assert line.highlights == ('768 RGB light source',)
    assert any(spec.label == 'DMX modes' and spec.value == '6 / 16 / 42 ch' for spec in line.specs)
    for highlight in line.highlights:
        assert highlight.isascii()
        assert 'None' not in highlight
        assert '0 ' not in highlight
        assert 'null' not in highlight.lower()
    # These additional values are the specification's example, not asserted
    # facts about the sample product.
    example = _snapshot()
    example['lines'][0]['item']['light_source']['white_count'] = 288
    example['lines'][0]['item']['pixel_segments'] = 48
    example['lines'][0]['item']['ip_rating'] = 'IP66'
    assert build_customer_dto(example).lines[0].highlights == (
        '768 RGB + 288 White dual light source', '48-segment pixel control', 'IP66',
    )


def test_dto_hides_unknowns():
    snapshot = _snapshot()
    item = snapshot['lines'][0]['item']
    item['light_source'] = {'rgb_count': 0, 'white_count': None, 'led_type': 'null', 'dual': None, 'label': 'None'}
    item['pixel_segments'] = 0
    item['dmx_modes'] = [0, None, 'null']
    item['control_protocols'] = [None, 'null', '']
    item['power_w'] = 0
    item['ip_rating'] = None
    item['voltage'] = 'null'
    item['net_weight_kg'] = 0
    item['certifications'] = None
    line = build_customer_dto(snapshot).lines[0]
    assert line.highlights == ()
    assert line.specs == ()
    assert line.packing.units_per_pkg is None
    assert line.packing.pkg_size_cm == ''
    assert line.packing.pkg_gw_kg is None
    assert line.qty == 40
    for field in ('unit_price_usd', 'amount_usd', 'gw_kg', 'packages'):
        incomplete = _snapshot()
        incomplete['lines'][0]['result'][field] = None
        with pytest.raises(ValueError):
            build_customer_dto(incomplete)


def test_dto_rounding_half_up():
    # Synthetic rounding vectors; these are not commercial prices or weights.
    snapshot = _snapshot()
    row = snapshot['lines'][0]
    row['result']['unit_price_usd'] = '186.505'
    row['result']['amount_usd'] = '7460.005'
    row['result']['gw_kg'] = '780.005'
    row['item']['carton']['gw_kg'] = '39.005'
    row['item']['net_weight_kg'] = '1.005'
    snapshot['totals']['price_usd'] = '7460.005'
    snapshot['totals']['gw_kg'] = '780.005'
    customer = build_customer_dto(snapshot)
    line = customer.lines[0]
    assert line.unit_price == 186.51
    assert line.amount == 7460.01
    assert line.packing.pkg_gw_kg == 39.01
    assert line.packing.gw_kg == 780.01
    assert customer.totals.total_amount == 7460.01
    assert customer.totals.gw_kg == 780.01
    assert any(spec.label == 'Net weight' and spec.value == '1.01 kg' for spec in line.specs)
    assert round2(1.005) == 1.01
    assert round2(-1.005) == -1.01


def test_dto_has_no_media_urls():
    snapshot = _snapshot()
    item = snapshot['lines'][0]['item']
    item['images'] = [deepcopy(SAMPLE_IMAGE)]
    item['thumb_url'] = SAMPLE_IMAGE['thumb_url']
    item['specs_full'] = [{'label': 'legacy', 'value': SAMPLE_IMAGE['orig_url']}]
    item['model'] = 'UNTRUSTED-FACTORY-MODEL'
    item['supplier_name'] = 'UNTRUSTED-SUPPLIER'
    customer = build_customer_dto(snapshot)
    serialized = json.dumps(asdict(customer), ensure_ascii=False, allow_nan=False)
    assert '/media/' not in serialized
    assert 'UNTRUSTED-' not in serialized
    assert customer.lines[0].image_refs == (SAMPLE_IMAGE['asset_uid'],)
    snapshot['lines'][0]['image_uids'] = []
    assert build_customer_dto(snapshot).lines[0].image_refs == ()
    snapshot['lines'][0]['image_uids'] = [SAMPLE_IMAGE['orig_url']]
    with pytest.raises(ValueError):
        build_customer_dto(snapshot)
