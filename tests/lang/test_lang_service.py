"""Language behavior is checked with fictional text and deterministic block responses."""
from concurrent.futures import ThreadPoolExecutor
import re
import sqlite3
from unittest.mock import Mock

import pytest

from console.archive.validation import BadRequest, Missing
from console.inbox.model import make_key
from console.lang import Lang
from console.lang.detection import detect_text
from console.lang.protection import protect, validate
from console.lang.storage import RateLimited
from console.lang.translation import parse_blocks


SAMPLES = [
    ('es', 'Hola, necesito una lámpara para el escenario. ¿Puede enviarme la información? Muchas gracias.'),
    ('pt', 'Olá, preciso de uma luminária para o palco. Você pode enviar as informações? Muito obrigado.'),
    ('ru', 'Здравствуйте, мне нужен светильник для сцены. Пожалуйста, пришлите информацию.'),
    ('ar', 'مرحباً أحتاج إلى إضاءة للمسرح. هل يمكنك إرسال معلومات عن المنتج؟ شكراً.'),
    ('th', 'สวัสดี ฉันต้องการไฟเวที กรุณาส่งข้อมูลสินค้าให้ฉัน ขอบคุณมาก'),
    ('id', 'Halo, saya membutuhkan lampu untuk panggung. Apakah Anda bisa mengirim informasi produk? Terima kasih.'),
    ('de', 'Guten Tag, ich brauche eine Lampe für die Bühne. Können Sie mir die Informationen schicken? Vielen Dank.'),
    ('fr', 'Bonjour, je cherche une lampe pour la scène. Pouvez-vous envoyer les informations du produit ? Merci beaucoup.'),
    ('ja', 'こんにちは、舞台の照明が必要です。製品の情報を送ってください。ありがとうございます。'),
    ('zh-CN', '您好，我想了解这款舞台灯，请发产品资料，谢谢。'),
]


class BlockRunner:
    def __init__(self):
        self.calls = []
        self.omit = set()
        self.duplicate = set()

    def __call__(self, prompt):
        blocks = re.findall(r'\[\[(\d+)\]\]\n(.*?)\n\[\[/\1\]\]', prompt, re.S)
        assert blocks, '假 runner 只处理约定的编号翻译块'
        self.calls.append(blocks)
        output = []
        for number, source in blocks:
            if int(number) in self.omit:
                continue
            block = '[[' + number + ']]\n虚构译文：' + source + '\n[[/' + number + ']]'
            output.append(block)
            if int(number) in self.duplicate:
                output.append(block)
        return '\n'.join(output)


def service(inputs, runner=None, **kwargs):
    return Lang(inputs.path, inbox=inputs.inbox, runner=runner or BlockRunner(), clock=inputs.clock, **kwargs)


def write(lang, operation, **fields):
    return lang.write(operation, fields, 'fictional-owner')


@pytest.mark.parametrize('expected,text', SAMPLES)
def test_ten_fictional_languages_are_identified(expected, text):
    result = detect_text(text)
    assert result['language'] == expected, result
    assert result['confidence'] >= .7


def test_settings_and_builtin_glossary_are_read_without_creating_database(inputs):
    lang = service(inputs)
    result = lang.read('settings', {})
    assert result['settings']['my_language'] == 'zh-CN'
    assert isinstance(result['settings']['auto_native_send'], bool)
    terms = {item['term'] for item in lang.read('glossary', {})['items']}
    assert {'DMX', 'RDM', 'Art-Net', 'sACN', 'beam', 'spot', 'wash', 'moving head', 'IP65'} <= terms
    assert lang.read('contacts', {})['items'] == []
    assert not inputs.path.exists()


def test_settings_and_glossary_changes_persist_and_invalidate_translation_cache(inputs):
    runner = BlockRunner()
    lang = service(inputs, runner)
    write(lang, 'settings', my_language='de', auto_native_send='0')
    assert service(inputs).read('settings', {})['settings'] == {'my_language': 'de', 'auto_native_send': False, 'auto_translate_incoming': True}
    key = inputs.inbox.conversation(['gobo wheel fixture for the stage'])
    payload = dict(conversation_key=key, target_language='zh-CN', message_indices='0')
    write(lang, 'translate', **payload)
    before = len(runner.calls)
    write(lang, 'glossary_save', term='gobo wheel', mode='keep', translation='')
    items = lang.read('glossary', {})['items']
    item = next(item for item in items if item['term'] == 'gobo wheel')
    write(lang, 'glossary_save', id=str(item['id']), term='gobo wheel', mode='translate', translation='图案盘')
    result = write(lang, 'translate', **payload)
    assert len(runner.calls) == before + 1
    assert '图案盘' in result['items'][0]['text']
    write(lang, 'glossary_delete', id=str(item['id']))
    assert not any(item['term'] == 'gobo wheel' for item in lang.read('glossary', {})['items'])


