"""Fictional, offline acceptance for automatic reading and bilingual drafting."""
from concurrent.futures import ThreadPoolExecutor
import json
import re
import sqlite3
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from console import ai_gateway
from console.archive.validation import BadRequest
from console.inbox import ai
from console.lang import Lang, PretranslateSchedule
from console.lang.detection import needs_chinese_translation
from console.lang.translation import parse_blocks
from console.lang.storage import SCHEMA
from test_lang_service import BlockRunner, service, write


class JsonRunner:
    def __init__(self, bad_call=0, addition=' EUR 777.77'):
        self.calls = []
        self.bad_call, self.addition = bad_call, addition

    def __call__(self, prompt):
        target = re.search('语言代码 ([a-zA-Z-]+)', prompt)[1]
        self.calls.append(target)
        prefix = {'en': 'Fictional translation: ', 'es': 'Traducción ficticia: ', 'zh-CN': '虚构回译：'}.get(target, '虚构译文：')
        blocks = [dict(index=int(n), text=prefix + text + (self.addition if len(self.calls) == self.bad_call else ''))
                  for n, text in re.findall(r'\[\[(\d+)\]\]\n(.*?)\n\[\[/\1\]\]', prompt, re.S)]
        return json.dumps(dict(answer='```json\n' + json.dumps(dict(blocks=blocks), ensure_ascii=False) + '\n```'), ensure_ascii=False)


def auto(lang, key, **kwargs):
    return write(lang, 'translate', conversation_key=key, mode='auto', **kwargs)


@pytest.mark.parametrize('role,text,expected', [
    ('buyer', '您好，请发虚构产品资料。', False), ('buyer', '謝謝，請確認虛構產品資料。', False),
    ('buyer', '好', False), ('buyer', '你好 FX-730 12 pcs', False),
    ('seller', 'Please send fictional data.', False), ('buyer', '👍', False), ('buyer', '', False),
    ('buyer', 'FX-730 123 https://example.invalid', False),
    ('buyer', 'Please send fictional data.', True), ('buyer', 'Hola, necesito información ficticia.', True),
    ('buyer', '製品の情報を送ってください。', True), ('buyer', '안녕하세요 제품 정보', True),
])
def test_only_foreign_incoming_prose_needs_chinese(role, text, expected):
    assert needs_chinese_translation(dict(role=role, text=text)) is expected


def test_auto_batches_all_foreign_messages_and_preserves_indices(inputs):
    runner = BlockRunner()
    texts = ['Please send fictional data ' + chr(97 + i) for i in range(17)]
    texts += ['您好，请发虚构资料', 'Thanks, fictional seller', '👍']
    key = inputs.inbox.conversation(texts, roles=['buyer'] * 18 + ['seller', 'buyer'])
    result = auto(service(inputs, runner), key)
    assert [i['index'] for i in result['items']] == list(range(17))
    assert all(i['complete'] for i in result['items'])
    assert [len(call) for call in runner.calls] == [8, 8, 1]
    assert result['target_language'] == 'zh-CN'


def test_auto_refresh_and_concurrent_workers_reuse_persistent_cache(inputs):
    key = inputs.inbox.conversation(['Please send fictional data.'])
    runner = BlockRunner()
    langs = [service(inputs, runner) for _ in range(2)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda lang: auto(lang, key), langs))
    assert len(runner.calls) == 1
    assert all(r['items'][0]['complete'] for r in results)
    forbidden = Mock(side_effect=AssertionError('缓存不调用模型'))
    cached = auto(service(inputs, forbidden), key)
    assert cached['items'][0]['cached']
    forbidden.assert_not_called()


