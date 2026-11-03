"""Conservative extraction from visible text; attachments are never read."""
import calendar
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
import re
import unicodedata
from zoneinfo import ZoneInfo

from console.archive.validation import MAX_FEN, normalize_model
from console.deals.waiting import seconds
from console.roles.common import message_ref, safe


NUMBER = r'(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{1,2})?'
CURRENCY = r'(?:人民币|美元|美金|港币|港元|日元|澳元|加元|RMB|CNY|USD|EUR|GBP|HKD|JPY|AUD|CAD|欧元|英镑|元|块钱|块|[¥￥$€£])'
UNIT = r'(?:公斤|千克|立方米|立方|kg\b|cbm\b|pcs\b|pieces?\b|units?\b|台|件|套|个|块|方|票|柜|箱)'
ATTACHMENT = re.compile(r'\[(?:图片|PDF|文件|附件|视频|语音|image|photo|document)[^\]]*\]', re.I)
CLUE = re.compile(CURRENCY + r'|每公斤|\b(?:kg|cbm|pcs|pieces?|units?|prices?|freight|air|sea|express|minimum|tax)\b|方|件|台|含税|不含税|运费|空运|海运|快递|最低收费|报价|单价', re.I)
RESTATED = re.compile(r'(?:你[们]?|贵司|对方|供应商|厂家|货代).{0,12}(?:报[的价]?|价格|单价|说[的]?|确认)|(?:按|按照|根据|复述|确认).{0,8}(?:你[们]?|贵司|对方|供应商|厂家|货代).{0,8}(?:报|价|说)|\b(?:you|supplier|forwarder)\s+(?:quoted|said|offered)|\byour\s+(?:price|quote)', re.I)
_PRICE = re.compile(
    r'(?P<prefix>' + CURRENCY + r')\s*(?P<a>' + NUMBER + r')'
    r'|(?<![A-Za-z0-9_.])(?P<b>' + NUMBER + r')\s*(?P<suffix>' + CURRENCY + r')'
    r'|(?<![A-Za-z0-9_.])(?P<c>' + NUMBER + r')\s*(?:/|每|一|per\s+)\s*(?P<unit>' + UNIT + r')'
    r'|(?P<label>含税|不含税|未税|税前|报价|单价|价格|运费|空运|海运|快递|price|rate)\s*[:：=]?\s*(?P<d>' + NUMBER + r')', re.I)
_MINIMUM = re.compile(r'(?:最低(?:收费)?|起步价|最低消费|minimum(?:\s+charge)?|min(?:imum)?\.?)\s*[:：=]?\s*(?:' + CURRENCY + r'\s*)?(?P<n>' + NUMBER + r')', re.I)
_MODES = {'air': ('空运', 'air freight', 'air'), 'sea': ('海运', 'sea freight', 'sea'),
          'express': ('快递', 'express'), 'rail': ('铁路', 'rail'), 'truck': ('卡车', '陆运', 'truck')}
_UNITS = {'公斤': 'kg', '千克': 'kg', 'kg': 'kg', '立方米': 'cbm', '立方': 'cbm', '方': 'cbm', 'cbm': 'cbm',
          '票': 'ticket', '柜': 'container', '台': '台', '件': '件', '套': '套', '个': '个', '块': '块', '箱': '箱',
          'pcs': 'pcs', 'piece': 'pcs', 'pieces': 'pcs', 'unit': 'pcs', 'units': 'pcs'}
_CURRENCIES = {'人民币': 'CNY', '元': 'CNY', '块钱': 'CNY', '块': 'CNY', '¥': 'CNY', '￥': 'CNY', 'RMB': 'CNY', 'CNY': 'CNY',
               '美金': 'USD', '美元': 'USD', '$': 'USD', 'USD': 'USD', '欧元': 'EUR', '€': 'EUR', 'EUR': 'EUR',
               '英镑': 'GBP', '£': 'GBP', 'GBP': 'GBP', 'HKD': 'HKD', '港币': 'HKD', '港元': 'HKD',
               'JPY': 'JPY', '日元': 'JPY', 'AUD': 'AUD', '澳元': 'AUD', 'CAD': 'CAD', '加元': 'CAD'}