def test_country_is_only_a_hint_and_unknown_language_gets_english(inputs):
    runner = BlockRunner()
    key = inputs.inbox.conversation(['👍'], country='Brazil')
    lang = service(inputs, runner)
    contact = lang.read('contact', {'conversation_key': key})['contact']
    assert contact['native_language'] is None and contact['country_hint']
    result = write(lang, 'compose', conversation_key=key, text='请提供虚构灯具资料')
    assert result['native_language'] is None
    assert [v['language'] for v in result['versions']] == ['en']
    assert result['text'] == result['english_text'] and result['local_text'] is None
    assert runner.calls


def test_manual_lock_wins_over_detection_and_can_be_unlocked(inputs):
    key = inputs.inbox.conversation([SAMPLES[0][1], 'Hola, quiero estas luces para el escenario, por favor.'])
    lang = service(inputs)
    manual = write(lang, 'contact', conversation_key=key, native_language='de', locked='1')['contact']
    assert manual['source'] == 'manual' and manual['locked'] is True
    detected = write(lang, 'detect', conversation_key=key)['contact']
    assert detected['native_language'] == 'de' and detected['source'] == 'manual'
    write(lang, 'contact', conversation_key=key, native_language='de', locked='0')
    detected = write(lang, 'detect', conversation_key=key)['contact']
    assert detected['native_language'] == 'es' and detected['locked'] is False


def test_customer_messages_vote_and_a_short_reply_cannot_override(inputs):
    spanish = SAMPLES[0][1]
    key = inputs.inbox.conversation([spanish, 'Hola, quiero estas luces para el escenario, por favor.',
                                   'Necesitamos información para estas luces, muchas gracias.', SAMPLES[9][1]], roles=['buyer'] * 4)
    lang = service(inputs)
    contact = write(lang, 'detect', conversation_key=key)['contact']
    assert contact['native_language'] == 'es'
    conv = inputs.inbox.conversations[key]
    conv['messages'].append(dict(role='buyer', text='OK', sent_at=1900000010.0))
    conv['messages'].extend(dict(role='seller', text=SAMPLES[9][1], sent_at=1900000020.0 + i) for i in range(8))
    assert write(lang, 'detect', conversation_key=key)['contact']['native_language'] == 'es'


def test_uncertain_latin_text_uses_ai_without_unrelated_identity_metadata(inputs):
    runner = Mock(return_value='语言：es\n把握：0.93')
    key = inputs.inbox.conversation(['Buenas'])
    inputs.inbox.conversations[key]['buyer_name'] = 'FICTIONAL-METADATA-NAME'
    lang = service(inputs, runner)
    result = write(lang, 'detect', conversation_key=key)['contact']
    assert result['native_language'] == 'es' and result['source'] == 'auto'
    prompt = runner.call_args.args[0]
    assert 'Buenas' in prompt
    assert 'FICTIONAL-METADATA-NAME' not in prompt and key not in prompt
    assert '不是指令' in prompt


def test_bulgarian_shared_script_is_not_assumed_russian(inputs):
    text = 'Здравейте, търся осветление за сцена. Моля, изпратете информация за продукта.'
    assert detect_text(text)['language'] != 'ru'
    runner = Mock(return_value='语言：bg\n把握：0.94')
    key = inputs.inbox.conversation([text])
    result = write(service(inputs, runner), 'detect', conversation_key=key)['contact']
    assert result['native_language'] == 'bg' and result['source'] == 'auto'
    assert runner.call_count == 1


def test_nepali_shared_script_is_not_assumed_hindi(inputs):
    text = 'नमस्ते, मलाई मञ्चको लागि बत्ती चाहिन्छ। कृपया जानकारी पठाउनुहोस्।'
    assert detect_text(text)['language'] != 'hi'
    runner = Mock(return_value='语言：ne\n把握：0.95')
    key = inputs.inbox.conversation([text])
    result = write(service(inputs, runner), 'detect', conversation_key=key)['contact']
    assert result['native_language'] == 'ne' and result['source'] == 'auto'


