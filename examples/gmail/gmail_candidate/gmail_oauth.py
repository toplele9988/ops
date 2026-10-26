"""Gmail desktop OAuth PKCE candidate. No action runs on import.

Only explicit CLI operations initiate network traffic. User must consent in
Google's browser; never log codes, tokens, client secrets or mail bodies.
"""
from __future__ import annotations
import argparse,base64,contextlib,fcntl,hashlib,hmac,json,os,re,secrets,ssl,stat,sys,time
from pathlib import Path
from urllib.error import HTTPError,URLError
from urllib.parse import urlencode,parse_qs,urlsplit
from urllib.request import Request,HTTPSHandler,build_opener
from http.server import HTTPServer,BaseHTTPRequestHandler
from gmail_sync import API,Store,canonical_account,sync,retry_failures
from gmail_transport import GmailHTTPError,GmailRestTransport,NoRedirect,TransportError
from encrypted_vault import EncryptedVault

AUTH='https://accounts.google.com/o/oauth2/v2/auth'
TOKEN='https://oauth2.googleapis.com/token'
READ_SCOPE='https://www.googleapis.com/auth/gmail.readonly'

class OAuthError(Exception):pass


def require_vault(vault):
    if type(vault) is not EncryptedVault:raise OAuthError('encrypted_vault_not_configured')
    return vault


def clean_value(s,code,limit=8192):
    if not isinstance(s,str) or not 1<=len(s)<=limit or any(ord(c)<33 or ord(c)>126 for c in s):raise OAuthError(code)
    return s


def secure_dir(path):
    path=Path(path);st=path.lstat()
    if path.is_symlink() or not stat.S_ISDIR(st.st_mode) or st.st_uid!=os.geteuid() or stat.S_IMODE(st.st_mode)!=0o700:
        raise OAuthError('unsafe_private_directory')
    return path


@contextlib.contextmanager
def credential_lock(path):
    path=Path(path);secure_dir(path.parent)
    fd=os.open(path.parent/'gmail-credential.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    try:
        st=os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_uid!=os.geteuid() or st.st_nlink!=1 or stat.S_IMODE(st.st_mode)!=0o600:
            raise OAuthError('unsafe_lock_file')
        fcntl.flock(fd,fcntl.LOCK_EX)
        yield
    finally:os.close(fd)


def desktop_client(vault,expected):
    require_vault(vault)
    data=vault.load(expected);cfg=data.get('installed')
    if not isinstance(cfg,dict) or cfg.get('token_uri')!=TOKEN or cfg.get('auth_uri') not in ('https://accounts.google.com/o/oauth2/auth',AUTH):raise OAuthError('invalid_desktop_client')
    cid=clean_value(cfg.get('client_id'),'invalid_client_id',512)
    if not cid.endswith('.apps.googleusercontent.com'):raise OAuthError('invalid_client_id')
    secret=clean_value(cfg.get('client_secret'),'invalid_client_secret',2048)
    return {'client_id':cid,'client_secret':secret}

def new_authorization(client_id,port):
    clean_value(client_id,'invalid_client_id',512)
    if not client_id.endswith('.apps.googleusercontent.com') or type(port) is not int or not 1024<=port<=65535:raise OAuthError('invalid_authorization_config')
    verifier=secrets.token_urlsafe(64);state=secrets.token_urlsafe(32)
    challenge=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode('ascii')).digest()).rstrip(b'=').decode('ascii')
    redirect=f'http://127.0.0.1:{port}/oauth/callback'
    params={'client_id':client_id,'redirect_uri':redirect,'response_type':'code','scope':READ_SCOPE,'state':state,'access_type':'offline','prompt':'consent','code_challenge':challenge,'code_challenge_method':'S256'}
    return AUTH+'?'+urlencode(params),state,verifier,redirect


