"""Serve the built operations web app (web/dist) behind the console auth gates.

The app is a static single-page build. The console's global middleware forces
`script-src 'self'; style-src 'self'`, so the build ships no inline code, and
index.html carries the session's CSRF token in a <meta> tag for write requests.
"""
import html
from pathlib import Path

from starlette.concurrency import run_in_threadpool
from starlette.responses import RedirectResponse, Response
from starlette.routing import Route

DIST_DIR = Path('/var/lib/imgw/ops-app/dist')
MAX_ASSET_BYTES = 8 * 1024 * 1024
ASSET_TYPES = {'.js': 'text/javascript', '.css': 'text/css', '.svg': 'image/svg+xml',
               '.png': 'image/png', '.woff2': 'font/woff2', '.ico': 'image/x-icon', '.map': 'application/json'}
HEADERS = {'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'same-origin'}


def _authorised(request):
    principal = getattr(request.state, 'password_principal', None)
    recheck = getattr(request.state, 'password_recheck', None)
    legacy = getattr(request.state, 'session', None)
    return bool(principal and getattr(principal, 'username', None) and callable(recheck)
                and legacy and legacy.authenticated)


def _read_asset(dist, relative):
    """Return (bytes, content_type) for a file strictly inside dist/assets, else None."""
    parts = Path(relative).parts
    if not parts or parts[0] != 'assets' or any(part in ('', '.', '..') for part in parts):
        return None
    path = (dist / relative)
    try:
        resolved = path.resolve(strict=True)
        resolved.relative_to((dist / 'assets').resolve(strict=True))
    except (OSError, ValueError):
        return None
    content_type = ASSET_TYPES.get(resolved.suffix.lower())
    if content_type is None or not resolved.is_file() or resolved.stat().st_size > MAX_ASSET_BYTES:
        return None
    return resolved.read_bytes(), content_type


def build(app_module, dist_dir=DIST_DIR):
    async def entry(request):
        if not _authorised(request):
            return app_module.error_response(request, 403)
        relative = request.path_params.get('path', '')
        recheck = request.state.password_recheck
        if relative.startswith('assets/'):
            asset = await run_in_threadpool(_read_asset, dist_dir, relative)
            if asset is None:
                return app_module.error_response(request, 404)
            if not await run_in_threadpool(recheck):
                return app_module.error_response(request, 403)
            return Response(asset[0], media_type=asset[1], headers=HEADERS)
        try:
            index = await run_in_threadpool(lambda: (dist_dir / 'index.html').read_text(encoding='utf8'))
        except OSError:
            return app_module.error_response(request, 503)
        if not await run_in_threadpool(recheck):
            return app_module.error_response(request, 403)
        token = html.escape(str(getattr(request.state, 'csrf', '') or ''), quote=True)
        return Response(index.replace('__CSRF__', token), media_type='text/html', headers=HEADERS)

    async def bare(request):
        return RedirectResponse('/app/', status_code=302)

    return [
        Route('/app', bare, methods=['GET'], name='v3_app_bare'),
        Route('/app/', entry, methods=['GET'], name='v3_app_root'),
        Route('/app/{path:path}', entry, methods=['GET'], name='v3_app'),
    ]
