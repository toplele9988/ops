"""Shared-owner conversation routes. Never installs itself on import.

Existing SecurityMiddleware must authenticate either the owner session or trusted
proxy first. A trusted proxy grants the same shared business access as existing
GET pages; it is not a distinct individual identity. This module saves internal
follow-up notes; it does not send customer messages.
"""
from __future__ import annotations
import asyncio
import sqlite3
from datetime import datetime, timezone
from urllib.parse import urlencode
from starlette.concurrency import run_in_threadpool
from starlette.routing import Route
from starlette.responses import RedirectResponse

from . import owner_inbox, conversation_notes

_slots = asyncio.Semaphore(2)  # per process; production multiworker limits need separate review


def build(app_module, backend=owner_inbox, notes=conversation_notes):
    async def inbox(request):
        # Only accept the server middleware's verified state, never a header or
        # query parameter. Proxy is the current shared business login, not a user.
        trusted_proxy = getattr(request.state, 'proxy_trusted', False) is True
        token = request.cookies.get(app_module.auth.COOKIE_NAME)
        session = None if trusted_proxy else await run_in_threadpool(app_module.STORE.get_session, token)
        if not trusted_proxy and (session is None or not session.authenticated):
            return app_module.error_response(request, 403)
        query=request.query_params
        if any(len(query.getlist(k)) > 1 for k in ('cursor','conversation','message_cursor','channel','q','message_q')):
            return app_module.error_response(request,400)
        cursor, key, message_cursor = (query.get(k) for k in ('cursor','conversation','message_cursor'))
        channel, search = query.get('channel', ''), query.get('q', '')
        message_search=query.get('message_q','')
        if channel not in ('', 'wechat', 'alibaba', 'whatsapp') or len(search) > 128 or len(message_search) > 128 or (message_search and not key):
            return app_module.error_response(request,400)
        if ((cursor is not None and len(cursor)>4096) or
                (key is not None and (len(key)!=64 or any(c not in '0123456789abcdef' for c in key))) or
                (message_cursor is not None and (key is None or len(message_cursor)>4096))):
            return app_module.error_response(request,400)
        try:
            await asyncio.wait_for(_slots.acquire(),timeout=0.15)
        except asyncio.TimeoutError:
            return app_module.error_response(request,503)
        try:
            config = await run_in_threadpool(app_module.read_json_mapping, app_module.data.CONFIG)
            owner = app_module.inquiry_owner(config)  # derived from trusted server config, not the browser
            # One source scan and one authority snapshot for the list and its
            # optional detail avoids displaying a list from an earlier grant.
            page = await run_in_threadpool(backend.owner_page, owner, cursor=cursor, key=key,
                                           message_cursor=message_cursor, limit=30, message_limit=30,
                                           channel=channel, search=search, message_search=message_search)
            selected = page.get('selected')
            if selected is not None:selected['key'] = key
            # Before returning private data re-check the relevant existing gate.
            # A proxy is a shared business access gate, not an individual identity.
            if trusted_proxy:
                settings = await run_in_threadpool(app_module.auth.load_settings)
                if (not settings.enabled or
                    not await run_in_threadpool(app_module.auth.proxy_request_allowed, settings) or
                    not app_module.auth.cf_access_allowed(request.headers, settings)):
                    return app_module.error_response(request,403)
            else:
                current = await run_in_threadpool(app_module.STORE.get_session, token)
                if current is None or not current.authenticated or current.token_hash != session.token_hash:
                    return app_module.error_response(request,403)
            def utc(ts):
                try:return datetime.fromtimestamp(ts,timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
                except (ValueError,OverflowError,OSError,TypeError):return '时间不可用'
            for item in page['items']:item['latest_at_display']=utc(item['latest_at'])
            if selected and selected.get('status')=='ok':
                for message in selected['items']:message['at_display']=utc(message['at'])
                for draft in selected.get('drafts',{}).get('items',[]):draft['created_at_display']=utc(draft.get('created_at'))
                principal=getattr(request.state,'password_principal',None)
                if principal and getattr(principal,'username',None):
                    try:
                        note=await run_in_threadpool(notes.read,key,selected['channel'],selected['account'],selected['scope'],selected['conversation_id'])
                        note['updated_at_display']=utc(note['updated_at']) if note.get('updated_at') else None
                        selected['followup']={'available':True, **note}
                    except (ValueError,PermissionError,sqlite3.Error,OSError,notes.ScopeMismatch):
                        selected['followup']={'available':False}
                else:selected['followup']={'available':False}
            params = {k:v for k,v in (('channel', channel), ('q', search), ('cursor', cursor)) if v}
            def link(**changes):
                values = dict(params)
                for k,v in changes.items():
                    if v: values[k] = v
                    else: values.pop(k,None)
                suffix = urlencode(values)
                return '/v3/workbench' + ('?' + suffix if suffix else '')
            for item in page['items']:
                item['detail_url'] = link(conversation=item['key'])
            page['next_url'] = link(cursor=page['next_cursor']) if page['next_cursor'] else None
            page['reset_url'] = link(cursor=None)
            page['filter_channel'] = channel
            page['filter_search'] = search
            if selected:
                selected['search_query']=message_search
                selected['search_action']=link(conversation=key)
                selected['latest_url']=link(conversation=key, message_q=message_search)
                selected['clear_search_url']=link(conversation=key)
                if selected.get('next_cursor'):
                    selected['next_url'] = link(conversation=key, message_cursor=selected['next_cursor'], message_q=message_search)
            page['login_mode']='shared_proxy' if trusted_proxy else 'owner_session'
            page['detail_enabled']=True
            page['selected']=selected
            response=app_module.render(request,'v3/owner_inbox.html',nav='workbench',workbench=page)
            response.headers['Cache-Control']='no-store'
            return response
        except ValueError:
            return app_module.error_response(request,400)
        except PermissionError:
            return app_module.error_response(request,403)
        except Exception:
            # No exception string, customer content, tokens or cursor in logs.
            return app_module.error_response(request,503)
        finally:
            _slots.release()
    async def save_note(request):
        # SecurityMiddleware validates same-session CSRF/form; verified username
        # is injected by the password gate, never read from the submitted form.
        session=getattr(request.state,'session',None)
        principal=getattr(request.state,'password_principal',None)
        token=request.cookies.get(app_module.auth.COOKIE_NAME)
        if not session or not session.authenticated or not principal or not getattr(principal,'username',None):
            return app_module.error_response(request,403)
        fields=getattr(request.state,'form',{})
        def one(name):
            x=fields.get(name,[])
            if len(x)!=1:raise ValueError('invalid_fields')
            return x[0]
        try:
            key,status,content,raw_version=(one(n) for n in ('conversation','status','content','version'))
            if not isinstance(key,str) or len(key)!=64 or any(c not in '0123456789abcdef' for c in key):raise ValueError('invalid_key')
            if not raw_version.isascii() or not raw_version.isdigit() or len(raw_version)>10:raise ValueError('invalid_version')
            version=int(raw_version)
            cfg=await run_in_threadpool(app_module.read_json_mapping,app_module.data.CONFIG)
            owner=app_module.inquiry_owner(cfg)
            try:await asyncio.wait_for(_slots.acquire(),timeout=0.15)
            except asyncio.TimeoutError:raise OSError('busy')
            try:page=await run_in_threadpool(backend.owner_page,owner,key=key,limit=1,message_limit=1)
            finally:_slots.release()
            selected=page.get('selected')
            if not selected or selected.get('status')!='ok':
                response=app_module.render(request,'v3/followup_result.html',nav='workbench',status_code=403,
                    error='会话暂不可用或权限已变化，备注未保存。请先复制下面的内容。',content=content,key=key)
                response.headers['Cache-Control']='no-store';return response
            current=await run_in_threadpool(app_module.STORE.get_session,token)
            if current is None or not current.authenticated or current.token_hash!=session.token_hash:return app_module.error_response(request,403)
            await run_in_threadpool(notes.save,key,selected['channel'],selected['account'],selected['scope'],selected['conversation_id'],
                actor=principal.username,status=status,content=content,expected_version=version)
            # Preserve valid current list/search context without accepting a redirect URL.
            args={'conversation':key}
            for field,maximum in (('channel',16),('q',128),('cursor',4096),('message_q',128),('message_cursor',4096)):
                values=request.query_params.getlist(field)
                if len(values)==1 and values[0] and len(values[0])<=maximum:
                    if field!='channel' or values[0] in ('wechat','alibaba','whatsapp'):args[field]=values[0]
            response=RedirectResponse('/v3/workbench?'+urlencode(args),status_code=303)
            response.headers['Cache-Control']='no-store';return response
        except notes.Conflict:
            response=app_module.render(request,'v3/followup_result.html',nav='workbench',status_code=409,
                error='其他人刚更新了这条备注，你的内容尚未保存。请先复制下面的内容，再返回查看最新记录。',content=content,key=key)
        except (ValueError,notes.ScopeMismatch):
            response=app_module.render(request,'v3/followup_result.html',nav='workbench',status_code=400,
                error='保存失败：备注最多 512 个字符，跟进状态需从列表中选择。',content=locals().get('content',''),key=locals().get('key',''))
        except (sqlite3.Error,OSError,PermissionError):
            response=app_module.render(request,'v3/followup_result.html',nav='workbench',status_code=503,
                error='备注暂时无法保存，请先复制下面的内容，稍后再试。',content=locals().get('content',''),key=locals().get('key',''))
        response.headers['Cache-Control']='no-store';return response
    return [Route('/v3/workbench',inbox,methods=['GET'],name='v3_owner_inbox'),
            Route('/v3/workbench/note',save_note,methods=['POST'],name='v3_followup_save')]
