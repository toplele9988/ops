"""Authenticated internal quotation API. Host retains authentication and CSRF."""
from starlette.concurrency import run_in_threadpool
from starlette.routing import Route
from .archive.store import ARCHIVE_PATH
from .archive.validation import BadRequest, Missing, Conflict
from .deals.validation import identity
from .inbox.http import authorised, forbidden, recheck, response
from .v3_archive_api import form_fields
from .quotes.service import Quotes
from .quotes.fx import FX_PATH
from .quotes.validation import parameters
from .lang import RateLimited


def build(app_module, archive_path=ARCHIVE_PATH, *, service=None, today=None, fx_path=FX_PATH, clock=None,
          state_path=None, lang_service=None):
    service = service or Quotes(archive_path, today=today, fx_path=fx_path, clock=clock,
                                state_path=state_path, lang_service=lang_service)

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
                child_id = next((identity(str(path[k])) for k in ('lid','cid') if k in path), None)
                if operation:
                    fields = await form_fields(request)
                    if not await recheck(request):
                        return forbidden(app_module, request)
                    payload = await run_in_threadpool(service.write, operation, row_id, fields,
                        request.state.password_principal.username, child_id)
                else:
                    params = parameters(request.query_params, resource)
                    payload = await run_in_threadpool(service.read, resource, params, row_id)
                code = 200
            except RateLimited as error:
                payload, code = {'error': '翻译额度已用完，请稍后重新生成；本轮仍为草稿'}, 429
                retry_after = error.retry_after
            except (BadRequest, Missing, Conflict) as error:
                payload, code = {'error': str(error)}, 404 if isinstance(error, Missing) else 409 if isinstance(error, Conflict) else 400
            except Exception:
                payload, code = {'error': '报价暂时无法保存' if operation else '报价暂时无法读取'}, 503
            if not operation and not await recheck(request):
                return forbidden(app_module, request)
            result = response(payload, code)
            if retry_after is not None:
                result.headers['Retry-After'] = str(retry_after)
            return result
        return serve

    definitions = [
        ('deals/{id}/quotes','list',None), ('deals/{id}/quotes',None,'create'),
        ('quotes/{id}','detail',None), ('quotes/{id}/update',None,'update'),
        ('quotes/{id}/lines',None,'line'), ('quotes/{id}/lines/{lid}/update',None,'line_update'),
        ('quotes/{id}/lines/{lid}/remove',None,'line_remove'),
        ('quotes/{id}/costs',None,'cost'), ('quotes/{id}/costs/{cid}/remove',None,'cost_remove'),
        ('quotes/{id}/costs/{cid}/recalc',None,'cost_recalc'),
        ('quotes/{id}/costs/{cid}/review',None,'cost_review'),
        ('quotes/{id}/freight-options','freight_options',None),
        ('quotes/{id}/freight-options/choose',None,'freight_choose'),
        ('freight/compare','freight_compare',None),
        ('quotes/{id}/negotiations',None,'negotiation'), ('quotes/{id}/status',None,'status'),
        ('quotes/{id}/text','text',None), ('fx','fx',None),
        ('quotes/{id}/terms',None,'terms'), ('quotes/{id}/native',None,'native'),
        ('quotes/{id}/review',None,'review'), ('quotes/{id}/pi','pi',None),
        ('quotes/{id}/drafts',None,'draft'),
    ]
    return [Route('/api/v1/' + path, handler(resource, operation), methods=['POST' if operation else 'GET'],
                  name='quotes_' + (operation or resource)) for path, resource, operation in definitions]
