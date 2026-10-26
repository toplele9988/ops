"""Experimental integration with the exact legacy session store interface."""
import hashlib,hmac,ipaddress,time
from urllib.parse import urlsplit
from starlette.datastructures import Headers
from starlette.responses import Response
from starlette.concurrency import run_in_threadpool
from .access_app import Access
from .legacy_gate import LegacyGate,legacy_token

def legacy_installer(legacy_store):
    def install(token,csrf,expires):
        token_hash=hashlib.sha256(token.encode('ascii')).hexdigest()
        with legacy_store.transaction() as c:
            c.execute('DELETE FROM sessions WHERE expires_at<=?',(time.time(),))
            c.execute('INSERT INTO sessions(token_hash,csrf,expires_at,authenticated) VALUES(?,?,?,1) ON CONFLICT(token_hash) DO UPDATE SET csrf=excluded.csrf,expires_at=excluded.expires_at,authenticated=1',(token_hash,csrf,expires))
    return install

def legacy_revoker(legacy_store,secret):
    def revoke(password_token):
        token=legacy_token(secret,password_token)
        with legacy_store.transaction() as c:c.execute('DELETE FROM sessions WHERE token_hash=?',(hashlib.sha256(token.encode('ascii')).hexdigest(),))
    return revoke

class PasswordMux:
    def __init__(self,legacy_app,legacy_store,legacy_auth,password_store,origin,secret,*,lan_bind=None,lan_cidr=None):
        self.auth=legacy_auth
        self.lan=lan_bind is not None
        if self.lan:
            self.bind=ipaddress.ip_address(lan_bind);self.network=ipaddress.ip_network(lan_cidr,strict=True)
            if self.bind not in self.network or not self.bind.is_private or self.network.prefixlen<24:raise ValueError('invalid_lan_binding')
            for o in ([origin] if isinstance(origin,str) else origin):
                try:host=ipaddress.ip_address(urlsplit(o).hostname)
                except ValueError:raise ValueError('lan_origin_not_ip')
                if host!=self.bind:raise ValueError('lan_origin_binding_mismatch')
        cookie='imgw_221_lan' if self.lan else '__Host-221_password';audience='lan_http' if self.lan else 'https'
        self.access=Access(password_store,origin,secret,legacy_revoker(legacy_store,secret),secure=not self.lan,password_cookie=cookie).app()
        self.gate=LegacyGate(legacy_app,lambda token:password_store.resolve(token,audience),legacy_installer(legacy_store),secret,audit=password_store.audit_action,password_cookie=cookie)
    async def __call__(self,scope,receive,send):
        if scope['type']=='http' and self.lan:
            try:allowed=scope.get('scheme')=='http' and ipaddress.ip_address(scope['server'][0])==self.bind and ipaddress.ip_address(scope['client'][0]) in self.network
            except (ValueError,TypeError,KeyError,IndexError):allowed=False
            if not allowed:
                await Response('Forbidden',status_code=403)(scope,receive,send);return
        if scope['type']=='http':
            try:
                settings=await run_in_threadpool(self.auth.load_settings)
                allowed=(settings.enabled or scope.get('path')=='/healthz') and self.auth.cf_access_allowed(Headers(scope=scope),settings)
            except Exception:
                await Response('Unavailable',status_code=503)(scope,receive,send);return
            if not allowed:
                await Response('Forbidden',status_code=403)(scope,receive,send);return
        if scope['type']=='http' and scope.get('path') in ('/access/login','/access/account','/access/user','/access/logout','/logout'):
            clean=dict(scope);clean['headers']=[(k,v) for k,v in scope.get('headers',[]) if k.lower()!=b'x-imgw-console-key']
            await self.access(clean,receive,send);return
        await self.gate(scope,receive,send)
