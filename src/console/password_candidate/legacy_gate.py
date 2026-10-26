"""Isolated password-to-legacy adapter. NOT a deployment or a login system.

The caller MUST supply a verified server-side password session resolver, stable
secret key and a legacy session writer. No proxy/legacy bearer supplied by the
client is trusted. All users have the same permissions after authentication.
"""
import base64, hashlib, hmac, time
from http.cookies import SimpleCookie, CookieError
from starlette.responses import RedirectResponse, Response
from starlette.concurrency import run_in_threadpool

PASSWORD_COOKIE='__Host-221_password'
LEGACY_COOKIE='imgw_console'
class SessionRevoked(Exception):pass
def legacy_token(secret,bearer):
    raw=hmac.new(secret,b'legacy-token\0'+bearer.encode('ascii'),hashlib.sha256).digest()
    return base64.urlsafe_b64encode(raw).decode('ascii').rstrip('=')

class LegacyGate:
    def __init__(self, app, resolve, install_legacy_session, secret, public=None, audit=None,password_cookie=PASSWORD_COOKIE):
        if not isinstance(secret,bytes) or len(secret)<32:raise ValueError('invalid_secret')
        self.app=app;self.resolve=resolve;self.install=install_legacy_session
        self.secret=secret;self.public=public or frozenset({'/healthz','/access/login','/access/logout'});self.audit=audit;self.password_cookie=password_cookie

    async def __call__(self, scope, receive, send):
        if scope['type']!='http':
            # Deny websocket/lifespan bypass; lifespan is handled normally.
            if scope['type']=='websocket':
                await send({'type':'websocket.close','code':1008});return
            await self.app(scope,receive,send);return
        path=scope.get('path','')
        if path in self.public:
            # Even public endpoints never receive a caller-provided proxy key.
            clean=dict(scope);clean['headers']=[(k,v) for k,v in scope.get('headers',[]) if k.lower()!=b'x-imgw-console-key']
            await self.app(clean,receive,send);return
        raw=[v for k,v in scope.get('headers',[]) if k.lower()==b'cookie']
        if len(raw)!=1:
            await self.reject(scope,receive,send);return
        try:
            pieces=raw[0].decode('latin1').split(';')
            if sum(part.strip().startswith(self.password_cookie+'=') for part in pieces)!=1:raise ValueError('duplicate_password_cookie')
            cookie=SimpleCookie();cookie.load(raw[0].decode('latin1'))
            if self.password_cookie not in cookie:raise ValueError('missing')
            bearer=cookie[self.password_cookie].value
            if len(bearer)<32 or len(bearer)>128 or not bearer.isascii():raise ValueError('invalid')
            identity=await run_in_threadpool(self.resolve,bearer)
            if identity is None or identity.expires<=time.time():raise ValueError('expired')
        except (CookieError,UnicodeError,ValueError):
            await self.reject(scope,receive,send);return
        # Derive an internal legacy bearer and csrf per authenticated session.
        # The legacy bearer is never delivered to the browser. The CSRF value
        # appears in same-origin forms. Discard caller-supplied legacy cookies.
        encoded=bearer.encode('ascii','strict')
        legacy=legacy_token(self.secret,bearer)
        csrf=hmac.new(self.secret,b'legacy-csrf\0'+encoded,hashlib.sha256).hexdigest()
        await run_in_threadpool(self.install,legacy,csrf,identity.expires)
        current=await run_in_threadpool(self.resolve,bearer)
        if current is None or current.expires<=time.time():
            await self.reject(scope,receive,send);return
        mutation=scope.get('method') in ('POST','PUT','PATCH','DELETE')
        if mutation and self.audit:
            try:await run_in_threadpool(self.audit,identity.username,scope['method'],path,'attempt')
            except Exception:
                await Response('Unavailable',status_code=503)(scope,receive,send);return
        # The copied legacy app consumes only its own session cookie. Do not
        # re-serialize attacker-controlled cookies into a new Cookie header.
        items=[LEGACY_COOKIE+'='+legacy]
        clean=dict(scope);clean['headers']=[(k,v) for k,v in scope.get('headers',[]) if k.lower() not in (b'x-imgw-console-key',b'cookie')]
        clean['headers'].append((b'cookie','; '.join(items).encode('latin1')))
        clean.setdefault('state',{})['password_principal']=identity
        async def filtered_send(message):
            if message['type']=='http.response.body':
                # Long-lived SSE must stop after logout or password reset even
                # if the legacy session has not expired or was recreated.
                current=await run_in_threadpool(self.resolve,bearer)
                if current is None or current.expires<=time.time():
                    await send({'type':'http.response.body','body':b'','more_body':False})
                    raise SessionRevoked()
            if message['type']=='http.response.start':
                if mutation and self.audit:
                    status=message.get('status',500);result='success' if status<400 else 'denied' if status<500 else 'error'
                    await run_in_threadpool(self.audit,identity.username,scope['method'],path,result)
                message=dict(message);message['headers']=[(k,v) for k,v in message.get('headers',[]) if not (k.lower()==b'set-cookie' and v.lower().startswith((LEGACY_COOKIE+'=').encode()))]
                message['headers']=[(k,v) for k,v in message['headers'] if k.lower()!=b'referrer-policy']
                message['headers'].append((b'referrer-policy',b'same-origin'))
            await send(message)
        try:await self.app(clean,receive,filtered_send)
        except SessionRevoked:pass
    async def reject(self,scope,receive,send):
        response=RedirectResponse('/access/login',status_code=303) if scope.get('method') in ('GET','HEAD') else Response('Unauthorized',status_code=401)
        response.headers['Cache-Control']='no-store'
        await response(scope,receive,send)
