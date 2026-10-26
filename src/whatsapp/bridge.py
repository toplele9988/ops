"""Private WAHA gateway: authenticated archive, explicit sends, persistent audit.
No autonomous send loop. QR portal is loopback-only and reached through SSH.
"""
import base64,hashlib,hmac,json,os,re,sqlite3,threading,time,urllib.request,urllib.error
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path

CONFIG=Path(os.environ.get('LECHU_WA_CONFIG', 'whatsapp-bridge.json'))
PAIR_REQUEST_DIR=Path(os.environ.get('LECHU_WA_PAIR_REQUEST_DIR', 'pairing-requests'))
MAX_BODY=32*1024*1024
CHAT=re.compile(r'^[0-9A-Za-z_-]{3,100}@(c\.us|lid|g\.us)$')
def canon(v):return json.dumps(v,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)
def sha(v):return hashlib.sha256(v if isinstance(v,bytes) else v.encode()).hexdigest()
def need(ok,code):
    if not ok:raise ValueError(code)
def cfg():
    info=CONFIG.lstat();need(not CONFIG.is_symlink() and info.st_uid==0 and info.st_mode&0o077==0o040,'config_not_private')
    return json.loads(CONFIG.read_text())
def db_open(c):
    d=sqlite3.connect(c['db'],timeout=10);d.row_factory=sqlite3.Row
    d.execute('PRAGMA busy_timeout=10000');d.execute('PRAGMA synchronous=FULL');return d

def initialize(c):
    with db_open(c) as d:
        d.executescript('''
CREATE TABLE IF NOT EXISTS binding(session TEXT PRIMARY KEY,account_id TEXT NOT NULL,owner TEXT NOT NULL,scope TEXT NOT NULL,bound_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS events(event_key TEXT PRIMARY KEY,event_id TEXT,event_type TEXT NOT NULL,session TEXT NOT NULL,body_sha TEXT NOT NULL,raw_json TEXT NOT NULL,received REAL NOT NULL);
CREATE TRIGGER IF NOT EXISTS immutable_events_u BEFORE UPDATE ON events BEGIN SELECT RAISE(ABORT,'immutable_event');END;
CREATE TRIGGER IF NOT EXISTS immutable_events_d BEFORE DELETE ON events BEGIN SELECT RAISE(ABORT,'immutable_event');END;
CREATE TABLE IF NOT EXISTS messages(message_key TEXT PRIMARY KEY,session TEXT NOT NULL,account_id TEXT NOT NULL,chat_id TEXT NOT NULL,message_id TEXT NOT NULL,direction TEXT NOT NULL,content TEXT NOT NULL,sent_at REAL,received REAL NOT NULL,raw_event TEXT NOT NULL,has_media INTEGER NOT NULL,media_json TEXT NOT NULL,source_sha TEXT NOT NULL,owner TEXT NOT NULL,scope TEXT NOT NULL,UNIQUE(session,account_id,message_id,source_sha));
CREATE TABLE IF NOT EXISTS recipients(session TEXT NOT NULL,account_id TEXT NOT NULL,chat_id TEXT NOT NULL,origin TEXT NOT NULL,created REAL NOT NULL,PRIMARY KEY(session,account_id,chat_id));
CREATE TABLE IF NOT EXISTS knowledge_jobs(message_key TEXT PRIMARY KEY,status TEXT NOT NULL,asset_id TEXT,error_code TEXT,updated REAL NOT NULL);
CREATE TABLE IF NOT EXISTS outbound(idempotency_key TEXT PRIMARY KEY,request_sha TEXT NOT NULL,session TEXT NOT NULL,account_id TEXT NOT NULL,chat_id TEXT NOT NULL,kind TEXT NOT NULL,state TEXT NOT NULL,request_json TEXT NOT NULL,response_json TEXT,error_code TEXT,created REAL NOT NULL,updated REAL NOT NULL);
CREATE TABLE IF NOT EXISTS audits(id INTEGER PRIMARY KEY AUTOINCREMENT,action TEXT NOT NULL,detail_json TEXT NOT NULL,created REAL NOT NULL);
''')
    os.chmod(c['db'],0o600)

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):return None

