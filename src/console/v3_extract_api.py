"""Owner-only quotation suggestions, using the established roles HTTP gates."""
from starlette.concurrency import run_in_threadpool
from starlette.routing import Route

from .archive.store import ARCHIVE_PATH
from .archive.validation import BadRequest, Missing, Conflict
from .channels import shared_inbox
from .deals.validation import identity
from .extract.service import Extract
from .extract.validation import query
from .inbox.http import authorised, forbidden, recheck, response
from .inbox.store import INBOUND_PATH
from .roles.ai import DEFAULT_RUNNER
from .roles.storage import RateLimited
from .v3_archive_api import form_fields


def build(app_module, archive_path=ARCHIVE_PATH, *, service=None, inbox=None,
          runner=DEFAULT_RUNNER, clock=None, config=None, inbound_path=INBOUND_PATH,
          wechat_path=None, whatsapp_config_path=None, fetch=None, stat_fn=None, group_gid=None):
    if service is None:
        if inbox is None:
            inbox = shared_inbox(inbound_path, wechat_path=wechat_path,
                                 whatsapp_config_path=whatsapp_config_path, fetch=fetch,
                                 stat_fn=stat_fn, group_gid=group_gid)
        service = Extract(archive_path, inbox=inbox, runner=runner, clock=clock, config=config)

    def handler(resource, operation=None):
        async def serve(request):
            if not authorised(request):
                return forbidden(app_module, request)
            if operation and not await recheck(request):
                return forbidden(app_module, request)
            retry_after = None
            try:
                path = request.path_params
                row_id = identity(str(path['id'])) if 'id' in path else None
                if operation:
                    media_type = request.headers.get('content-type', '').split(';', 1)[0].strip().lower()
                    if media_type and media_type != 'application/x-www-form-urlencoded':
                        raise BadRequest('请使用 URL 编码表单提交')
                    fields = await form_fields(request)
                    payload = await run_in_threadpool(service.write, operation, row_id, fields,
                        request.state.password_principal.username)
                else:
                    values = query(request.query_params, resource)
                    payload = await run_in_threadpool(service.read, resource, values, row_id)
                code = 200
            except RateLimited as error:
                payload, code = dict(error=str(error), retry_after=error.retry_after, result=error.result), 429
                retry_after = error.retry_after
            except (BadRequest, Missing, Conflict) as error:
                payload, code = {'error': str(error)}, 404 if isinstance(error, Missing) else 409 if isinstance(error, Conflict) else 400
            except Exception:
                payload, code = {'error': '报价建议暂时无法保存' if operation else '报价建议暂时无法读取'}, 503
            if not operation and not await recheck(request):
                return forbidden(app_module, request)
            result = response(payload, code)
            if retry_after is not None:
                result.headers['Retry-After'] = str(retry_after)
            return result
        return serve

    definitions = [
        ('suggestions', 'suggestions', None), ('summary', 'summary', None),
        ('scan', None, 'scan'), ('suggestions/{id}/accept', None, 'accept'),
        ('suggestions/{id}/dismiss', None, 'dismiss'),
    ]
    return [Route('/api/v1/extract/' + path, handler(resource, operation),
                  methods=['POST' if operation else 'GET'], name='extract_' + (operation or resource))
            for path, resource, operation in definitions]
