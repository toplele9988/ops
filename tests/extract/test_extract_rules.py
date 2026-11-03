"""All messages and product identities below are fictional; no I/O dependencies."""
from datetime import datetime, timezone

import pytest

from console.extract import ai, rules
from console.inbox.ai import parse_output
from console.inbox.model import make_key
from console.roles.common import message_ref


NOW = datetime(2030, 1, 9, 8, tzinfo=timezone.utc).timestamp()


def conversation(text, *, role='buyer', sent_at=NOW):
    return dict(key=make_key('conversation', 'wechat', 'fictional-account', 'fictional-group'),
                channel='wechat', buyer_name='SUP99虚构工厂', counterpart_role='supplier',
                messages=[dict(role=role, text=text, sent_at=sent_at)])


def extract(text, **kwargs):
    return rules.extract(conversation(text), NOW, timezone='UTC', **kwargs)


@pytest.mark.parametrize('text', [
    '报价1280元/台', 'CNY 35', 'RMB 36/pcs', 'USD 12.50', '12美金/件', '¥13/套', '$15/kg',
    '空运45一公斤', '海运120一方', '最低收费500元', '运费100', '10pcs 含税报价待确认',
])
def test_prefilter_chinese_and_english(text):
    assert rules.has_price_clue(text)


@pytest.mark.parametrize('text', ['你好', '已收，稍后回复', '[图片]', '[PDF]', '请报价', '型号ZX300',
                                       'https://fictional.invalid/price/500', '电话13800138000'])
def test_prefilter_has_no_unprotected_price_clue(text):
    assert not rules.has_price_clue(text)


@pytest.mark.parametrize(('text', 'price', 'currency', 'unit'), [
    ('单价1280元/台', 128000, 'CNY', '台'),
    ('虚构灯 USD 12.50/pcs', 1250, 'USD', 'pcs'),
    ('虚构灯 RMB 1,234.50 per piece', 123450, 'CNY', 'pcs'),
    ('虚构配件 99块/件', 9900, 'CNY', '件'),
    ('含税 1280，交期7天', 128000, None, None),
    ('报价20元/桶', 2000, 'CNY', None),
    ('USD 1,234.50 per kg', 123450, 'USD', 'kg'),
])
def test_money_unit_only_when_explicit(text, price, currency, unit):
    item, = extract(text)
    assert (item['price_fen'], item['currency'], item['unit']) == (price, currency, unit)
    assert item['source'] == 'rules' and item['confidence'] < .5


@pytest.mark.parametrize('text', ['报价 300W', '报价10天内有效', '空运 30kg，价格待定', 'USD12.345',
    '报价12.345元/台', '-10元/台', '-USD10/pcs', '100-200元/台', 'USD10-20/pcs',
    '10pcs，交期7天', '300W 追光灯，数量10台', 'USD1e3', '含税价格20天有效'])
def test_no_specs_counts_ranges_negative_or_partial_decimal_prices(text):
    assert extract(text) == []


def test_freight_minimum_is_not_another_unit_price():
    item, = extract('空运 45 一公斤，最低 500，到美国，本周有效', party_kind='forwarder')
    assert item['kind'] == 'freight' and item['mode'] == 'air'
    assert item['unit'] == 'kg' and item['price_fen'] == 4500 and item['min_charge_fen'] == 50000
    assert item['destination_country'] == '美国'
    assert item['origin'] is None and item['currency'] is None
    assert item['suggested_valid_until'] == '2030-01-13'


def test_sea_port_does_not_invent_country():
    item, = extract('海运拼箱 120 一方，目的港 洛杉矶', party_kind='forwarder')
    assert item['mode'] == 'sea' and item['unit'] == 'cbm'
    assert item['destination_detail'] == '洛杉矶'
    assert item['destination_country'] is None


def test_product_and_shipping_cost_in_one_message_keep_their_kind():
    first, second = extract('虚构ZX-300灯 CNY1280/台，快递运费 CNY30/票')
    assert (first['kind'], first['price_fen'], first['mode']) == ('offer', 128000, None)
    assert (second['kind'], second['price_fen'], second['mode'], second['unit']) == ('freight', 3000, 'express', 'ticket')


def test_two_routes_do_not_share_country_or_terms():
    first, second = extract('到美国空运45元/kg；到英国空运50元/kg', party_kind='forwarder')
    assert first['destination_country'] == '美国' and second['destination_country'] == '英国'
    first, second = extract('虚构甲灯100元/台，本周有效；虚构乙灯120元/台')
    assert first['suggested_valid_until'] is None and second['suggested_valid_until'] is None


