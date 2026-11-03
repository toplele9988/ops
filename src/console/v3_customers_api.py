"""Customers grouped by channel/account/buyer_name, not verified identities."""
from starlette.routing import Route

from .customers import public_customer
from .inbox.http import get_handler
from .inbox.model import decode_key, matches, paginate, parameters
from .inbox.store import INBOUND_PATH
from .channels import build_inbox


def build(app_module, inbound_path=INBOUND_PATH, *, inbox=None, wechat_path=None,
          whatsapp_config_path=None, fetch=None, clock=None, stat_fn=None, group_gid=None):
    cache = inbox if inbox is not None else build_inbox(inbound_path, wechat_path=wechat_path,
        whatsapp_config_path=whatsapp_config_path, fetch=fetch, clock=clock, stat_fn=stat_fn, group_gid=group_gid)

    def customers(values):
        snapshot = cache.read()
        rows = [c for c in snapshot['customers'].values() if matches(c, values)]
        rows.sort(key=lambda c: (-c['last_seen'], c['key']))
        return paginate([public_customer(c) for c in rows], values, snapshot['ready']), 200

    def key_parameter(request):
        key = request.path_params.get('key', '')
        decode_key(key, 'customer')
        return key

    def detail(key):
        customer = cache.read()['customers'].get(key)
        if customer is None:
            return {'error': '未找到客户'}, 404
        return public_customer(customer, detail=True), 200

    def get(parse, read):
        return get_handler(app_module, parse, read, '客户资料暂时无法读取')

    return [Route('/api/v1/customers', get(lambda r: parameters(r.query_params), customers),
                  methods=['GET'], name='customers_list'),
            Route('/api/v1/customers/{key}', get(key_parameter, detail), methods=['GET'], name='customers_detail')]


def build_parties(app_module, archive_path=None, *, service=None, inbox=None):
    """Additive W4/W5 mount; legacy customer GET contracts stay unchanged.

    Host must retain CSRF middleware. Tests supply a temporary DB and offline inbox.
    No send endpoint is provided: all message operations remain dry-run drafts.
    """
    from starlette.concurrency import run_in_threadpool
    from .archive import validation as v
    from .customers.operations import Parties, parameters as party_parameters
    from .inbox.http import authorised, forbidden, recheck, response
    from .v3_archive_api import form_fields

    service = service or Parties(archive_path, inbox=inbox if inbox is not None else build_inbox())

    def handler(resource=None, operation=None):
        async def serve(request):
            if not authorised(request):
                return forbidden(app_module, request)
            if operation and not await recheck(request):
                return forbidden(app_module, request)
            try:
                raw = request.path_params.get('id')
                row_id = v.integer(raw, maximum=2**63-1) if raw is not None else None
                if operation:
                    if request.query_params:
                        raise v.BadRequest('保存时不接受筛选条件')
                    media = request.headers.get('content-type', '').split(';')[0].strip().lower()
                    if media != 'application/x-www-form-urlencoded':
                        raise v.BadRequest('请使用表单保存资料')
                    fields = await form_fields(request)
                    if not await recheck(request):
                        return forbidden(app_module, request)
                    payload = await run_in_threadpool(service.write, operation, row_id, fields,
                                                     request.state.password_principal.username)
                else:
                    if resource == 'list':
                        params = party_parameters(request.query_params)
                    else:
                        if request.query_params:
                            raise v.BadRequest('此处不接受筛选条件')
                        params = {}
                    payload = await run_in_threadpool(service.read, resource, params, row_id)
                code = 200
            except (v.BadRequest, v.Missing, v.Conflict) as error:
                payload = {'error': str(error)}
                code = 404 if isinstance(error, v.Missing) else 409 if isinstance(error, v.Conflict) else 400
            except Exception:
                payload, code = {'error': '往来资料暂时无法保存' if operation else '往来资料暂时无法读取'}, 503
            if not operation and not await recheck(request):
                return forbidden(app_module, request)
            return response(payload, code)
        return serve

    definitions = [('', 'list', None), ('/conversations', 'conversations', None),
                   ('', None, 'create'), ('/{id}', 'detail', None)]
    definitions += [('/{id}/' + op, None, op) for op in
                    ('update', 'link', 'merge', 'undo', 'draft', 'review', 'verify', 'deal', 'term', 'term-status')]
    return [Route('/api/v1/parties-ops' + path, handler(resource, operation),
                  methods=['POST' if operation else 'GET'], name='parties_ops_' + (operation or resource))
            for path, resource, operation in definitions]
