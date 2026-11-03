"""Read-only order intake; the host retains authentication and CSRF checks."""
from pathlib import Path
import re

from starlette.concurrency import run_in_threadpool
from starlette.routing import Route

from .alibaba_open.errors import OpenError
from .archive.store import ARCHIVE_PATH
from .archive.validation import BadRequest, Conflict, Missing, integer, whitelist
from .inbox.http import authorised, forbidden, recheck, response
from .v3_archive_api import form_fields


FIELDS = {
    'authorize': ('code', 'confirm'),
    'poll': ('confirm',),
    'settings': ('paused', 'daily_limit', 'confirm'),
    'claim': ('deal_id', 'confirm'),
}


def build(app_module, archive_path=ARCHIVE_PATH, *, service=None, db_path=None,
          key_path=None, network_enabled=False, **options):
    if service is None:
        from .alibaba_open.service import AlibabaOpen
        # A fixture archive must never create production token or encryption files.
        if Path(archive_path) != Path(ARCHIVE_PATH):
            db_path = db_path or Path(archive_path).parent / 'alibaba-open.sqlite3'
            key_path = key_path or Path(archive_path).parent / 'alibaba-open.key'
        service = AlibabaOpen(archive_path=archive_path, db_path=db_path,
            key_path=key_path, network_enabled=network_enabled, **options)

    def handler(resource=None, operation=None):
        async def serve(request):
            if not authorised(request):
                return forbidden(app_module, request)
            if operation and not await recheck(request):
                return forbidden(app_module, request)
            retry_after = None
            try:
                if request.query_params:
                    raise BadRequest('此入口不接受查询参数，请从阿里订单接入页操作')
                if operation:
                    media_type = request.headers.get('content-type', '').split(';', 1)[0].strip().lower()
                    if media_type != 'application/x-www-form-urlencoded':
                        raise BadRequest('请使用 URL 编码表单提交')
                    fields = await form_fields(request)
                    whitelist(fields, FIELDS[operation])
                    if fields.get('confirm') != '1':
                        raise BadRequest('请核对后点击确认操作')
                    if operation == 'authorize':
                        code_value = fields.get('code', '')
                        if (not code_value or len(code_value) > 2048 or any(ord(c) < 33 for c in code_value)
                                or any(part in code_value for part in ('://', '?', '&'))):
                            raise BadRequest('请只粘贴地址栏里的 code，不要粘贴整个网址')
                    if operation == 'claim':
                        order_id = request.path_params['order_id']
                        if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', order_id):
                            raise BadRequest('订单编号无效，请重新读取订单列表')
                        integer(fields.get('deal_id', ''), maximum=2**63-1)
                        fields['order_id'] = order_id
                    # Body parsing can suspend while a login is revoked.
                    if not await recheck(request):
                        return forbidden(app_module, request)
                    payload = await run_in_threadpool(service.write, operation, fields,
                        request.state.password_principal.username)
                else:
                    payload = await run_in_threadpool(service.read, resource)
                code = 200
            except OpenError as error:
                retry_after = getattr(error, 'retry_after', None)
                code = 409 if error.reauthorize else 503
                payload = dict(error=str(error), unknown=error.unknown, reauthorize=error.reauthorize)
                if retry_after is not None:
                    payload['retry_after'] = retry_after
            except (BadRequest, Missing, Conflict) as error:
                payload = {'error': str(error)}
                code = 404 if isinstance(error, Missing) else 409 if isinstance(error, Conflict) else 400
            except Exception:
                payload, code = {'error': '阿里订单操作结果还需核对，请重新读取状态，不要重复提交' if operation
                                 else '阿里订单接入状态暂时无法读取，请重新读取'}, 503
            if not operation and not await recheck(request):
                return forbidden(app_module, request)
            result = response(payload, code)
            if retry_after is not None:
                result.headers['Retry-After'] = str(retry_after)
            return result
        return serve

    definitions = [
        ('status', 'status', None), ('authorization', 'authorization', None),
        ('candidates', 'candidates', None), ('authorize', None, 'authorize'),
        ('poll', None, 'poll'), ('settings', None, 'settings'),
        ('candidates/{order_id}/claim', None, 'claim'),
    ]
    return [Route('/api/v1/alibaba-open/' + path, handler(resource, operation),
                  methods=['POST' if operation else 'GET'], name='alibaba_open_' + (operation or resource))
            for path, resource, operation in definitions]