@pytest.mark.parametrize('minimum', ['12.345', '500-800', '-500', 'USD-500', '500~800', '30kg', '1e3'])
def test_minimum_charge_does_not_truncate_or_infer(minimum):
    item, = extract('空运45元/kg，最低收费' + minimum, party_kind='forwarder')
    assert item['min_charge_fen'] is None


def test_explicit_product_terms_and_multiple_prices():
    first, second = extract('虚构甲灯，含税100元/台；虚构乙灯，不含税120元/台；MOQ 10pcs，交期7天')
    assert [first['price_fen'], second['price_fen']] == [10000, 12000]
    assert first['tax_text'] == '含税' and second['tax_text'] == '不含税'
    assert first['moq_units'] is None and first['lead_time_text'] is None
    assert '虚构乙灯' in second['product_text']


@pytest.mark.parametrize(('text', 'expected'), [
    ('本周有效', '2030-01-13'), ('有效期：10天内', '2030-01-19'), ('10天内有效', '2030-01-19'),
    ('月底前有效', '2030-01-31'), ('本月底有效', '2030-01-31'),
    ('有效期至2030-02-03', '2030-02-03'), ('valid for 10 days', '2030-01-19'),
    ('valid until 2030-02-03', '2030-02-03'), ('本周或10天内有效', None), ('大概本周有效', None),
    ('有效期10天左右', None), ('有效至2030-02-30', None), ('有效至2029-12-31', None),
    ('不是本周有效，待确认', None), ('有效期10天，以出货日期为准', None),
    ('月底前交货', None), ('交期10天内', None), ('本周发货', None), ('月底前', None),
])
def test_expiry_is_explicit_and_based_on_message_date(text, expected):
    assert rules.validity(text, NOW, 'UTC')[1] == expected


def test_expiry_leap_month_and_timezone():
    leap = datetime(2032, 2, 20, tzinfo=timezone.utc).timestamp()
    assert rules.validity('月底前有效', leap, 'UTC')[1] == '2032-02-29'
    sunday = datetime(2030, 1, 13, 20, tzinfo=timezone.utc).timestamp()
    assert rules.validity('本周有效', sunday, 'UTC')[1] == '2030-01-13'
    assert rules.validity('本周有效', sunday, 'Asia/Shanghai')[1] == '2030-01-20'


@pytest.mark.parametrize(('role', 'text', 'allowed'), [
    ('buyer', '1280元/台', True), ('seller', '我卖1280元/台', False),
    ('seller', '你们报价1280元/台，我再确认一下', True),
    ('seller', '供应商说1280元/台', True), ('seller', 'your quote is USD12/pcs', True),
    ('unknown', '1280元/台', False),
])
def test_only_counterparty_or_explicit_restated_prices(role, text, allowed):
    result = rules.prepare(conversation(text, role=role), NOW)
    assert bool(result['candidates']) is allowed
    assert bool(result['messages']) is allowed


def test_recent_window_derived_reference_and_no_raw_key_in_prompt():
    conv = conversation('very old USD1/pcs', sent_at=NOW-31*86400)
    conv['messages'].extend([
        dict(role='buyer', text='虚构产品 ZX-300', sent_at=NOW-20),
        dict(role='buyer', text='报价30元/台', sent_at=NOW-10),
        dict(role='seller', text='给客户报价80元/台', sent_at=NOW-9),
        dict(role='buyer', text='交期7天', sent_at=NOW-8),
        dict(role='unknown', text='80元/台', sent_at=NOW-7),
        dict(role='buyer', text='future USD2/pcs', sent_at=NOW+1),
    ])
    prepared = rules.prepare(conv, NOW, window=2)
    assert [m['text'] for m in prepared['messages']] == ['虚构产品 ZX-300', '报价30元/台', '交期7天']
    item, = prepared['candidates']
    assert item['source_message_refs'] == [message_ref(conv['key'], conv['messages'][2])]
    assert item['source_message_refs'][0]['derived'] is True
    assert conv['key'] not in ai.prompt(prepared) and 'derived:' not in ai.prompt(prepared)


def test_window_and_prompt_have_hard_bounds():
    conv = conversation('')
    conv['messages'] = [dict(role='buyer', text='报价20元/台 ' + '虚构说明'*1000, sent_at=NOW-i)
                        for i in range(300, 0, -1)]
    prepared = rules.prepare(conv, NOW)
    assert len(prepared['messages']) <= 12
    assert sum(len(m['text']) for m in prepared['messages']) <= 10000
    assert len(ai.prompt(prepared)) < 13000
    assert len(prepared['candidates']) == 300


