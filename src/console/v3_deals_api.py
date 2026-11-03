"""Authenticated deal routes; the host must retain its CSRF middleware."""
from starlette.concurrency import run_in_threadpool
from starlette.routing import Route

from .archive.store import ARCHIVE_PATH
from .archive.validation import BadRequest, Missing, Conflict
from .deals.service import Deals
from .deals.validation import identity, parameters
from .inbox.http import authorised, forbidden, recheck, response
from .inbox.store import INBOUND_PATH
from .v3_archive_api import form_fields


def shared_inbox(*args, **kwargs):
    # Keep injected/offline services independent of optional channel packages.
    from .channels import shared_inbox as factory
    return factory(*args, **kwargs)


def build(app_module, archive_path=ARCHIVE_PATH, *, service=None, inbox=None, lookup=None, clock=None,
          inbound_path=INBOUND_PATH, wechat_path=None, whatsapp_config_path=None, fetch=None,
          stat_fn=None, group_gid=None, overdue_hours=48):
    if service is None:
        if inbox is None and lookup is None:
            inbox = shared_inbox(inbound_path, wechat_path=wechat_path, whatsapp_config_path=whatsapp_config_path,
                                 fetch=fetch, stat_fn=stat_fn, group_gid=group_gid)
        service = Deals(archive_path, inbox=inbox, lookup=lookup, clock=clock, overdue_hours=overdue_hours)

    def handler(resource, operation=None):
        async def serve(request):
            if not authorised(request):
                return forbidden(app_module, request)
            if operation and not await recheck(request):
                return forbidden(app_module, request)
            try:
                path = request.path_params
                row_id = identity(str(path['id'])) if 'id' in path else None
                child_id = next((identity(str(path[k])) for k in ('pid', 'cid', 'iid') if k in path), None)
                if operation:
                    fields = await form_fields(request)
                    if not await recheck(request):
                        return forbidden(app_module, request)
                    payload = await run_in_threadpool(service.write, operation, row_id, fields,
                        request.state.password_principal.username, child_id)
                else:
                    values = parameters(request.query_params, resource)
                    payload = await run_in_threadpool(service.read, resource, values, row_id)
                code = 200
            except (BadRequest, Missing, Conflict) as error:
                payload = {'error': str(error)}
                code = 404 if isinstance(error, Missing) else 409 if isinstance(error, Conflict) else 400
            except Exception:
                payload, code = {'error': '案子暂时无法保存' if operation else '案子暂时无法读取'}, 503
            if not operation and not await recheck(request):
                return forbidden(app_module, request)
            return response(payload, code)
        return serve

    definitions = [
        ('deals/summary', 'summary', None),
        ('deals/by-conversation', 'by_conversation', None),
        ('deals', 'list', None), ('deals', None, 'create'),
        ('deals/{id}', 'detail', None), ('deals/{id}/update', None, 'update'),
        ('deals/{id}/participants', None, 'participant'),
        ('deals/{id}/participants/{pid}/remove', None, 'participant_remove'),
        ('deals/{id}/conversations', None, 'conversation'),
        ('deals/{id}/conversations/{cid}/remove', None, 'conversation_remove'),
        ('deals/{id}/items', None, 'item'), ('deals/{id}/items/{iid}/remove', None, 'item_remove'),
        ('deals/{id}/tasks', None, 'task'), ('deal-tasks/{id}/status', None, 'task_status'),
        ('deals/{id}/events', None, 'event'),
        ('deals/{id}/drafts', None, 'draft'),
        ('identities/lookup', 'identity_lookup', None), ('identities', None, 'identity'),
    ]
    return [Route('/api/v1/' + path, handler(resource, operation), methods=['POST' if operation else 'GET'],
                  name='deals_' + (operation or resource)) for path, resource, operation in definitions]