def test_auto_quota_is_normal_result_with_partial_cache_and_minutes(inputs):
    runner = BlockRunner()
    lang = service(inputs, runner, hourly_limit=1)
    key = inputs.inbox.conversation(['Please send fictional data ' + chr(97 + i) for i in range(9)])
    result = auto(lang, key)
    assert result['quota_exhausted'] and result['retry_after'] == 3600
    assert result['hint'] == '本小时翻译额度已用完，60 分钟后继续'
    assert all(i['complete'] for i in result['items'][:8]) and not result['items'][8]['complete']
    inputs.clock.advance(60)
    again = auto(service(inputs, runner, hourly_limit=1), key)
    assert all(i['cached'] for i in again['items'][:8])
    assert again['hint'] == '本小时翻译额度已用完，59 分钟后继续'
    assert len(runner.calls) == 1
    inputs.clock.advance(3540)
    done = auto(lang, key)
    assert not done['quota_exhausted'] and all(i['complete'] for i in done['items'])
    assert len(runner.calls) == 2


def test_auto_disabled_and_cache_only_do_not_spend_quota(inputs):
    key = inputs.inbox.conversation(['Please send fictional data.'])
    runner = BlockRunner()
    lang = service(inputs, runner)
    write(lang, 'settings', auto_translate_incoming='0')
    assert not auto(lang, key)['items'][0]['complete']
    assert not write(lang, 'translate', conversation_key=key, mode='cached')['items'][0]['complete']
    assert not runner.calls
    write(lang, 'settings', auto_translate_incoming='1')
    auto(lang, key)
    write(lang, 'settings', auto_translate_incoming='0')
    assert auto(lang, key)['items'][0]['cached']
    assert len(runner.calls) == 1


def test_existing_settings_database_migrates_without_losing_preferences(inputs):
    with sqlite3.connect(inputs.path) as db:
        db.executescript(SCHEMA)
        db.execute("INSERT INTO settings VALUES(1,'de',0)")
    lang = service(inputs)
    assert lang.read('settings')['settings']['auto_translate_incoming'] is True
    write(lang, 'settings', auto_translate_incoming='0')
    assert service(inputs).read('settings')['settings'] == dict(my_language='de', auto_native_send=False, auto_translate_incoming=False)


def test_invalid_incoming_toggle_is_rejected(inputs):
    with pytest.raises(BadRequest):
        write(service(inputs), 'settings', auto_translate_incoming='yes')
    assert not inputs.path.exists()


def test_preview_uses_whole_chunk_cache_without_models_or_detail_fetches(inputs):
    text = 'Please send fictional product details. ' * 100
    key = inputs.inbox.conversation([text])
    runner = BlockRunner()
    lang = service(inputs, runner)
    fields = dict(mode='preview', conversation_keys=json.dumps([key]))
    assert write(lang, 'translate', **fields)['previews'] == []
    auto(lang, key)
    calls = len(runner.calls)
    inputs.inbox.calls.clear()
    previews = write(service(inputs, runner), 'translate', **fields)['previews']
    assert previews[0]['source_text'] == text[:140] and previews[0]['text']
    assert len(runner.calls) == calls and not inputs.inbox.calls
    write(lang, 'glossary_save', term='fictional', mode='keep', translation='')
    assert write(lang, 'translate', **fields)['previews'] == []
    inputs.inbox.conversations[key]['messages'].append(dict(role='buyer', text='A fictional newer message.', sent_at=inputs.clock()))
    assert write(lang, 'translate', **fields)['previews'] == []


@pytest.mark.parametrize('keys', ['invalid', '{}', '[1]', '["forged"]', json.dumps(['fake'] * 41)])
def test_invalid_preview_keys_rejected_before_source_read(inputs, keys):
    inputs.inbox.read = Mock(side_effect=AssertionError('无效参数不得读取消息'))
    with pytest.raises(BadRequest):
        write(service(inputs), 'translate', mode='preview', conversation_keys=keys)
    inputs.inbox.read.assert_not_called()


