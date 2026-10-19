from html.parser import HTMLParser
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]
CPQ = ROOT / 'src/console/static/js/cpq'
STROBE = ROOT / 'src/console/static/strobe'


def javascript_files():
    files = []
    for directory in (CPQ, STROBE):
        assert directory.is_dir(), f'Missing directory: {directory}'
        found = sorted(directory.rglob('*.js'))
        assert found, f'No JavaScript files in {directory}'
        files.extend(found)
    assert CPQ / 'cpq-drawer.js' in files
    assert CPQ / 'entry.js' in files
    return files


def test_drawer_js_no_forbidden_apis():
    patterns = (
        r'\beval\s*\(', r'\bnew\s+Function\b',
        r'\binnerHTML\b', r'\bouterHTML\b',
        r'\binsertAdjacentHTML\b', r'\bdocument\s*\.\s*write\b',
    )
    violations = []
    for path in javascript_files():
        if path == CPQ / 'cpq-engine.js':
            continue
        source = path.read_text(encoding='utf-8')
        for pattern in patterns:
            for match in re.finditer(pattern, source):
                line = source.count('\n', 0, match.start()) + 1
                violations.append(f'{path.relative_to(ROOT)}:{line}: {match.group()}')
    assert not violations, '\n'.join(violations)


def test_drawer_js_no_hardcoded_thresholds():
    forbidden = re.compile(r'(?<![\w$.])(?:0\.15|0\.021|260)(?![\w.])')
    excluded = {CPQ / 'cpq-engine.js', CPQ / 'drawer-model.js'}
    violations = []
    for path in javascript_files():
        if path in excluded:
            continue
        source = path.read_text(encoding='utf-8')
        for match in forbidden.finditer(source):
            line = source.count('\n', 0, match.start()) + 1
            violations.append(f'{path.relative_to(ROOT)}:{line}: {match.group()}')
    assert not violations, '\n'.join(violations)


def test_drawer_posts_json_only():
    source = (CPQ / 'cpq-drawer.js').read_text(encoding='utf-8')
    calls = re.findall(
        r'''\bfetch\s*\(\s*['"]/api/cpq/calculate['"]\s*,\s*\{(.*?)\}\s*\)''',
        source, flags=re.S,
    )
    assert calls, 'Missing calculate fetch call'
    assert len(calls) == len(re.findall(r'''\bmethod\s*:\s*['"]POST['"]''', source))
    for options in calls:
        assert re.search(r'''\bmethod\s*:\s*['"]POST['"]''', options)
        assert re.search(r'''\bcredentials\s*:\s*['"]same-origin['"]''', options)
        assert re.search(
            r'''\bheaders\s*:\s*\{[^}]*['"]Content-Type['"]\s*:\s*['"]application/json['"]''',
            options, flags=re.S,
        )
        assert re.search(r'\bbody\s*:\s*JSON\.stringify\s*\(', options)
    assert 'X-CSRF-Token' in source
    assert 'csrf-token' in source
    assert not re.search(r'\bnew\s+FormData\b', source)


class CostBasisParser(HTMLParser):
    VOID = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.found = 0
        self.parts = []
        self.masked = None

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        target = attributes.get('id') == 'cpq-cost-basis'
        if target:
            self.found += 1
            self.masked = attributes.get('data-masked')
        if self.depth:
            if tag not in self.VOID:
                self.depth += 1
        elif target and tag not in self.VOID:
            self.depth = 1

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self.VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if self.depth and tag not in self.VOID:
            self.depth -= 1

    def handle_data(self, data):
        if self.depth:
            self.parts.append(data)


def test_cost_basis_not_in_markup(manager):
    response = manager.get('/strobes')
    assert response.status_code == 200
    parser = CostBasisParser()
    parser.feed(response.text)
    parser.close()
    assert parser.found == 1, 'Expected exactly one cost-basis element'
    assert parser.masked == 'true'
    content = ''.join(parser.parts)
    assert content.strip(), 'Expected masked placeholder text'
    assert not any(character.isdigit() for character in content), 'Cost basis must not appear in server-rendered markup'
