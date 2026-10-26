"""Gmail HTTPS transport candidate. No OAuth flow, token storage, sending or logs."""
from __future__ import annotations
import json
import re
import ssl
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote, urlencode
from urllib.request import Request, HTTPRedirectHandler, HTTPSHandler, build_opener
from gmail_sync import API, ApiError, InvalidResponse

BASE='https://gmail.googleapis.com'

class TransportError(Exception): pass

class GmailHTTPError(ApiError):
    def __init__(self,status,retry_after_seconds=None):
        super().__init__(status)
        self.category=('authentication' if status==401 else 'forbidden_or_quota' if status==403 else
                       'not_found' if status==404 else 'rate_limited' if status==429 else
                       'server_error' if 500<=status<=599 else 'redirect_denied' if 300<=status<=399 else 'http_error')
        self.retry_after_seconds=retry_after_seconds

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Do not forward a bearer token to any redirect destination.
        return None

def _safe_retry_after(headers):
    if headers is None:return None
    value=headers.get('Retry-After')
    if isinstance(value,str) and re.fullmatch(r'[0-9]{1,5}',value):
        seconds=int(value)
        if 0<=seconds<=86400:return seconds
    return None

def _validate(path,params):
    if not isinstance(path,str) or not isinstance(params,dict):raise ValueError('invalid_request')
    if path==API+'/profile':allowed=set()
    elif path==API+'/messages':allowed={'maxResults','pageToken','includeSpamTrash'}
    elif path==API+'/history':allowed={'maxResults','pageToken','startHistoryId'}
    elif path.startswith(API+'/messages/'):
        segment=path[len(API+'/messages/'):]
        try:mid=unquote(segment,errors='strict')
        except (UnicodeError,ValueError):raise ValueError('invalid_request') from None
        if not mid or len(mid)>512 or mid in ('.','..') or '/' in mid or '\\' in mid or any(ord(c)<32 or ord(c)==127 for c in mid) or quote(mid,safe='')!=segment:
            raise ValueError('invalid_request')
        allowed={'format'}
    else:raise ValueError('invalid_request')
    if set(params)-allowed:raise ValueError('invalid_request')
    for key,value in params.items():
        if not isinstance(key,str) or not isinstance(value,(str,int)) or isinstance(value,bool):raise ValueError('invalid_request')
        text=str(value)
        if len(text)>8192 or not text or any(ord(c)<32 or ord(c)==127 for c in text):raise ValueError('invalid_request')
    if path==API+'/history' and not re.fullmatch(r'[0-9]{1,128}',str(params.get('startHistoryId',''))):raise ValueError('invalid_request')
    if 'maxResults' in params:
        try:n=int(params['maxResults'])
        except (TypeError,ValueError):raise ValueError('invalid_request') from None
        if not 1<=n<=500:raise ValueError('invalid_request')
    if 'includeSpamTrash' in params and params['includeSpamTrash'] not in ('true','false'):raise ValueError('invalid_request')
    if 'format' in params and params['format']!='full':raise ValueError('invalid_request')

class GmailRestTransport:
    """Token provider is called for each request. Inject opener for offline tests.

    Default opener verifies TLS using system CA roots and refuses redirects. It may
    honor the host's HTTPS proxy settings. No automatic retries or token refresh.
    """
    def __init__(self,token_provider,*,opener=None,timeout=20,max_bytes=32*1024*1024):
        if not callable(token_provider) or not isinstance(timeout,(int,float)) or isinstance(timeout,bool) or not 1<=timeout<=60 or type(max_bytes) is not int or not 1<=max_bytes<=32*1024*1024:
            raise ValueError('invalid_transport_configuration')
        self._token_provider=token_provider
        self._opener=opener if opener is not None else build_opener(NoRedirect(),HTTPSHandler(context=ssl.create_default_context()))
        if not callable(getattr(self._opener,'open',None)):raise ValueError('invalid_transport_configuration')
        self._timeout=timeout;self._max_bytes=max_bytes

    def get(self,path,params):
        _validate(path,params)
        try:token=self._token_provider()
        except Exception:token=None
        if not isinstance(token,str) or not 1<=len(token)<=8192 or any(ord(c)<33 or ord(c)>126 for c in token):
            token=None
            raise TransportError('access_token_unavailable')
        query=urlencode(params)
        url=BASE+path+('?' + query if query else '')
        request=Request(url,method='GET',headers={'Authorization':'Bearer '+token,'Accept':'application/json','Accept-Encoding':'identity'})
        failure=None;raw=None
        try:
            with self._opener.open(request,timeout=self._timeout) as response:
                status=response.status
                if status!=200:failure=('http',status,_safe_retry_after(response.headers))
                else:raw=response.read(self._max_bytes+1)
        except HTTPError as exc:
            failure=('http',exc.code,_safe_retry_after(exc.headers))
            try:exc.close()
            except Exception:pass
        except (URLError,TimeoutError,OSError):failure=('network',None,None)
        except Exception:failure=('transport',None,None)
        # Raise after leaving except blocks so diagnostic tracebacks contain neither
        # original exception payloads nor request objects with sensitive headers.
        request.remove_header('Authorization'); request=None; token=None; response=None
        if failure:
            if failure[0]=='http':
                if type(failure[1]) is not int or not 100<=failure[1]<=599:raise TransportError('transport_error')
                raise GmailHTTPError(failure[1],failure[2])
            raise TransportError('network_error' if failure[0]=='network' else 'transport_error')
        if not isinstance(raw,bytes) or len(raw)>self._max_bytes:
            raw=None
            raise InvalidResponse('response_too_large')
        try:result=json.loads(raw.decode('utf-8','strict'))
        except (UnicodeError,ValueError):result=None
        raw=None
        if not isinstance(result,dict):raise InvalidResponse('invalid_json_response')
        return result