class TokenClient:
    def __init__(self,opener=None,timeout=20):
        if not isinstance(timeout,(float,int)) or isinstance(timeout,bool) or not 1<=timeout<=60:raise OAuthError('invalid_timeout')
        self._opener=opener or build_opener(NoRedirect(),HTTPSHandler(context=ssl.create_default_context()))
        self._timeout=timeout
    def post(self,form):
        if not isinstance(form,dict) or form.get('grant_type') not in ('authorization_code','refresh_token'):raise OAuthError('invalid_token_request')
        allowed=({'client_id','client_secret','code','code_verifier','grant_type','redirect_uri'} if form['grant_type']=='authorization_code' else {'client_id','client_secret','refresh_token','grant_type'})
        if set(form)!=allowed:raise OAuthError('invalid_token_request')
        for key,value in form.items():clean_value(value,'invalid_token_request',8192)
        if form['grant_type']=='authorization_code' and not re.fullmatch(r'http://127\.0\.0\.1:[0-9]{4,5}/oauth/callback',form['redirect_uri']):raise OAuthError('invalid_redirect')
        request=Request(TOKEN,data=urlencode(form).encode(),method='POST',headers={'Accept':'application/json','Content-Type':'application/x-www-form-urlencoded','Accept-Encoding':'identity'})
        raw=None;status=None;bad=False
        try:
            with self._opener.open(request,timeout=self._timeout) as response:
                status=response.status;raw=response.read(65537)
        except HTTPError as e:
            status=e.code
            try:raw=e.read(65537);e.close()
            except Exception:raw=None
        except (URLError,OSError,TimeoutError):bad=True
        except Exception:bad=True
        request=None;form=None;response=None
        if bad:raise OAuthError('token_network_error')
        if not isinstance(raw,bytes) or len(raw)>65536:raise OAuthError('invalid_token_response')
        try:value=json.loads(raw.decode('utf-8','strict'))
        except (ValueError,UnicodeError):value=None
        raw=None
        if type(status) is not int or not isinstance(value,dict):raise OAuthError('invalid_token_response')
        if status!=200:
            code=value.get('error');value=None
            if code not in ('invalid_grant','invalid_client','invalid_scope','access_denied'):code='oauth_error'
            raise OAuthError(code)
        return value


def checked_token(data,require_refresh,known_scopes=None):
    if not isinstance(data,dict) or data.get('token_type','').casefold()!='bearer':raise OAuthError('invalid_token_response')
    access=clean_value(data.get('access_token'),'invalid_token_response')
    expiry=data.get('expires_in')
    if type(expiry) is not int or not 30<=expiry<=86400:raise OAuthError('invalid_token_response')
    scopes=data.get('scope')
    if isinstance(scopes,str):scopes=scopes.split()
    elif scopes is None and known_scopes is not None:scopes=list(known_scopes)
    else:raise OAuthError('missing_granted_scopes')
    if READ_SCOPE not in scopes:raise OAuthError('required_scope_denied')
    refresh=data.get('refresh_token')
    if require_refresh:refresh=clean_value(refresh,'missing_refresh_token')
    elif refresh is not None:refresh=clean_value(refresh,'invalid_token_response')
    return access,expiry,refresh,scopes


def save_authorized(vault,client,expected,token_data,profile_opener=None,prior=None):
    require_vault(vault)
    expected=canonical_account(expected)
    access,_,refresh,scopes=checked_token(token_data,True)
    transport=GmailRestTransport(lambda:access,opener=profile_opener)
    result=transport.get(API+'/profile',{})
    if canonical_account(result.get('emailAddress'))!=expected:raise OAuthError('account_mismatch')
    credential={'schema':1,'account':expected,'client_id':client['client_id'],'client_secret':client['client_secret'],'refresh_token':refresh,'token_uri':TOKEN,'scopes':scopes}
    with credential_lock(vault.path):
        if prior is None:vault.create(expected,credential)
        else:vault.replace(expected,prior,credential)
    return {'account_verified':True,'scope_verified':True,'credential_saved':True,'prior_backed_up':prior is not None}


