"""Owner-only listing drafts API. Host middleware remains responsible for CSRF."""
from urllib.parse import parse_qs, urlencode

from starlette.concurrency import run_in_threadpool
from starlette.routing import Route

from .archive.store import ARCHIVE_PATH
from .archive.validation import BadRequest, Conflict, Missing
from .deals.validation import identity
from .inbox.http import authorised, forbidden, recheck, response
from .listing.service import DEFAULT_RUNNER, Listing

MAX_BODY = 1024 * 1024
MAX_FIELDS = 32


async def form_fields(request):
    try:
        length = request.headers.get('content-length')
        if length is not None and (not length.isdigit() or len(length) > 9 or int(length) > MAX_BODY):
            raise ValueError
        cached = getattr(request, '_body', b'')
        if len(cached) > MAX_BODY:
            raise ValueError
        form = getattr(request.state, 'form', None)
        if form is not None and length is None and not hasattr(request, '_body'):
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
        fields.pop('csrf', None)  # The host already checks it; never store or audit it.
        return fields
    except (ValueError, TypeError, AttributeError, UnicodeError):
        raise BadRequest('表单无效、字段重复或超过接口大小限制') from None


def build(app_module, archive_path=ARCHIVE_PATH, *, service=None, runner=DEFAULT_RUNNER,
          hub=None, clock=None, today=None, fx_reader=None, config=None, brand_terms=(), media_lookup=None):
    options = dict(runner=runner, hub=hub, clock=clock, today=today, config=config,
                   brand_terms=brand_terms, media_lookup=media_lookup)
    if fx_reader is not None:
        options['fx_reader'] = fx_reader
    listing = service or Listing(archive_path, **options)

    def handler(resource, operation=None, *, export_route=False):
        async def serve(request):
            if not authorised(request):
                return forbidden(app_module, request)
            if operation and not await recheck(request):
                return forbidden(app_module, request)
            try:
                raw_id = request.path_params.get('id')
                row_id = identity(str(raw_id)) if raw_id is not None else None
                if operation:
                    media_type = request.headers.get('content-type', '').split(';', 1)[0].strip().lower()
                    if media_type and media_type != 'application/x-www-form-urlencoded':
                        raise BadRequest('请使用 URL 编码表单提交')
                    fields = await form_fields(request)
                    payload = await run_in_threadpool(listing.write, operation, row_id, fields,
                        request.state.password_principal.username)
                elif export_route:
                    params = request.query_params
                    if set(params.keys()) - {'format'}:
                        raise BadRequest('导出参数无效')
                    formats = (params.getlist('format') if hasattr(params, 'getlist')
                               else [params['format']] if 'format' in params else [])
                    if len(formats) > 1:
                        raise BadRequest('导出格式只能传入一次')
                    format_name = params.get('format', 'text')
                    payload = await run_in_threadpool(listing.read, 'export', {}, row_id, format_name)
                else:
                    payload = await run_in_threadpool(listing.read, resource, request.query_params, row_id)
                code = 200
            except (BadRequest, Missing, Conflict) as error:
                payload = {'error': str(error)}
                code = 404 if isinstance(error, Missing) else 409 if isinstance(error, Conflict) else 400
            except Exception:
                payload, code = ({'error': '发品草稿暂时无法保存'} if operation
                                 else {'error': '发品草稿暂时无法读取'}), 503
            if not operation and not await recheck(request):
                return forbidden(app_module, request)
            return response(payload, code)
        return serve

    definitions = [
        ('drafts', 'drafts', None, False), ('drafts/generate', 'generate', 'generate', False),
        ('drafts/{id}', 'draft_detail', None, False), ('drafts/{id}/update', 'update', 'update', False),
        ('drafts/{id}/status', 'status', 'status', False), ('drafts/{id}/export', 'export', None, True),
        ('products-ready', 'products_ready', None, False),
    ]
    return [Route('/api/v1/listing/' + path, handler(resource, operation, export_route=is_export),
                  methods=['POST' if operation else 'GET'], name='listing_' + (operation or resource))
            for path, resource, operation, is_export in definitions]

