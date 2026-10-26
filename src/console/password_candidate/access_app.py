"""Isolated password login endpoint; does not deploy or create accounts."""
import asyncio,base64,hashlib,hmac,html,secrets,time
from collections import deque
from urllib.parse import parse_qs,urlsplit
from starlette.applications import Starlette
from starlette.responses import HTMLResponse,RedirectResponse
from starlette.routing import Route
from starlette.concurrency import run_in_threadpool
from .legacy_gate import PASSWORD_COOKIE

PRE='__Host-221_prelogin'
SETUP='__Host-221_presetup'
CSS='''
:root{color-scheme:light;--ink:#17233b;--muted:#59687c;--blue:#3459d1;--line:#dfe7f1;--wash:#f2f5fa}*{box-sizing:border-box}
body{margin:0;min-height:100vh;background:var(--wash);color:var(--ink);font-family:Inter,-apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei",sans-serif;line-height:1.55}
.shell{min-height:100vh;display:grid;grid-template-columns:minmax(310px,40%) 1fr}.brand-panel{position:relative;overflow:hidden;display:flex;flex-direction:column;justify-content:space-between;padding:48px clamp(32px,5vw,80px);color:white;background:#1a2b3b}
.brand-panel:after{content:"";position:absolute;left:clamp(32px,5vw,80px);right:clamp(32px,5vw,80px);bottom:100px;height:1px;background:rgba(255,255,255,.22);pointer-events:none}
.wordmark{display:flex;gap:12px;align-items:center;font-size:14px;letter-spacing:.04em;font-weight:700}.mark{display:grid;place-items:center;width:38px;height:38px;border:1px solid #8fc5f7;border-radius:12px;font-size:17px;letter-spacing:0;color:#d3ebff}
.hero{position:relative;z-index:1;max-width:430px;padding:64px 0}.eyebrow{margin:0 0 15px;color:#b7d9f4;font-size:12px;letter-spacing:.18em;font-weight:700}.hero h2{font-size:clamp(30px,2.7vw,40px);line-height:1.25;letter-spacing:.02em;margin:0 0 18px}.hero p{color:#cfdded;max-width:380px;margin:0;font-size:15px}.hero ul{display:flex;flex-wrap:wrap;gap:0;list-style:none;padding:0;margin:32px 0 0}.hero li{color:#dbe7ee;font-size:13px;line-height:1.4;padding-right:20px;margin-right:20px;border-right:1px solid rgba(219,231,238,.4)}.hero li:last-child{border:0}
.panel-foot{position:relative;z-index:1;color:#b4c8dc;font-size:12px}.content{display:flex;justify-content:center;align-items:center;padding:40px 24px}.content-inner{width:min(100%,760px)}.content-inner.narrow{max-width:440px}.topline{font-size:12px;color:var(--blue);font-weight:700;letter-spacing:.12em;margin-bottom:10px}.content h1{font-size:clamp(26px,2.7vw,34px);letter-spacing:-.03em;line-height:1.2;margin:0 0 10px}.subtitle{color:var(--muted);font-size:14px;margin:0 0 26px}
.card{background:white;border:1px solid #e4eaf2;border-radius:10px;box-shadow:0 8px 28px rgba(27,50,86,.05);padding:30px}.cards{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:18px}.cards .card{padding:26px}.card h2{margin:0 0 6px;font-size:18px}.card-desc{margin:0 0 22px;color:var(--muted);font-size:13px}.current{display:inline-flex;gap:8px;align-items:center;margin-bottom:22px;background:#eaf0ff;border:1px solid #dce6ff;padding:7px 13px;border-radius:100px;font-size:13px;color:#294db9}.current strong{overflow-wrap:anywhere}
form{display:grid;gap:17px}label{display:grid;gap:7px;font-size:13px;font-weight:650;color:#263852}input{display:block;width:100%;min-width:0;height:46px;padding:0 13px;border:1px solid #cbd6e5;border-radius:10px;background:#fff;color:#15243e;font:inherit;font-size:15px;transition:border-color .15s,box-shadow .15s}input::placeholder{color:#8795a8}input:focus{outline:2px solid transparent;border-color:#3c63d8;box-shadow:0 0 0 3px rgba(52,89,209,.18)}button{min-height:47px;border:0;border-radius:10px;background:#3459d1;color:#fff;font:inherit;font-size:14px;font-weight:700;cursor:pointer;transition:background .15s,transform .15s}button:hover{background:#2748b8}button:active{transform:translateY(1px)}button:focus-visible,a:focus-visible{outline:3px solid #8faeff;outline-offset:3px}a{color:#2f53c3;text-decoration:none;font-weight:600}a:hover{text-decoration:underline}.footnote{color:#53657a;font-size:12px;margin:16px 0 0}.back{display:inline-block;margin-top:22px;font-size:14px}.message{font-size:15px;overflow-wrap:anywhere}.message p{margin:0 0 16px}.small-brand{display:none}
.remember-row{display:flex;flex-wrap:wrap;justify-content:space-between;gap:12px}.remember-row label{display:flex;align-items:center;gap:8px;color:#43536b;font-size:13px;font-weight:500}.remember-row input{width:17px;height:17px;min-height:0;padding:0;accent-color:#3459d1}.remember-hint{margin:-6px 0 0;color:#53657a;font-size:12px;line-height:1.5}
.account-list{padding:20px 24px;margin-bottom:18px}.account-list h2{margin:0 0 5px;font-size:17px}.account-list p{margin:0 0 14px;color:#53657a;font-size:13px}.accounts{list-style:none;margin:0;padding:0}.accounts li{display:flex;align-items:center;gap:12px;padding:12px 0;border-top:1px solid #e7edf3}.accounts strong{overflow-wrap:anywhere}.accounts small{margin-left:auto;color:#53657a}.notice{padding:12px 14px;border-radius:8px;margin-bottom:18px;font-size:14px}.notice.success{background:#eaf7ef;color:#185c39;border-left:4px solid #218451}.notice.error{background:#fff2f0;color:#8a2f2a;border-left:4px solid #bd4d44}.field-error{font-size:12px;font-weight:600;color:#9c312b}.field-hint{font-size:12px;font-weight:400;color:#53657a}.full-state{color:#53657a;font-size:14px;line-height:1.6}
.account-bar{display:flex;align-items:center;gap:16px;padding:16px clamp(18px,4vw,56px);background:#1a2b3b;color:#fff}.account-bar a{color:#fff;text-decoration:none;font-weight:700}.account-bar span{font-size:13px;color:#d4e0ea}.account-screen .content{align-items:flex-start;padding-top:48px}.account-screen .content-inner{max-width:900px}.account-screen .cards .card{min-width:0}
@media(max-width:900px){.shell{grid-template-columns:1fr;grid-template-rows:auto 1fr}.brand-panel{padding:18px 24px}.brand-panel:after{display:none}.hero,.panel-foot{display:none}.content{align-items:flex-start;padding:34px 18px 50px}.wordmark{font-size:11px}.mark{width:34px;height:34px}.content-inner{max-width:760px}}
@media(max-width:600px){.cards{grid-template-columns:1fr}.card,.cards .card{padding:22px;border-radius:10px}.subtitle{margin-bottom:20px}.content{padding:28px 16px 44px}input,button{min-height:48px}}
@media(prefers-reduced-motion:reduce){*,*:before,*:after{transition:none!important;animation:none!important}}
'''
STYLE_HASH=base64.b64encode(hashlib.sha256(CSS.encode()).digest()).decode()
REMEMBER_JS = """(()=>{const f=document.getElementById('login-form');if(!f)return;const u=document.getElementById('login-user'),p=document.getElementById('login-password'),a=document.getElementById('remember-user'),b=document.getElementById('remember-password');try{const saved=localStorage.getItem('lechu-login-username');if(saved){u.value=saved;a.checked=true}b.checked=localStorage.getItem('lechu-login-browser-password')==='yes'}catch(e){}const mode=()=>{p.autocomplete=b.checked?'current-password':'off'};mode();b.addEventListener('change',mode);f.addEventListener('submit',()=>{mode();try{if(a.checked)localStorage.setItem('lechu-login-username',u.value);else localStorage.removeItem('lechu-login-username');if(b.checked)localStorage.setItem('lechu-login-browser-password','yes');else localStorage.removeItem('lechu-login-browser-password')}catch(e){}})})();"""
SCRIPT_HASH=base64.b64encode(hashlib.sha256(REMEMBER_JS.encode()).digest()).decode()
def page(title,body,status=200):
    title=html.escape(title)
    if title=='登录':
        intro='<h1>欢迎回来</h1><p class="subtitle">登录乐初运营工作台，继续处理你的业务。</p>'
        content='<div class="content-inner narrow">'+intro+'<section class="card" aria-label="登录">'+body+'</section></div>'
    elif title=='账号设置':
        content='<div class="content-inner"><h1>账号设置</h1><p class="subtitle">管理你的登录信息，也可以为同事创建一个同权限账号。</p>'+body+'</div>'
    else:
        content='<div class="content-inner narrow"><h1>'+title+'</h1><section class="card message"><p>'+body+'</p><a href="/v3/">返回工作台</a> <span aria-hidden="true"> · </span><a href="/access/login">返回登录</a></section></div>'
    if title=='账号设置':
        shell='<header class="account-bar"><a href="/v3/admin">乐初</a><span>系统管理 / 账号与成员</span></header><main class="content">'+content+'</main>'
    else:
        shell='<div class="shell"><aside class="brand-panel"><div class="wordmark"><span class="mark">L</span><span>乐初 · 运营工作台</span></div><div class="hero"><h2>让业务协作更清晰</h2><p>客户沟通、产品资料和业务知识，汇集在同一个工作空间。</p><ul><li>客户沟通</li><li>产品资料</li><li>业务知识</li></ul></div><div class="panel-foot">乐初 · 外贸 AI 运营工作台</div></aside><main class="content">'+content+'</main></div>'
    document='<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'+title+' · 乐初运营工作台</title><style>'+CSS+'</style></head><body'+(' class="account-screen"' if title=='账号设置' else '')+'>'+shell+('<script>'+REMEMBER_JS+'</script>' if title=='登录' else '')+'</body></html>'
    r=HTMLResponse(document,status_code=status)
    r.headers.update({'Cache-Control':'no-store','Content-Security-Policy':"default-src 'none'; style-src 'sha256-"+STYLE_HASH+"'; script-src 'sha256-"+SCRIPT_HASH+"'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'",'X-Frame-Options':'DENY','Referrer-Policy':'same-origin'})
    return r