def test_pretranslate_recent_incoming_limit_and_shared_cache(inputs):
    runner = BlockRunner()
    lang = service(inputs, runner)
    key = inputs.inbox.conversation(['Please send fictional data ' + chr(97 + i) for i in range(8)] + ['您好，请确认虚构资料'])
    messages = inputs.inbox.conversations[key]['messages']
    for i, m in enumerate(messages):
        m['sent_at'] = inputs.clock() - i * 60
    messages[0]['sent_at'] = inputs.clock() - 48 * 3600 - 1
    messages[1]['role'] = 'seller'
    messages[2]['sent_at'] = inputs.clock() + 60
    first = lang.pretranslate(2)
    assert first['attempted'] == first['translated'] == 2
    second = lang.pretranslate(20)
    assert second['attempted'] == second['translated'] == 3
    count = len(runner.calls)
    assert lang.pretranslate(20)['attempted'] == 0
    assert len(runner.calls) == count
    assert write(lang, 'translate', conversation_key=key, mode='cached')['items'][3]['cached']


def test_schedule_throttles_concurrent_instances_and_limits_twenty(inputs):
    key = inputs.inbox.conversation(['Please send fictional data ' + str(i) for i in range(25)])
    for m in inputs.inbox.conversations[key]['messages']:
        m['sent_at'] = inputs.clock() - 30
    runner = BlockRunner()
    one, two = [PretranslateSchedule(service(inputs, runner)) for _ in range(2)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda schedule: schedule.poll(), [one, two]))
    assert sum(r['skipped'] for r in results) == 1
    assert next(r for r in results if not r['skipped'])['attempted'] == 20
    inputs.clock.advance(299)
    assert two.poll()['skipped']
    inputs.clock.advance(1)
    result = PretranslateSchedule(service(inputs, runner)).poll()
    assert not result['skipped'] and result['translated'] == 5


def test_pretranslate_quota_and_disabled_schedule_are_bounded(inputs):
    key = inputs.inbox.conversation(['Please send fictional data ' + chr(97 + i) for i in range(9)])
    for m in inputs.inbox.conversations[key]['messages']:
        m['sent_at'] = inputs.clock()
    runner = BlockRunner()
    lang = service(inputs, runner, hourly_limit=1)
    result = PretranslateSchedule(lang).poll()
    assert result['translated'] == 8 and result['quota_exhausted']
    assert len(runner.calls) == 1
    inputs.clock.advance(300)
    write(lang, 'settings', auto_translate_incoming='0')
    assert PretranslateSchedule(lang).poll()['disabled']
    assert len(runner.calls) == 1


@pytest.mark.parametrize('limit', [0, -1, 301, True, '20'])
def test_pretranslate_rejects_invalid_limit(inputs, limit):
    with pytest.raises(BadRequest):
        service(inputs).pretranslate(limit)


@pytest.mark.parametrize('native,country,expected', [('en', 'United Kingdom', ['en']), (None, 'Brazil', ['en']), ('es', 'Spain', ['en', 'es'])])
def test_compose_english_unknown_and_local_cases(inputs, native, country, expected):
    runner = JsonRunner()
    lang = service(inputs, runner)
    key = inputs.inbox.conversation(['Fictional incoming text'], country=country)
    if native:
        write(lang, 'contact', conversation_key=key, native_language=native, locked='1')
    result = write(lang, 'compose', conversation_key=key, text='请确认虚构灯具 FX-730，12 pcs，USD 42.75')
    assert [v['language'] for v in result['versions']] == expected
    assert result['validation']['ok'] and result['back_validation']['ok']
    assert result['text'] == '\n\n'.join(v['text'] for v in result['versions'])
    assert result['english_text'] == result['versions'][0]['text']
    assert all(v['back_translation'] for v in result['versions'])
    assert result['native_language'] == native
    assert len(inputs.inbox.conversations[key]['messages']) == 1
    count = len(runner.calls)
    assert write(lang, 'compose', conversation_key=key, text='请确认虚构灯具 FX-730，12 pcs，USD 42.75')['text'] == result['text']
    assert len(runner.calls) == count


def test_reply_local_toggle_off_still_generates_english(inputs):
    runner = JsonRunner()
    lang = service(inputs, runner)
    key = inputs.inbox.conversation(['Fictional incoming text'])
    write(lang, 'contact', conversation_key=key, native_language='es', locked='1')
    write(lang, 'settings', auto_native_send='0')
    result = write(lang, 'compose', conversation_key=key, text='请确认虚构灯具资料')
    assert [v['language'] for v in result['versions']] == ['en']
    assert not result['include_local'] and result['local_text'] is None


