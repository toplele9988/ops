"""Authenticated dossier reads, signed local media, and CSRF-protected drafts."""
import hmac

from starlette.concurrency import run_in_threadpool
from starlette.datastructures import QueryParams
from starlette.responses import Response
from starlette.routing import Route

from .dossiers import Dossiers
from .dossiers.drafts import Drafts
from .dossiers.service import Missing
from .dossiers.sources import Sources
from .inbox.http import authorised, forbidden, recheck, response, form_fields
from .inbox.model import BadRequest


def parameters(params, allowed):
    if set(params) - set(allowed) or any(len(params.getlist(k)) != 1 for k in params):
        raise BadRequest('筛选参数无效')
    result = dict(params)
    if any(len(v) > 200 for v in result.values()):
        raise BadRequest('筛选内容过长')
    for key in ('page', 'page_size', 'priority'):
        if result.get(key):
            if not result[key].isascii() or not result[key].isdigit() or not 1 <= int(result[key]) <= (100 if key == 'page_size' else 100000):
                raise BadRequest('页码或优先级无效')
    if result.get('type') and 'q' in allowed and result['type'] not in ('产品', '物流', '配套'):
        raise BadRequest('供应商类型无效')
    return result


def build(app_module, *, service=None, drafts=None, resolver=None):
    resolver = resolver or getattr(service, 'resolver', None) or Sources()
    service = service or Dossiers(resolver=resolver)
    drafts = drafts or Drafts(service)

    def handler(resource, allowed=(), write=False):
        async def serve(request):
            if not authorised(request):
                return forbidden(app_module, request)
            try:
                params = parameters(request.query_params, allowed)
                identity = request.path_params.get('id', '')
                if write:
                    if not await recheck(request):
                        return forbidden(app_module, request)
                    # Retain host CSRF middleware, and enforce its session/header contract here too.
                    headers = request.headers.getlist('x-csrf-token')
                    expected = getattr(request.state.session, 'csrf', '')
                    if len(headers) != 1 or not expected or not hmac.compare_digest(headers[0], expected):
                        return forbidden(app_module, request)
                    if request.headers.get('content-type', '').split(';')[0] != 'application/x-www-form-urlencoded' or int(request.headers.get('content-length', '0')) > 4096:
                        raise BadRequest('请用表单生成草稿')
                    fields = await form_fields(request)
                    if set(fields) - {'priority', 'type'} or any(len(v) > 200 for v in fields.values()):
                        raise BadRequest('草稿筛选无效')
                    params = parameters(QueryParams(fields), ('priority', 'type'))
                    if not await recheck(request):
                        return forbidden(app_module, request)
                    payload = await run_in_threadpool(drafts.create, identity, params, request.state.password_principal.username)
                elif resource == 'media':
                    try:
                        data, mime = await run_in_threadpool(resolver.media, request.path_params['token'], service)
                    except Exception:
                        raise Missing from None
                    if not await recheck(request):
                        return forbidden(app_module, request)
                    headers = {'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'}
                    if mime == 'application/pdf':
                        headers['Content-Disposition'] = 'inline; filename="source.pdf"'
                    elif not mime.startswith('image/'):
                        headers['Content-Disposition'] = 'attachment; filename="source"'
                    return Response(data, media_type=mime, headers=headers)
                else:
                    read = {'suppliers': lambda: service.suppliers(params), 'supplier': lambda: service.supplier(identity),
                            'families': lambda: service.families(params), 'family': lambda: service.family(identity),
                            'gaps': lambda: service.gaps(params), 'source': lambda: service.source(identity),
                            'drafts': lambda: drafts.list(identity)}[resource]
                    payload = await run_in_threadpool(read)
                code = 200
            except Missing:
                payload, code = {'error': '档案、原件或缺口不存在'}, 404
            except BadRequest as error:
                payload, code = {'error': str(error)}, 400
            except Exception:
                payload, code = {'error': '档案暂时无法读取或草稿未能存档，请刷新重试'}, 503
            if not await recheck(request):
                return forbidden(app_module, request)
            return response(payload, code)
        return serve

    definitions = [('/suppliers', 'suppliers', ('q', 'type')), ('/suppliers/{id}', 'supplier', ()),
                   ('/families', 'families', ('q', 'category')), ('/families/{id}', 'family', ()),
                   ('/gaps', 'gaps', ('supplier', 'priority', 'type', 'page', 'page_size')),
                   ('/sources/{id}', 'source', ()), ('/media/{token}', 'media', ()),
                   ('/suppliers/{id}/drafts', 'drafts', ())]
    routes = [Route('/api/v1/dossiers' + path, handler(resource, allowed), methods=['GET'], name='dossiers_' + resource) for path, resource, allowed in definitions]
    routes.append(Route('/api/v1/dossiers/suppliers/{id}/drafts', handler('create_draft', write=True), methods=['POST'], name='dossiers_create_draft'))
    return routes
