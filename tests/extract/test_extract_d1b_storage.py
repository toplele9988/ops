"""D1b persistence examples are fictional, with temporary SQLite only."""
from copy import deepcopy
import json

import pytest
from starlette.datastructures import QueryParams

from console.archive.store import Store
from console.archive.validation import BadRequest
from console.extract import storage, validation
from console.extract.service import Extract
from console.inbox.model import make_key
from console.roles.common import fingerprint

NOW = 1969660800.0
KEY = make_key('conversation', 'wechat', 'fictional-d1b', 'fictional-supplier')


@pytest.fixture
def store(tmp_path):
    result = Store(tmp_path / 'fictional-d1b.sqlite3', target_version=7)
    result.initialize()
    return result


def item(message='fictional-first', *, at=NOW, **changes):
    result = dict(kind='offer', conversation_key=KEY, product_text='虚构 ZX-220 追光灯',
                  price_fen=55000, currency='CNY', unit=None, confidence=.35, source='rules',
                  excerpt='虚构 ZX-220 追光灯单价550元', quoted_on='2032-06-01',
                  source_message_refs=[dict(kind='message', id=message, conversation_key=KEY, sent_at=at, derived=False)])
    result.update(changes)
    result['input_fingerprint'] = fingerprint(['extract-price-v2', result['conversation_key'], message, result['kind'],
                                             result['currency'], result['price_fen'],
                                             storage.normalized_product(result['product_text'])])
    return result


def save(store, values, now=NOW):
    with store.write() as db:
        return storage.save_suggestion(db, values, 'fictional-owner', now)


def test_in_scan_merge_retains_earliest_source_and_completes_facts():
    first = item()
    repeat = item('fictional-repeat', at=NOW + 10, product_text='你们报的虚构 ｚｘ－２２０ 追光灯',
                  unit='台', lead_time_text='交期7天', confidence=.8, source='ai')
    untouched = deepcopy([repeat, first])
    rows = storage.merge_items([repeat, first])
    assert [repeat, first] == untouched
    assert len(rows) == 1
    row = rows[0]
    assert row['excerpt'] == first['excerpt']
    assert row['product_text'] == first['product_text']
    assert (row['unit'], row['lead_time_text'], row['source'], row['confidence']) == ('台', '交期7天', 'ai', .8)
    assert [ref['id'] for ref in row['source_message_refs']] == ['fictional-first', 'fictional-repeat']
    assert 'restated' not in row['source_message_refs'][0]
    assert row['source_message_refs'][1]['restated'] is True


@pytest.mark.parametrize('changes', [
    dict(currency='USD'), dict(price_fen=55100), dict(product_text='虚构 ZX-220 电源'), dict(kind='freight'),
    dict(conversation_key=make_key('conversation', 'wechat', 'fictional-d1b', 'fictional-another')),
])
def test_semantic_key_keeps_different_quotes_separate(store, changes):
    first = item()
    later = item('fictional-other', **changes)
    assert len(storage.merge_items([first, later])) == 2
    assert save(store, first)[1]
    assert save(store, later)[1]
    assert storage.list_suggestions(store)['total'] == 2


def test_same_message_can_quote_two_distinct_products_at_same_price(store):
    assert save(store, item())[1]
    assert save(store, item(product_text='虚构 ZX-220 电源'))[1]
    assert storage.list_suggestions(store)['total'] == 2


def test_repeated_complete_ai_freight_keeps_confidence_tier(store):
    freight = dict(kind='freight', product_text=None, mode='air', unit='kg', source='ai', confidence=.8)
    save(store, item(**freight))
    row, added = save(store, item('fictional-air-repeat', at=NOW + 1, **freight))
    assert not added and row['confidence'] == .8 and row['product_text'] in (None, '')


def test_persisted_repetition_merges_refs_without_duplicate_or_replacing_facts(store):
    first, _ = save(store, item(unit='台'))
    later = item('fictional-repeat', at=NOW + 20, product_text='虚构 zx 220 追光灯',
                 unit='件', lead_time_text='交期7天', tax_text='含税')
    merged, added = save(store, later, NOW + 20)
    assert added is False
    assert merged['id'] == first['id'] and merged['created_at'] == first['created_at']
    assert merged['unit'] == '台'
    assert merged['lead_time_text'] == '交期7天' and merged['tax_text'] == '含税'
    assert len(merged['source_message_refs']) == 2
    with store.read() as db:
        audits = db.execute('SELECT count(*) FROM audit').fetchone()[0]
    assert save(store, later, NOW + 30)[1] is False
    with store.read() as db:
        assert db.execute('SELECT count(*) FROM audit').fetchone()[0] == audits


def test_backfilled_earlier_quote_preserves_earliest_date_and_all_refs(store):
    first, _ = save(store, item('fictional-later', at=NOW + 86400, quoted_on='2032-06-02', unit='台'))
    older = item('fictional-earlier', at=NOW, quoted_on='2032-06-01', excerpt='虚构更早的原始报价550元')
    result, added = save(store, older, NOW + 86401)
    assert not added and result['id'] == first['id']
    assert result['quoted_on'] == '2032-06-01' and result['excerpt'] == older['excerpt']
    assert result['unit'] == '台'
    assert [ref['id'] for ref in result['source_message_refs']] == ['fictional-earlier', 'fictional-later']


