from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[2]
CHART = ROOT / 'src/console/static/js/cpq/cpq-cost-chart.js'
DRAWER_CSS = ROOT / 'src/console/static/strobe/strobe_drawer.css'


def test_chart_module_has_no_forbidden_apis():
    source = CHART.read_text(encoding='utf-8')
    forbidden = (
        'innerHTML',
        'eval(',
        'new Function',
        'document.write',
        'http://',
        'https://',
    )
    for token in forbidden:
        assert token not in source, f'Forbidden chart token: {token}'


def test_drawer_css_no_external():
    source = DRAWER_CSS.read_text(encoding='utf-8').lower()
    for token in ('http', '@import', 'url(', 'expression'):
        assert token not in source, f'Forbidden CSS token: {token}'


def test_drawer_css_covers_alert_severities():
    source = DRAWER_CSS.read_text(encoding='utf-8')
    source = re.sub(r'/\*.*?\*/', '', source, flags=re.DOTALL)
    selectors = re.findall(r'([^{}]+)\{', source)
    for severity in ('fuse', 'heavy', 'warn', 'info', 'ok'):
        pattern = (
            r'\.cpq-alert\s*\[\s*data-severity\s*=\s*'
            r'(?:"' + severity + r'"|\x27' + severity + r'\x27|'
            + severity + r')\s*\]'
        )
        assert any(re.search(pattern, selector) for selector in selectors), (
            f'Missing alert severity selector: {severity}'
        )
