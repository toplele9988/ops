"""Conservative script and stop-word clues; countries are suggestions only."""
from collections import Counter
import re

LANGUAGES = (
    ('zh-CN', '简体中文'), ('zh-TW', '繁体中文'), ('en', '英语'), ('es', '西班牙语'),
    ('pt', '葡萄牙语'), ('ru', '俄语'), ('ar', '阿拉伯语'), ('th', '泰语'),
    ('id', '印尼语'), ('de', '德语'), ('fr', '法语'), ('ja', '日语'), ('ko', '韩语'),
    ('hi', '印地语'), ('it', '意大利语'), ('tr', '土耳其语'), ('vi', '越南语'),
    ('uk', '乌克兰语'), ('fa', '波斯语'), ('ur', '乌尔都语'),
    ('pl', '波兰语'), ('nl', '荷兰语'), ('el', '希腊语'), ('he', '希伯来语'),
    ('ms', '马来语'), ('bn', '孟加拉语'), ('ta', '泰米尔语'), ('te', '泰卢固语'),
    ('sv', '瑞典语'), ('da', '丹麦语'), ('fi', '芬兰语'), ('no', '挪威语'),
    ('cs', '捷克语'), ('ro', '罗马尼亚语'), ('hu', '匈牙利语'), ('sw', '斯瓦希里语'),
    ('fil', '菲律宾语'), ('sk', '斯洛伐克语'), ('bg', '保加利亚语'), ('sr', '塞尔维亚语'),
    ('hr', '克罗地亚语'), ('lt', '立陶宛语'), ('lv', '拉脱维亚语'), ('et', '爱沙尼亚语'),
    ('km', '高棉语'), ('lo', '老挝语'), ('my', '缅甸语'), ('ne', '尼泊尔语'),
    ('si', '僧伽罗语'), ('am', '阿姆哈拉语'), ('az', '阿塞拜疆语'), ('uz', '乌兹别克语'),
    ('kk', '哈萨克语'), ('mn', '蒙古语'), ('ka', '格鲁吉亚语'), ('hy', '亚美尼亚语'),
)
CODES = frozenset(code for code, _ in LANGUAGES)


def needs_chinese_translation(message):
    """Skip our own words, empty/media-only text, and Chinese prose without a model call."""
    if message.get('role') != 'buyer':
        return False
    text = message.get('text', '').strip()
    clean = re.sub(r'https?://\S+|\b[A-Za-z0-9_.-]*\d[A-Za-z0-9_.-]*\b', '', text)
    letters = re.findall(r'[^\W\d_]', clean, re.U)
    if not letters:
        return False
    # Kana and Hangul distinguish Japanese/Korean from Chinese, even in mixed text.
    if re.search(r'[\u3040-\u30ff\uac00-\ud7af]', clean):
        return True
    chinese = len(re.findall(r'[\u3400-\u9fff\U00020000-\U0002fa1f]', clean))
    return not chinese or chinese / len(letters) < .4
WORDS = {
    'es': 'hola gracias necesito necesitamos quiero queremos precio precios cuánto cuanto puede puede enviar envío envio para por favor estos estas luces gustaría gustaria',
    'pt': 'olá ola obrigado obrigada preciso precisamos gostaria você voce vocês voces não nao preço preco preços precos são sao para por favor essas estas luzes envio',
    'fr': 'bonjour merci veuillez voudrais besoin pouvez vous nous avec pour une des les est sont prix livraison éclairage eclairage combien',
    'de': 'hallo danke bitte möchte mochte möchten moechte ich wir sie können konnen für fur und der die das preis preise versand brauche benötigen',
    'id': 'halo terima kasih saya kami anda ingin butuh bisa berapa harga untuk dengan lampu kirim pengiriman apakah ini',
    'en': 'hello thanks thank please would could need want price prices shipping the this these that for with lights you your we are how much can',
    'it': 'ciao grazie vorrei bisogno potete avete quanto prezzo prezzi spedizione sono per delle della voglio luci',
    'tr': 'merhaba teşekkür tesekkur istiyorum ihtiyacım ihtiyacim fiyat kaç kac için icin lütfen lutfen gönderim gonderim bir bu ve',
    'vi': 'xin chào chao cảm cam ơn cần can tôi toi bạn ban giá gia bao nhiêu nhieu gửi gui đèn den và không khong',
}
STOPWORDS = {language: set(words.split()) for language, words in WORDS.items()}
COUNTRIES = {
    'spain': 'es', 'españa': 'es', '西班牙': 'es', 'mexico': 'es', '墨西哥': 'es',
    'brazil': 'pt', '巴西': 'pt', 'portugal': 'pt', '葡萄牙': 'pt',
    'russia': 'ru', '俄罗斯': 'ru', 'thailand': 'th', '泰国': 'th',
    'japan': 'ja', '日本': 'ja', 'indonesia': 'id', '印度尼西亚': 'id',
    'germany': 'de', '德国': 'de', 'france': 'fr', '法国': 'fr',
    'china': 'zh-CN', '中国': 'zh-CN', 'saudi arabia': 'ar', '沙特阿拉伯': 'ar',
}