_SEPARATOR = re.compile(r'[，。;；\n]|(?<!\d),(?!\d)')
# This vocabulary is intentionally small and extensible. A generic pronoun or
# a dimensional fragment is not a product; a model may identify an unfamiliar
# noun from the exact price-bearing product phrase instead.
PRODUCT_CLUE = re.compile(
    r'灯|电源|箱|线|架|控制器|地板|砖|配件|屏|电池|电机|镜头|支座|接头|音响|喇叭|幕布|'
    r'\d+(?:\.\d+)?\s*(?:瓦|[Ww](?![A-Za-z]))|(?:型号\s*[:：]?\s*)?[A-Za-z][A-Za-z0-9]*[-_]?\d+[A-Za-z0-9-]*|'
    r'\b(?:light|lamp|power\s+supply|cable|controller|case|panel|speaker|bracket)\b', re.I)
_SPEC = re.compile(r'\d+(?:\.\d+)?\s*(?:米|寸|瓦|(?:cm|mm|kg|[WV])(?![A-Za-z]))|\d\s*[x*×]\s*\d', re.I)
_EXPLICIT_PRICE = re.compile(r'元|块钱|RMB|CNY|USD|EUR|GBP|HKD|JPY|AUD|CAD|人民币|美元|美金|欧元|英镑|[¥￥$€£]|单价|报价|价格|含税', re.I)
_NEGOTIATION = re.compile(r'增加|加价|多加|优惠|便宜|减少|少|让|降|涨|差|加收|减去')
_QUESTION = re.compile(r'[?？]|(?:吗|么|呢|多少|能否|是不是)\s*[。.!！]*$|能否|是不是|是否')


def is_bundle_formula(text):
    # A price label such as 单价=100 is not an equation. Count only operators
    # between numbers; slashes before kg/pcs and dimensional x are not totals.
    operators = re.findall(r'(?=\d\s*([+*×÷/\-])\s*\d)', text)
    return bool(re.search(r'\d[^，。;；\n]*[=＝]\s*\d', text) or len(operators) >= 2)


def product_phrase(text, *, from_ai=False):
    value = safe(text, 200).strip(' ，,:：')
    if not value or re.fullmatch(r'(?:这款|那款|这个|那个|你原来的|原来的|价格可能高一点|就要)', value):
        return None
    return value if from_ai or PRODUCT_CLUE.search(value) else None


def has_price_clue(text):
    text = safe(ATTACHMENT.sub('', str(text or '')), 1000)
    return bool(re.search(r'\d', text) and CLUE.search(text))


def is_source(message):
    return message.get('role') == 'buyer' or (message.get('role') == 'seller' and bool(RESTATED.search(message.get('text') or '')))


def source_text(message):
    text = str(message.get('text') or '')
    if message.get('role') == 'seller':
        # A seller can restate a supplier quote and counteroffer in one message.
        # Only clauses explicitly attributed to the counterparty may contribute.
        text = '；'.join(part for part in _SEPARATOR.split(text) if RESTATED.search(part))
    return safe(text, 1000)


def money(value):
    try:
        value = str(value).replace(',', '')
        if not re.fullmatch(r'\d+(?:\.\d{1,2})?', value):
            return None
        fen = int(Decimal(value) * 100)
        return fen if 0 <= fen <= MAX_FEN else None
    except (InvalidOperation, ValueError):
        return None


def currency(value):
    if not value:
        return None
    value = unicodedata.normalize('NFKC', str(value)).strip()
    return _CURRENCIES.get(value.upper())


def unit(value):
    if not value:
        return None
    value = unicodedata.normalize('NFKC', str(value)).casefold()
    value = re.sub(r'\s+', '', value)
    pieces = re.split(r'[/／]', value)
    if len(pieces) > 1:
        found = []
        for piece in pieces:
            piece = re.sub(r'^(?:每|一)', '', piece)
            if _UNITS.get(piece):
                found.append(_UNITS[piece])
            elif currency(piece):
                continue
            else:
                return None
        if found and len(set(found)) == 1:
            return found[0]
    # AI answers often include the price basis with the denomination. Keep only
    # the denomination; the currency is validated separately against the text.
    value = re.sub(r'^(?:(?:人民币|rmb|cny|美元|美金|usd|元|块钱|块|¥|\$|€|£)(?:/|／|每)?)', '', value)
    value = re.sub(r'^(?:每|一)(?=[\u4e00-\u9fff])', '', value)
    value = value.strip('/／')
    return _UNITS.get(value)


def _day(sent_at, timezone):
    at = seconds(sent_at)
    return datetime.fromtimestamp(at, ZoneInfo(timezone)).date() if at is not None else None


