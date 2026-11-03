"""Serve the pre-generated supplier packing report behind the console auth gates.

Mount through v3.build. The report is a single self-contained HTML file written
by scripts/m1_packing_report.py; this module only reads it. Nothing here
queries the knowledge database.

The console's global middleware forces `script-src 'self'; style-src 'self'` on
every response, so the single-file report is split at serve time into the page
plus same-origin /report.css and /report.js, all behind the same auth gate.
"""
import re
from pathlib import Path

from starlette.concurrency import run_in_threadpool
from starlette.responses import HTMLResponse, Response
from starlette.routing import Route

REPORT_PATH = Path('/var/lib/imgw/supplier-packing/packing_report.html')
MAX_BYTES = 8 * 1024 * 1024
PAGE_URL = '/v3/supplier-packing'
CSS_URL = PAGE_URL + '/report.css'
JS_URL = PAGE_URL + '/report.js'
BACK_LINK = '<p class="console-back"><a href="/v3/">&larr; 返回控制台</a></p>'
BACK_CSS = '\n.console-back{margin:12px 16px}\n'
_BLOCK = re.compile(r'<(style|script)(?:\s[^>]*)?>(.*?)</\1>', re.S | re.I)
_EXTERNAL = re.compile(
    r'<(?:script|link|img|iframe|embed|object|form|source|video|audio)\b[^>]*\b(?:src|href|action|data)\s*='
    r'|<a\b[^>]*\bhref\s*=\s*["\']?\s*(?:[a-z][a-z0-9+.-]*:|//)',
    re.I)
_CSS_FETCH = re.compile(r'@import|url\s*\(', re.I)
_JS_NETWORK = re.compile(
    r'\b(?:fetch|XMLHttpRequest|WebSocket|EventSource|sendBeacon|importScripts|eval)\b'
    r'|\bimport\s*\(|new\s+Function|document\.write', re.I)


def split_report(html):
    """Return (page_html, css, js). Raises ValueError for unsafe content."""
    blocks = _BLOCK.findall(html)
    kinds = [tag.lower() for tag, _ in blocks]
    if kinds.count('style') != 1 or kinds.count('script') != 1:
        raise ValueError('report must contain exactly one style and one script block')
    css = next(body for tag, body in blocks if tag.lower() == 'style')
    js = next(body for tag, body in blocks if tag.lower() == 'script')
    page = _BLOCK.sub(lambda m: '', html)
    if _EXTERNAL.search(page):
        raise ValueError('report references external resources')
    if re.search(r'\son[a-z]+\s*=', page, re.I):
        raise ValueError('report contains inline event handlers')
    if _CSS_FETCH.search(css) or _JS_NETWORK.search(js):
        raise ValueError('report style or script fetches resources')
    if '<body>' not in page or '</head>' not in page or '</body>' not in page:
        raise ValueError('report has no head/body')
    page = page.replace('</head>', f'<link rel="stylesheet" href="{CSS_URL}"></head>', 1)
    page = page.replace('<body>', '<body>' + BACK_LINK, 1)
    page = page.replace('</body>', f'<script src="{JS_URL}" defer></script></body>', 1)
    return page, css + BACK_CSS, js


def _read(path):
    with open(path, 'rb') as handle:
        data = handle.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError('report too large')
    return data.decode('utf8')


HEADERS = {'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
           'Referrer-Policy': 'same-origin'}


def build(app_module, report_path=REPORT_PATH):
    def handler(part):
        async def serve(request):
            principal = getattr(request.state, 'password_principal', None)
            recheck = getattr(request.state, 'password_recheck', None)
            legacy = getattr(request.state, 'session', None)
            if (not principal or not getattr(principal, 'username', None)
                    or not callable(recheck) or not legacy or not legacy.authenticated):
                return app_module.error_response(request, 403)
            try:
                page, css, js = await run_in_threadpool(lambda: split_report(_read(report_path)))
            except FileNotFoundError:
                return app_module.error_response(request, 404)
            except (OSError, ValueError, UnicodeDecodeError):
                # Do not log source text or exception strings.
                return app_module.error_response(request, 503)
            if not await run_in_threadpool(recheck):
                return app_module.error_response(request, 403)
            if part == 'css':
                return Response(css, media_type='text/css', headers=HEADERS)
            if part == 'js':
                return Response(js, media_type='text/javascript', headers=HEADERS)
            return HTMLResponse(page, headers=HEADERS)
        return serve

    return [
        Route(PAGE_URL, handler('page'), methods=['GET'], name='v3_supplier_packing'),
        Route(CSS_URL, handler('css'), methods=['GET'], name='v3_supplier_packing_css'),
        Route(JS_URL, handler('js'), methods=['GET'], name='v3_supplier_packing_js'),
    ]
