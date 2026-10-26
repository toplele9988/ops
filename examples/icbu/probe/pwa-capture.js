/* REFERENCE ONLY: anonymized example; never install into a real browser. */
/* New PWA capture lane. Initial rendered messages form a memory-only baseline.
 * All old GM history/live keys and old collectors remain untouched. */
(function(root,factory){if(typeof module==='object'&&module.exports)module.exports=factory();else root.ICBUPWACapture=factory();})(typeof globalThis!=='undefined'?globalThis:this,function(){
 'use strict';
 const PREFIX='icbu.example.pwa.v1.',fail=code=>Object.assign(new Error(code),{code});
 function storage(store,core){
  const map=k=>k.startsWith(core.PREFIX)?PREFIX+'event.'+k.slice(core.PREFIX.length):k.startsWith(core.RECEIPT_PREFIX)?PREFIX+'receipt.'+k.slice(core.RECEIPT_PREFIX.length):(()=>{throw fail('pwa_storage_key_invalid');})();
  return {keys:async()=>(await store.keys()).filter(k=>k.startsWith(PREFIX+'event.')).map(k=>core.PREFIX+k.slice((PREFIX+'event.').length)),get:k=>store.get(map(k)),put:(k,v)=>store.put(map(k),v),remove:k=>store.remove(map(k))};
 }
 function create({adapter,core,gate,store,post,getToken,heartbeat,uuid,monotonic=()=>performance.now(),now=Date.now,
   visible=()=>true,online=()=>true,ownsLease=()=>true,hash,onStatus=()=>{}}){
  let active=false,busy=false,lastReason='off',baseline=null,lastRows=[],serverClock=null,armAfter=0;
  const freshness=gate.create({uuid,monotonic,maxGapMs:5000});
  const probe=core.createProbe({storage:storage(store,core),post,getToken,uuid,now,
   shouldSend:()=>active&&ownsLease(),beforePost:()=>{if(!active||!ownsLease())throw fail('pwa_not_active');adapter.identity();},
   onStatus:s=>{if(s.lastError)lastReason=s.lastError;}});
  const who=s=>JSON.stringify([s.account,s.conversation_id,s.buyer_name]);
  const reset=reason=>{baseline=null;lastRows=[];serverClock=null;freshness.reset(reason);lastReason=reason;};
  function status(){return {version:'0.3.5',active,busy,state:baseline?'armed':lastReason,baseline:!!baseline,server_time_utc:serverClock?new Date(serverClock.utc).toISOString():null,freshness:freshness.status()};}
  async function arm(first){
   const time=await heartbeat('GET',null,await getToken());
   if(!time||time.ok!==true||typeof time.server_time_utc!=='string')throw fail('pwa_server_clock_unavailable');
   const stamp=Date.parse(time.server_time_utc);
   if(!/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,9})?(?:Z|\+00:00)$/.test(time.server_time_utc)||!Number.isFinite(stamp))throw fail('pwa_server_clock_invalid');
   const serverTime=new Date(stamp).toISOString();
   if(serverTime.slice(0,19)!==time.server_time_utc.slice(0,19))throw fail('pwa_server_clock_invalid');
   if(!active||!visible()||!online()||!ownsLease())return reset('pwa_not_continuously_visible');
   const current=adapter.snapshot();if(who(current)!==who(first)||current.container!==first.container)throw fail('pwa_identity_changed_during_baseline');
   if(!freshness.arm({...current,server_time_utc:serverTime,visible:visible(),online:online()}))throw fail('pwa_baseline_not_ready');
   baseline={identity:who(current),container:current.container};lastRows=current.rows;
   serverClock={utc:stamp,mono:monotonic()};lastReason=null;
  }
  async function tick(){
   if(!active||busy)return;busy=true;
   try{
    if(!ownsLease())throw fail('pwa_lease_lost');
    if(!visible()||!online()){reset(!visible()?'pwa_hidden':'pwa_offline');return;}
    // A crash between prepared-record and spool writes resumes the same event ID/body.
    for(const key of (await store.keys()).filter(k=>k.startsWith(PREFIX+'prepared.'))){
     const pending=await store.get(key);await probe.enqueue(pending);await store.remove(key);
    }
    const current=adapter.snapshot();
    if(!baseline||baseline.identity!==who(current)||baseline.container!==current.container){if(now()<armAfter)return;reset('pwa_new_baseline');armAfter=now()+30000;await arm(current);armAfter=0;return;}
    if(!freshness.notice({...current,visible:visible(),online:online()})){reset('pwa_dom_continuity_lost');return;}
    const extras=current.rows.slice(lastRows.length);
    for(const row of extras){
     if(!row.content||!['buyer','seller'].includes(row.role))continue;
     const candidate=freshness.admit({...current,message_id:row.id,role:row.role,sent_at:row.sent_at});
     const event_id='pwa-'+await hash(JSON.stringify([current.account,current.conversation_id,row.id]));
     const journalKey=PREFIX+'prepared.'+event_id;
     let event=await store.get(journalKey);
     if(!event){
      const observed_at=new Date(serverClock.utc+Math.max(0,Math.floor(monotonic()-serverClock.mono))).toISOString();
      event={account:current.account,conversation_id:current.conversation_id,buyer_name:current.buyer_name,country:'',url:adapter.URL_CANONICAL||'https://messenger.fixture.invalid/message/weblitePWA.htm',content:row.content,message_id:row.id,sent_at:row.sent_at,history:true,role:row.role,observed_at,event_id,...(candidate||{})};
      // Validate before retaining a prepared record; never relabel an initial/old message as live.
      core.normalize(event,uuid,now);await store.put(journalKey,event);
     }
     await probe.enqueue(event);await store.remove(journalKey);
    }
    lastRows=current.rows;await probe.flush();lastReason=(await probe.status()).lastError||null;
   }catch(e){
    const code=e&&e.code||'pwa_capture_failed';reset(code);
    if(/spool|receipt|storage|event_id|prepared|lease/.test(code)){active=false;probe.stop();}
   }finally{busy=false;onStatus(status());}
  }
  async function start(){active=true;armAfter=0;reset('pwa_baseline_required');probe.resume(false);await tick();return status();}
  function stop(){active=false;probe.stop();reset('off');onStatus(status());}
  return Object.freeze({start,stop,tick,status,queueStatus:()=>probe.status(),reset});
 }
 return Object.freeze({PREFIX,storage,create});
});
