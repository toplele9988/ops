"""Independent regressions from reviewing extraction boundaries; fictional only."""
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from console.extract import rules
from console.inbox.model import make_key

NOW = datetime(2030, 1, 2, 12, tzinfo=ZoneInfo('Asia/Shanghai')).timestamp()


@pytest.mark.parametrize('text', [
    '本周有效，具体以明天确认为准',
    '本周有效，但暂定',
    '月底前有效，日期待定',
    '10天内有效，不含周末',
])
def test_uncertain_separate_clause_prevents_expiry(text):
    assert rules.validity(text, NOW)[1] is None


@pytest.mark.parametrize('text,forbidden_price', [
    ('你报价1280元/台，我还价 1000 元/台', 100000),
    ('你们说交期七天，我方报价 3000 元/台', 300000),
    ('你报价 45元/kg，我报价 50元/kg', 5000),
])
def test_supplier_restatement_cannot_promote_own_counteroffer(text, forbidden_price):
    conversation = dict(key=make_key('conversation', 'wechat', 'fictional-account', 'fictional-supplier'),
                        messages=[dict(role='seller', text=text, sent_at=NOW)])
    assert forbidden_price not in {r['price_fen'] for r in rules.extract(conversation, NOW, party_kind='supplier')}


def test_ai_cannot_move_price_to_another_product_in_same_message():
    from console.extract import ai
    conversation = dict(key=make_key('conversation', 'wechat', 'fictional-account', 'fictional-supplier'),
                        messages=[dict(role='buyer', text='虚构A灯报价 100 元/台；虚构B灯报价 200 元/台', sent_at=NOW)])
    prepared = rules.prepare(conversation, NOW, party_kind='supplier')
    item, = ai.parse(dict(text='消息：1\n类型：产品报价\n价格：100\n产品：虚构B灯'), prepared)
    assert item['product_text'] is None and item['confidence'] <= .5


def test_restatement_retains_attachment_warning_from_other_clause():
    conversation = dict(key=make_key('conversation', 'wechat', 'fictional-account', 'fictional-supplier'),
                        messages=[dict(role='seller', text='你报价1280元/台，[PDF]', sent_at=NOW)])
    item = rules.extract(conversation, NOW, party_kind='supplier')[0]
    assert item['has_attachment'] and '有附件，需人工看' in item['excerpt']