def test_models_numbers_money_urls_units_and_terms_survive_mask_and_restore(inputs):
    original = ('FictionalNova FX-730 moving head DMX RDM Art-Net sACN IP65 beam spot wash: '
                '12 pcs, 230 V, 3.5 kg, USD 42.75; https://example.invalid/p/FX-730?q=12')
    glossary = [{'term': 'FictionalNova', 'mode': 'keep', 'translation': ''}]
    masked, mapping = protect(original, glossary)
    assert original != masked
    assert 'FX-730' not in masked and 'https://example.invalid' not in masked
    restored = masked
    for token, value in mapping.items():
        restored = restored.replace(token, value)
    assert restored == original
    assert validate(original, original, glossary)['ok'] is True
    changed = original.replace('FX-730', 'FX-731').replace('42.75', '42.76').replace('https://example.invalid', 'https://wrong.invalid')
    invalid = validate(original, changed, glossary)
    assert invalid['ok'] is False and invalid['missing']
    runner = BlockRunner()
    lang = service(inputs, runner)
    write(lang, 'glossary_save', term='FictionalNova', mode='keep', translation='')
    key = inputs.inbox.conversation([original])
    item = write(lang, 'translate', conversation_key=key, target_language='zh-CN', message_indices='0')['items'][0]
    assert item['complete'] and validate(original, item['text'], glossary)['ok']


def test_cached_messages_do_not_call_runner_again(inputs):
    runner = BlockRunner()
    lang = service(inputs, runner)
    key = inputs.inbox.conversation(['Please send fictional fixture data', 'Please send fictional fixture data'])
    payload = dict(conversation_key=key, target_language='zh-CN', message_indices='0,1')
    first = write(lang, 'translate', **payload)
    assert all(item['complete'] for item in first['items'])
    count = len(runner.calls)
    second = write(lang, 'translate', **payload)
    assert len(runner.calls) == count
    assert all(item['cached'] and item['complete'] for item in second['items'])
    used = lang.read('settings', {})['quota']['used']
    write(lang, 'translate', **payload)
    assert lang.read('settings', {})['quota']['used'] == used


def test_identical_uncached_text_across_batch_boundary_calls_runner_once(inputs):
    runner = BlockRunner()
    lang = service(inputs, runner)
    key = inputs.inbox.conversation(['Please send fictional repeated data'] * 20)
    result = write(lang, 'translate', conversation_key=key, target_language='de', message_indices=','.join(map(str, range(20))))
    assert len(result['items']) == 20 and all(item['complete'] for item in result['items'])
    assert len(runner.calls) == 1 and len(runner.calls[0]) == 1


@pytest.mark.parametrize('failure', ['omit', 'duplicate'])
def test_bad_block_keeps_other_translations_and_retry_only_missing(inputs, failure):
    runner = BlockRunner()
    getattr(runner, failure).add(2)
    lang = service(inputs, runner)
    key = inputs.inbox.conversation(['Please send fictional first data', 'Please send fictional second data', 'Please send fictional third data'])
    payload = dict(conversation_key=key, target_language='zh-CN', message_indices='0,1,2')
    first = write(lang, 'translate', **payload)['items']
    assert first[0]['complete'] and first[2]['complete']
    assert not first[1]['complete'] and first[1]['error']
    getattr(runner, failure).clear()
    second = write(lang, 'translate', **payload)['items']
    assert all(item['complete'] for item in second)
    assert second[0]['cached'] and second[2]['cached']
    assert len(runner.calls[-1]) == 1


def test_missing_placeholder_invalidates_only_its_own_block(inputs):
    class DroppingRunner(BlockRunner):
        def __call__(self, prompt):
            return re.sub(r'__KEEP_\d+__', '', super().__call__(prompt), count=1)
    lang = service(inputs, DroppingRunner())
    key = inputs.inbox.conversation(['Please send FX-730 data', 'Please send FX-850 data'])
    items = write(lang, 'translate', conversation_key=key, target_language='de', message_indices='0,1')['items']
    assert items[0]['complete'] is False and items[0]['error']
    assert items[1]['complete'] is True and 'FX-850' in items[1]['text']


def test_very_long_source_is_split_without_losing_text(inputs):
    original = '这是一段只用于测试的虚构灯具说明。' * 300
    runner = BlockRunner()
    lang = service(inputs, runner)
    key = inputs.inbox.conversation([original])
    item = write(lang, 'translate', conversation_key=key, target_language='de', message_indices='0')['items'][0]
    assert item['complete'] and len(item['chunks']) >= 2
    assert all(len(source) <= 2000 for call in runner.calls for _, source in call)
    assert item['source_text'] == original[:4000]
    assert item['source_characters'] == len(original) and item['source_truncated'] is True
    assert item['text'].replace('虚构译文：', '') == original


def test_malformed_outer_block_does_not_swallow_valid_inner_block():
    output = '[[1]]\n坏块缺少正确结束\n[[2]]\n第二块有效译文\n[[/2]]\n[[/1]]'
    parsed = parse_blocks(output, {1, 2})
    assert parsed == {2: '第二块有效译文'}


