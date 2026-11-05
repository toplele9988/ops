from types import SimpleNamespace
import json

import pytest

from console.profiles.model import AiTask, ModelFailure, clean_answer, stage_for
from console.profiles.sources import ReadonlyWhatsApp
from console.inbox.model import make_key
from conftest import NOW, Runner, conversation, message
from console.profiles.windows import normalise
from console.profiles.model import map_prompt


def answer():
    sources = normalise([message(1)])
    raw, meta = AiTask(Runner()).extract(map_prompt(conversation(), sources))
    return raw, sources


def test_fabricated_quote_direction_country_and_language_validation():
    value, sources = answer()
    value['evidence'][0]['quote'] = '虚构不存在的原话'
    result = clean_answer(value, sources)
    # A made-up citation is dropped; the conclusion keeps too little confidence to drive ownership.
    assert result['evidence'] == [] and result['confidence'] <= 0.4
    value, sources = answer()
    value['evidence'][0]['claim'] = 'quoted'
    result = clean_answer(value, sources)
    assert all(e['claim'] != 'quoted' for e in result['evidence'])
    value, sources = answer()
    value.update(country='ZZ', country_name='Unknown', language='invalid!')
    result = clean_answer(value, sources)
    assert result['country'] is None and result['country_name'] is None and result['language'] is None


@pytest.mark.parametrize('change', [dict(provider='paid'), dict(route='paid'), dict(actual_model='paid-model'), dict(answer={}), dict(output='{}', answer=None), dict(error={})])
def test_reject_untrusted_gateway_envelopes(change):
    env = dict(status='ok', provider='dashscope', route='bailian_free', actual_model='deepseek-v4.1-flash', answer='{}')
    env.update(change)
    if 'error' in change:
        env['status'] = 'failed'
    task = AiTask(lambda *a, **kw: SimpleNamespace(stdout=json.dumps(env), returncode=0))
    with pytest.raises(ModelFailure):
        task.extract('fictional prompt')


def test_stages_require_completed_sale_before_after_sales_and_dormancy_only_for_leads():
    base = dict(kind='customer', evidence=[dict(claim='quoted', sent_at=NOW - 200 * 86400)])
    assert stage_for(base, NOW - 91 * 86400, NOW) == 'dormant'
    base['evidence'].append(dict(claim='after_sales', sent_at=NOW))
    assert stage_for(base, NOW, NOW) == 'quoted'
    base['evidence'].append(dict(claim='paid', sent_at=NOW - 86400))
    assert stage_for(base, NOW - 91 * 86400, NOW) == 'after_sales'
    base['kind'] = 'supplier'
    assert stage_for(base, NOW, NOW) is None


def test_whatsapp_paginates_without_media_writes(tmp_path):
    pages = []
    adapter = ReadonlyWhatsApp(tmp_path / 'disabled-config.json')
    adapter._configuration = lambda: dict(session='fictional')
    def get(config, path):
        pages.append(path)
        if 'offset=200&' in path:
            return []
        start = 100 if 'offset=100&' in path else 0
        return [dict(id=f'fictional-{i}', timestamp=NOW + i, body=f'fictional message {i}', fromMe=False)
                for i in range(start, 125 if start else 100)]
    adapter._get = get
    key = make_key('conversation', 'whatsapp', 'fictional', 'fictional@c.us')
    adapter._view, adapter.ready = {key: {}}, True
    messages = adapter.messages(key)
    assert len(messages) == 125 and messages[0]['sent_at'] < messages[-1]['sent_at']
    assert len(pages) == 3 and all('downloadMedia=false' in p for p in pages)
    assert not list(tmp_path.iterdir())


def test_whatsapp_ignored_offset_is_not_silently_truncated(tmp_path):
    adapter = ReadonlyWhatsApp(tmp_path / 'disabled-config.json')
    adapter._configuration = lambda: dict(session='fictional')
    adapter._get = lambda *args: [dict(id=f'fictional-{i}', timestamp=NOW + i, body='fictional') for i in range(100)]
    key = make_key('conversation', 'whatsapp', 'fictional', 'fictional@c.us')
    adapter._view, adapter.ready = {key: {}}, True
    with pytest.raises(RuntimeError):
        adapter.messages(key)


@pytest.mark.parametrize('summary', ['English only', '', '\ud800'])
def test_summary_must_be_chinese_and_valid_utf8(summary):
    value, sources = answer()
    value['summary'] = summary
    with pytest.raises(ModelFailure):
        clean_answer(value, sources)


def test_imperfect_formatting_is_normalized_not_rejected():
    value, sources = answer()
    good = dict(value['evidence'][0])
    value['people'] = [dict(name='虚构联系人'), '虚构采购', dict(role='无名字')]
    value['products'] = [dict(model='ZX-1', qty=20), 'ZX-2']
    value['summary'] = '虚构' * 150
    value['confidence'] = '0.8'
    value['extra'] = 'ignored'
    # Wrong index and timestamp, extra spaces: the citation is resolved to the real message.
    value['evidence'] = [dict(good, message_index=999, sent_at=1, quote='  ' + good['quote'] + ' ')]
    result = clean_answer(value, sources)
    assert result['people'] == [dict(name='虚构联系人', role=''), dict(name='虚构采购', role='')]
    assert result['products'] == ['ZX-1 20', 'ZX-2'] and len(result['summary']) == 200 and result['confidence'] == 0.8
    assert result['evidence'] == [dict(good)]
