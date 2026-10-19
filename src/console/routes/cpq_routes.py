import json
from math import isfinite
import sqlite3
import yaml
from starlette.concurrency import run_in_threadpool
from starlette.requests import ClientDisconnect
from starlette.responses import JSONResponse
from starlette.routing import Route
from console.cpq.quotes import QuoteService
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


def identity(value):
    if isinstance(value,str):
        return value.strip() or None
    if type(value) is int:
        return str(value)
    if isinstance(value,dict):
        for key in ('id','sub','username','email'):
            candidate = value.get(key)
            if isinstance(candidate,str) and candidate.strip():
                return candidate.strip()
            if key=='id' and type(candidate) is int:
                return str(candidate)
    elif value is not None and getattr(value,'is_authenticated',False):
        for key in ('identity','username','display_name'):
            candidate = getattr(value,key,None)
            if isinstance(candidate,str) and candidate.strip():
                return candidate.strip()
    return None


class CPQAPI:
    def __init__(self,settings):
        self.settings,self.service = settings,CPQService(settings)
        self.quotes = QuoteService(settings,service=self.service)

    def authorize(self,request,permission='cpq.quote'):
        permissions = permissions_for(request,self.settings.testing)
        if permissions is None:
            raise CPQError(401,'UNAUTHENTICATED')
        if permission not in permissions:
            raise CPQError(403,'FORBIDDEN',{'required':permission})
        return permissions

    def actor(self,request):
        if self.settings.testing:
            actor = identity(request.headers.get('X-Test-User'))
            if actor:
                return actor
        actor = identity(request.scope.get('user'))
        if actor:
            return actor
        for container in (request.scope.get('state',{}),request.scope.get('session',{})):
            if isinstance(container,dict):
                for key in ('user','user_id','username'):
                    actor = identity(container.get(key))
                    if actor:
                        return actor
        state = getattr(request,'state',None)
        session = getattr(state,'session',None)
        if getattr(session,'authenticated',False) or getattr(state,'proxy_trusted',False):
            return 'console-owner'
        raise CPQError(401,'ACTOR_UNAVAILABLE')

    async def call(self,method,*args,status_code=200):
        try:
            return JSONResponse(await run_in_threadpool(method,*args),status_code=status_code)
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
            if len(values)>1:
                raise CPQError(400,'BAD_MODEL_IDS')
            ids = [v.strip() for v in values[0].split(',')] if values else []
            if len(ids)>50 or (values and not all(ids)):
                raise CPQError(400,'BAD_MODEL_IDS')
        except CPQError as exc:
            return error(exc.status_code,exc.code,exc.details)
        return await self.call(self.service.build_context,ids,permissions)

    async def read_json(self,request):
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
            return json.loads(raw.decode('utf-8'),object_pairs_hook=pairs,parse_constant=bad_constant,parse_float=finite_float)
        except (ValueError,UnicodeError,RecursionError) as exc:
            raise CPQError(400,'INVALID_JSON') from exc

    async def _post(self,request,freeze):
        try:
            permissions = self.authorize(request)
            body = await self.read_json(request)
            actor = self.actor(request) if freeze else None
        except CPQError as exc:
            return error(exc.status_code,exc.code,exc.details)
        except ClientDisconnect:
            return error(400,'BAD_REQUEST')
        if freeze:
            return await self.call(self.quotes.freeze,body,permissions,actor,status_code=201)
        return await self.call(self.service.calculate,body,permissions)

    async def calculate(self,request):
        return await self._post(request,False)

    async def freeze(self,request):
        return await self._post(request,True)

    async def get_quote(self,request):
        try:
            permissions = self.authorize(request,'cpq.admin')
        except CPQError as exc:
            return error(exc.status_code,exc.code,exc.details)
        return await self.call(self.quotes.get_internal,request.path_params['token'],permissions)


def build_cpq_routes(settings):
    api = CPQAPI(settings)
    return [Route('/api/cpq/context',api.context,methods=['GET'],name='cpq_context'),
            Route('/api/cpq/calculate',api.calculate,methods=['POST'],name='cpq_calculate'),
            Route('/api/cpq/quotes',api.freeze,methods=['POST'],name='cpq_quotes'),
            Route('/api/cpq/quotes/{token}',api.get_quote,methods=['GET'],name='cpq_quote')]
