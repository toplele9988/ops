"""D1c block-level AI parsing regressions; every message and answer is invented."""
import json
from datetime import datetime, timezone
from unittest.mock import Mock

import pytest

from console.extract import ai, rules
from console.inbox.model import make_key
from test_extract_service import Env, answer


NOW = datetime(2034, 5, 6, 8, tzinfo=timezone.utc).timestamp()


def prepare(*texts, party_kind='supplier'):
    key = make_key('conversation', 'wechat', 'fictional-d1c', 'fictional-quote-window')
    conversation = dict(key=key, messages=[
        dict(role='buyer', text=text, sent_at=NOW - len(texts) + index)
        for index, text in enumerate(texts)])
    return rules.prepare(conversation, NOW, timezone='UTC', party_kind=party_kind)


def block(price, *, message=1, kind='产品报价', product='虚构投光灯', currency='CNY', unit='台', confidence='0.9'):
    values = [f'消息：{message}', f'类型：{kind}', f'价格：{price}']
    if product is not None:
        values.append(f'产品：{product}')
    if currency is not None:
        values.append(f'币种：{currency}')
    if unit is not None:
        values.append(f'单位：{unit}')
    if confidence is not None:
        values.append(f'把握：{confidence}')
    return '\n'.join(values)


@pytest.mark.parametrize(('source', 'currency', 'unit'), [
    ('虚构投光灯 RMB1250一台', '元', '一台'),
    ('虚构投光灯 人民币1,250元/台', 'RMB', '元/台'),
    ('虚构投光灯 CNY1250/台', '人民币', '台'),
    ('虚构货品 USD1250/公斤', '美元', '公斤'),
    ('虚构货品 $1,250/kg', '$', 'kg'),
])
def test_currency_and_unit_aliases_are_grounded_in_text(source, currency, unit):
    prepared = prepare(source)
    # Simulate the exact D1c case: the source has explicit forms, while the
    # rule candidate did not retain them as structured fields.
    for candidate in prepared['candidates']:
        candidate['currency'] = None
        candidate['unit'] = None
    product = '虚构投光灯' if '投光灯' in source else '虚构货品'
    item, = ai.parse(dict(text=block('1250', product=product, currency=currency, unit=unit)), prepared)
    assert item['currency'] == rules.currency(currency)
    assert item['unit'] == rules.unit(unit)


def test_currency_and_unit_absence_only_clears_fields_and_lowers_confidence():
    prepared = prepare('虚构投光灯报价 1250元/台')
    item, = ai.parse(dict(text=block('1250', currency='USD', unit='kg')), prepared)
    assert item['currency'] is None and item['unit'] is None
    assert item['confidence'] <= .5


def test_unknown_currency_can_stay_empty_while_price_basis_unit_is_kept():
    prepared = prepare('虚构投光灯报价 1250元/台')
    item, = ai.parse(dict(text=block('1250', currency='无', unit='元/台')), prepared)
    assert item['currency'] is None and item['unit'] == '台'
    assert item['confidence'] <= .5


def test_freight_keeps_grounded_name_with_currency_alias_and_unknown_unit():
    prepared = prepare('香港UPS 运价 RMB250', party_kind='forwarder')
    item, = ai.parse(dict(text=block('250', kind='货代运价', product='香港UPS', currency='RMB', unit='无')),
                     prepared)
    assert item['kind'] == 'freight' and item['currency'] == 'CNY'
    assert item['unit'] is None and item['product_text'] == '香港UPS'


def test_ai_price_can_be_valid_without_a_matching_rule_candidate():
    prepared = prepare('虚构投光灯报价金额 1,840元/台')
    prepared['candidates'] = []
    prepared['candidate_numbers'] = []
    prepared['candidate_product_texts'] = []
    item, = ai.parse(dict(text=block('1840')), prepared)
    assert item['price_fen'] == 184000 and item['product_text'] == '虚构投光灯'


def test_two_prices_from_one_message_are_kept_as_separate_blocks():
    source = '虚构灯具RMB1840+超重费附加费RMB250'
    prepared = prepare(source)
    body = (block('1840', product='虚构灯具') + '\n\n---\n\n'
            + block('250', product='超重费附加费'))
    results = ai.parse(dict(text=body), prepared)
    assert [item['price_fen'] for item in results] == [184000, 25000]
    assert [item['product_text'] for item in results] == ['虚构灯具', '超重费附加费']


def test_no_quote_and_bundle_without_price_are_successful_empty_results():
    prepared = prepare('虚构整机报价需进一步核算')
    assert ai.parse(dict(text='  \n语言：中文\n无报价\n=====待确认=====\n人工复核'), prepared) == []
    result, summary = ai.parse_detailed(dict(text='消息：99\n类型：整单报价参考\n价格：无\n产品：无'), prepared)
    assert result == [] and summary == dict(blocks_total=1, accepted=0, dropped_by_reason={})


