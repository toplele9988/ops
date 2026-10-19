"""Mount before the existing generic /media/{folder}/{name}."""
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
import os
import sqlite3
import yaml
from jinja2 import Environment, FileSystemLoader, select_autoescape
from starlette.concurrency import run_in_threadpool
from starlette.responses import HTMLResponse, JSONResponse, Response
from starlette.routing import Route
from console.security.media import MediaError, MediaService, parse_width, valid_uid
from console.security.roles import permissions_for
from console.strobe.repository import StrobeRepository

@dataclass(frozen=True)
class Settings:
    db_path: Path
    media_root: Path
    media_cache: Path
    work_db: Path
    media_uid_secret: str
    testing: bool = False

    def __post_init__(self):
        for field in ('db_path', 'media_root', 'media_cache', 'work_db'):
            object.__setattr__(self, field, Path(getattr(self, field)))
        if not isinstance(self.media_uid_secret, str) or not self.media_uid_secret:
            raise ValueError('MEDIA_UID_SECRET is required')

def settings_from_env():
    secret = os.environ.get('MEDIA_UID_SECRET')
    if not secret:
        raise ValueError('MEDIA_UID_SECRET is required; no default is permitted')
    work = Path(os.environ.get('CPQ_WORK_DB', '/var/lib/imgw/console/cpq_work.sqlite3'))
    return Settings(Path(os.environ.get('XL_DB_PATH', 'catalog.sqlite3')), Path(os.environ.get('MEDIA_ROOT', '/var/lib/imgw/media')), Path(os.environ.get('MEDIA_CACHE', str(work.parent/'media_cache'))), work, secret, False)

def _error(status, code, details=None):
    value = {'code': code}
    if details is not None:
        value['details'] = details
    return JSONResponse({'error': value}, status_code=status)

SCALARS = ('model_id', 'internal_sku', 'product_name', 'ip_rating', 'pixel_segments', 'power_w', 'voltage', 'net_weight_kg', 'certifications')
OBJECTS = {
    'light_source': ('rgb_count', 'white_count', 'led_type', 'dual', 'label'),
    'dimensions_cm': ('l', 'w', 'h'),
    'carton': ('source', 'units_per_pkg', 'l_cm', 'w_cm', 'h_cm', 'gw_kg'),
    'flight_case': ('source', 'units_per_pkg', 'l_cm', 'w_cm', 'h_cm', 'gw_kg'),
}
LISTS = ('dmx_modes', 'control_protocols')
SPEC_KEYS = set(SCALARS) | set(OBJECTS) | set(LISTS) | set(OBJECTS['light_source'])
CSP = "default-src 'self'; img-src 'self' data:; script-src 'self'; style-src 'self'; frame-ancestors 'none'"

def _scalar(value):
    return value if value is None or isinstance(value, (str, int, float, bool)) else None

def _public(item):
    result = {key: _scalar(item.get(key)) for key in SCALARS}
    for key in LISTS:
        result[key] = None if item.get(key) is None else [_scalar(v) for v in item.get(key)]
    for key, fields in OBJECTS.items():
        source = item.get(key) or {}
        result[key] = {field: _scalar(source.get(field)) for field in fields}
    complete = item.get('spec_completeness') or {}
    result['spec_completeness'] = {'score': _scalar(complete.get('score')), 'missing': [v for v in complete.get('missing') or [] if isinstance(v, str) and v in SPEC_KEYS]}
    result['images'] = []
    for image in item.get('images') or []:
        uid = image.get('asset_uid')
        if not isinstance(uid, str) or not valid_uid(uid) or image.get('customer_safe') is not True:
            continue
        base = '/media/a/' + uid
        if not all(image.get(key) in (base, base+'?w=640', base+'?w=1280') for key in ('thumb_url', 'large_url', 'orig_url')):
            continue
        result['images'].append({key: _scalar(image.get(key)) for key in ('asset_uid', 'role', 'customer_safe', 'thumb_url', 'large_url', 'orig_url')})
    return result

