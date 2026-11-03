"""The same principal, legacy-session and recheck gates as packing JSON."""
from urllib.parse import parse_qs

from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse

from .model import BadRequest

HEADERS = {'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'}


def authorised(request):
    principal = getattr(request.state, 'password_principal', None)
    legacy = getattr(request.state, 'session', None)
    return bool(principal and getattr(principal, 'username', None)
                and callable(getattr(request.state, 'password_recheck', None))
                and legacy and getattr(legacy, 'authenticated', False))


async def recheck(request):
    try:
        return bool(await run_in_threadpool(request.state.password_recheck))
    except Exception:
        return False


def forbidden(app_module, request):
    response = app_module.error_response(request, 403)
    if hasattr(response, 'headers'):
        response.headers.update(HEADERS)
    return response


def response(payload, code=200):
    return JSONResponse(payload, status_code=code, headers=HEADERS)


def get_handler(app_module, parse, read, unavailable):
    async def serve(request):
        if not authorised(request):
            return forbidden(app_module, request)
        try:
            values = parse(request)
            payload, code = await run_in_threadpool(read, values)
        except BadRequest as error:
            payload, code = {'error': str(error)}, 400
        except Exception:
            payload, code = {'error': unavailable}, 503
        # Even missing sources, invalid parameters and read failures recheck.
        if not await recheck(request):
            return forbidden(app_module, request)
        return response(payload, code)
    return serve


async def form_fields(request):
    form = getattr(request.state, 'form', None)
    if form is None:
        try:
            form = parse_qs((await request.body()).decode('utf8', 'replace'),
                            keep_blank_values=True, max_num_fields=16)
        except (ValueError, TypeError):
            raise BadRequest('表单参数无效') from None
    try:
        if len(form) > 16:
            raise ValueError
        fields = {}
        for key in form:
            values = form.getlist(key) if hasattr(form, 'getlist') else form[key]
            if (not isinstance(values, (list, tuple)) or len(values) != 1
                    or not isinstance(values[0], str)):
                raise ValueError
            fields[key] = values[0]
        return fields
    except (ValueError, TypeError, AttributeError):
        raise BadRequest('表单参数须各传入一个文本值') from None