def load_credentials(vault,expected):
    require_vault(vault)
    expected=canonical_account(expected);value=vault.load(expected)
    if set(value)!={'schema','account','client_id','client_secret','refresh_token','token_uri','scopes'} or value['schema']!=1 or value['account']!=expected or value['token_uri']!=TOKEN:
        raise OAuthError('credential_binding_invalid')
    if not clean_value(value['client_id'],'invalid_client_id',512).endswith('.apps.googleusercontent.com'):raise OAuthError('invalid_client_id')
    clean_value(value['client_secret'],'invalid_client_secret',2048);clean_value(value['refresh_token'],'invalid_refresh_token')
    if not isinstance(value['scopes'],list) or READ_SCOPE not in value['scopes'] or not all(isinstance(s,str) for s in value['scopes']):raise OAuthError('required_scope_denied')
    return value


class TokenProvider:
    def __init__(self,vault,account,client=None,clock=time.monotonic):
        require_vault(vault)
        self.vault=vault;self.path=Path(vault.path);self.account=canonical_account(account);self.client=client or TokenClient();self.clock=clock;self.token=None;self.expires=0
    def __call__(self):
        if self.token and self.clock()<self.expires:return self.token
        with credential_lock(self.path):
            if self.token and self.clock()<self.expires:return self.token
            cred=load_credentials(self.vault,self.account)
            data=self.client.post({'client_id':cred['client_id'],'client_secret':cred['client_secret'],'refresh_token':cred['refresh_token'],'grant_type':'refresh_token'})
            access,seconds,rotated,_=checked_token(data,False,cred['scopes'])
            if rotated and rotated!=cred['refresh_token']:
                new=dict(cred,refresh_token=rotated)
                self.vault.replace(self.account,cred,new)
            self.token=access;self.expires=self.clock()+max(1,seconds-30)
            return access



class RefreshingTransport:
    """GET-only transport: refresh once on 401, never retry other failures."""
    def __init__(self,provider,opener=None):
        self.provider=provider;self.transport=GmailRestTransport(provider,opener=opener)
    def get(self,path,params):
        self.provider()  # Surface refresh failures such as invalid_grant.
        try:return self.transport.get(path,params)
        except GmailHTTPError as exc:
            if exc.status!=401:raise
        self.provider.token=None;self.provider.expires=0
        self.provider()
        return self.transport.get(path,params)


def callback_server(port,state):
    result={'code':None,'denied':False}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_GET(self):
            parsed=urlsplit(self.path)
            if self.client_address[0]!='127.0.0.1' or self.headers.get('Host')!=f'127.0.0.1:{port}' or parsed.path!='/oauth/callback' or len(parsed.query)>16384:
                self.send_error(400);return
            try:q=parse_qs(parsed.query,keep_blank_values=True,max_num_fields=10)
            except ValueError:self.send_error(400);return
            supplied=q.get('state',[''])[0]
            if any(len(v)!=1 for v in q.values()) or not re.fullmatch(r'[A-Za-z0-9_-]{30,128}',supplied) or not hmac.compare_digest(supplied,state):self.send_error(400);return
            if 'error' in q:
                result['denied']=True
            else:
                try:result['code']=clean_value(q.get('code',[None])[0],'invalid_code')
                except OAuthError:self.send_error(400);return
            body=b'Authorization received. Return to your terminal; this page can be closed.'
            self.send_response(200);self.send_header('Content-Type','text/plain; charset=utf-8');self.send_header('Cache-Control','no-store');self.send_header('Referrer-Policy','no-referrer');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
    server=HTTPServer(('127.0.0.1',port),Handler);server.timeout=1
    return server,result