def validity(text, sent_at, timezone='Asia/Shanghai'):
    """Only explicit validity expressions. A delivery '10天内' is not expiry."""
    fragments = [part.strip() for part in re.split(r'[，,。;；\n]', text)
                 if re.search(r'有效|valid\b|expires?\b', part, re.I)]
    original = safe('；'.join(fragments), 200) or None
    day = _day(sent_at, timezone)
    if not original or day is None or re.search(r'大概|可能|预计|暂定|左右|待定|待确认|不确定|不保证|不是|以.+为准|不含周末|工作日|节假日|尽量|约\s*\d|maybe|about|approximately|tentative|business\s+days?|working\s+days?', text, re.I):
        return original, None
    # More than one temporal expression is deliberately left for the owner.
    dates = []
    for fragment in fragments:
        if '本周' in fragment or re.search(r'valid\s+(?:through\s+)?this\s+week', fragment, re.I):
            dates.append(day + timedelta(days=6 - day.weekday()))
        if '月底前' in fragment or '本月底' in fragment:
            dates.append(day.replace(day=calendar.monthrange(day.year, day.month)[1]))
        for match in re.finditer(r'(?<!\d)(\d{1,3})\s*天(?:内)?|valid\s+for\s+(\d{1,3})\s+days?', fragment, re.I):
            number = int(match[1] or match[2])
            if number > 0:
                dates.append(day + timedelta(days=number))
        for match in re.finditer(r'\d{4}-\d{2}-\d{2}', fragment):
            try:
                dates.append(date.fromisoformat(match[0]))
            except ValueError:
                return original, None
    if len(dates) != 1 or dates[0] < day:
        return original, None
    return original, dates[0].isoformat()


def _mode(text):
    found = {mode for mode, words in _MODES.items() if any(
        (word in text if not word.isascii() else re.search(r'\b' + re.escape(word) + r'\b', text, re.I)) for word in words)}
    return next(iter(found)) if len(found) == 1 else None


def _text_after(text, label):
    match = re.search(label + r'\s*[:：]?\s*([^，,。;；\n]{1,100})', text, re.I)
    return safe(match[1], 100) if match else None


def _country(text):
    explicit = _text_after(text, r'(?:目的国|destination\s+country)')
    if explicit:
        return explicit
    match = re.search(r'(?:发往|运到|到|to\s+)(美国|英国|德国|法国|加拿大|澳大利亚|日本|USA\b|US\b|UK\b|Germany\b|France\b|Canada\b|Australia\b|Japan\b)', text, re.I)
    return match[1] if match else None


def _minimum(text):
    found = list(_MINIMUM.finditer(text))
    if len(found) != 1:
        return None
    match = found[0]
    rest = re.sub(r'^\s*' + CURRENCY, '', text[match.end():], count=1, flags=re.I).lstrip()
    if re.match(r'(?:\d|\.\d|[-~～至]\s*\d|[A-Za-z]|公斤|千克|立方|方|件|台|天)', rest):
        return None
    return money(match['n'])