def message_projection(payload):
    need(isinstance(payload,dict),'message_payload_invalid')
    mid=payload.get('id');outgoing=payload.get('fromMe')
    need(isinstance(mid,str) and 1<=len(mid)<=500 and type(outgoing) is bool,'message_identity_invalid')
    chat=payload.get('to') if outgoing else payload.get('from')
    need(isinstance(chat,str) and CHAT.fullmatch(chat),'unsupported_chat_kind')
    content=payload.get('body') or '';need(isinstance(content,str) and len(content)<=200000,'message_content_invalid')
    ts=payload.get('timestamp');ts=ts if type(ts) in (int,float) and 0<ts<10**12 else None
    media=payload.get('media') or {};need(isinstance(media,dict),'message_media_invalid')
    has_media=payload.get('hasMedia',False);need(type(has_media) is bool,'message_media_invalid')
    return {'message_id':mid,'chat_id':chat,'direction':'outbound' if outgoing else 'inbound','content':content,'sent_at':ts,'has_media':int(has_media),'media_json':canon(media),'source_sha':sha(canon(payload))}

def waha(c,path,body=None,method=None,timeout=30,raw=False):
    need(path.startswith('/api/') and '\n' not in path,'waha_path_invalid')
    headers={'X-Api-Key':c['waha_api_key'],'Accept':'image/png' if raw else 'application/json'}
    data=None
    if body is not None:data=canon(body).encode();headers['Content-Type']='application/json'
    request=urllib.request.Request(c['waha_url']+path,data=data,headers=headers,method=method)
    with urllib.request.build_opener(NoRedirect(),urllib.request.ProxyHandler({})).open(request,timeout=timeout) as r:
        payload=r.read(MAX_BODY+1);need(len(payload)<=MAX_BODY,'upstream_response_large')
        return (payload,r.headers.get('Content-Type','application/octet-stream')) if raw else json.loads(payload)

def bind(c,api=waha):
    session=api(c,'/api/sessions/'+c['session'])
    me=(session.get('me') or {}).get('id')
    need(session.get('status')=='WORKING' and isinstance(me,str) and CHAT.fullmatch(me),'session_not_working')
    with db_open(c) as d:
        old=d.execute('SELECT * FROM binding WHERE session=?',(c['session'],)).fetchone()
        if old:need(old['account_id']==me and old['owner']==c['owner'],'account_rebind_denied');return dict(old)
        scope='private:'+c['profile_id']+':'+c['owner']+':whatsapp:'+sha(me)[:24]
        d.execute('INSERT INTO binding VALUES(?,?,?,?,?)',(c['session'],me,c['owner'],scope,time.time()))
        d.execute('INSERT INTO audits(action,detail_json,created) VALUES(?,?,?)',('first_account_binding',canon({'session':c['session'],'account_sha':sha(me),'scope_sha':sha(scope)}),time.time()))
        return dict(d.execute('SELECT * FROM binding WHERE session=?',(c['session'],)).fetchone())

