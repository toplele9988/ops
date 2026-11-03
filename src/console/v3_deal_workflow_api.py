"""Deal comparisons, confirmed demands and draft-only follow-ups; host retains CSRF."""
from pathlib import Path

from starlette.concurrency import run_in_threadpool
from starlette.routing import Route

from .archive.store import ARCHIVE_PATH
from .archive.validation import BadRequest, Conflict, Missing
from .channels import shared_inbox
from .deals.demands import Demands
from .deals.followups import Followups
from .deals.offers import Offers
from .deals.validation import identity
from .inbox import ai as inbox_ai
from .inbox.http import authorised, forbidden, recheck, response
from .inbox.store import INBOUND_PATH
from .lang import Lang
from .lang.storage import RateLimited
from .v3_archive_api import form_fields


def build(app_module, archive_path=ARCHIVE_PATH, *, offers_service=None, demands_service=None,
          followups_service=None, offers_path=None, demands_path=None, followups_path=None,
          lang=None, lang_path=None, inbox=None, runner=inbox_ai.official_runner, clock=None,
          inbound_path=INBOUND_PATH, wechat_path=None, whatsapp_config_path=None, fetch=None,
          stat_fn=None, group_gid=None):
    # A custom archive is an isolated fixture/copy: never fall back to live sidecars.
    isolated = Path(archive_path) != Path(ARCHIVE_PATH)
    def sidecar(path, name):
        return path if path is not None else Path(archive_path).parent / name if isolated else None

    if any(service is None for service in (offers_service, demands_service, followups_service)):
        if inbox is None:
            inbox = shared_inbox(inbound_path, wechat_path=wechat_path,
                whatsapp_config_path=whatsapp_config_path, fetch=fetch,
                stat_fn=stat_fn, group_gid=group_gid)
        if offers_service is None:
            offers_service = Offers(archive_path, path=sidecar(offers_path, 'deal-offers.sqlite3'),
                                    inbox=inbox, clock=clock)
        if demands_service is None:
            demands_service = Demands(archive_path, path=sidecar(demands_path, 'deal-demands.sqlite3'),
                                      inbox=inbox, clock=clock)
        if followups_service is None:
            if lang is None:
                options = dict(inbox=inbox, runner=runner, clock=clock)
                selected_lang = sidecar(lang_path, 'lang.sqlite3')
                lang = Lang(selected_lang, **options) if selected_lang is not None else Lang(**options)
            followups_service = Followups(path=sidecar(followups_path, 'deal-followups.sqlite3'),
                archive_path=archive_path, inbox=inbox, lang=lang, runner=runner, clock=clock,
                demands=demands_service)

    def dispatch(resource, operation, deal_id, child_id, fields, who):
        if resource == 'offers':
            return offers_service.choose(deal_id, child_id, fields, who) if operation else offers_service.read(deal_id)
        if resource == 'demands':
            if operation == 'scan':
                return demands_service.scan(deal_id, fields, who)
            if operation == 'accept':
                return demands_service.accept(deal_id, child_id, fields, who)
            if operation == 'dismiss':
                if fields:
                    raise BadRequest('忽略候选不接受额外字段')
                return demands_service.dismiss(deal_id, child_id, who)
            return demands_service.read(deal_id)
        if operation:
            return followups_service.write(operation, deal_id, fields, who, child_id)
        return followups_service.read(deal_id)

    def handler(resource, operation=None):
        async def serve(request):
            if not authorised(request):
                return forbidden(app_module, request)
            if operation and not await recheck(request):
                return forbidden(app_module, request)
            retry_after = None
            try:
                deal_id = identity(str(request.path_params['id']))
                child = request.path_params.get('child')
                child_id = identity(str(child)) if child is not None else None
                if request.query_params:
                    raise BadRequest('此入口不接受查询参数，请从案子页面操作')
                fields = {}
                if operation:
                    media_type = request.headers.get('content-type', '').split(';', 1)[0].strip().lower()
                    if media_type and media_type != 'application/x-www-form-urlencoded':
                        raise BadRequest('请使用 URL 编码表单提交')
                    fields = await form_fields(request)
                payload = await run_in_threadpool(dispatch, resource, operation, deal_id, child_id,
                    fields, request.state.password_principal.username)
                code = 200
            except RateLimited as error:
                retry_after = error.retry_after
                payload, code = dict(error=str(error), retry_after=retry_after, result=error.result), 429
            except (BadRequest, Missing, Conflict) as error:
                payload = {'error': str(error)}
                code = 404 if isinstance(error, Missing) else 409 if isinstance(error, Conflict) else 400
            except Exception:
                payload, code = {'error': '案子资料暂时无法保存，请先重新读取核对结果' if operation
                                 else '案子资料暂时无法读取，请重新读取'}, 503
            if not operation and not await recheck(request):
                return forbidden(app_module, request)
            result = response(payload, code)
            if retry_after is not None:
                result.headers['Retry-After'] = str(retry_after)
            return result
        return serve

    definitions = [
        ('offers', 'offers', None), ('items/{child}/offer', 'offers', 'choose'),
        ('demands', 'demands', None), ('demands/scan', 'demands', 'scan'),
        ('demands/{child}/accept', 'demands', 'accept'), ('demands/{child}/dismiss', 'demands', 'dismiss'),
        ('waiting', 'waiting', None), ('waiting', 'waiting', 'create'),
        ('waiting/{child}/resolve', 'waiting', 'resolve'), ('waiting/{child}/remind', 'waiting', 'remind'),
        ('waiting/{child}/draft', 'waiting', 'draft'),
    ]
    return [Route('/api/v1/deals/{id}/' + path, handler(resource, operation),
                  methods=['POST' if operation else 'GET'], name='deal_workflow_' + resource + '_' + (operation or 'read'))
            for path, resource, operation in definitions]
