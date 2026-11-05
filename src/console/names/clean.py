"""Display text only. Never turn a fallback label into identity evidence."""
import re
import unicodedata


def display_text(value, limit=128):
    if not isinstance(value, str):
        return None
    value = unicodedata.normalize('NFC', value)
    value = ''.join(c for c in value if unicodedata.category(c) not in ('Cc', 'Cf', 'Cs'))
    return ' '.join(value.split()).replace('@', '＠')[:limit].strip() or None


def clean_name(value, limit=128):
    # roles.service imports channels; defer until channel modules are initialized.
    from console.roles.triage import placeholder_name
    value = display_text(value, limit)
    if (placeholder_name(value) or value in ('未命名群', '未命名联系人', '未命名公众号')
            or value and re.fullmatch(r'(?:WhatsApp(?: 群)?\s*·\s*\S+|成员\s+\S{1,4})', value)):
        return None
    return value or None


def first_name(*values):
    return next((name for value in values if (name := clean_name(value))), None)
