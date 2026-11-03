"""Data audit routes using the roles authentication/form/CSRF host contract."""
from starlette.concurrency import run_in_threadpool
from starlette.routing import Route

from .archive.store import ARCHIVE_PATH
from .archive.validation import BadRequest, Missing, Conflict
from .audit import Audit
from .inbox.http import authorised, forbidden, recheck, response
from .roles.storage import RateLimited
from .v3_archive_api import form_fields


def build(app_module, archive_path=ARCHIVE_PATH, *, service=None, **options):
    service = service if service is not None else Audit(archive_path, **options)

    def handler(operation=False):
        async def serve(request):
            if not authorised(request):
                return forbidden(app_module, request)
            if operation and not await recheck(request):
                return forbidden(app_module, request)
            retry_after = None
            try:
                if operation:
                    media_type = request.headers.get('content-type', '').split(';', 1)[0].strip().lower()
                    if media_type and media_type != 'application/x-www-form-urlencoded':
                        raise BadRequest('请使用 URL 编码表单提交')
                    fields = await form_fields(request)
                    payload = await run_in_threadpool(service.run, fields, request.state.password_principal.username)
                else:
                    payload = await run_in_threadpool(service.read, request.path_params.get('channel'), request.query_params)
                code = 200
            except RateLimited as error:
                payload, code = dict(error=str(error), retry_after=error.retry_after, result=error.result), 429
                retry_after = error.retry_after
            except (BadRequest, Missing, Conflict) as error:
                payload, code = {'error': str(error)}, 404 if isinstance(error, Missing) else 409 if isinstance(error, Conflict) else 400
            except Exception:
                payload, code = {'error': '体检结果暂时无法保存' if operation else '数据体检暂时无法读取'}, 503
            if not operation and not await recheck(request):
                return forbidden(app_module, request)
            result = response(payload, code)
            if retry_after is not None:
                result.headers['Retry-After'] = str(retry_after)
            return result
        return serve

    return [Route('/api/v1/audit', handler(), methods=['GET'], name='audit_all'),
            Route('/api/v1/audit/run', handler(True), methods=['POST'], name='audit_run'),
            Route('/api/v1/audit/{channel}', handler(), methods=['GET'], name='audit_channel')]
