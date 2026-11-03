"""Mask exact business fragments before translation and check them afterwards."""
from collections import Counter
import re

_URL = r'https?://[^\s<>"\u3000]+'
_NUMBER = r'[+\-−]?\d+(?:[.,]\d+)*'
_MONEY = (r'(?:[+\-−]?(?:USD|EUR|GBP|CNY|RMB|JPY|AUD|CAD|HKD|US\$|[$€£¥￥])\s*' + _NUMBER +
          r'|' + _NUMBER + r'\s*(?:USD|EUR|GBP|CNY|RMB|JPY|AUD|CAD|HKD|美元|人民币|欧元))')
# Chinese units and counters (台, 件, 天, 瓦...) are words; they are translated, only the number is checked.
_UNIT = _NUMBER + r'\s*(?:kW|KW|kVA|MHz|kHz|Hz|mAh|Ah|VAC|VDC|V|W|A|mm|cm|km|kg|mg|g|m|ft|inch|in|°C|℃|°|%|pcs|units?)(?![A-Za-z])'
_MODEL = r'\b(?=[A-Za-z0-9_.-]*[A-Za-z])(?=[A-Za-z0-9_.-]*\d)[A-Za-z0-9]+(?:[_.-][A-Za-z0-9]+)*\b'
# Only acronyms (DMX, FOB), CamelCase names (GrandMA) and marked names (Brand®) are masked. Plain Title Case
# words are ordinary prose in many languages (sentence starts, German nouns, place names) and must be translated.
_BRAND = r'\b(?:[A-Z][a-z]+(?:[A-Z][A-Za-z]*)+|[A-Z]{2,})(?:[®™])?\b|\b[A-Z][a-z]+[®™]'
_GRAMMAR = frozenset('The A An This These That Those I We You It He She They Please Hello Hi Thanks Thank Need Want Can Could Would Will Do Does Is Are Our Your My Dear Bonjour Hola Ola'.split())
_BASE = re.compile('|'.join((_URL, _MONEY, _UNIT, _MODEL, _NUMBER, _BRAND, r'\[\[/?\d+\]\]|__KEEP_\d+__')))
TOKEN = re.compile(r'__KEEP_\d+__')


def facts(text):
    expression = re.compile('|'.join((_URL, _MONEY, _UNIT, _MODEL, _NUMBER)))
    return Counter(match[0].rstrip('.,;!?，。；！？）)') for match in expression.finditer(text))


def _matches(text, glossary):
    matches = []
    for match in _BASE.finditer(text):
        value = match[0]
        if value in _GRAMMAR:
            continue
        # Closing punctuation belongs to prose, rather than a URL.
        end = match.end()
        if value.startswith(('http://', 'https://')):
            value = value.rstrip('.,;!?，。；！？）)')
            end = match.start() + len(value)
        if value:
            matches.append((match.start(), end, value, value))
    for term in glossary:
        expression = re.escape(term['term'])
        if term['term'][0].isascii() and term['term'][0].isalnum():
            expression = r'(?<![A-Za-z0-9_])' + expression
        if term['term'][-1].isascii() and term['term'][-1].isalnum():
            expression += r'(?![A-Za-z0-9_])'
        for match in re.finditer(expression, text, re.I):
            replacement = match[0] if term['mode'] == 'keep' else term['translation']
            matches.append((match.start(), match.end(), match[0], replacement))
    # Prefer a complete URL/amount/term over its component numbers or words.
    matches.sort(key=lambda item: (item[0], -(item[1] - item[0]), item[2] == item[3]))
    accepted, end = [], -1
    for match in matches:
        if match[0] >= end:
            accepted.append(match)
            end = match[1]
    return accepted


def protect(text, glossary):
    mapping, parts, start = {}, [], 0
    for left, right, _, replacement in _matches(text, glossary):
        index = len(mapping)
        token = '__KEEP_%04d__' % index
        while token in text or token in mapping:
            index += 1
            token = '__KEEP_%04d__' % index
        parts.extend((text[start:left], token))
        mapping[token] = replacement
        start = right
    parts.append(text[start:])
    return ''.join(parts), mapping


def restore(text, mapping):
    expected = Counter(mapping.keys())
    actual = Counter(TOKEN.findall(text))
    if expected != actual:
        raise ValueError('译文丢失或改动了保留片段，请单独重试这一块')
    return TOKEN.sub(lambda match: mapping[match[0]], text)


def validate(source, translation, glossary=()):
    expected = Counter(match[3] for match in _matches(source, glossary))
    missing = [value for value, count in expected.items() if translation.count(value) < count]
    # Added numeric/model/link claims need the same attention as lost ones.
    original, actual = facts(source), facts(translation)
    missing = list(dict.fromkeys(missing + [value for value, count in original.items() if count > actual[value]]))
    added = [value for value, count in actual.items() if count > original[value]]
    return dict(ok=not missing and not added, missing=missing, added=added)
