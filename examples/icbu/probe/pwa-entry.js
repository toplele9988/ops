/* REFERENCE ONLY: anonymized example; never install into a real browser. */
/* PWA-only lifecycle. Explicit first opt-in; persistent opt-in can resume after refresh.
 * This surface cannot switch accounts or conversations. Existing history entry is independent. */
(function(){
 'use strict';
 if(location.origin!=='https://messenger.fixture.invalid'||location.pathname!=='/message/weblitePWA.htm'||window.top!==window.self)return;
 if(typeof GM_xmlhttpRequest!=='function'||typeof GM_getValue!=='function')return;
 const ROOT='icbu.example.pwa.v1.',TOKEN_STORAGE_KEY='icbu.example.v1.probe_token';
 const store={keys:async()=>GM_listValues(),list:async()=>GM_listValues(),get:async k=>GM_getValue(k,null),put:async(k,v)=>GM_setValue(k,v),set:async(k,v)=>GM_setValue(k,v),remove:async k=>GM_deleteValue(k)};
 const uuid=()=>crypto.randomUUID(),hash=async s=>[...new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(s)))].map(n=>n.toString(16).padStart(2,'0')).join('');
 const rawAdapter=ICBUPWADOM.create(document,location);
 let userAt=-Infinity,userVersion=0;
 for(const name of ['pointermove','pointerdown','keydown','input','wheel','touchstart'])document.addEventListener(name,e=>{if(e.isTrusted){userAt=performance.now();userVersion++;}},true);
 const human={active:()=>performance.now()-userAt<5000,version:()=>userVersion};
 let locked=false,release=null,loop=null,operationsLoop=null,starting=false,capture=null,operations=null,lastState='off',lastPoll=null;
 const guard=()=>{if(!locked||document.visibilityState!=='visible'||!navigator.onLine)throw Object.assign(new Error('pwa_not_visible_or_owner'),{code:'pwa_not_visible_or_owner'});};
 const adapter={URL_CANONICAL:ICBUPWADOM.URL_CANONICAL,
  identity:()=>{guard();return rawAdapter.identity();},snapshot:()=>{guard();return rawAdapter.snapshot();},
  readMessages:()=>{guard();return rawAdapter.readMessages();},readInput:()=>{guard();return rawAdapter.readInput();},
  fill:text=>{guard();return rawAdapter.fill(text);},clickSend:()=>{guard();return rawAdapter.clickSend();}};
 function request(kind,body){
  if(!['poll','ack'].includes(kind))return Promise.reject(new Error('pwa_transport_kind_invalid'));
  const endpoint=(globalThis.ICBU_EXAMPLE_CONFIG?.operationsBase || 'http://gateway.invalid:8791/api/operations/alibaba/')+kind;
  return new Promise((resolve,reject)=>{
   const token=GM_getValue(TOKEN_STORAGE_KEY,'');if(typeof token!=='string'||!token.trim()||/[\r\n]/.test(token))return reject(new Error('pwa_missing_token'));
   let finished=false,handle;const finish=(fn,value)=>{if(!finished){finished=true;clearTimeout(timer);fn(value);}};
   const timer=setTimeout(()=>{finish(reject,new Error('pwa_transport_timeout'));if(handle)handle.abort();},12000);
   try{handle=GM_xmlhttpRequest({method:'POST',url:endpoint,headers:{'Content-Type':'application/json',Authorization:'Bearer '+token},data:JSON.stringify(body),anonymous:true,redirect:'error',
    onload:r=>{if(r.finalUrl&&r.finalUrl!==endpoint)return finish(reject,new Error('pwa_transport_redirect'));
     if(r.status<200||r.status>=300)return finish(reject,new Error('pwa_http_'+r.status));let result;try{result=JSON.parse(r.responseText);}catch(_){return finish(reject,new Error('pwa_transport_json'));}if(kind==='poll')lastPoll={http_status:r.status,ok:!!result&&result.ok===true,empty_command:!!result&&result.command===null,at:new Date().toISOString()};finish(resolve,result);},
    onerror:()=>finish(reject,new Error('pwa_transport_error')),onabort:()=>finish(reject,new Error('pwa_transport_aborted')),ontimeout:()=>finish(reject,new Error('pwa_transport_timeout'))});
   }catch(_){finish(reject,new Error('pwa_transport_failed'));}
  });
 }
 async function start(){
  if(starting||locked)return;starting=true;
  try{
   rawAdapter.identity();if(!navigator.locks||!navigator.locks.request)throw new Error('pwa_browser_lock_unavailable');
   let acquiredResolve;const acquired=new Promise(r=>acquiredResolve=r);
   void navigator.locks.request('icbu-example-pwa-v1-single-executor',{ifAvailable:true},async lock=>{
    if(!lock){acquiredResolve(false);return;}
    locked=true;const lifetime=new Promise(r=>release=r);acquiredResolve(true);await lifetime;locked=false;
   }).catch(()=>{locked=false;acquiredResolve(false);});
   if(!await acquired)throw new Error('pwa_other_tab_active');
   const clientId='example-pwa-'+uuid();
   capture=ICBUPWACapture.create({adapter,core:ICBUPWACore,gate:ICBUPWAFreshGate,store,post:ICBUPWACore.createGMTransport(GM_xmlhttpRequest),getToken:async()=>GM_getValue(TOKEN_STORAGE_KEY,''),heartbeat:ICBULiveCapture.createHeartbeatTransport(GM_xmlhttpRequest),uuid,hash,visible:()=>document.visibilityState==='visible',online:()=>navigator.onLine,ownsLease:()=>locked,onStatus:s=>lastState=s.state});
   operations=ICBUOperationsClient.create({adapter,transport:request,store,clientId,hash,human});
   await capture.start();
   loop=setInterval(()=>{void capture.tick();},400);
   operationsLoop=setInterval(()=>{if(locked&&document.visibilityState==='visible'&&navigator.onLine)void operations.tick();},5000);
   await GM_setValue(ROOT+'enabled',true);
   console.info('[ICBU PWA 0.3.5] 已启用：当前会话新消息归档与221任务执行。初始旧消息不重放；发送须由221指定任务；后台/身份不明即暂停。');
  }catch(e){await stop(false);lastState=/^[a-z_]+$/.test(e.message||'')?e.message:'pwa_start_failed';throw new Error(lastState);}
  finally{starting=false;}
 }
 async function stop(persist=true){
  if(loop)clearInterval(loop);if(operationsLoop)clearInterval(operationsLoop);loop=operationsLoop=null;
  if(capture)capture.stop();if(operations)operations.stop();locked=false;if(release)release();release=null;
  if(persist)await GM_setValue(ROOT+'enabled',false);lastState='off';
 }
 function report(e){console.error('[ICBU PWA] 已暂停/等待，安全代码：'+(e&&e.message||'pwa_error'));}
 GM_registerMenuCommand('ICBU实时聊天：启用监听与221指定任务',()=>{void start().catch(report);});
 GM_registerMenuCommand('ICBU实时聊天：停止并保持关闭',()=>{void stop().catch(report);});
 GM_registerMenuCommand('ICBU实时聊天：查看状态（不显示正文或令牌）',()=>{void(async()=>{
  let identityOk=false;try{rawAdapter.identity();identityOk=true;}catch(_){}
  const stats={version:'0.3.5',account_verified:identityOk,lock_owned:locked,persistent_opt_in:!!await GM_getValue(ROOT+'enabled',false),state:lastState,last_poll:lastPoll,capture:capture&&capture.status(),queue:capture&&await capture.queueStatus(),operations:operations&&operations.status()};
  // Queue's lastAck contains no message text; strip its identifiers for this UI diagnostic.
  if(stats.queue)stats.queue.lastAck=stats.queue.lastAck?{persisted:stats.queue.lastAck.persisted,at:stats.queue.lastAck.at}:null;
  console.info('[ICBU PWA状态] '+JSON.stringify(stats,null,2));alert(JSON.stringify(stats,null,2));
 })().catch(report);});
 document.addEventListener('visibilitychange',()=>{if(capture&&document.visibilityState!=='visible')capture.reset('pwa_hidden');});
 window.addEventListener('offline',()=>{if(capture)capture.reset('pwa_offline');});
 window.addEventListener('pagehide',()=>{void stop(false);});
 // A persisted opt-in is never inferred from the older collector's settings.
 if(GM_getValue(ROOT+'enabled',false)){
  const until=Date.now()+60000;const boot=setInterval(()=>{
   if(Date.now()>until){clearInterval(boot);lastState='pwa_boot_identity_timeout';return;}
   try{rawAdapter.identity();}catch(_){return;}
   clearInterval(boot);void start().catch(report);
  },1000);
 }
 console.info('[ICBU PWA 0.3.5] 已加载。首次须油猴菜单显式启用；原历史断点与队列保持。');
})();
