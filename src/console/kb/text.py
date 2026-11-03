"""Display-only sanitization and inexpensive lexical tokenization."""
import re
import unicodedata

TOKEN = re.compile(r'[\u3400-\u9fff]+|[a-z0-9]+', re.I)
HASH = re.compile(r'(?i)[a-f0-9]{64}')
PATH = re.compile(r'(?:[A-Za-z]:[\\/]|(?<!\w)(?:/|\.\.?/)|\\\\)[^\s，。；、）)\]<>]+')
RELATIVE_PATH = re.compile(r'(?<![\w/])[\w.-]+(?:[/\\][\w.-]+){2,}|'
                           r'(?<![\w/])[\w.-]+[/\\][\w.-]+\.[A-Za-z0-9]{1,8}\b')


def clean(value):
    return ''.join(c for c in str(value or '') if c in '\n\t' or unicodedata.category(c) not in ('Cc', 'Cf', 'Cs'))


def display(value):
    value = RELATIVE_PATH.sub('〔路径已隐藏〕', clean(value))
    return HASH.sub('〔标识已隐藏〕', PATH.sub('〔路径已隐藏〕', value))


_OPAQUE = re.compile(r'(?:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|[0-9a-f]{24,})(?:\.\w{1,5})?', re.I)


def title(value, fallback):
    # Filename metadata may contain absolute paths; expose the basename only.
    value = str(value or '').replace('\\', '/').rsplit('/', 1)[-1]
    value = display(value).strip()
    if _OPAQUE.fullmatch(value):  # a bare UUID/hash tells a person nothing; use the readable family name
        value = ''
    return (value or fallback)[:160]


def tokens(value):
    for match in TOKEN.finditer(value.casefold()):
        word = match.group()
        if '\u3400' <= word[0] <= '\u9fff':
            if len(word) == 1:
                yield word
            else:
                yield from (word[i:i+2] for i in range(len(word)-1))
        else:
            yield word


def snippet(value, query, limit=300):
    value = display(value)
    lower = value.casefold()
    positions = [lower.find(t) for t in tokens(query)]
    found = [p for p in positions if p >= 0]
    start = max(0, min(found) - 70) if found else 0
    return (('…' if start else '') + value[start:start + limit - (1 if start else 0)])[:limit]
