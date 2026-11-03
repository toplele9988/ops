"""Owner WhatsApp login routes; the host must retain its form CSRF gate."""
from starlette.concurrency import run_in_threadpool
from starlette.responses import Response
from starlette.routing import Route

from .archive.store import ARCHIVE_PATH
from .archive.validation import BadRequest
from .channels.whatsapp_login import LoginError, WhatsAppLogin
from .inbox.http import HEADERS, authorised, forbidden, recheck, response
from .v3_archive_api import form_fields


def build(app_module, archive_path=ARCHIVE_PATH, *, service=None, inbox=None, fetch=None,
          clock=None, whatsapp_config_path=None, stat_fn=None, group_gid=None, allow_writes=True):
    if service is None:
        service = WhatsAppLogin(archive_path, config_path=whatsapp_config_path, fetch=fetch,
            clock=clock, inbox=inbox, stat_fn=stat_fn, group_gid=group_gid, allow_writes=allow_writes)

    def handler(action, writing=False):
        async def serve(request):
            if not authorised(request):
                return forbidden(app_module, request)
            if writing and not await recheck(request):
                return forbidden(app_module, request)
            if writing and not allow_writes:
                return response({'error': '演练环境禁止修改 WhatsApp 登录状态', 'code': 'writes_disabled'}, 403)
            retry_after = None
            image = None
            try:
                if request.query_params:
                    raise BadRequest('此接口不接受查询参数')
                if writing:
                    media_type = request.headers.get('content-type', '').split(';', 1)[0].strip().lower()
                    if media_type and media_type != 'application/x-www-form-urlencoded':
                        raise BadRequest('请使用 URL 编码表单提交')
                    fields = await form_fields(request)
                    payload = await run_in_threadpool(service.write, action, fields,
                                                     request.state.password_principal.username)
                elif action == 'qr':
                    image, fetched_at = await run_in_threadpool(service.qr)
                    payload = None
                else:
                    payload = await run_in_threadpool(service.status)
                code = 200
            except LoginError as error:
                payload, code = {'error': str(error), 'code': error.code}, error.status_code
                retry_after = error.retry_after
                if retry_after is not None:
                    payload['retry_after'] = retry_after
                if error.outcome is not None:
                    payload['outcome'] = error.outcome
            except BadRequest as error:
                payload, code = {'error': str(error)}, 400
            except Exception:
                payload, code = {'error': '登录操作暂时无法完成' if writing else 'WhatsApp 登录状态暂时无法读取'}, 503
            if not writing and not await recheck(request):
                return forbidden(app_module, request)
            if image is not None and code == 200:
                return Response(image, media_type='image/png', headers={**HEADERS, 'X-Qr-Fetched-At': str(fetched_at)})
            result = response(payload, code)
            if retry_after is not None:
                result.headers['Retry-After'] = str(retry_after)
            return result
        return serve

    return [Route('/api/v1/system/whatsapp/' + action, handler(action, writing),
                  methods=['POST' if writing else 'GET'], name='whatsapp_login_' + action)
            for action, writing in (('status', False), ('qr', False), ('start', True), ('logout', True))]
