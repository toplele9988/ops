"""D1b quality regressions. Every message, price and model reply is invented."""
from datetime import datetime, timezone

import pytest

from console.extract import ai, rules
from console.inbox.model import make_key


NOW = datetime(2033, 4, 6, 8, tzinfo=timezone.utc).timestamp()


def prepare(*texts, party_kind='supplier'):
    conv = dict(key=make_key('conversation', 'wechat', 'fictional-quality', 'fictional-factory'),
                messages=[dict(role='buyer', text=text, sent_at=NOW - len(texts) + i)
                          for i, text in enumerate(texts)])
    return rules.prepare(conv, NOW, timezone='UTC', party_kind=party_kind)


FORMULAS = [
    '40*120+200+5*60=5300元',
    '18×90+380=2000元',
    '12*300+500+260元',
]


@pytest.mark.parametrize('text', FORMULAS)
def test_formula_requires_explicit_ai_bundle_classification(text):
    prepared = prepare(text)
    candidate, = prepared['candidates']
    assert candidate['is_bundle'] and candidate['product_text'] is None
    assert candidate['confidence'] <= .2
    body = f"消息：1\n类型：整单报价参考\n价格：{candidate['price_fen'] / 100:.2f}\n币种：CNY\n单位：无"
    item, = ai.parse(dict(text=body), prepared)
    assert item['source'] == 'ai' and item['is_bundle'] and item['product_text'] is None
    assert item['confidence'] <= .2
    with pytest.raises(ai.AIParseError, match='没有给出可用的块'):
        ai.parse(dict(text=body.replace('整单报价参考', '产品报价')), prepared)


@pytest.mark.parametrize('text', [
    '增加0.8米，增加12块钱',
    '增加0.8米，就要12块钱',
    '600台的话可以优惠15元一台',
    '又要增加18块钱成本',
    '价格要多加260元',
    '可以便宜9元',
    '少收8元',
    '让20元',
    '降12元',
    '涨8元',
    '差30元',
])
def test_adjustments_are_not_unit_quotes(text):
    assert prepare(text)['candidates'] == []


@pytest.mark.parametrize('text', [
    '需要多少电源',
    '120元一块？',
    '这款灯210元吗',
    '灯价格220元能否少',
    '灯报价220元是不是',
    '灯报价220元，能否少？',
])
def test_questions_are_not_quotes(text):
    assert prepare(text)['candidates'] == []


@pytest.mark.parametrize('text', [
    '价格可能高一点 230元',
    '你原来的 240元',
    '这款 250元',
    '0.8米，就要260元',
])
def test_meaningless_product_fragments_are_empty_and_low(text):
    item, = prepare(text)['candidates']
    assert item['product_text'] is None and item['confidence'] <= .2


@pytest.mark.parametrize('text', [
    '4.5米x6米，80块虚构地板砖',
    '24W虚构灯，30块灯板',
    '50cm×70cm虚构箱，20个',
    '6mm虚构线，40件',
    '24V虚构电源，50台',
    '3kg虚构箱，60块',
    '报价3.5米虚构线，数量40件',
    '报价24寸虚构屏，数量50台',
    '报价60瓦虚构灯，数量80个',
])
def test_specifications_and_counts_are_not_prices(text):
    assert prepare(text)['candidates'] == []


@pytest.mark.parametrize('text', [
    '4.5米x6米虚构地板砖，单价80块',
    '24W虚构灯30块钱/台',
    '6mm虚构线报价40元/件',
])
def test_explicit_money_allows_price_next_to_specification(text):
    item, = prepare(text)['candidates']
    assert item['currency'] == 'CNY' and item['product_text']
    assert .2 < item['confidence'] <= .5


def test_product_example_preserves_terms_without_inventing_unit():
    item, = prepare('200W 追光灯 单价550元含税，交期7天，本周有效')['candidates']
    assert item['product_text'] == '200W 追光灯'
    assert item['price_fen'] == 55000 and item['currency'] == 'CNY'
    assert item['unit'] is None and item['lead_time_text'] == '交期7天'
    assert item['tax_text'] == '含税' and item['suggested_valid_until'] == '2033-04-10'


def test_freight_example_preserves_route_minimum_and_expiry():
    item, = prepare('空运 45 元/公斤，最低收费 500，到美国，10天内有效', party_kind='forwarder')['candidates']
    assert item['kind'] == 'freight' and item['mode'] == 'air'
    assert item['price_fen'] == 4500 and item['currency'] == 'CNY' and item['unit'] == 'kg'
    assert item['min_charge_fen'] == 50000 and item['destination_country'] == '美国'
    assert item['suggested_valid_until'] == '2033-04-16'