def detect_text(text):
    clean = re.sub(r'https?://\S+|\b[A-Za-z]*\d[A-Za-z0-9-]*\b', '', text)
    counts = {
        'ja': len(re.findall(r'[\u3040-\u30ff]', clean)),
        'ko': len(re.findall(r'[\uac00-\ud7af]', clean)),
        'th': len(re.findall(r'[\u0e00-\u0e7f]', clean)),
        'hi': len(re.findall(r'[\u0900-\u097f]', clean)),
        'ar': len(re.findall(r'[\u0600-\u06ff]', clean)),
        'ru': len(re.findall(r'[\u0400-\u052f]', clean)),
        'zh-CN': len(re.findall(r'[\u3400-\u9fff]', clean)),
    }
    # Kana disambiguates Japanese even when most characters are kanji.
    for language in ('ja', 'ko', 'th'):
        if counts[language] >= 2:
            return dict(language=language, confidence=.98, method='script')
    language = max(counts, key=counts.get)
    if counts[language] >= 3:
        words = set(re.findall(r'[^\W\d_]+', clean.casefold(), re.U))
        uncertain = dict(language=None, confidence=0.0, method='uncertain')
        if language == 'ru':
            # Cyrillic is shared by many languages. Script alone is not a
            # confident Russian identity, and distinctive non-Russian letters
            # take precedence over common letters such as ы.
            if re.search('[әғқңөұүһјљњћђџ]', clean.casefold()):
                return uncertain
            if re.search('[іїєґ]', clean.casefold()):
                return dict(language='uk', confidence=.95, method='script')
            russian = {'мне', 'нужен', 'нужно', 'пожалуйста', 'пришлите', 'здравствуйте', 'хочу', 'сколько', 'спасибо', 'светильник'}
            if not re.search('[ыэё]', clean.casefold()) and len(words & russian) < 2:
                return uncertain
        elif language == 'hi':
            # Greetings and कृपया are shared with Nepali and are not evidence.
            hindi = {'मुझे', 'चाहिए', 'आपके', 'भेजें', 'चाहता', 'चाहते', 'चाहती'}
            devanagari = set(re.findall(r'[\u0900-\u097f]+', clean))
            if len(devanagari & hindi) < 2:
                return uncertain
        elif language == 'ar':
            arabic = {'مرحبا', 'مرحباً', 'أحتاج', 'إلى', 'هل', 'يمكنك', 'إرسال', 'المنتج', 'شكرا', 'شكراً', 'المسرح'}
            if re.search('[پچژگےڈڑٹھں]', clean) or len(words & arabic) < 2:
                return uncertain
        return dict(language=language, confidence=.95, method='script')
    words = set(re.findall(r"[^\W\d_]+", clean.casefold(), re.U))
    scores = Counter({code: len(words & stops) for code, stops in STOPWORDS.items()})
    ordered = scores.most_common(2)
    if ordered and ordered[0][1] >= 2 and ordered[0][1] >= ordered[1][1] + 1:
        code, score = ordered[0]
        confidence = min(.97, .78 + .035 * score + .025 * (score - ordered[1][1]))
        return dict(language=code, confidence=round(confidence, 3), method='stopwords')
    return dict(language=None, confidence=0.0, method='uncertain')


def vote(messages, existing=None):
    results = [detect_text(text) for text in dict.fromkeys(messages[-20:])]
    totals = Counter()
    for result in results:
        if result['language'] and result['confidence'] >= .8:
            totals[result['language']] += result['confidence']
    if not totals:
        return None
    ordered = totals.most_common()
    language, weight = ordered[0]
    confidence = weight / sum(totals.values())
    supporters = sum(r['language'] == language for r in results)
    # An isolated different sentence must not replace an established language.
    if confidence < .7 or (existing and existing != language and supporters < 2):
        return None
    evidence_confidence = sum(r['confidence'] for r in results if r['language'] == language) / supporters
    return dict(language=language, confidence=round(.8 * evidence_confidence + .2 * confidence, 3), supporters=supporters)
