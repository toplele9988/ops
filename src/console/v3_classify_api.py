"""Classification review routes. Mount behind the host authentication and CSRF middleware."""
import json

from starlette.concurrency import run_in_threadpool
from starlette.routing import Route

from .archive.store import ARCHIVE_PATH
from .archive.validation import BadRequest, Conflict, Missing
from .channels import build_inbox
from .classify import Classifier, Policy
from .directory import WechatDirectory
from .inbox.http import authorised, forbidden, recheck, response
from .inbox.model import decode_key
from .lang import Lang
from .v3_archive_api import form_fields


def build(app_module, *, service=None, inbox=None, directory=None, path=None,
          archive_path=ARCHIVE_PATH, lang=None):
    if service is None:
        inbox = inbox if inbox is not None else build_inbox()
        directory = directory if directory is not None else WechatDirectory()
        service = Classifier(inbox=inbox, directory=directory, path=path, archive_path=archive_path,
                             lang=lang, lang_factory=lambda snapshot: Lang(inbox=snapshot))
    policy = Policy(service.store.path)

    def handler(action):
        writing = action in ('scan', 'decide')

        async def serve(request):
            if not authorised(request) or (writing and not await recheck(request)):
                return forbidden(app_module, request)
            try:
                query = request.query_params
                for key in query:
                    if hasattr(query, 'getlist') and len(query.getlist(key)) != 1:
                        raise BadRequest('同一参数只能传入一次')
                allowed = {'day', 'status'} if action == 'list' else {'day'} if action == 'summary' else {'key'} if action == 'flags' else set()
                if set(query) - allowed:
                    raise BadRequest('包含不支持的查询参数')
                if writing:
                    media = request.headers.get('content-type', '').split(';', 1)[0].strip().lower()
                    if media != 'application/x-www-form-urlencoded':
                        raise BadRequest('请使用 URL 编码表单提交')
                    fields = await form_fields(request)
                    who = request.state.password_principal.username
                    if action == 'scan':
                        if fields != {'confirm': '1'}:
                            raise BadRequest('扫描本地记录须确认 confirm=1')
                        payload = await run_in_threadpool(service.scan, who)
                    else:
                        if set(fields) != {'items', 'action'}:
                            raise BadRequest('请提供要操作的建议和分类动作')
                        try:
                            items = json.loads(fields['items'])
                        except (ValueError, TypeError):
                            raise BadRequest('建议列表格式无效，请刷新列表后重试') from None
                        payload = await run_in_threadpool(service.decide, items, fields['action'], who)
                elif action == 'list':
                    payload = await run_in_threadpool(service.list, query.get('day'), query.get('status', 'pending'))
                elif action == 'summary':
                    payload = await run_in_threadpool(service.summary, query.get('day'))
                else:
                    try:
                        decode_key(query.get('key'), 'conversation')
                    except ValueError:
                        raise BadRequest('会话标识无效，请重新选择会话') from None
                    payload = await run_in_threadpool(policy.flags, query['key'])
                code = 200
            except (BadRequest, Missing, Conflict) as error:
                payload, code = {'error': str(error)}, 404 if isinstance(error, Missing) else 409 if isinstance(error, Conflict) else 400
            except Exception:
                payload, code = {'error': '分类暂时无法保存，请刷新核对结果后再操作' if writing else '分类暂时无法读取，请检查分类库后重试'}, 503
            if not writing and not await recheck(request):
                return forbidden(app_module, request)
            return response(payload, code)
        return serve

    return [Route('/api/v1/classify' + suffix, handler(action), methods=['POST' if action in ('scan', 'decide') else 'GET'],
                  name='classify_' + action) for suffix, action in
            (('', 'list'), ('/summary', 'summary'), ('/flags', 'flags'), ('/scan', 'scan'), ('/decide', 'decide'))]