def _extract_message(conversation, message, timezone, party_kind, text=None):
    # Work on the same bounded, redacted visible text sent to the model.
    text = source_text(message) if text is None else text
    visible = ATTACHMENT.sub('', text)
    if not (re.search(r'\d', visible) and CLUE.search(visible)):
        return []
    attachment = bool(ATTACHMENT.search(str(message.get('text') or '')))
    minimums = list(_MINIMUM.finditer(visible))
    results, seen = [], set()
    matches = [match for match in _PRICE.finditer(visible)
               if not any(start.start() <= match.start() < start.end() for start in minimums)]
    separators = list(_SEPARATOR.finditer(visible))
    for match in matches:
        before, after = visible[:match.start()].rstrip(), visible[match.end():].lstrip()
        # Reject negative/ranged amounts and partial matches (precision, model,
        # weight, power, quantity, lead time). A bare label is insufficient.
        if (re.search(r'[-−~～至]$', before) or re.match(r'(?:\d|\.\d|[-~～至]\s*\d)', after)
                or (match['d'] is not None and re.match(r'(?:[A-Za-z]|天|周|日|小时|公斤|千克|立方|米|寸|瓦|台|件|套|个|方)', after))
                or ((match['a'] is not None or match['b'] is not None) and re.match(r'[A-Za-z0-9_.]', visible[match.end():]))):
            continue
        price = money(next(value for value in (match['a'], match['b'], match['c'], match['d']) if value is not None))
        if price is None:
            continue
        local_start = max((part.end() for part in separators if part.end() <= match.start()), default=0)
        local_end = min((part.start() for part in separators if part.start() >= match.end()), default=len(visible))
        local = visible[local_start:local_end]
        sentence_start = max((visible.rfind(c, 0, match.start()) for c in (';', '；', '\n', '。')), default=-1) + 1
        sentence_end = min((position for c in (';', '；', '\n', '。')
                            if (position := visible.find(c, match.end())) >= 0), default=len(visible))
        if _QUESTION.search(visible[sentence_start:sentence_end]):
            continue
        local_prefix = visible[local_start:match.start()]
        adjustment = _NEGOTIATION.search(visible[sentence_start:match.start()])
        explicit_baseline = (PRODUCT_CLUE.search(local_prefix) and (
            match['label'] in ('报价', '单价', '价格', 'price', 'rate')
            or re.search(r'(?:原价|现价|基准价|单价|报价|价格)\s*[:：]?\s*$', local_prefix)))
        if adjustment and not explicit_baseline:
            continue
        # 块 can be either currency or a count. A specification nearby makes
        # count units unsafe unless the same sentence explicitly names money.
        if (match['suffix'] == '块' and _SPEC.search(visible)
                and not _EXPLICIT_PRICE.search(local)):
            continue
        if (match['suffix'] == '块' and re.match(r'(?:灯|电源|地板|砖|箱|控制器)', after)
                and not _EXPLICIT_PRICE.search(local)):
            continue
        bundle = is_bundle_formula(local)
        if bundle and re.search(r'[=＝]', local) and match.start() < local_start + max(local.rfind('='), local.rfind('＝')):
            continue
        facts = visible if len(matches) == 1 else local
        expiry_text, expiry = validity(facts, message.get('sent_at'), timezone)
        # An uncertainty qualifier elsewhere still vetoes a date, even when
        # multiple prices require facts to be confined to this local clause.
        if expiry and validity(visible, message.get('sent_at'), timezone)[1] is None:
            expiry = None
        minimum = _minimum(facts)
        lead = re.search(r'(?:交期|货期|lead\s*time)\s*[:：]?\s*\d+(?:\s*[-~至]\s*\d+)?\s*(?:天|days?|周|weeks?)', facts, re.I)
        moq = re.search(r'(?:起订量?|MOQ)\s*[:：]?\s*(\d{1,7})\s*(?:台|件|套|pcs)?', facts, re.I)
        destination = _country(facts)
        detail = _text_after(facts, r'(?:目的港|目的地|destination\s+(?:port|detail))')
        origin = _text_after(facts, r'(?:起运地|始发地|出发地|origin)')
        mode = _mode(local) or (_mode(visible) if party_kind == 'forwarder' else None)
        kind = 'freight' if party_kind == 'forwarder' or mode or re.search(r'运费|货代|freight', local, re.I) else 'offer'
        suffix = visible[match.end():match.end() + 35]
        unit_match = re.match(r'\s*(?:' + CURRENCY + r')?\s*(?:/|每|一|per\s+)\s*(' + UNIT + r')', suffix, re.I)
        denomination = unit(match['unit'] or (unit_match[1] if unit_match else None))
        denomination = denomination if kind == 'offer' or denomination in ('kg', 'cbm', 'ticket', 'container') else None
        code = currency(match['prefix'] or match['suffix'])
        if not code:
            after_currency = re.match(r'\s*(' + CURRENCY + r')', suffix, re.I)
            code = currency(after_currency[1]) if after_currency else None
        previous_price = max((other.end() for other in matches if other.end() <= match.start()), default=0)
        section_start = max(visible.rfind(c, 0, match.start()) for c in (';', '；', '\n')) + 1
        start = local_start if previous_price > section_start else section_start
        product_context = safe(visible[start:match.start()].strip(' ，,:：'), 200) if kind == 'offer' else None
        product = product_phrase(product_context) if kind == 'offer' and not bundle else None
        if bundle:
            kind, product_context = 'offer', None
        token = (price, code, denomination, product)
        if token in seen:
            continue
        seen.add(token)
        excerpt = safe(text, 180 if attachment else 200)
        if attachment:
            excerpt += '；有附件，需人工看'
        local_taxes = [word for word in ('不含税', '未税', '含税') if word in local]
        local_tax = local_taxes[0] if local_taxes else None
        results.append(dict(kind=kind, source_message_refs=[message_ref(conversation['key'], message)],
            product_text=product, price_fen=price, currency=code, unit=denomination,
            moq_units=int(moq[1]) if moq and 1 <= int(moq[1]) <= 1000000 else None,
            lead_time_text=safe(lead[0], 100) if lead else None, tax_text=local_tax,
            route=safe(facts, 200) if kind == 'freight' else None,
            mode=mode if kind == 'freight' else None, origin=origin if kind == 'freight' else None,
            destination_country=destination if kind == 'freight' else None,
            destination_detail=detail if kind == 'freight' else None,
            min_charge_fen=minimum if kind == 'freight' else None,
            validity_text=expiry_text, suggested_valid_until=expiry,
            excerpt=excerpt[:200], confidence=.2 if bundle or (kind == 'offer' and not product) else .35,
            source='rules', is_bundle=bundle, currency_guess=False, _product_context=product_context,
            quoted_on=_day(message.get('sent_at'), timezone).isoformat(), has_attachment=attachment))
    return results


