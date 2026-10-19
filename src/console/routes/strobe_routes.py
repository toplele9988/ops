"""Mount these routes before the existing generic /media/{folder}/{name}."""
from dataclasses import dataclass
from pathlib import Path
import os
import sqlite3
import yaml
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
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


def settings_from_env() -> Settings:
    secret = os.environ.get('MEDIA_UID_SECRET')
    if not secret:
        raise ValueError('MEDIA_UID_SECRET is required; no default is permitted')
    work = Path(os.environ.get('CPQ_WORK_DB', '/var/lib/imgw/console/cpq_work.sqlite3'))
    return Settings(
        db_path=Path(os.environ.get('XL_DB_PATH', 'catalog.sqlite3')),
        media_root=Path(os.environ.get('MEDIA_ROOT', '/var/lib/imgw/media')),
        media_cache=Path(os.environ.get('MEDIA_CACHE', str(work.parent / 'media_cache'))),
        work_db=work, media_uid_secret=secret, testing=False,
    )


def _error(status, code, details=None):
    error = {'code': code}
    if details is not None:
        error['details'] = details
    return JSONResponse({'error': error}, status_code=status)


class StrobeAPI:
    def __init__(self, settings):
        self.settings = settings
        self.repository = StrobeRepository(settings.db_path, settings.work_db, settings.media_uid_secret)
        self.media = MediaService(settings.media_root, settings.media_cache)

    def authorize(self, request):
        permissions = permissions_for(request, self.settings.testing)
        if permissions is None:
            return _error(401, 'UNAUTHENTICATED')
        if 'strobe.view' not in permissions:
            return _error(403, 'FORBIDDEN', {'required': 'strobe.view'})
        return None

    async def list_strobes(self, request: Request):
        denial = self.authorize(request)
        if denial is not None:
            return denial
        try:
            items = await run_in_threadpool(self.repository.list_strobes)
        except (sqlite3.Error, OSError):
            return _error(503, 'CATALOG_UNAVAILABLE')
        except (ValueError, yaml.YAMLError):
            return _error(503, 'CONFIGURATION_ERROR')
        except RuntimeError:
            return _error(503, 'MEDIA_UNAVAILABLE')
        return JSONResponse({'count': len(items), 'items': items})

    async def get_strobe(self, request: Request):
        denial = self.authorize(request)
        if denial is not None:
            return denial
        try:
            item = await run_in_threadpool(self.repository.get_strobe, request.path_params['model_id'])
        except (sqlite3.Error, OSError):
            return _error(503, 'CATALOG_UNAVAILABLE')
        except (ValueError, yaml.YAMLError):
            return _error(503, 'CONFIGURATION_ERROR')
        except RuntimeError:
            return _error(503, 'MEDIA_UNAVAILABLE')
        return _error(404, 'NOT_FOUND') if item is None else JSONResponse(item)

    async def get_media(self, request: Request):
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
        headers = {'Cache-Control': 'private, max-age=86400',
                   'X-Content-Type-Options': 'nosniff', 'ETag': payload.etag,
                   'Vary': 'Cookie, Authorization'}
        candidates = request.headers.get('if-none-match', '').split(',')
        if any(tag.strip() == '*' or tag.strip().removeprefix('W/') == payload.etag for tag in candidates):
            return Response(status_code=304, headers=headers)
        return Response(payload.body, media_type=payload.content_type, headers=headers)


def build_routes(settings: Settings) -> list[Route]:
    api = StrobeAPI(settings)
    return [
        Route('/api/strobes', api.list_strobes, methods=['GET'], name='strobe_list'),
        Route('/api/strobes/{model_id}', api.get_strobe, methods=['GET'], name='strobe_detail'),
        Route('/media/a/{asset_uid}', api.get_media, methods=['GET'], name='strobe_media'),
    ]