@pytest.mark.parametrize('status', ['accepted', 'dismissed'])
def test_decided_repetition_preserves_decision_and_business_fields(store, status):
    original, _ = save(store, item())
    with store.write() as db:
        db.execute('UPDATE extraction_suggestions SET status=?,decided_at=?,decided_by=?,accepted_record_id=? WHERE id=?',
                   (status, '2032-06-01', 'fictional-decider', 99 if status == 'accepted' else None, original['id']))
    before = storage.list_suggestions(store, dict(status='all'))['items'][0]
    after, added = save(store, item('fictional-repeat', at=NOW + 1, unit='台', source='ai', confidence=.8))
    assert not added
    assert {key: value for key, value in after.items() if key != 'source_message_refs'} == {
        key: value for key, value in before.items() if key != 'source_message_refs'}
    assert len(after['source_message_refs']) == 2
    assert storage.list_suggestions(store)['items'] == []


def test_legacy_decision_message_guard_survives_changed_ai_labels(store):
    old = item()
    old['input_fingerprint'] = fingerprint(['extract-price-v1', KEY, 'fictional-first', 55000])
    row, _ = save(store, old)
    with store.write() as db:
        db.execute("UPDATE extraction_suggestions SET status='dismissed',decided_at='2032-06-01',decided_by='fictional-owner' WHERE id=?", (row['id'],))
    changed = item(currency='USD', product_text='虚构 AI 改名灯', source='ai', confidence=.8)
    result, added = save(store, changed)
    assert not added and result['id'] == row['id']
    assert result['currency'] == 'CNY' and result['product_text'] == old['product_text']
    assert result['status'] == 'dismissed'


def test_existing_v7_row_without_metadata_and_new_flags_read_compatibly(store):
    normal, _ = save(store, item())
    low, _ = save(store, item('fictional-bundle', product_text=None, price_fen=110000,
                              confidence=.2, source='ai', is_bundle=True, currency_guess=True))
    with store.read() as db:
        schema = [tuple(row) for row in db.execute('SELECT name,sql FROM sqlite_master ORDER BY name')]
        raw = json.loads(db.execute('SELECT source_message_refs FROM extraction_suggestions WHERE id=?', (low['id'],)).fetchone()[0])
    assert raw[0]['extraction_metadata'] == dict(currency_guess=True, is_bundle=True)
    assert normal['currency_guess'] is False and normal['is_bundle'] is False
    assert low['currency_guess'] is True and low['is_bundle'] is True
    assert [row['id'] for row in storage.list_suggestions(store)['items']] == [normal['id']]
    result = storage.list_suggestions(store, validation.query({'include_low': '1'}, 'suggestions'))
    assert result['total'] == 2
    assert storage.summary(store)['pending_quality'] == dict(trusted=1, low=1)
    assert storage.summary(store)['pending'] == dict(offer=2, freight=0)
    with store.read() as db:
        assert [tuple(row) for row in db.execute('SELECT name,sql FROM sqlite_master ORDER BY name')] == schema


def test_explicit_currency_repetition_clears_currency_guess(store):
    row, _ = save(store, item(currency_guess=True, confidence=.25))
    assert row['currency_guess'] is True
    row, _ = save(store, item('fictional-explicit', currency_guess=False, confidence=.35))
    assert row['currency_guess'] is False and row['confidence'] == .35
    assert storage.list_suggestions(store)['items'][0]['currency_guess'] is False


@pytest.mark.parametrize('ai_first', [False, True])
def test_merging_rules_and_ai_never_fills_a_missing_ai_unit(store, ai_first):
    model = item('fictional-model', unit=None, source='ai', confidence=.5)
    rule = item('fictional-rule', unit='台', source='rules', confidence=.35)
    earlier, later = (model, rule) if ai_first else (rule, model)
    save(store, earlier)
    merged, added = save(store, later)
    assert not added and merged['source'] == 'ai'
    assert merged['unit'] is None and merged['confidence'] <= .5


def test_low_filter_applies_before_pagination_and_keeps_decisions_visible(store):
    save(store, item())
    low, _ = save(store, item('fictional-low', price_fen=60000, confidence=.2), NOW + 1)
    result = storage.list_suggestions(store, dict(page=1, page_size=1))
    assert result['total'] == 1 and result['items'][0]['price_fen'] == 55000
    with store.write() as db:
        db.execute("UPDATE extraction_suggestions SET status='dismissed',decided_at='2032-06-01',decided_by='fictional-owner' WHERE id=?", (low['id'],))
    assert storage.list_suggestions(store, dict(status='dismissed'))['total'] == 1
    assert storage.summary(store)['pending_quality'] == dict(trusted=1, low=0)


@pytest.mark.parametrize('params', [
    {'include_low': 'true'}, {'include_low': '2'}, {'include_low': ''},
    QueryParams('include_low=1&include_low=0'),
])
def test_include_low_validation_rejects_ambiguous_values(params):
    with pytest.raises(BadRequest):
        validation.query(params, 'suggestions')


def test_http_include_low_and_summary_use_existing_api_without_schema_changes(store):
    from test_extract_api import Client
    save(store, item())
    save(store, item('fictional-low', product_text=None, confidence=.2))
    service = Extract(store=store, runner=None, clock=lambda: NOW)
    client = Client(service)
    assert client.data('suggestions')['total'] == 1
    assert client.data('suggestions', query={'include_low': '1'})['total'] == 2
    assert client.data('summary')['pending_quality'] == dict(trusted=1, low=1)

