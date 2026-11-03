"""Internal payment and fulfilment routes; host authentication and CSRF remain required."""
from starlette.concurrency import run_in_threadpool
from starlette.routing import Route

from .archive import validation as v
from .archive.store import ARCHIVE_PATH
from .deals.validation import identity
from .inbox.http import authorised, forbidden, recheck, response
from .v3_archive_api import form_fields
from .fulfil.service import Fulfil


FIELDS = {
    'payment_create': ('kind', 'amount_fen', 'currency', 'received_on', 'method',
                       'quote_round_id', 'note', 'evidence', 'currency_note'),
    'payment_confirm': ('confirm', 'qualifies', 'currency_note'),
    'payment_void': ('note',),
    'node_update': ('status', 'planned_on', 'completed_on', 'responsible', 'party_id',
                    'note', 'promised_on', 'inspection_result', 'inspector', 'carrier',
                    'tracking_number', 'etd', 'eta', 'departed_on', 'signed_on'),
    'issue_create': ('kind', 'quote_line_id', 'product_id', 'responsible', 'party_id',
                     'severity', 'description', 'impact_kind', 'impact_fen', 'currency'),
    'issue_update': ('kind', 'quote_line_id', 'product_id', 'responsible', 'party_id',
                     'severity', 'status', 'description', 'resolution', 'impact_kind',
                     'impact_fen', 'currency'),
    'issue_note': ('note',),
    'reorder': (),
    'issue_draft': ('target',),
}
FIELDS['node_update_id'] = FIELDS['node_update']


def parameters(params, resource):
    """Reject unknown/repeated query values before accessing any business data."""
    allowed = {'issue_summary': ('start', 'end'), 'reorder_leads': ('days',),
               'issue_draft': ('target',)}.get(resource, ())
    v.whitelist(params, allowed)
    result = {}
    for name, value in params.items():
        if hasattr(params, 'getlist') and len(params.getlist(name)) != 1:
            raise v.BadRequest('同一参数只能传入一次')
        if name in ('start', 'end'):
            result[name] = v.iso_date(value)
        elif name == 'target':
            result[name] = v.choice(value, ('supplier', 'forwarder'))
        elif name == 'days':
            parts = value.split(',') if isinstance(value, str) else ()
            if not parts or len(parts) > 12:
                raise v.BadRequest('返单天数须填写逗号分隔的正整数，最多十二项')
            days = tuple(v.integer(part, maximum=3650) for part in parts)
            if len(set(days)) != len(days):
                raise v.BadRequest('返单天数不能重复')
            result[name] = tuple(sorted(days))
    if result.get('start') and result.get('end') and result['start'] > result['end']:
        raise v.BadRequest('开始日期不能晚于结束日期')
    return result


def build(app_module, archive_path=ARCHIVE_PATH, *, service=None, today=None, clock=None):
    service = service or Fulfil(archive_path, today=today, clock=clock)

    def handler(resource, operation=None, *, draft_post=False):
        async def serve(request):
            if not authorised(request):
                return forbidden(app_module, request)
            post = bool(operation or draft_post)
            if post and not await recheck(request):
                return forbidden(app_module, request)
            try:
                path = request.path_params
                raw_id = next((path[key] for key in ('id', 'pid', 'iid') if key in path), None)
                if operation == 'node_update_id':
                    raw_id = path['node']
                row_id = identity(str(raw_id)) if raw_id is not None else None
                child_id = path.get('node') if operation == 'node_update' else None
                if post:
                    media_type = request.headers.get('content-type', '').split(';', 1)[0].strip().lower()
                    if media_type and media_type != 'application/x-www-form-urlencoded':
                        raise v.BadRequest('请使用 URL 编码表单提交')
                    fields = await form_fields(request)
                    v.whitelist(fields, FIELDS[operation or 'issue_draft'] + (('operation_key',) if operation else ()))
                    # A form/body may suspend; recheck again immediately before
                    # the business call so a revoked login cannot confirm money.
                    if not await recheck(request):
                        return forbidden(app_module, request)
                    if draft_post:
                        payload = await run_in_threadpool(service.read, resource,
                            parameters(fields, resource), row_id)
                    else:
                        payload = await run_in_threadpool(service.write, operation, row_id,
                            fields, request.state.password_principal.username, child_id)
                else:
                    payload = await run_in_threadpool(service.read, resource,
                        parameters(request.query_params, resource), row_id)
                code = 200
            except (v.BadRequest, v.Missing, v.Conflict) as error:
                payload, code = {'error': str(error)}, 404 if isinstance(error, v.Missing) else 409 if isinstance(error, v.Conflict) else 400
            except Exception:
                payload, code = {'error': '履约记录暂时无法保存' if operation else '履约记录暂时无法读取'}, 503
            if not operation and not await recheck(request):
                return forbidden(app_module, request)
            return response(payload, code)
        return serve

    definitions = [
        ('deals/{id}/payments', 'payments', None),
        ('deals/{id}/payments', None, 'payment_create'),
        ('payments/{pid}/confirm', None, 'payment_confirm'),
        ('payments/{pid}/void', None, 'payment_void'),
        ('deals/{id}/fulfilment', 'fulfilment', None),
        ('deals/{id}/fulfilment/{node}/update', None, 'node_update'),
        ('fulfilment/{node}/update', None, 'node_update_id'),
        ('fulfilment/board', 'board', None),
        ('deals/{id}/issues', 'issues', None),
        ('deals/{id}/issues', None, 'issue_create'),
        ('issues/{iid}/update', None, 'issue_update'),
        ('issues/{iid}/note', None, 'issue_note'),
        ('issues/{iid}/draft', 'issue_draft', None),
        ('issues/summary', 'issue_summary', None),
        ('reorder/leads', 'reorder_leads', None),
        ('deals/{id}/reorder', None, 'reorder'),
    ]
    routes = [Route('/api/v1/' + path, handler(resource, operation),
                    methods=['POST' if operation else 'GET'],
                    name='fulfil_' + (operation or resource))
              for path, resource, operation in definitions]
    routes.append(Route('/api/v1/issues/{iid}/draft', handler('issue_draft', draft_post=True),
                        methods=['POST'], name='fulfil_issue_draft_post'))
    return routes
