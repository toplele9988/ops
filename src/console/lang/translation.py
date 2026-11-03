"""Bounded numbered blocks: one invalid block never discards its neighbours."""
import hashlib
import json
import re
from collections import Counter

from console.inbox.ai import _answer_text
from . import prompts
from .protection import _matches, protect, restore, validate

CHUNK_SIZE = 1800
BATCH_SIZE = 8


def digest(text):
    return hashlib.sha256(text.encode('utf8')).hexdigest()


def chunks(text, size=CHUNK_SIZE, glossary=()):
    # Prefer a sentence or whitespace boundary without dropping any characters.
    result = []
    while len(text) > size:
        end = max(text.rfind(mark, 0, size + 1) + 1 for mark in ('\n', '。', '. ', ' '))
        end = end if end >= size // 2 else size
        # Do not split inside a protected URL/model/money fragment.
        for start, stop, _, _ in _matches(text, glossary):
            if start < end < stop:
                end = start if start >= size // 2 else stop
        result.append(text[:end])
        text = text[end:]
    if text:
        result.append(text)
    return result


def parse_blocks(output, expected):
    if not isinstance(output, str) or len(output) > 1024 * 1024:
        return {}
    answer = _answer_text(output)
    # Slice at every start marker first. A malformed outer block may not swallow
    # a correctly closed neighbouring block, even if its own close comes later.
    starts = list(re.finditer(r'^\[\[(\d+)\]\][ \t]*\n', answer, re.M))
    counts = Counter(int(marker[1]) for marker in starts)
    result = {}
    for index, marker in enumerate(starts):
        number = int(marker[1])
        if number not in expected or counts[number] != 1:
            continue
        end = starts[index + 1].start() if index + 1 < len(starts) else len(answer)
        body = answer[marker.end():end]
        close = re.search(r'^\[\[/%d\]\][ \t]*(?=\n|$)' % number, body, re.M)
        text = body[:close.start()].strip() if close else ''
        if text and len(text) <= 20000 and not re.search(r'\[\[/?\d+\]\]', text):
            result[number] = text
    return result


def glossary_fingerprint(glossary):
    return digest(json.dumps([(item['term'], item['mode'], item['translation']) for item in glossary], ensure_ascii=False))


def translate_blocks(service, entries, target, glossary, *, strict=True):
    """Mutate each entry independently and persist validated successes immediately."""
    fingerprint = glossary_fingerprint(glossary)
    pending, aliases = [], {}
    with service.store.read() as db:
        for entry in entries:
            entry['cached'] = False
            row = db.execute('SELECT text FROM translation_cache WHERE source_hash=? AND target_language=? AND glossary_hash=?',
                             (digest(entry['source']), target, fingerprint)).fetchone() if db else None
            if row:
                entry.update(text=row['text'], error=None, cached=True)
            else:
                key = digest(entry['source'])
                if key not in aliases:
                    pending.append(entry)
                    aliases[key] = []
                aliases[key].append(entry)

    def publish(entry):
        for alias in aliases[digest(entry['source'])]:
            if alias is not entry:
                alias.update(text=entry.get('text'), error=entry.get('error'), cached=entry.get('cached', False))
    for offset in range(0, len(pending), BATCH_SIZE):
        batch = pending[offset:offset + BATCH_SIZE]
        masked = [protect(entry['source'], glossary) for entry in batch]
        numbered = [(number, item[0]) for number, item in enumerate(masked, 1)]
        try:
            output = service.invoke(prompts.translation(numbered, target))
        except service.rate_error:
            raise
        except Exception:
            for entry in batch:
                entry.update(text=None, error='这一块暂时没有译好，请单独重试；其他译文已保留')
                publish(entry)
            continue
        parsed = parse_blocks(output, set(range(1, len(batch) + 1)))
        for number, (entry, (_, mapping)) in enumerate(zip(batch, masked), 1):
            try:
                if number not in parsed:
                    raise ValueError
                text = restore(parsed[number], mapping)
                source = entry['source']
                leading = source[:len(source) - len(source.lstrip())]
                trailing = source[len(source.rstrip()):]
                text = leading + text.strip() + trailing
                check = validate(entry['source'], text, glossary)
                if strict and not check['ok']:
                    raise ValueError
                text.encode('utf8')
                entry.update(text=text, error=None)
                if check['ok']:
                    with service.store.write() as db:
                        db.execute('INSERT OR IGNORE INTO translation_cache VALUES(?,?,?,?,?)',
                                   (digest(entry['source']), target, fingerprint, text, service.clock()))
            except (ValueError, UnicodeError):
                entry.update(text=None, error='这一块的格式或保留片段未通过校验，请单独重试；其他译文已保留')
            publish(entry)