@pytest.mark.parametrize('bad_call', [1, 2, 3, 4])
@pytest.mark.parametrize('addition', [' EUR 777.77', ' ZX-999', ' 998 pcs'])
def test_both_reply_versions_and_both_back_translations_check_added_facts(inputs, bad_call, addition):
    runner = JsonRunner(bad_call=bad_call, addition=addition)
    lang = service(inputs, runner)
    key = inputs.inbox.conversation(['Fictional incoming text'])
    write(lang, 'contact', conversation_key=key, native_language='es', locked='1')
    result = write(lang, 'compose', conversation_key=key, text='请确认虚构灯具 FX-730，12 pcs')
    assert runner.calls == ['en', 'zh-CN', 'es', 'zh-CN']
    assert not result['validation']['ok']
    assert any(w['kind'] == 'error' and addition.strip() in w['fragments'] for w in result['warnings'])
    version = result['versions'][(bad_call - 1) // 2]
    assert not version['validation' if bad_call % 2 else 'back_validation']['ok']


def test_gateway_json_answer_parser_rejects_duplicate_blocks():
    output = json.dumps(dict(answer=json.dumps(dict(blocks=[dict(index=1, text='甲'), dict(index=1, text='乙'), dict(index=2, text='丙')]))))
    assert parse_blocks(output, {1, 2}) == {2: '丙'}


def test_language_detection_accepts_gateway_json_string(inputs):
    key = inputs.inbox.conversation(['Buenas'])
    runner = Mock(return_value=json.dumps(dict(answer='```json\n{"language":"es","confidence":0.93}\n```')))
    assert write(service(inputs, runner), 'detect', conversation_key=key)['contact']['native_language'] == 'es'


def test_official_runner_routes_through_common_gateway(monkeypatch):
    fake = Mock(return_value=SimpleNamespace(returncode=0, stdout=json.dumps(dict(provider='deepseek', route='deepseek_official', status='ok', actual_model='deepseek-flash', answer='{"blocks":[]}'))))
    monkeypatch.setattr(ai_gateway, 'run', fake)
    assert ai.official_runner('虚构测试提示').model == 'deepseek-flash'
    assert fake.call_args.args[0] == ['ai-task', 'run', 'deepseek_official', '--model', 'deepseek-flash']
    assert fake.call_args.kwargs['input'] == '虚构测试提示'


@pytest.mark.parametrize('extra', [dict(provider='unapproved'), dict(route='bailian_free'), dict(actual_model='unknown-model'), dict(answer={})])
def test_official_runner_rejects_wrong_gateway_identity_or_answer(monkeypatch, extra):
    value = dict(provider='deepseek_official', route='deepseek_official', actual_model='deepseek-flash', answer='虚构回答')
    value.update(extra)
    fake = Mock(return_value=SimpleNamespace(returncode=0, stdout=json.dumps(value)))
    monkeypatch.setattr(ai_gateway, 'run', fake)
    with pytest.raises(RuntimeError):
        ai.official_runner('虚构测试提示')
    assert fake.call_count <= 3


@pytest.mark.parametrize('bad_call,label', [(1, '英文版'), (2, '英文版'), (3, '当地语言版'), (4, '当地语言版')])
def test_lost_number_or_model_placeholder_blocks_each_version_and_back_translation(inputs, bad_call, label):
    class DroppingRunner(JsonRunner):
        def __call__(self, prompt):
            output = super().__call__(prompt)
            return re.sub(r'__KEEP_\d+__', '', output, count=1) if len(self.calls) == bad_call else output
    lang = service(inputs, DroppingRunner())
    key = inputs.inbox.conversation(['Fictional incoming text'])
    write(lang, 'contact', conversation_key=key, native_language='es', locked='1')
    with pytest.raises(BadRequest, match=label):
        write(lang, 'compose', conversation_key=key, text='请确认虚构灯具 FX-730，12 pcs，USD 42.75')