class StrobeAPI:
    def __init__(self, settings):
        self.settings = settings
        self.repository = StrobeRepository(settings.db_path, settings.work_db, settings.media_uid_secret)
        self.media = MediaService(settings.media_root, settings.media_cache)
        self.templates = Environment(loader=FileSystemLoader(Path(__file__).resolve().parents[1]/'templates'), autoescape=select_autoescape(('html', 'xml')))

    def authorize(self, request):
        permissions = permissions_for(request, self.settings.testing)
        if permissions is None:
            return _error(401, 'UNAUTHENTICATED')
        if 'strobe.view' not in permissions:
            return _error(403, 'FORBIDDEN', {'required': 'strobe.view'})
        return None

    async def _load(self, method, *args):
        try:
            return await run_in_threadpool(method, *args), None
        except (sqlite3.Error, OSError):
            return None, _error(503, 'CATALOG_UNAVAILABLE')
        except (ValueError, yaml.YAMLError):
            return None, _error(503, 'CONFIGURATION_ERROR')
        except RuntimeError:
            return None, _error(503, 'MEDIA_UNAVAILABLE')

    async def gallery(self, request):
        denial = self.authorize(request)
        if denial is not None:
            return denial
        items, failure = await self._load(self.repository.list_strobes)
        if failure is not None:
            return failure
        state = SimpleNamespace(session=getattr(request.state, 'session', None), proxy_trusted=getattr(request.state, 'proxy_trusted', False))
        context = dict(request=SimpleNamespace(state=state), nav='strobes', panels=[], csrf=getattr(request.state, 'csrf', ''), items=[_public(item) for item in items], can_admin='cpq.admin' in (permissions_for(request, self.settings.testing) or set()))
        html = await run_in_threadpool(self.templates.get_template('strobe/gallery.html').render, context)
        return HTMLResponse(html, headers={'Content-Security-Policy': CSP, 'Cache-Control': 'private, no-store', 'Vary': 'Cookie, Authorization', 'X-Content-Type-Options': 'nosniff'})

    async def list_strobes(self, request):
        denial = self.authorize(request)
        if denial is not None:
            return denial
        items, failure = await self._load(self.repository.list_strobes)
        return failure if failure is not None else JSONResponse({'count': len(items), 'items': items})

    async def get_strobe(self, request):
        denial = self.authorize(request)
        if denial is not None:
            return denial
        item, failure = await self._load(self.repository.get_strobe, request.path_params['model_id'])
        if failure is not None:
            return failure
        return _error(404, 'NOT_FOUND') if item is None else JSONResponse(item)

    async def get_media(self, request):
        denial = self.authorize(request)
        if denial is not None:
            return denial
        uid = request.path_params['asset_uid']
        if not valid_uid(uid):
            return _error(404, 'NOT_FOUND')
        try:
            width = parse_width(request.query_params.getlist('w'))
            asset = await run_in_threadpool(self.repository.asset_by_uid, uid)
            if asset is None:
                return _error(404, 'NOT_FOUND')
            payload = await run_in_threadpool(self.media.render, uid, asset, width)
        except MediaError as exc:
            return _error(exc.status_code, exc.code)
        except (sqlite3.Error, OSError):
            return _error(503, 'CATALOG_UNAVAILABLE')
        except RuntimeError:
            return _error(503, 'MEDIA_UNAVAILABLE')
        headers = {'Cache-Control': 'private, max-age=86400', 'X-Content-Type-Options': 'nosniff', 'ETag': payload.etag, 'Vary': 'Cookie, Authorization'}
        if any(tag.strip() == '*' or tag.strip().removeprefix('W/') == payload.etag for tag in request.headers.get('if-none-match', '').split(',')):
            return Response(status_code=304, headers=headers)
        return Response(payload.body, media_type=payload.content_type, headers=headers)

def build_routes(settings: Settings) -> list[Route]:
    from console.routes.cpq_routes import build_cpq_routes
    from console.routes.export_routes import build_export_routes
    api = StrobeAPI(settings)
    return [Route('/api/strobes', api.list_strobes, methods=['GET'], name='strobe_list'), Route('/api/strobes/{model_id}', api.get_strobe, methods=['GET'], name='strobe_detail'), Route('/media/a/{asset_uid}', api.get_media, methods=['GET'], name='strobe_media'), Route('/strobes', api.gallery, methods=['GET'], name='strobe_gallery')] + build_cpq_routes(settings) + build_export_routes(settings)
