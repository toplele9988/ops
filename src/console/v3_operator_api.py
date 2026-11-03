"""Authenticated operator API. Host must retain its existing session/CSRF middleware.

No default runtime, sender or credentials: the integrator injects the engine.
"""
from starlette.concurrency import run_in_threadpool
from starlette.routing import Route
from .archive.validation import BadRequest, Conflict, Missing
from .inbox.model import BadRequest as InboxBadRequest
from .deals.validation import identity
from .inbox.http import authorised, forbidden, recheck, response, form_fields


def build(app_module, *, service):
    def handler(operation=None):
        async def serve(request):
            if not authorised(request) or not await recheck(request):
                return forbidden(app_module, request)
            try:
                if operation:
                    if request.headers.get('content-type', '').split(';')[0] != 'application/x-www-form-urlencoded':
                        raise BadRequest('请使用 URL 编码表单提交')
                    if request.query_params:
                        raise BadRequest('不接受额外查询参数')
                    fields = await form_fields(request)
                    if not await recheck(request):
                        return forbidden(app_module, request)
                    row_id = identity(str(request.path_params['id'])) if 'id' in request.path_params else None
                    result = await run_in_threadpool(service.write, operation, row_id, fields,
                                                    request.state.password_principal.username)
                else:
                    if request.query_params:
                        raise BadRequest('不接受额外查询参数')
                    result = await run_in_threadpool(service.read)
                code = 200
            except (BadRequest, InboxBadRequest, Conflict, Missing) as error:
                result, code = dict(error=str(error)), 404 if isinstance(error, Missing) else 409 if isinstance(error, Conflict) else 400
            except Exception:
                result, code = dict(error='AI 运营员暂时无法处理，请刷新核对记录'), 503
            if not await recheck(request):
                return forbidden(app_module, request)
            return response(result, code)
        return serve
    return [Route('/api/v1/operator', handler(), methods=['GET'])] + [
        Route('/api/v1/operator/'+path, handler(operation), methods=['POST'])
        for path, operation in [('settings','settings'), ('profiles','profile'),
                               ('actions/{id}/approve','approve'), ('actions/{id}/edit','edit'),
                               ('actions/{id}/reject','reject')]]
