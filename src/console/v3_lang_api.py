"""Draft-only language routes using the existing authentication and CSRF host contract."""
from pathlib import Path

from starlette.concurrency import run_in_threadpool
from starlette.routing import Route

from .archive.validation import BadRequest, Conflict, Missing
from .channels import shared_inbox
from .classify import Policy, PolicyLang
from .inbox.http import authorised, forbidden, recheck, response
from .inbox.store import INBOUND_PATH
from .lang import LANG_PATH, Lang
from .lang.storage import RateLimited
from .v3_archive_api import form_fields


def parameters(params, resource):
    allowed = {'conversation_key'} if resource == 'contact' else set()
    if set(params) - allowed:
        raise BadRequest('包含不支持的查询参数')
    values = {}
    for name in params:
        if hasattr(params, 'getlist') and len(params.getlist(name)) != 1:
            raise BadRequest('同一参数只能传入一次')
        value = params[name]
        if not isinstance(value, str) or len(value) > 8192:
            raise BadRequest('查询参数无效或过长')
        values[name] = value
    return values


def build(app_module, lang_path=LANG_PATH, *, service=None, inbox=None, runner=None,
          clock=None, hourly_limit=None, inbound_path=INBOUND_PATH, wechat_path=None,
          whatsapp_config_path=None, fetch=None, stat_fn=None, group_gid=None, classify_path=None):
    if service is None:
        # Explicit fixture sources must not create a language database in production.
        if Path(inbound_path) != Path(INBOUND_PATH) and Path(lang_path) == Path(LANG_PATH):
            lang_path = Path(inbound_path).parent / 'lang.sqlite3'
        if inbox is None:
            inbox = shared_inbox(inbound_path, wechat_path=wechat_path,
                whatsapp_config_path=whatsapp_config_path, fetch=fetch, clock=clock,
                stat_fn=stat_fn, group_gid=group_gid)
        service = Lang(lang_path, inbox=inbox, runner=runner, clock=clock, hourly_limit=hourly_limit)

    if classify_path is None and Path(lang_path) != Path(LANG_PATH):
        classify_path = Path(lang_path).parent / 'classify.sqlite3'
    service = PolicyLang(service, Policy(classify_path))

    def handler(resource=None, operation=None):
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
                    payload = await run_in_threadpool(service.write, operation, fields,
                                                     request.state.password_principal.username)
                else:
                    values = parameters(request.query_params, resource)
                    payload = await run_in_threadpool(service.read, resource, values)
                code = 200
            except RateLimited as error:
                payload, code = dict(error=str(error), retry_after=error.retry_after, result=error.result), 429
                retry_after = error.retry_after
            except (BadRequest, Missing, Conflict) as error:
                payload, code = {'error': str(error)}, 404 if isinstance(error, Missing) else 409 if isinstance(error, Conflict) else 400
            except Exception:
                payload, code = {'error': '语言操作暂时无法完成，请稍后再试' if operation else '语言设置暂时无法读取，请稍后再试'}, 503
            if not operation and not await recheck(request):
                return forbidden(app_module, request)
            result = response(payload, code)
            if retry_after is not None:
                result.headers['Retry-After'] = str(retry_after)
            return result
        return serve

    definitions = [
        ('settings', 'settings', None), ('glossary', 'glossary', None),
        ('contacts', 'contacts', None), ('contact', 'contact', None),
        ('settings', None, 'settings'), ('glossary/save', None, 'glossary_save'),
        ('glossary/delete', None, 'glossary_delete'), ('contact', None, 'contact'),
        ('detect', None, 'detect'), ('translate', None, 'translate'), ('compose', None, 'compose'),
    ]
    return [Route('/api/v1/lang/' + path, handler(resource, operation),
                  methods=['POST' if operation else 'GET'], name='lang_' + (operation or resource))
            for path, resource, operation in definitions]
