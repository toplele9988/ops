import json
from math import isfinite
import sqlite3
import yaml
from starlette.concurrency import run_in_threadpool
from starlette.requests import ClientDisconnect
from starlette.responses import JSONResponse
from starlette.routing import Route
from console.cpq.service import CPQError, CPQService
from console.security.roles import permissions_for

MAX_BODY_BYTES = 64*1024


def error(status,code,details=None):
    value = {'code':code}
    if details is not None:
        value['details'] = details
    return JSONResponse({'error':value},status_code=status)


def pairs(items):
    result = {}
    for key,value in items:
        if key in result:
            raise ValueError('duplicate JSON key')
        result[key] = value
    return result


def bad_constant(value):
    raise ValueError('nonfinite JSON number')


def finite_float(value):
    value = float(value)
    if not isfinite(value):
        raise ValueError('nonfinite JSON number')
    return value


class CPQAPI:
    def __init__(self,settings):
        self.settings,self.service = settings,CPQService(settings)

    def authorize(self,request):
        permissions = permissions_for(request,self.settings.testing)
        if permissions is None:
            raise CPQError(401,'UNAUTHENTICATED')
        if 'cpq.quote' not in permissions:
            raise CPQError(403,'FORBIDDEN',{'required':'cpq.quote'})
        return permissions

    async def call(self,method,*args):
        try:
            return JSONResponse(await run_in_threadpool(method,*args))
        except CPQError as exc:
            return error(exc.status_code,exc.code,exc.details)
        except (sqlite3.Error,OSError):
            return error(503,'STORAGE_UNAVAILABLE')
        except (ValueError,yaml.YAMLError):
            return error(503,'CONFIGURATION_ERROR')
        except RuntimeError:
            return error(503,'MEDIA_UNAVAILABLE')

    async def context(self,request):
        try:
            permissions = self.authorize(request)
            values = request.query_params.getlist('model_ids')
            if len(values)!=1:
                raise CPQError(400,'BAD_MODEL_IDS')
            ids = [v.strip() for v in values[0].split(',')]
            if not 1<=len(ids)<=50 or not all(ids):
                raise CPQError(400,'BAD_MODEL_IDS')
        except CPQError as exc:
            return error(exc.status_code,exc.code,exc.details)
        return await self.call(self.service.build_context,ids,permissions)

    async def calculate(self,request):
        try:
            permissions = self.authorize(request)
            declared = request.headers.get('content-length')
            if declared is not None:
                if not declared.isascii() or not declared.isdecimal():
                    raise CPQError(400,'BAD_REQUEST')
                declared = declared.lstrip('0') or '0'
                if len(declared)>len(str(MAX_BODY_BYTES)) or int(declared)>MAX_BODY_BYTES:
                    raise CPQError(413,'REQUEST_TOO_LARGE')
            if request.headers.get('content-type','').split(';',1)[0].strip().lower()!='application/json':
                raise CPQError(415,'UNSUPPORTED_MEDIA_TYPE')
            raw = bytearray()
            async for chunk in request.stream():
                if len(raw)+len(chunk)>MAX_BODY_BYTES:
                    raise CPQError(413,'REQUEST_TOO_LARGE')
                raw.extend(chunk)
            try:
                body = json.loads(raw.decode('utf-8'),object_pairs_hook=pairs,parse_constant=bad_constant,parse_float=finite_float)
            except (ValueError,UnicodeError,RecursionError) as exc:
                raise CPQError(400,'INVALID_JSON') from exc
        except CPQError as exc:
            return error(exc.status_code,exc.code,exc.details)
        except ClientDisconnect:
            return error(400,'BAD_REQUEST')
        return await self.call(self.service.calculate,body,permissions)


def build_cpq_routes(settings):
    api = CPQAPI(settings)
    return [Route('/api/cpq/context',api.context,methods=['GET'],name='cpq_context'),Route('/api/cpq/calculate',api.calculate,methods=['POST'],name='cpq_calculate')]
