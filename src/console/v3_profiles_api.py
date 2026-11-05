"""Authenticated, read-only profiles API. Route construction performs no I/O."""
from starlette.routing import Route

from .inbox.http import get_handler
from .inbox.model import BadRequest, single
from .profiles.schema import CHANNELS, KINDS, STAGES
from .profiles.store import DB_PATH, Profiles


def parameters(params):
    if set(params) - {'kind', 'stage', 'channel', 'q'}:
        raise BadRequest('包含不支持的查询参数')
    values = {key: single(params, key) for key in ('kind', 'stage', 'channel', 'q')}
    if (values['kind'] not in ('', *KINDS) or values['stage'] not in ('', *STAGES) or
            values['channel'] not in ('', *CHANNELS) or len(values['q']) > 200):
        raise BadRequest('画像查询条件无效')
    return values


def build(app_module, *, service=None, path=DB_PATH):
    service = service if service is not None else Profiles(path)

    def parse_detail(request):
        if request.query_params:
            raise BadRequest('详情不接受查询参数')
        key = request.path_params['conversation_key']
        if len(key) > 8192:
            raise BadRequest('会话标识过长')
        return key

    def detail(key):
        profile = service.get(key)
        return (profile, 200) if profile is not None else ({'error': '未找到该会话画像'}, 404)

    return [Route('/api/v1/profiles', get_handler(app_module, lambda r: parameters(r.query_params),
                  lambda values: (service.list(values), 200), '会话画像暂时无法读取'), methods=['GET'], name='profiles_list'),
            Route('/api/v1/profiles/{conversation_key}', get_handler(app_module, parse_detail, detail,
                  '会话画像暂时无法读取'), methods=['GET'], name='profiles_detail')]