def cookie(r,k,v,age,secure=True):r.set_cookie(k,v,max_age=age,secure=secure,httponly=True,samesite='strict',path='/')
class Access:
    def __init__(self,store,origins,secret,revoke_legacy=None,*,secure=True,password_cookie=PASSWORD_COOKIE):
        if isinstance(origins,str):origins=[origins]
        if not isinstance(origins,list) or not 1<=len(origins)<=4 or len(secret)<32:raise ValueError('invalid_settings')
        self.origins={}
        for origin in origins:
            if not isinstance(origin,str):raise ValueError('invalid_origin')
            p=urlsplit(origin)
            if p.scheme!=('https' if secure else 'http') or not p.netloc or p.username or p.password or p.path not in ('','/') or p.query or p.fragment or '*' in p.netloc or p.netloc in self.origins:raise ValueError('invalid_origin')
            self.origins[p.netloc]=origin.rstrip('/')
        self.store=store;self.secret=secret;self.revoke_legacy=revoke_legacy;self.secure=secure;self.password_cookie=password_cookie;self.audience='https' if secure else 'lan_http'
        self.pre=PRE if secure else 'imgw_lan_prelogin';self.setup_cookie=SETUP if secure else 'imgw_lan_presetup';self.flash_cookie='__Host-221_accountflash' if secure else 'imgw_lan_accountflash'
        # Shared within one worker for login and enrollment. Deployment must
        # additionally constrain worker count and limit at the trusted proxy.
        self.hash_slots=asyncio.Semaphore(2);self.attempts=deque()
    async def limited_hash(self,fn,*args):
        now=time.monotonic()
        while self.attempts and self.attempts[0]<=now-60:self.attempts.popleft()
        if len(self.attempts)>=30:raise RuntimeError('rate_limited')
        self.attempts.append(now)
        try:await asyncio.wait_for(self.hash_slots.acquire(),timeout=.05)
        except asyncio.TimeoutError:raise RuntimeError('busy')
        try:return await run_in_threadpool(fn,*args)
        finally:self.hash_slots.release()
    def allowed(self,r):return len(r.headers.getlist('host'))==1 and r.headers['host'] in self.origins
    def post_allowed(self,r):return self.allowed(r) and r.headers.getlist('origin')==[self.origins[r.headers['host']]]
    async def fields(self,r):
        if r.headers.get('content-type','').split(';')[0]!='application/x-www-form-urlencoded':raise ValueError('content_type')
        b=bytearray()
        async for c in r.stream():
            b.extend(c)
            if len(b)>4096:raise ValueError('body_large')
        f=parse_qs(b.decode('utf-8'),strict_parsing=True,keep_blank_values=True,max_num_fields=8)
        if any(len(x)!=1 for x in f.values()):raise ValueError('duplicate')
        return {k:v[0] for k,v in f.items()}
    def logout_csrf(self,token):return hmac.new(self.secret,('logout|'+token).encode(),hashlib.sha256).hexdigest()
    async def login(self,r):
        if not self.allowed(r):return page('请求被拒绝','',403)
        if r.method=='GET':
            token=secrets.token_urlsafe(32)
            flash=self.read_flash(r,'changed')
            banner='<div class="notice success" role="status">账号和密码已保存，请使用新信息登录。</div>' if flash else ''
            out=page('登录',banner+'<form id="login-form" method="post"><input type="hidden" name="csrf" value="'+html.escape(token)+'"><label>账号<input id="login-user" name="username" autocomplete="username" placeholder="请输入账号" required></label><label>密码<input id="login-password" type="password" name="password" autocomplete="off" placeholder="请输入密码" required></label><div class="remember-row"><label><input id="remember-user" type="checkbox">记住账号</label><label><input id="remember-password" type="checkbox">记住密码</label></div><p class="remember-hint">密码由浏览器的密码管理器保存；勾选后请按浏览器提示操作。</p><button type="submit">登录工作台 →</button></form><p class="footnote">使用你自己的账号和密码登录。</p>')
            cookie(out,self.pre,token,600,self.secure)
            if r.cookies.get(self.flash_cookie):out.delete_cookie(self.flash_cookie,path='/',secure=self.secure,httponly=True,samesite='strict')
            return out
        if not self.post_allowed(r):return page('请求被拒绝','',403)
        try:
            f=await self.fields(r);pre=r.cookies.get(self.pre,'')
            if not pre or not hmac.compare_digest(pre,f.get('csrf','')):raise ValueError('csrf')
            token=await self.limited_hash(self.store.login,f.get('username',''),f.get('password',''),self.audience)
            if token is None:return page('登录未成功','账号或密码不正确，请稍后重试。',401)
            out=RedirectResponse('/v3/workbench',status_code=303);cookie(out,self.password_cookie,token,8*3600,self.secure);out.delete_cookie(self.pre,path='/',secure=self.secure,httponly=True,samesite='strict');return out
        except (ValueError,UnicodeError,TypeError):return page('请求被拒绝','',400)
        except RuntimeError:return page('请求过多','请稍后重试。',429)
    def account_csrf(self,token):return hmac.new(self.secret,('account|'+token).encode(),hashlib.sha256).hexdigest()
    def make_flash(self,kind):
        stamp=str(int(time.time()));payload=kind+'.'+stamp
        return payload+'.'+hmac.new(self.secret,('flash|'+payload).encode(),hashlib.sha256).hexdigest()
    def read_flash(self,r,kind):
        try:
            k,stamp,signature=r.cookies.get(self.flash_cookie,'').split('.')
            payload=k+'.'+stamp
            return k==kind and 0<=time.time()-int(stamp)<=120 and hmac.compare_digest(signature,hmac.new(self.secret,('flash|'+payload).encode(),hashlib.sha256).hexdigest())
        except (ValueError,TypeError):return False
    def account_page(self,identity,names,csrf,*,error=None,which=None,flash=False,status=200,submitted_username=None):
        head='<input type="hidden" name="csrf" value="'+csrf+'">'
        hint='账号须以小写英文字母开头，共 3–64 位，可用小写字母、数字、点、下划线和连字符。'
        def field(label,name,kind,placeholder,form,*,value='',hint_text=''):
            key=form+'-'+name;show=error is not None and which==form and error[0]==name
            extra=' aria-invalid="true" aria-describedby="'+key+'-error"' if show else (' aria-describedby="'+key+'-hint"' if hint_text else '')
            attrs=' type="'+kind+'" name="'+name+'" id="'+key+'" placeholder="'+placeholder+'" required'+extra
            if name=='username':attrs+=' value="'+html.escape(value,quote=True)+'" minlength="3" maxlength="64" pattern="[a-z][a-z0-9_.\\-]{2,63}" autocomplete="'+('username' if form=='own' else 'off')+'"'
            else:attrs+=' autocomplete="'+('current-password' if name=='current_password' else 'new-password')+'"'+(' minlength="5" maxlength="256"' if name!='current_password' else '')
            txt='<label for="'+key+'">'+label+'</label>'
            if hint_text:txt+='<span class="field-hint" id="'+key+'-hint">'+hint_text+'</span>'
            if show:txt+='<span class="field-error" id="'+key+'-error">'+html.escape(error[1])+'</span>'
            return '<div>'+txt+'<input'+attrs+'></div>'
        def form_body(form):
            entered=submitted_username if error and which==form and isinstance(submitted_username,str) else (identity.username if form=='own' else '')
            items=[field('当前密码','current_password','password','输入你的当前密码',form),field('账号' if form=='own' else '同事账号','username','text','设置账号',form,value=entered,hint_text=hint),field('新密码','password','password','至少 5 个字符',form,hint_text='长度 5–256 个字符。'),field('重复新密码','confirm','password','再次输入新密码',form)]
            return ''.join(items)
        accounts=''.join('<li><strong>'+html.escape(name)+'</strong><small>'+('当前登录' if name==identity.username else '同事账号')+' · 同等权限</small></li>' for name in names)
        banner=('<div class="notice success" role="status">同事账号已创建，可以使用新账号登录。</div>' if flash else '')
        if error:banner+='<div class="notice error" role="alert">保存未成功：'+html.escape(error[1])+'</div>'
        own='<section class="card"><h2>修改我的账号</h2><p class="card-desc">保存后请使用新的账号密码重新登录。</p><form method="post" action="/access/account">'+head+form_body('own')+'<button type="submit">保存我的账号</button></form></section>'
        if len(names)<2:
            other='<section class="card"><h2>添加同事账号</h2><p class="card-desc">创建第二个账号，双方业务权限相同。</p><form method="post" action="/access/user">'+head+form_body('staff')+'<button type="submit">创建同事账号</button></form><p class="footnote">目前最多可设置两个账号。</p></section>'
        else:other=''
        capacity='<p class="full-state">已创建 2 个账号，当前账号数量已满。</p>' if len(names)>=2 else ''
        body=banner+'<section class="card account-list"><h2>已有账号 · '+str(len(names))+' / 2</h2><p>两人拥有相同业务权限。</p><ul class="accounts">'+accounts+'</ul>'+capacity+'</section><div class="cards">'+own+other+'</div><a class="back" href="/v3/admin">← 返回系统管理</a>'
        return page('账号设置',body,status)
    async def account(self,r):
        if not self.allowed(r):return page('请求被拒绝','',403)
        token=r.cookies.get(self.password_cookie,'');identity=await run_in_threadpool(self.store.resolve,token,self.audience)
        if not identity:return RedirectResponse('/access/login',status_code=303)
        csrf=self.account_csrf(token)
        if r.method=='GET':
            names=await run_in_threadpool(self.store.list_accounts,token,self.audience)
            if names is None:return RedirectResponse('/access/login',status_code=303)
            out=self.account_page(identity,names,csrf,flash=self.read_flash(r,'created'))
            if r.cookies.get(self.flash_cookie):out.delete_cookie(self.flash_cookie,path='/',secure=self.secure,httponly=True,samesite='strict')
            return out
        if not self.post_allowed(r):return page('请求被拒绝','',403)
        form='staff' if r.url.path=='/access/user' else 'own'
        f={}
        try:
            f=await self.fields(r)
            if not hmac.compare_digest(f.get('csrf',''),csrf):return page('请求被拒绝','请刷新页面后重试。',400)
            if f.get('password')!=f.get('confirm'):raise ValueError('password_mismatch')
            method=self.store.create_second if form=='staff' else self.store.update_self
            await self.limited_hash(method,token,self.audience,f.get('current_password',''),f.get('username',''),f.get('password',''))
            if form=='staff':
                out=RedirectResponse('/access/account',status_code=303);cookie(out,self.flash_cookie,self.make_flash('created'),120,self.secure);return out
            if self.revoke_legacy:await run_in_threadpool(self.revoke_legacy,token)
            out=RedirectResponse('/access/login',status_code=303);cookie(out,self.flash_cookie,self.make_flash('changed'),120,self.secure);out.delete_cookie(self.password_cookie,path='/',secure=self.secure,httponly=True,samesite='strict');return out
        except (ValueError,UnicodeError,TypeError) as exc:
            errors={'denied':('current_password','当前密码不正确或登录已失效，请核对后重试。'),'invalid_user':('username','账号格式不正确，请按页面提示填写。'),'user_exists':('username','该账号已存在，请使用其他账号。'),'user_limit':('username','已达到两个账号的上限。'),'invalid_password':('password','新密码长度须为 5–256 个字符。'),'password_mismatch':('confirm','两次输入的新密码不一致。')}
            err=errors.get(str(exc),('current_password','输入格式有误，请检查后重试。'))
            names=await run_in_threadpool(self.store.list_accounts,token,self.audience)
            if names is None:return RedirectResponse('/access/login',status_code=303)
            return self.account_page(identity,names,csrf,error=err,which=form,status=400,submitted_username=f.get('username'))
        except RuntimeError:return page('请求过多','请稍后重试。',429)
    async def logout(self,r):
        if not self.allowed(r):return page('请求被拒绝','',403)
        token=r.cookies.get(self.password_cookie,'');identity=await run_in_threadpool(self.store.resolve,token,self.audience)
        if not identity:return RedirectResponse('/access/login',status_code=303)
        if r.method=='GET':return page('退出登录','<form method="post"><input type="hidden" name="csrf" value="'+self.logout_csrf(token)+'"><button>退出</button></form>')
        if not self.post_allowed(r):return page('请求被拒绝','',403)
        try:
            f=await self.fields(r)
            expected=self.logout_csrf(token) if r.url.path=='/access/logout' else hmac.new(self.secret,b'legacy-csrf\0'+token.encode('ascii'),hashlib.sha256).hexdigest()
            if not hmac.compare_digest(f.get('csrf',''),expected):raise ValueError('csrf')
            await run_in_threadpool(self.store.logout,token)
            if self.revoke_legacy:await run_in_threadpool(self.revoke_legacy,token)
            out=RedirectResponse('/access/login',status_code=303);out.delete_cookie(self.password_cookie,path='/',secure=self.secure,httponly=True,samesite='strict');return out
        except (ValueError,UnicodeError,TypeError):return page('请求被拒绝','',400)
    def app(self):return Starlette(routes=[Route('/access/login',self.login,methods=['GET','POST']),Route('/access/account',self.account,methods=['GET','POST']),Route('/access/user',self.account,methods=['POST']),Route('/access/logout',self.logout,methods=['GET','POST']),Route('/logout',self.logout,methods=['POST'])])
