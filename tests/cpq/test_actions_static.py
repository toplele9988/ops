from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[2]
ACTIONS_PATH = ROOT / 'src/console/static/js/cpq/cpq-actions.js'
CSS_PATH = ROOT / 'src/console/static/strobe/strobe_actions.css'


def actions_source():
    return ACTIONS_PATH.read_text(encoding='utf-8')


def test_actions_js_no_forbidden_apis():
    source = actions_source()
    for forbidden in (
        'innerHTML', 'outerHTML', 'insertAdjacentHTML', 'eval(',
        'new Function', 'document.write', 'window.open', 'http://', 'https://',
    ):
        assert forbidden not in source, f'Forbidden source fragment: {forbidden}'
    assert not re.search(r'\beval\s*\(', source)
    assert not re.search(r'\bnew\s+Function\b', source)
    assert not re.search(r'\bdocument\s*\.\s*write\s*\(', source)
    assert not re.search(r'\bwindow\s*\.\s*open\s*\(', source)


def test_actions_js_no_hardcoded_thresholds():
    source = actions_source()
    for forbidden in ('0.15', '0.021', '260'):
        assert forbidden not in source, f'Hardcoded business value: {forbidden}'


def test_actions_posts_json_with_csrf_header():
    source = actions_source()
    assert 'application/json' in source
    assert 'Content-Type' in source
    assert 'X-CSRF-Token' in source
    assert 'csrf-token' in source
    assert re.search(r'\bmethod\s*=\s*[\x27\x22]POST[\x27\x22]', source)
    assert re.search(r'\bcredentials\s*:\s*[\x27\x22]same-origin[\x27\x22]', source)
    assert 'JSON.stringify(payload)' in source
    assert '/api/cpq/quotes' in source


def test_no_send_buttons():
    source = actions_source()
    lowered = source.lower()
    for forbidden in ('mailto:', 'wa.me', 'whatsapp.com', 'smtp', 'api.whatsapp'):
        assert forbidden not in lowered, f'Forbidden delivery mechanism: {forbidden}'
    assert not re.search(r'\bsend[A-Za-z0-9_$]*\s*(?:\(|=|:)', source)


def test_actions_css_no_external():
    source = CSS_PATH.read_text(encoding='utf-8')
    lowered = source.lower()
    assert '#cpq-quote-panel' in source
    assert 'http://' not in lowered
    assert 'https://' not in lowered
    assert not re.search(r'url\s*\(\s*[\x27\x22]?\s*//', source, re.IGNORECASE)
    assert not re.search(r'@import\b', source, re.IGNORECASE)