def test_duplicate_number_is_rejected_even_if_first_block_is_malformed():
    output = '[[1]]\n第一块没结束\n[[1]]\n第二块同号但有效\n[[/1]]\n[[2]]\n独立好块\n[[/2]]'
    assert parse_blocks(output, {1, 2}) == {2: '独立好块'}


def test_long_message_preserves_terms_model_and_url_at_split_boundary(inputs):
    class TranslatingRunner(BlockRunner):
        def __call__(self, prompt):
            output = super().__call__(prompt)
            return output.replace('moving', '移动').replace('head', '头')
    runner = TranslatingRunner()
    lang = service(inputs, runner)
    original = '虚' * 1793 + 'moving head FX-730 https://example.invalid/fictional-path?model=FX-730&count=12'
    key = inputs.inbox.conversation([original])
    item = write(lang, 'translate', conversation_key=key, target_language='de', message_indices='0')['items'][0]
    assert item['complete']
    for fragment in ('moving head', 'FX-730', 'https://example.invalid/fictional-path?model=FX-730&count=12'):
        assert fragment in item['text']


def test_exhausted_quota_is_clear_and_does_not_call_runner(inputs):
    runner = BlockRunner()
    lang = service(inputs, runner, hourly_limit=1)
    key = inputs.inbox.conversation(['Please send fictional first data', 'Please send fictional second data'])
    write(lang, 'translate', conversation_key=key, target_language='zh-CN', message_indices='0')
    with pytest.raises(RateLimited) as caught:
        write(lang, 'translate', conversation_key=key, target_language='zh-CN', message_indices='1')
    assert '额度' in str(caught.value) or '上限' in str(caught.value)
    assert caught.value.retry_after > 0 and len(runner.calls) == 1
    cached = write(lang, 'translate', conversation_key=key, target_language='zh-CN', message_indices='0')
    assert cached['items'][0]['cached']
    inputs.clock.advance(3601)
    assert write(lang, 'translate', conversation_key=key, target_language='zh-CN', message_indices='1')['items'][0]['complete']


def test_quota_exhaustion_after_first_batch_preserves_successful_items(inputs):
    runner = BlockRunner()
    lang = service(inputs, runner, hourly_limit=1)
    key = inputs.inbox.conversation(['虚构消息' + chr(0x7532 + i) for i in range(9)])
    with pytest.raises(RateLimited) as caught:
        write(lang, 'translate', conversation_key=key, target_language='de', message_indices=','.join(map(str, range(9))))
    partial = caught.value.result['items']
    assert len(partial) == 9
    assert all(item['complete'] for item in partial[:8])
    assert partial[8]['complete'] is False
    assert len(runner.calls) == 1


def test_runner_failure_consumes_quota_without_automatic_retry(inputs):
    runner = Mock(side_effect=RuntimeError('FICTIONAL-PRIVATE-MODEL-FAILURE'))
    lang = service(inputs, runner, hourly_limit=1)
    key = inputs.inbox.conversation(['Please send fictional data'])
    result = write(lang, 'translate', conversation_key=key, target_language='de', message_indices='0')
    assert not result['items'][0]['complete']
    assert 'FICTIONAL-PRIVATE-MODEL-FAILURE' not in repr(result)
    assert runner.call_count == 1
    with pytest.raises(RateLimited):
        write(lang, 'translate', conversation_key=key, target_language='de', message_indices='0')
    assert runner.call_count == 1


def test_compose_returns_native_and_back_translation_without_changing_inbox(inputs):
    original = 'Please quote FX-730, 12 pcs, USD 42.75, https://example.invalid/lamp'
    key = inputs.inbox.conversation([SAMPLES[0][1]])
    lang = service(inputs)
    write(lang, 'contact', conversation_key=key, native_language='es', locked='1')
    result = write(lang, 'compose', conversation_key=key, text=original)
    assert result['native_language'] == 'es' and result['my_language'] == 'zh-CN'
    assert result['text'] and result['back_translation']
    assert result['validation']['ok'] is True
    assert all(validate(original, version['text'], [])['ok'] for version in result['versions'])
    assert result['text'] == result['english_text'] + '\n\n' + result['local_text']
    assert len(inputs.inbox.conversations[key]['messages']) == 1


