from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit, unquote
import re

ROOT = Path(__file__).resolve().parents[2]
CONSOLE = ROOT / 'src' / 'console'
SCOPES = (CONSOLE/'templates/strobe', CONSOLE/'static/strobe', CONSOLE/'static/js/cpq')

class Page(HTMLParser):
    def __init__(self, text):
        super().__init__(convert_charrefs=True)
        self.tags = []
        self.feed(text)
    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))
    handle_startendtag = handle_starttag

PATTERNS = [
    re.compile(r'''\b(?:src|href)\s*=\s*['"]([^'"]+)''', re.I),
    re.compile(r'''\burl\(\s*['"]?([^\s)'";]+)''', re.I),
    re.compile(r'''@import\s+(?:url\(\s*)?['"]([^'"]+)''', re.I),
    re.compile(r'''\bfetch\(\s*['"]([^'"]+)''', re.I),
    re.compile(r'''\bimport\s+(?:[^;\n]*?\s+from\s*)?['"]([^'"]+)''', re.I),
    re.compile(r'''\bimport\(\s*['"]([^'"]+)''', re.I),
]

def assert_inline_safe(text):
    for tag, attrs in Page(text).tags:
        if tag == 'script':
            assert attrs.get('src') or attrs.get('type', '').lower() == 'application/json', attrs
        for key, value in attrs.items():
            assert key != 'style' and not re.fullmatch(r'on[a-z]+', key), (tag, key)
            if value:
                assert not re.sub(r'[\x00-\x20]', '', value).lower().startswith('javascript:'), value

def refs(page):
    for tag, attrs in page.tags:
        for key in ('src', 'href', 'poster', 'action'):
            if attrs.get(key):
                yield tag, key, attrs[key]
        if attrs.get('srcset'):
            for part in attrs['srcset'].split(','):
                yield tag, 'srcset', part.strip().split()[0]

def assert_relative(value):
    parsed = urlsplit(value)
    assert not parsed.scheme and not parsed.netloc and not value.startswith('//') and '\\' not in value, value

def rendered(client):
    response = client.get('/strobes')
    assert response.status_code == 200, response.text
    return response

def test_no_external_urls_in_templates_and_static(sales):
    paths = set()
    for scope in SCOPES:
        assert scope.is_dir(), scope
        paths.update(p for p in scope.rglob('*') if p.is_file() and p.suffix in {'.html', '.css', '.js', '.mjs'})
    response = rendered(sales)
    for tag, key, value in refs(Page(response.text)):
        if value.startswith('/static/') and tag in {'script', 'link'}:
            path = (CONSOLE / unquote(urlsplit(value).path).lstrip('/')).resolve()
            assert path.is_relative_to(CONSOLE.resolve()) and path.is_file(), path
            paths.add(path)
    assert paths
    for path in sorted(paths):
        text = path.read_text(encoding='utf-8')
        for pattern in PATTERNS:
            for match in pattern.finditer(text):
                value = match.group(1).strip()
                if value.startswith('http://www.w3.org/'):
                    continue
                assert not re.match(r'^(?:https?:)?//', value, re.I), (path, value)

def test_no_inline_script_or_handlers():
    paths = list((CONSOLE/'templates/strobe').rglob('*.html'))
    assert paths
    for path in paths:
        assert_inline_safe(path.read_text(encoding='utf-8'))

def test_rendered_page_has_no_external_refs(sales):
    response = rendered(sales)
    assert_inline_safe(response.text)
    page = Page(response.text)
    for _, _, value in refs(page):
        assert_relative(value)
    links = [attrs.get('href') for tag, attrs in page.tags if tag == 'link']
    assert '/static/strobe/strobe.css' in links, 'Integration must add head_extra to v3/base3.html'
    scripts = [attrs.get('src') for tag, attrs in page.tags if tag == 'script']
    assert scripts.count('/static/strobe/gallery.js') == 1
    assert scripts.count('/static/vendor/tabler.min.js') == 1
    assert scripts.count('/static/vendor/echarts.min.js') == 1

def test_csp_header_compatible(sales):
    response = rendered(sales)
    assert_inline_safe(response.text)
    for _, _, value in refs(Page(response.text)):
        assert_relative(value)
    header = response.headers.get('content-security-policy')
    if header:
        directives = {}
        for part in header.split(';'):
            tokens = part.split()
            if tokens:
                directives[tokens[0]] = tokens[1:]
        scripts = directives.get('script-src', directives.get('default-src', []))
        assert "'unsafe-inline'" not in scripts
        assert "'unsafe-eval'" not in scripts
        assert "'self'" in scripts
