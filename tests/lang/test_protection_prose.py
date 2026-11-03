from console.lang.protection import protect


def kept(text):
    return set(protect(text, [])[1].values())


def test_plain_title_case_words_are_prose_not_brands():
    # Sentence starts and German nouns must reach the model, otherwise they stay untranslated.
    assert kept('Preciso de dez peças. Qual o prazo? Contacto: ver abaixo. Lieferung nach Brasil') == set()


def test_acronyms_camel_case_and_marked_names_stay_exact():
    found = kept('Need DMX512 support and a GrandMA desk, FOB price for Acme® lights')
    assert {'DMX512', 'GrandMA', 'FOB', 'Acme®'} <= found


def test_chinese_counters_and_units_are_translated_words():
    from console.lang.protection import validate
    # 20 件 / 15 天 become "20 unidades" / "15 días"; only the numbers are facts to preserve.
    assert kept('MH-300 摇头灯 20 台，交期 15 天，功率 350 瓦') == {'MH-300', '20', '15', '350'}
    assert validate('Necesito 20 unidades', '我需要 20 件')['ok']