def test_attachments_are_markers_only_and_sensitive_text_is_redacted():
    conv = conversation('[PDF] 虚构 ZX-300 报价1280元/台，邮箱 fictional@example.invalid，电话+86 138 0013 8000，token=fictional-secret')
    prepared = rules.prepare(conv, NOW)
    item, = prepared['candidates']
    assert item['has_attachment'] is True and '有附件，需人工看' in item['excerpt']
    assert len(item['excerpt']) <= 200
    prompt = ai.prompt(prepared)
    for secret in ('fictional@example.invalid', '138 0013 8000', 'fictional-secret'):
        assert secret not in prompt and secret not in item['excerpt']
    assert '1280' in prompt
    assert not rules.prepare(conversation('[图片]\n[PDF]'), NOW)['messages']


PRODUCTS = [dict(id=1, name='虚构追光灯', model='ZX-300', supplier_id=7, status='confirmed')]


@pytest.mark.parametrize(('text', 'expected'), [
    ('虚构追光灯', 1), ('这款 虚构追光灯', 1), ('型号：ZX-300', 1), ('ZX-300', 1),
    ('旧虚构追光灯升级版', None), ('旧ZX-300升级版', None), ('ZX-3000', None),
    ('300W', None), ('追光灯', None), ('ZX300', None),
])
def test_product_matching_requires_unique_complete_identity(text, expected):
    assert rules.match_product(text, PRODUCTS, 7) == expected


def test_product_matching_filters_supplier_hidden_and_ambiguity():
    assert rules.match_product('ZX-300', PRODUCTS, 8) is None
    assert rules.match_product('ZX-300', [dict(PRODUCTS[0], status='hidden')], 7) is None
    assert rules.match_product('ZX-300', PRODUCTS + [dict(PRODUCTS[0], id=2)], 7) is None
    assert rules.match_product('300W', [dict(PRODUCTS[0], model='300W')], 7) is None


def parsed(body):
    return parse_output('语言：中文\n' + body + '\n=====待确认=====\n请老板核对')


def test_ai_parse_multiple_chinese_blocks_and_grounded_products():
    prepared = rules.prepare(conversation('虚构甲灯100元/台；虚构乙灯120元/台'), NOW)
    result = ai.parse(parsed('消息：1\n类型：产品报价\n价格：100\n产品：虚构甲灯\n币种：CNY\n单位：台\n把握：0.9\n---\n消息：1\n类型：产品报价\n价格：120\n产品：虚构乙灯'), prepared)
    assert [item['price_fen'] for item in result] == [10000, 12000]
    assert all(item['source'] == 'ai' for item in result)
    assert result[0]['confidence'] == .9
    assert result[0]['product_text'] == '虚构甲灯'
    assert result[1]['currency'] is None and result[1]['unit'] is None
    assert result[1]['confidence'] <= .5


@pytest.mark.parametrize('body', [
    'garbage', '消息：99\n类型：产品报价\n价格：100', '消息：1\n类型：产品报价\n价格：999',
    '消息：1\n类型：产品报价\n价格：100\n把握：NaN',
    '消息：1\n类型：产品报价\n价格：100\n有效期：2099-01-01',
])
def test_unusable_ai_blocks_are_rejected_for_service_fallback(body):
    prepared = rules.prepare(conversation('[PDF] 虚构甲灯100元/台'), NOW)
    with pytest.raises(ValueError):
        ai.parse(parsed(body), prepared)
    assert prepared['candidates'][0]['source'] == 'rules'


def test_unverified_currency_unit_and_product_are_cleared_without_dropping_block():
    prepared = rules.prepare(conversation('[PDF] 虚构甲灯100元/台'), NOW)
    item, = ai.parse(parsed('消息：1\n类型：产品报价\n价格：100\n产品：附件里的虚构产品\n币种：USD\n单位：kg'), prepared)
    assert item['currency'] is None and item['unit'] is None and item['product_text'] is None
    assert item['confidence'] <= .5


def test_duplicate_ai_field_keeps_first_value_and_records_dropped_issue():
    prepared = rules.prepare(conversation('[PDF] 虚构甲灯100元/台'), NOW)
    result, summary = ai.parse_detailed(parsed('消息：1\n类型：产品报价\n价格：100\n价格：999\n产品：虚构甲灯'), prepared)
    assert len(result) == 1 and result[0]['price_fen'] == 10000
    assert summary == dict(blocks_total=1, accepted=1, dropped_by_reason={'bad_field': 1})


def test_partial_ai_answer_does_not_backfill_rule_prices():
    prepared = rules.prepare(conversation('虚构甲灯100元/台；虚构乙灯120元/台'), NOW)
    result = ai.parse(parsed('消息：1\n类型：产品报价\n价格：100'), prepared)
    assert len(result) == 1 and result[0]['source'] == 'ai'