def test_baseline_quote_survives_separate_adjustment():
    items = prepare('虚构甲灯原价100元，优惠10元一台')['candidates']
    assert [item['price_fen'] for item in items] == [10000]
    assert prepare('虚构甲灯优惠后单价90元/台')['candidates'][0]['price_fen'] == 9000


def test_price_label_equal_sign_is_not_a_bundle():
    item, = prepare('虚构甲灯 单价=130元/台')['candidates']
    assert not item['is_bundle'] and item['price_fen'] == 13000


@pytest.mark.parametrize('explicit', ['虚构甲灯100元/台', '虚构甲灯RMB100/台', '虚构甲灯100块/台'])
def test_rule_currency_guess_is_local_cny_with_penalty(explicit):
    prepared = prepare(explicit, '虚构乙灯 单价120/台')
    first, second = prepared['candidates']
    assert not first['currency_guess']
    assert second['currency'] == 'CNY' and second['currency_guess']
    isolated = prepare('虚构乙灯 单价120/台')['candidates'][0]
    assert second['confidence'] == pytest.approx(isolated['confidence'] - .1)


def test_mixed_currencies_do_not_establish_a_cny_default():
    items = prepare('虚构甲灯100元/台', '虚构乙灯USD20/台', '虚构丙灯 单价120/台')['candidates']
    assert items[2]['currency'] is None and not items[2]['currency_guess']


def test_far_away_cny_price_does_not_supply_a_window_guess():
    prepared = prepare('虚构甲灯100元/台', '已收', '谢谢', '明天确认', '虚构乙灯 单价120/台')
    assert prepared['candidates'][1]['currency'] is None


@pytest.mark.parametrize('missing', ['产品', '币种', '单位'])
def test_ai_missing_fields_remain_empty_and_confidence_is_capped(missing):
    prepared = prepare('虚构甲灯100元/台')
    fields = {'消息': '1', '类型': '产品报价', '价格': '100', '产品': '虚构甲灯', '币种': 'CNY', '单位': '台', '把握': '0.99'}
    del fields[missing]
    item, = ai.parse(dict(text='\n'.join(f'{key}：{value}' for key, value in fields.items())), prepared)
    assert item[{'产品': 'product_text', '币种': 'currency', '单位': 'unit'}[missing]] is None
    assert item['confidence'] <= .5


@pytest.mark.parametrize('confidence', ['0.1', '0.7', '1'])
def test_complete_ai_confidence_is_in_meaningful_band(confidence):
    item, = ai.parse(dict(text='消息：1\n类型：产品报价\n价格：100\n产品：虚构甲灯\n币种：CNY\n单位：台\n把握：' + confidence),
                     prepare('虚构甲灯100元/台'))
    assert .7 <= item['confidence'] <= .9


def test_ai_can_name_grounded_product_outside_rule_vocabulary():
    prepared = prepare('虚构晶盘100元/台')
    assert prepared['candidates'][0]['product_text'] is None
    item, = ai.parse(dict(text='消息：1\n类型：产品报价\n价格：100\n产品：虚构晶盘\n币种：CNY\n单位：台'), prepared)
    assert item['product_text'] == '虚构晶盘' and item['confidence'] >= .7


def test_ai_cannot_promote_filtered_noise():
    for text in ('虚构灯能优惠10元吗', '4米x6米，70块地板砖'):
        prepared = prepare(text)
        with pytest.raises(ValueError):
            ai.parse(dict(text='消息：1\n类型：产品报价\n价格：10\n产品：虚构灯'), prepared)


def test_ai_no_quote_has_no_rule_backfill():
    assert ai.parse(dict(text='无报价'), prepare('虚构甲灯100元/台')) == []


def test_same_price_different_products_are_distinct_and_grounded():
    prepared = prepare('虚构甲灯100元/台；虚构乙灯100元/台')
    assert len(prepared['candidates']) == 2
    result = ai.parse(dict(text='消息：1\n类型：产品报价\n价格：100\n产品：虚构甲灯\n---\n消息：1\n类型：产品报价\n价格：100\n产品：虚构乙灯'), prepared)
    assert [item['product_text'] for item in result] == ['虚构甲灯', '虚构乙灯']