def test_wrapped_markdown_ascii_colons_numbered_fields_and_blank_separators_are_tolerated():
    prepared = prepare('虚构投光灯 RMB 1,250元/台')
    body = ('  ```text\n**语言：中文**\n\n'
            '1) **消息**: 1\n2. **类型**：产品报价\n3、价格：**1,250**\n'
            '4) 产品：虚构投光灯\n5) 币种：RMB\n6) 单位：一台\n7) 把握：0.9\n'
            '\n --- \n\n```\n=====待确认=====\n请老板核对')
    item, = ai.parse(dict(text=body), prepared)
    assert item['price_fen'] == 125000 and item['currency'] == 'CNY' and item['unit'] == '台'


def test_bad_block_does_not_discard_a_good_block_and_counts_reason_code():
    prepared = prepare('虚构投光灯报价 1250元/台')
    body = block('1250') + '\n---\n' + block('99999')
    result, summary = ai.parse_detailed(dict(text=body), prepared)
    assert len(result) == 1 and result[0]['price_fen'] == 125000
    assert summary == dict(blocks_total=2, accepted=1, dropped_by_reason={'price_not_in_message': 1})


def test_all_bad_blocks_and_non_format_text_have_distinct_errors():
    prepared = prepare('虚构投光灯报价 1250元/台')
    with pytest.raises(ai.AIParseError, match='没有给出可用的块') as no_blocks:
        ai.parse(dict(text=block('99999')), prepared)
    assert no_blocks.value.code == 'no_usable_blocks'
    with pytest.raises(ai.AIParseError, match='完全不是约定格式') as bad_format:
        ai.parse(dict(text='无关说明：稍后联系'), prepared)
    assert bad_format.value.code == 'format_invalid'


@pytest.mark.parametrize(('source', 'price', 'expected_reason'), [
    ('可以优惠15元/台', '15', 'noise_adjustment'),
    ('虚构投光灯100元/台吗？', '100', 'noise_question'),
    ('虚构投光灯24W，数量30台', '30', 'noise_spec_number'),
])
def test_d1b_noise_cannot_be_promoted_by_ai(source, price, expected_reason):
    prepared = prepare(source)
    with pytest.raises(ai.AIParseError) as failed:
        ai.parse(dict(text=block(price)), prepared)
    assert failed.value.summary['dropped_by_reason'][expected_reason] == 1


def test_products_are_grounded_in_the_cited_message_or_two_neighbor_messages():
    prepared = prepare('报价150元/台', '香港UPS适配器')
    item, = ai.parse(dict(text=block('150', product='香港UPS')), prepared)
    assert item['product_text'] == '香港UPS'
    prepared = prepare('虚构投光灯报价150元/台')
    item, = ai.parse(dict(text=block('150', product='不存在的产品')), prepared)
    assert item['product_text'] is None and item['confidence'] <= .5


def test_nineteen_of_twenty_four_mixed_windows_now_save_good_blocks(tmp_path):
    env = Env(tmp_path)
    for _ in range(24):
        env.conversation('虚构投光灯报价 1250元/台')
    good = block('1250')
    mixed_answer = answer(good + '\n---\n' + block('98765'))
    good_answer = answer(good)
    runner = Mock(side_effect=[mixed_answer] * 19 + [good_answer] * 5)
    service = env.service(runner=runner)
    results = [service.scan('fictional-owner') for _ in range(3)]
    assert sum(result['ai_windows'] for result in results) == 24
    assert sum(result['ai_failed'] for result in results) == 0
    assert sum(result['dropped_total'] for result in results) == 19
    assert sum(result['added'] for result in results) == 24
    assert service.read('suggestions', {'include_low': True})['total'] == 24
    with service.store.read() as db:
        runs = [dict(row) for row in db.execute(
            "SELECT output_json FROM role_runs WHERE role='extract' AND subject_kind='conversation'")]
    assert len(runs) == 24
    for run in runs:
        output = json.loads(run['output_json'])
        assert 'ai_parse' in output and 'items' not in output
        assert '虚构投光灯报价' not in str(output) and '产品报价' not in str(output)


def test_scan_reports_failed_ai_window_and_reason_summary(tmp_path):
    env = Env(tmp_path)
    env.conversation('虚构投光灯报价 1250元/台')
    service = env.service(runner=Mock(return_value=answer(block('98765'))))
    result = service.scan('fictional-owner')
    assert (result['ai_windows'], result['ai_failed'], result['dropped_total']) == (1, 1, 1)
    assert '没有给出可用的块' in result['deferred'][0]['reason']
    summary = result['runs'][0]['output_json']['ai_parse']
    assert summary == dict(blocks_total=1, accepted=0, dropped_by_reason={'price_not_in_message': 1})
