"""Owner archive JSON routes. Host middleware must retain its CSRF check."""
from urllib.parse import parse_qs, urlencode

from starlette.concurrency import run_in_threadpool
from starlette.routing import Route

from .inbox.http import authorised, forbidden, recheck, response
from .archive.service import Archive, parameters
from .archive.store import ARCHIVE_PATH
from .archive.validation import BadRequest, Conflict, Missing, integer

MAX_BODY = 8192
MAX_FIELDS = 40


async def form_fields(request):
    try:
        length = getattr(request, 'headers', {}).get('content-length')
        if length is not None and (not str(length).isdigit() or len(str(length)) > 8 or int(length) > MAX_BODY):
            raise ValueError
        cached = getattr(request, '_body', b'')
        if len(cached) > MAX_BODY:
            raise ValueError
        form = getattr(request.state, 'form', None)
        if form is not None and length is None and not hasattr(request, '_body'):
            # A pre-parsed form cannot prove the original octet count (e.g.
            # redundant percent escapes). Fail closed instead of re-reading a
            # middleware-consumed stream or accepting an unbounded raw body.
            raise ValueError
        if form is None:
            if hasattr(request, 'stream'):
                body = bytearray()
                async for block in request.stream():
                    body.extend(block)
                    if len(body) > MAX_BODY:
                        raise ValueError
            else:
                body = await request.body()
            if len(body) > MAX_BODY:
                raise ValueError
            form = parse_qs(body.decode('utf8', 'strict'), keep_blank_values=True,
                            max_num_fields=MAX_FIELDS, errors='strict')
        if not hasattr(form, 'keys') or len(form) > MAX_FIELDS:
            raise ValueError
        fields = {}
        for key in form:
            values = form.getlist(key) if hasattr(form, 'getlist') else form[key]
            if (not isinstance(key, str) or not isinstance(values, (list, tuple)) or len(values) != 1
                    or not isinstance(values[0], str)):
                raise ValueError
            fields[key] = values[0]
        if len(urlencode(fields).encode('utf8')) > MAX_BODY:
            raise ValueError
        fields.pop('csrf', None)  # Already verified by the host, never persisted/audited.
        return fields
    except (ValueError, TypeError, AttributeError, UnicodeError):
        raise BadRequest('表单无效、字段重复或超过 8KB') from None


def build(app_module, archive_path=ARCHIVE_PATH, *, service=None, today=None, conversation_lookup=None):
    archive = service or Archive(archive_path, today=today, conversation_lookup=conversation_lookup)

    def row_id(request):
        raw = request.path_params.get('id')
        return integer(str(raw), maximum=2**63-1) if raw is not None else None

    def handler(resource, operation=None):
        async def serve(request):
            if not authorised(request):
                return forbidden(app_module, request)
            if operation and not await recheck(request):
                return forbidden(app_module, request)
            try:
                identity = row_id(request)
                if operation:
                    fields = await form_fields(request)
                    payload = await run_in_threadpool(archive.write, operation, identity, fields,
                                                     request.state.password_principal.username)
                else:
                    values = parameters(request.query_params, resource) if identity is None and resource != 'summary' else {}
                    payload = await run_in_threadpool(archive.read, resource, values, identity)
                code = 200
            except (BadRequest, Missing, Conflict) as error:
                payload = {'error': str(error)}
                code = 404 if isinstance(error, Missing) else 409 if isinstance(error, Conflict) else 400
            except Exception:
                payload, code = {'error': '档案暂时无法保存' if operation else '档案暂时无法读取'}, 503
            if not operation and not await recheck(request):
                return forbidden(app_module, request)
            return response(payload, code)
        return serve

    prefix = '/api/v1/archive/'
    definitions = [
        ('summary', 'summary', None), ('parties', 'parties', None), ('parties/{id}', 'parties', None),
        ('parties', 'parties', 'party_create'), ('parties/{id}/update', 'parties', 'party_update'),
        ('parties/{id}/terms', 'parties', 'term_create'), ('terms/{id}/status', 'terms', 'term_status'),
        ('products', 'products', None), ('products/{id}', 'products', None),
        ('products', 'products', 'product_create'), ('products/{id}/update', 'products', 'product_update'),
        ('products/{id}/confirm', 'products', 'product_confirm'), ('products/{id}/hide', 'products', 'product_hide'),
        ('products/{id}/restore', 'products', 'product_restore'), ('products/{id}/merge', 'products', 'product_merge'),
        ('products/{id}/offers', 'offers', 'offer_create'), ('offers/{id}/status', 'offers', 'offer_status'),
        ('freight', 'freight', None), ('freight', 'freight', 'freight_create'),
        ('freight/{id}/update', 'freight', 'freight_update'),
        ('freight/{id}/status', 'freight', 'freight_status'),
    ]
    return [Route(prefix + path, handler(resource, operation), methods=['POST' if operation else 'GET'],
                  name='archive_' + (operation or resource + ('_detail' if '{id}' in path else '_list')))
            for path, resource, operation in definitions]