def prepare(conversation, now, *, days=30, window=2, timezone='Asia/Shanghai', party_kind=None):
    """Keep only recent, known-direction windows around a visible price clue."""
    all_messages = list(conversation.get('messages') or ())[-300:]
    eligible = {i for i, message in enumerate(all_messages)
                if (at := seconds(message.get('sent_at'))) is not None
                and 0 <= now - at <= days * 86400 and is_source(message)}
    texts = {i: source_text(all_messages[i]) for i in eligible}
    clues = {i for i in eligible if re.search(r'\d', ATTACHMENT.sub('', texts[i])) and CLUE.search(ATTACHMENT.sub('', texts[i]))}
    selected = {i for clue in clues for i in range(max(0, clue-window), min(len(all_messages), clue+window+1)) if i in eligible}
    # Preserve every rule suggestion, but bound the model's newest windows to
    # twelve messages / ten thousand text characters.
    bounded, budget = [], 10000
    for i in sorted(selected, reverse=True)[:12]:
        text = texts[i][:budget]
        budget -= len(text)
        if text:
            bounded.append((i, text))
        if budget <= 0:
            break
    messages = [dict(number=i + 1, role=all_messages[i]['role'], text=text,
                     sent_at=all_messages[i]['sent_at'], clue=i in clues,
                     has_attachment=bool(ATTACHMENT.search(all_messages[i].get('text') or '')),
                     source_ref=message_ref(conversation['key'], all_messages[i]))
                for i, text in reversed(bounded)]
    candidates, numbers, contexts = [], [], []
    for i in sorted(clues):
        found = _extract_message(conversation, all_messages[i], timezone, party_kind, texts[i])
        contexts.extend(item.pop('_product_context') for item in found)
        candidates.extend(found)
        numbers.extend([i + 1] * len(found))
    # Infer only from explicit supplier prices in this clue's local window.
    # Mixed currencies and ambiguous count/specification text never establish
    # a CNY convention, and a later AI answer never inherits this guess.
    for item, number in zip(candidates, numbers):
        if item['currency']:
            continue
        currencies = {other['currency'] for other, other_number in zip(candidates, numbers)
                      if abs(other_number - number) <= window and other['currency'] and not other['currency_guess']}
        if currencies == {'CNY'}:
            item.update(currency='CNY', currency_guess=True, confidence=round(max(0, item['confidence'] - .1), 2))
    return dict(messages=messages, candidates=candidates, candidate_numbers=numbers,
                candidate_product_texts=contexts, timezone=timezone,
                conversation_key=conversation['key'], party_kind=party_kind)


def extract(conversation, now, **options):
    return [item for item in prepare(conversation, now, **options)['candidates'] if not item['is_bundle']]


def match_product(product_text, products, party_id=None):
    """Require one explicit full name/model; wattage and fuzzy overlap never match."""
    text = str(product_text or '').strip()
    phrases = {re.sub(r'^(?:这款|产品|型号|品名)\s*', '', phrase.strip()).strip().casefold()
               for phrase in re.split(r'[，,：:；;。\n]', text)}
    matched = set()
    for product in products:
        if product.get('status') == 'hidden' or (party_id is not None and product.get('supplier_id') != party_id):
            continue
        name, model = str(product.get('name') or '').strip(), str(product.get('model') or '').strip()
        exact_name = bool(len(name) >= 2 and name.casefold() in phrases)
        # A plain wattage/number or short acronym is not a model identity.
        exact_model = bool(len(normalize_model(model)) >= 3 and re.search(r'[A-Za-z]', model)
                           and re.search(r'\d', model) and not re.fullmatch(r'\d+\s*[Ww]', model)
                           and any(re.search(r'(?<![\w-])' + re.escape(model) + r'(?![\w-])', phrase, re.I)
                                   for phrase in phrases))
        if exact_name or exact_model:
            matched.add(product['id'])
    return next(iter(matched)) if len(matched) == 1 else None