def receive(c,raw,headers,api=waha):
    expected=hmac.new(c['webhook_key'].encode(),raw,hashlib.sha512).hexdigest()
    need(headers.get('X-Webhook-Hmac-Algorithm')=='sha512' and hmac.compare_digest(expected,headers.get('X-Webhook-Hmac','')),'webhook_auth_failed')
    event=json.loads(raw);need(isinstance(event,dict) and event.get('session')==c['session'],'session_mismatch')
    kind=event.get('event');need(kind in ('message.any','session.status','message.ack','message.revoked'),'event_not_subscribed')
    bodysha=sha(raw);eventkey=sha(canon([event.get('id'),kind,c['session'],bodysha]));stamp=time.time()
    with db_open(c) as d:
        d.execute('INSERT OR IGNORE INTO events VALUES(?,?,?,?,?,?,?)',(eventkey,str(event.get('id','')),kind,c['session'],bodysha,raw.decode(),stamp))
    if kind!='message.any':return {'archived':True,'event_key':eventkey}
    binding=bind(c,api)
    try:projection=message_projection(event.get('payload'))
    except ValueError as e:
        if str(e)=='unsupported_chat_kind':return {'archived':True,'message':'unsupported_chat_kind'}
        raise
    key=sha(canon([c['session'],binding['account_id'],projection['message_id'],projection['source_sha']]))
    with db_open(c) as d:
        d.execute('INSERT OR IGNORE INTO messages VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(key,c['session'],binding['account_id'],projection['chat_id'],projection['message_id'],projection['direction'],projection['content'],projection['sent_at'],stamp,eventkey,projection['has_media'],projection['media_json'],projection['source_sha'],binding['owner'],binding['scope']))
        d.execute('INSERT OR IGNORE INTO recipients VALUES(?,?,?,?,?)',(c['session'],binding['account_id'],projection['chat_id'],'signed_observed_message',stamp))
        d.execute('INSERT OR IGNORE INTO knowledge_jobs VALUES(?,?,?,?,?)',(key,'pending',None,None,stamp))
    return {'archived':True,'message_key':key,'direction':projection['direction']}

def send(c,kind,packet,api=waha):
    need(kind in ('sendText','sendFile','sendImage','sendVoice'),'send_kind_invalid')
    need(isinstance(packet,dict),'request_invalid')
    key=packet.get('idempotency_key');need(isinstance(key,str) and re.fullmatch('[A-Za-z0-9._-]{8,120}',key),'idempotency_key_required')
    need(packet.get('session')==c['session'],'session_mismatch')
    account=packet.get('account_id');chat=packet.get('chat_id')
    need(isinstance(account,str) and isinstance(chat,str) and CHAT.fullmatch(chat),'recipient_invalid')
    actor=packet.get('actor')
    need(actor in ('owner_explicit','approved_operator','model_auto'),'explicit_actor_required')
    if actor=='model_auto':
        need(packet.get('auto_send') is True and c.get('auto_send') is True and chat in c.get('auto_send_chat_ids',[]),'automatic_send_scope_denied')
    else:need(packet.get('auto_send',False) is False,'automatic_send_actor_mismatch')
    body={'session':c['session'],'chatId':chat}
    if kind=='sendText':
        text=packet.get('text');need(isinstance(text,str) and 0<len(text)<=10000,'text_invalid');body['text']=text
    else:
        f=packet.get('file');need(isinstance(f,dict) and set(f)=={'filename','mimetype','data'},'base64_file_required')
        need(isinstance(f['filename'],str) and 0<len(f['filename'])<250 and '/' not in f['filename'] and '\\' not in f['filename'],'filename_invalid')
        need(isinstance(f['mimetype'],str) and re.fullmatch('[A-Za-z0-9.+-]+/[A-Za-z0-9.+-]+',f['mimetype']),'mimetype_invalid')
        try:binary=base64.b64decode(f['data'],validate=True)
        except Exception:raise ValueError('base64_invalid') from None
        need(0<len(binary)<=20*1024*1024,'file_size_invalid');body['file']=f
        cap=packet.get('caption','');need(isinstance(cap,str) and len(cap)<=10000,'caption_invalid');body['caption']=cap
    requestsha=sha(canon([kind,packet]));stamp=time.time()
    # Check duplicate before network: unknown in-flight sends must never repeat.
    with db_open(c) as d:
        old=d.execute('SELECT * FROM outbound WHERE idempotency_key=?',(key,)).fetchone()
        if old:
            need(old['request_sha']==requestsha,'idempotency_conflict')
            return {'idempotency_key':key,'state':old['state'],'replayed':True}
    bound=bind(c,api);need(bound['account_id']==account,'account_mismatch')
    with db_open(c) as d:
        d.execute('BEGIN IMMEDIATE')
        old=d.execute('SELECT * FROM outbound WHERE idempotency_key=?',(key,)).fetchone()
        if old:
            need(old['request_sha']==requestsha,'idempotency_conflict')
            return {'idempotency_key':key,'state':old['state'],'replayed':True}
        need(d.execute('SELECT 1 FROM recipients WHERE session=? AND account_id=? AND chat_id=?',(c['session'],account,chat)).fetchone(),'recipient_not_bound')
        d.execute('INSERT INTO outbound VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(key,requestsha,c['session'],account,chat,kind,'in_flight',canon(packet),None,None,stamp,stamp))
    # No DB cursor/transaction spans provider HTTP. Complete response is private.
    try:
        answer=api(c,'/api/'+kind,body,timeout=60)
        need(isinstance(answer,dict) and isinstance(answer.get('id'),str),'send_response_unconfirmed')
        state='accepted';code=None;response=canon(answer)
    except urllib.error.HTTPError as e:
        state='rejected' if 400<=e.code<500 else 'unknown';code='provider_http_'+str(e.code);response=None
    except Exception:
        state='unknown';code='send_outcome_unknown';response=None
    with db_open(c) as d:d.execute('UPDATE outbound SET state=?,response_json=?,error_code=?,updated=? WHERE idempotency_key=?',(state,response,code,time.time(),key))
    return {'idempotency_key':key,'state':state,'error_code':code}

def bind_recipient(c,packet,api=waha):
    need(isinstance(packet,dict) and packet.get('actor') in ('owner_explicit','approved_operator') and packet.get('confirmed') is True,'recipient_confirmation_required')
    need(packet.get('session')==c['session'],'session_mismatch')
    chat=packet.get('chat_id');reason=packet.get('reason')
    need(isinstance(chat,str) and CHAT.fullmatch(chat),'recipient_invalid')
    need(isinstance(reason,str) and 3<=len(reason.strip())<=240,'binding_reason_required')
    bound=bind(c,api);need(packet.get('account_id')==bound['account_id'],'account_mismatch')
    with db_open(c) as d:
        inserted=d.execute('INSERT OR IGNORE INTO recipients VALUES(?,?,?,?,?)',(c['session'],bound['account_id'],chat,'explicit_owner_confirmation',time.time())).rowcount
        if inserted:d.execute('INSERT INTO audits(action,detail_json,created) VALUES(?,?,?)',('recipient_bound',canon({'chat_id':chat,'account_id':bound['account_id'],'reason':reason,'actor':packet['actor']}),time.time()))
    return {'bound':True,'new':bool(inserted)}

def status(c):
    with db_open(c) as d:
        out={'session':c['session'],'auto_send':c['auto_send'],'messages':d.execute('SELECT count(*) FROM messages').fetchone()[0],
             'bound':bool(d.execute('SELECT 1 FROM binding').fetchone()),'knowledge':dict(d.execute('SELECT status,count(*) FROM knowledge_jobs GROUP BY status').fetchall()),'outbound':dict(d.execute('SELECT state,count(*) FROM outbound GROUP BY state').fetchall())}
    try:
        path=PAIR_REQUEST_DIR/'result.json';info=path.lstat()
        if not path.is_symlink() and info.st_uid==0 and info.st_mode&0o777==0o640 and info.st_nlink==1 and info.st_size<2048:
            result=json.loads(path.read_text());out['pairing_refresh']={k:result[k] for k in ('status','error_code') if k in result and isinstance(result[k],str) and re.fullmatch('[a-z_]{1,80}',result[k])}
    except (OSError,ValueError,TypeError):pass
    try:out['waha_status']=waha(c,'/api/sessions/'+c['session']).get('status')
    except Exception:out['waha_status']='unavailable'
    return out

def refresh_unpaired(c,api=waha):
    with db_open(c) as d:need(not d.execute('SELECT 1 FROM binding').fetchone(),'paired_session_restart_denied')
    current=api(c,'/api/sessions/'+c['session'])
    need(not current.get('me') and current.get('status') in ('FAILED','STOPPED','SCAN_QR_CODE','STARTING'),'pairing_refresh_not_needed')
    need(PAIR_REQUEST_DIR.is_dir() and not PAIR_REQUEST_DIR.is_symlink(),'pairing_request_directory_missing')
    request={'request_id':os.urandom(16).hex(),'requested_at':time.time(),'action':'prepare_unpaired_qr'}
    path=PAIR_REQUEST_DIR/'request.json';tmp=PAIR_REQUEST_DIR/('.request-'+request['request_id'])
    fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    try:
        with os.fdopen(os.dup(fd),'w') as f:f.write(canon(request));f.flush();os.fsync(f.fileno())
        tmp.replace(path)
    finally:os.close(fd);tmp.unlink(missing_ok=True)
    return {'status':'PREPARING','request_id':request['request_id']}

QR_PAGE='''<!doctype html><html lang="zh"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>221 WhatsApp 关联设备</title><style>body{font:18px system-ui;margin:40px auto;max-width:700px;padding:20px}img{max-width:320px;border:1px solid #ddd}p{line-height:1.7}button{padding:10px}</style><h1>221 WhatsApp 关联设备</h1><p>在业务手机 WhatsApp 打开“关联设备”，扫描下方二维码。此页面经本机 SSH 通道访问；请勿分享二维码。</p><p id="state">正在读取状态…</p><p><button id="reset" onclick="restartPairing()" disabled>二维码过期或启动卡住时，重新准备扫码</button></p><p>只有点击按钮才会重新准备。系统会完整备份并隔离旧的未登录浏览器资料；已关联账号不会被此按钮重置。</p><img id="qr" hidden alt="二维码准备中"><p>关联后开始双向归档。完整发送接口保留给明确授权的操作。</p><script>
let loading=false,nextManualRefresh=0;
async function restartPairing(){nextManualRefresh=Date.now()+60000;document.getElementById('reset').disabled=true;document.getElementById('qr').hidden=true;document.getElementById('state').textContent='正在重新准备二维码，请稍候…';try{let r=await fetch('/qr/restart',{method:'POST',headers:{'Content-Type':'application/json','X-WA-Portal':'1'},body:'{}'});if(!r.ok)document.getElementById('state').textContent='当前不需要刷新，或账号已经关联';else refresh()}catch(e){document.getElementById('state').textContent='重新准备中，请稍候…'}}
async function refresh(){if(loading)return;loading=true;try{let s=await(await fetch('/status',{cache:'no-store'})).json();let label={STARTING:'正在启动浏览器并生成新码，请稍候…',SCAN_QR_CODE:'请扫描当前二维码；手机提示过期时点击上方按钮',WORKING:'关联成功，正在监听消息',FAILED:'扫码会话已过期，请点击上方按钮',STOPPED:'会话未启动，请点击上方按钮'};document.getElementById('state').textContent=(label[s.waha_status]||'服务准备中')+'；已归档 '+s.messages+' 条'+(s.pairing_refresh?.status==='blocked'?'；重新准备未完成，请稍后重试；已绑定账号不会被重置':'');document.getElementById('reset').disabled=s.bound||Date.now()<nextManualRefresh||!['SCAN_QR_CODE','FAILED','STOPPED','STARTING'].includes(s.waha_status);let q=document.getElementById('qr');q.hidden=s.waha_status!=='SCAN_QR_CODE';if(!q.hidden){q.onerror=()=>{q.hidden=true};q.src='/qr?t='+Date.now()}}catch(e){document.getElementById('state').textContent='暂不可达，请稍后刷新'}finally{loading=false}}refresh();setInterval(refresh,5000)
</script></html>'''

class Handler(BaseHTTPRequestHandler):
    server_version='221-WhatsApp/1';sys_version=''
    def log_message(self,*_):pass
    def reply(self,code,body,ctype='application/json'):
        raw=body if isinstance(body,bytes) else canon(body).encode() if ctype=='application/json' else body.encode()
        self.send_response(code);self.send_header('Content-Type',ctype);self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.send_header('X-Frame-Options','DENY');self.send_header('Content-Security-Policy',"frame-ancestors 'none'");self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
    def api_auth(self,c):return hmac.compare_digest(self.headers.get('Authorization',''),'Bearer '+c['bridge_api_key'])
    def do_GET(self):
        try:
            c=cfg();path=self.path.split('?')[0]
            if self.server.role=='qr':
                if path=='/':return self.reply(200,QR_PAGE,'text/html; charset=utf-8')
                if path=='/status':return self.reply(200,status(c))
                if path=='/qr':
                    raw,ct=waha(c,'/api/'+c['session']+'/auth/qr?format=image',raw=True)
                    need(ct.split(';')[0] in ('image/png','image/jpeg'),'qr_format_invalid');return self.reply(200,raw,ct)
            if self.server.role=='api' and path=='/health':return self.reply(200,{'service':'whatsapp-bridge','ready':True})
            if self.server.role=='api' and path=='/api/status' and self.api_auth(c):return self.reply(200,status(c))
            if self.server.role=='api' and path=='/api/binding' and self.api_auth(c):
                with db_open(c) as d:binding=d.execute('SELECT session,account_id FROM binding WHERE session=?',(c['session'],)).fetchone()
                return self.reply(200,dict(binding) if binding else {'bound':False})
            return self.reply(404,{'error':'not_found'})
        except Exception:return self.reply(503,{'error':'temporarily_unavailable'})
    def do_POST(self):
        try:
            c=cfg();self.connection.settimeout(15)
            valid=(self.server.role=='qr' and self.path=='/qr/restart') or (self.server.role in ('webhook','api') and self.path=='/events') or (self.server.role=='api' and (self.path=='/api/recipients/bind' or re.fullmatch('/api/(sendText|sendFile|sendImage|sendVoice)',self.path)))
            if not valid:return self.reply(404,{'error':'not_found'})
            if self.server.role=='api' and self.path!='/events':need(self.api_auth(c),'api_auth_failed')
            length=int(self.headers.get('Content-Length','0'));need(0<length<=MAX_BODY,'body_size_invalid')
            raw=self.rfile.read(length);need(len(raw)==length,'body_incomplete')
            if self.server.role=='qr' and self.path=='/qr/restart':
                need(self.headers.get('X-WA-Portal')=='1','portal_origin_required')
                return self.reply(200,refresh_unpaired(c))
            if self.server.role in ('webhook','api') and self.path=='/events':return self.reply(200,receive(c,raw,self.headers))
            need(self.server.role=='api' and self.api_auth(c),'api_auth_failed')
            if self.path=='/api/recipients/bind':return self.reply(200,bind_recipient(c,json.loads(raw)))
            match=re.fullmatch('/api/(sendText|sendFile|sendImage|sendVoice)',self.path)
            need(match is not None,'path_not_allowed');return self.reply(200,send(c,match.group(1),json.loads(raw)))
        except ValueError as e:
            code=str(e);code=code if re.fullmatch('[a-z_]{3,80}',code) else 'request_invalid'
            return self.reply(401 if 'auth' in code else 409 if any(x in code for x in ['bound','working','mismatch','conflict']) else 400,{'error':code})
        except Exception:return self.reply(503,{'error':'request_not_completed'})

def main():
    os.umask(0o077);c=cfg();initialize(c)
    servers=[]
    for host,port,role in [('127.0.0.1',8788,'api'),(c['webhook_bind'],8788,'webhook'),('127.0.0.1',8789,'qr')]:
        s=ThreadingHTTPServer((host,port),Handler);s.role=role;s.daemon_threads=True;servers.append(s)
        threading.Thread(target=s.serve_forever,daemon=True).start()
    print(canon({'event':'started','listeners':3,'customer_sends':0}),flush=True)
    while True:time.sleep(60)
if __name__=='__main__':main()