def authorize(private_dir,expected,port,timeout=300,replace_existing=False,*,vault=None,client_vault=None):
    require_vault(vault);require_vault(client_vault)
    private_dir=secure_dir(private_dir);expected=canonical_account(expected)
    target=private_dir/'gmail-credential.enc'
    if Path(vault.path)!=target or Path(client_vault.path)!=private_dir/'desktop-client.enc':raise OAuthError('invalid_vault_location')
    client=desktop_client(client_vault,expected)
    prior=None
    if target.exists() or target.is_symlink():
        if not replace_existing:raise OAuthError('credential_exists_or_unavailable')
        prior=load_credentials(vault,expected)
        if prior['client_id']!=client['client_id']:raise OAuthError('client_identity_changed')
    elif replace_existing:raise OAuthError('credential_not_found')
    url,state,verifier,redirect=new_authorization(client['client_id'],port);server,result=callback_server(port,state)
    with server:
        print('在本机浏览器中打开以下 Google 官方授权链接；不要转发此链接：',flush=True)
        print(url,flush=True)
        end=time.monotonic()+timeout
        while not result['code'] and not result['denied'] and time.monotonic()<end:server.handle_request()
    if result['denied']:raise OAuthError('authorization_denied')
    if not result['code']:raise OAuthError('authorization_timeout')
    data=TokenClient().post({'client_id':client['client_id'],'client_secret':client['client_secret'],'code':result['code'],'code_verifier':verifier,'grant_type':'authorization_code','redirect_uri':redirect})
    return save_authorized(vault,client,expected,data,prior=prior)


def run_sync(private_dir,expected,*,vault=None):
    require_vault(vault)
    private_dir=secure_dir(private_dir);expected=canonical_account(expected)
    if Path(vault.path)!=private_dir/'gmail-credential.enc':raise OAuthError('invalid_vault_location')
    provider=TokenProvider(vault,expected)
    transport=RefreshingTransport(provider)
    store=Store(private_dir/'gmail.sqlite3')
    try:return sync(store,transport,expected)
    finally:store.close()


def run_retry(private_dir,expected,*,vault=None):
    require_vault(vault)
    private_dir=secure_dir(private_dir);expected=canonical_account(expected)
    if Path(vault.path)!=private_dir/'gmail-credential.enc':raise OAuthError('invalid_vault_location')
    provider=TokenProvider(vault,expected);transport=RefreshingTransport(provider)
    store=Store(private_dir/'gmail.sqlite3')
    try:return retry_failures(store,transport,expected)
    finally:store.close()

def safe_failure(exc):
    # Provider descriptions, URLs, payloads, tokens and exception repr are never returned.
    if isinstance(exc,GmailHTTPError):
        result={'status':'failed','error':'gmail_http_error','http_status':exc.status,'category':exc.category}
        if exc.retry_after_seconds is not None:result['retry_after_seconds']=exc.retry_after_seconds
        return result
    if isinstance(exc,TransportError) and str(exc) in ('network_error','transport_error','access_token_unavailable'):
        return {'status':'failed','error':str(exc)}
    if isinstance(exc,OAuthError) and re.fullmatch(r'[a-z_]{1,80}',str(exc)):
        result={'status':'failed','error':str(exc)}
        if str(exc)=='invalid_grant':result['recovery']='reauthorize'
        return result
    return {'status':'failed','error':'operation_failed'}

def main():
    p=argparse.ArgumentParser(description='Gmail OAuth candidate: run only after explicit user authorization')
    p.add_argument('action',choices=['authorize','sync','retry']);p.add_argument('--private-dir',required=True);p.add_argument('--expected-account',required=True);p.add_argument('--port',type=int,default=18792);p.add_argument('--replace-existing',action='store_true')
    a=p.parse_args();os.umask(0o077)
    try:
        out=authorize(a.private_dir,a.expected_account,a.port,replace_existing=a.replace_existing) if a.action=='authorize' else run_sync(a.private_dir,a.expected_account) if a.action=='sync' else run_retry(a.private_dir,a.expected_account)
        print(json.dumps(out,ensure_ascii=False),flush=True)
    except Exception as exc:
        print(json.dumps(safe_failure(exc)),file=sys.stderr,flush=True);raise SystemExit(1)
if __name__=='__main__':main()