@pytest.mark.parametrize('bad_call', [1, 2])
def test_added_amount_in_native_or_back_translation_is_red_warning(inputs, bad_call):
    class AddingRunner(BlockRunner):
        def __call__(self, prompt):
            output = super().__call__(prompt)
            if len(self.calls) == bad_call:
                output = output.replace('\n[[/1]]', ' EUR 777.77\n[[/1]]')
            return output
    runner = AddingRunner()
    lang = service(inputs, runner)
    key = inputs.inbox.conversation([SAMPLES[0][1]])
    write(lang, 'contact', conversation_key=key, native_language='es', locked='1')
    result = write(lang, 'compose', conversation_key=key, text='Please quote FX-730, 12 pcs')
    assert result['validation']['ok'] is False
    assert any(warning['kind'] == 'error' and 'EUR 777.77' in warning['fragments'] for warning in result['warnings'])
    assert result['text'] and result['back_translation']
    assert len(inputs.inbox.conversations[key]['messages']) == 1


def test_numeric_validator_reports_added_amount_and_link():
    source = 'Please quote FX-730, 12 pcs, USD 42.75'
    result = validate(source, source + ' EUR 777.77 https://example.invalid/added')
    assert result['ok'] is False
    assert 'EUR 777.77' in result['added']
    assert 'https://example.invalid/added' in result['added']


@pytest.mark.parametrize('source,replacement', [('10 USD', '十美元'), ('ZX42', '灯具')])
def test_glossary_translation_cannot_hide_changed_number_or_model(source, replacement):
    glossary = [{'term': source, 'mode': 'translate', 'translation': replacement}]
    assert validate(source, replacement, glossary)['ok'] is False


@pytest.mark.parametrize('source,changed', [('-$10', '$10'), ('+$10', '$10'),
                                         ('-3.5 kg', '3.5 kg'), ('+3.5 kg', '3.5 kg')])
def test_numeric_sign_cannot_disappear_unnoticed(source, changed):
    assert validate(source, changed)['ok'] is False


def test_unknown_or_forged_conversation_is_rejected_before_model(inputs):
    runner = Mock(side_effect=AssertionError('参数验证前不调用模型'))
    lang = service(inputs, runner)
    forged = make_key('conversation', 'alibaba', 'fictional-account', 'fictional-nonexistent')
    for operation, fields in (('detect', {}), ('translate', {'message_indices': '0'}),
                              ('compose', {'text': 'Fictional'}), ('contact', {'native_language': 'de', 'locked': '1'})):
        with pytest.raises((BadRequest, Missing)):
            write(lang, operation, conversation_key=forged, **fields)
    assert not inputs.path.exists()
    runner.assert_not_called()


@pytest.mark.parametrize('operation,fields', [
    ('settings', {'my_language': '../../private'}),
    ('settings', {'auto_native_send': 'true'}),
    ('settings', {'database_path': '/fictional/private.sqlite3'}),
    ('glossary_save', {'term': 'x\ny', 'mode': 'keep'}),
    ('glossary_save', {'term': '__KEEP_0000__', 'mode': 'keep'}),
    ('glossary_save', {'term': 'fixture', 'mode': 'translate', 'translation': ''}),
    ('glossary_delete', {'id': '1 OR 1=1'}),
    ('contact', {'conversation_key': '../../private', 'native_language': 'de', 'locked': '1'}),
    ('compose', {'conversation_key': 'invalid', 'text': '\ud800'}),
])
def test_invalid_language_forms_cannot_create_database(inputs, operation, fields):
    runner = Mock(side_effect=AssertionError('无效请求不得调用模型'))
    with pytest.raises(BadRequest):
        write(service(inputs, runner), operation, **fields)
    assert not inputs.path.exists()
    runner.assert_not_called()


def test_parallel_identical_translations_are_single_flight(inputs):
    runner = BlockRunner()
    one, two = service(inputs, runner), service(inputs, runner)
    key = inputs.inbox.conversation(['Please send fictional concurrent data'])
    fields = dict(conversation_key=key, target_language='zh-CN', message_indices='0')
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda lang: write(lang, 'translate', **fields), (one, two)))
    assert all(result['items'][0]['complete'] for result in results)
    assert len(runner.calls) == 1


def test_audit_excludes_raw_text_and_model_exception(inputs):
    original = 'FICTIONAL-PRIVATE-CONTENT FX-730'
    key = inputs.inbox.conversation([original])
    lang = service(inputs)
    write(lang, 'translate', conversation_key=key, target_language='zh-CN', message_indices='0')
    with sqlite3.connect(inputs.path) as db:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        audit_tables = [table for table in tables if 'audit' in table]
        assert audit_tables
        for table in audit_tables:
            # Table names are enumerated from the temporary database, not user input.
            rows = list(db.execute('SELECT * FROM "' + table + '"'))
            assert original not in repr(rows)
            assert '虚构译文' not in repr(rows)
