/* REFERENCE ONLY: anonymized example; never install into a real browser. */
/* Conservative live DOM admission. No platform requests, credentials, DOM writes or customer sends. */
(function(root,factory){if(typeof module==='object'&&module.exports)module.exports=factory();else root.ICBUPWAFreshGate=factory();})(typeof globalThis!=='undefined'?globalThis:this,function(){
 'use strict';
 const VERSION='0.3.5',SCHEMA='example-live-v1',MODE='dom_tail_append';
 const fail=code=>Object.assign(new Error(code),{code});
 const iso=x=>typeof x==='string'&&/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$/.test(x)&&Number.isFinite(Date.parse(x))&&new Date(x).toISOString()===x;
 function proof(raw){
  const p=raw&&raw.live_provenance;
  if(!p||Object.keys(p).sort().join()!=='baseline_server_time_utc,baseline_watermark_sent_at,epoch_uuid,mode,observed_seq,schema'
   ||p.schema!==SCHEMA||![MODE,'native_poll_after_baseline'].includes(p.mode)||typeof p.epoch_uuid!=='string'||!/^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/i.test(p.epoch_uuid)
   ||!iso(p.baseline_server_time_utc)||!iso(p.baseline_watermark_sent_at)||!Number.isSafeInteger(p.observed_seq)||p.observed_seq<1||p.observed_seq>1000000
   ||raw.account!=='Example'||raw.role!=='buyer'||raw.history!==false||raw.probe_version!==VERSION||!iso(raw.sent_at)||!iso(raw.observed_at)
   ||!/^tm:[A-Za-z0-9_-]{1,128}$/.test(raw.conversation_id||'')||!/^\d{1,30}(?:\.PNM)?$/.test(raw.message_id||'')
   ||Date.parse(raw.sent_at)<=Math.max(Date.parse(p.baseline_server_time_utc),Date.parse(p.baseline_watermark_sent_at))
   ||Date.parse(raw.observed_at)<Date.parse(raw.sent_at)||Date.parse(raw.observed_at)-Date.parse(raw.sent_at)>300000)throw fail('live_provenance_invalid');
  return {...p};
 }
 function create({uuid,monotonic=()=>performance.now(),maxGapMs=5000}){
  let state=null,lastReason='not_armed';
  const reset=reason=>{state=null;lastReason=reason||'baseline_required';};
  const signature=r=>JSON.stringify([r.id,r.sent_at,r.role,r.fingerprint]);
  function validRows(rows){return Array.isArray(rows)&&rows.length>0&&rows.every(r=>r.node&&/^\d{1,30}(?:\.PNM)?$/.test(r.id||'')&&iso(r.sent_at))&&new Set(rows.map(r=>r.id)).size===rows.length;}
  function arm({account,conversation_id,container,rows,server_time_utc,visible=true,online=true}){
   reset('baseline_required');if(account!=='Example'||!/^tm:[A-Za-z0-9_-]{1,128}$/.test(conversation_id||'')||!container||!validRows(rows)||!iso(server_time_utc)||!visible||!online)return false;
   const watermark=new Date(Math.max(...rows.map(r=>Date.parse(r.sent_at)))).toISOString();
   state={account,conversation_id,container,rows:rows.map(r=>({...r})),server:server_time_utc,watermark,baselineWatermark:watermark,epoch:uuid(),seq:0,tick:monotonic(),armed:monotonic(),candidates:new Map()};lastReason=null;return true;
  }
  function tick({visible=true,online=true}={}){if(!state)return false;const n=monotonic();if(!visible||!online||n<state.tick||n-state.tick>maxGapMs){reset(!visible?'hidden':!online?'offline':'observation_gap');return false;}state.tick=n;return true;}
  function notice({account,conversation_id,container,rows,visible=true,online=true}){
   if(!tick({visible,online}))return false;
   if(account!==state.account||conversation_id!==state.conversation_id||container!==state.container||!validRows(rows)){reset('identity_or_dom_gap');return false;}
   if(rows.length<state.rows.length||state.rows.some((r,i)=>rows[i].node!==r.node||signature(rows[i])!==signature(r))){reset('prepend_replace_or_changed_prefix');return false;}
   const extra=rows.slice(state.rows.length);if(!extra.length)return true;
   // Each addition must be an untouched DOM suffix with strictly increasing platform timestamps.
   let watermark=Date.parse(state.watermark);const decisions=[];
   for(const r of extra){const stamp=Date.parse(r.sent_at);if(stamp<=watermark){reset('non_increasing_platform_time');return false;}watermark=stamp;
    const observed=new Date(Date.parse(state.server)+Math.max(0,Math.floor(monotonic()-state.armed))).toISOString();
    const candidate={account,conversation_id,message_id:r.id,role:r.role,sent_at:r.sent_at,observed_at:observed,history:false,probe_version:VERSION,
      live_provenance:{schema:SCHEMA,mode:MODE,epoch_uuid:state.epoch,baseline_server_time_utc:state.server,baseline_watermark_sent_at:state.baselineWatermark,observed_seq:++state.seq}};
    if(r.role==='buyer'&&stamp>Date.parse(state.server)){try{proof(candidate);decisions.push([r.id,candidate]);}catch(_){/* timestamps outside the receiver age contract archive only */}}
   }
   for(const [id,c] of decisions)state.candidates.set(id,c);
   state.rows=rows.map(r=>({...r}));state.watermark=new Date(watermark).toISOString();return true;
  }
  function admit(raw){if(!state)return null;const c=state.candidates.get(raw.message_id);if(!c||raw.account!==c.account||raw.conversation_id!==c.conversation_id||raw.role!=='buyer'||raw.sent_at!==c.sent_at)return null;return JSON.parse(JSON.stringify(c));}
  return Object.freeze({arm,reset,tick,notice,admit,status:()=>({armed:!!state,last_reason:lastReason,epoch_uuid:state&&state.epoch||null,candidates:state?state.candidates.size:0})});
 }
 function createPoll({uuid}){
  // Intentionally memory-only. A new page/boot must baseline every conversation again.
  const baselines=new Map(),sig=r=>JSON.stringify([r.id,r.sent_at,r.role,r.fingerprint]);
  function eligible(input){return input&&input.account==='Example'&&/^tm:[A-Za-z0-9_-]{1,128}$/.test(input.conversation_id||'')&&iso(input.server_time_utc)&&Number.isSafeInteger(input.query_generation)&&Array.isArray(input.rows)&&input.rows.length>0&&input.rows.every(r=>/^\d{1,30}(?:\.PNM)?$/.test(r.id||'')&&iso(r.sent_at))&&new Set(input.rows.map(r=>r.id)).size===input.rows.length;}
  function baseline(input){if(!eligible(input)){baselines.delete(input&&input.conversation_id);return false;}const anchor=input.rows.reduce((a,b)=>Date.parse(b.sent_at)>Date.parse(a.sent_at)?b:a);
   baselines.set(input.conversation_id,{epoch:uuid(),server:input.server_time_utc,watermark:anchor.sent_at,anchor:sig(anchor),anchorId:anchor.id,generation:input.query_generation,seq:0,lastSent:Date.parse(anchor.sent_at)});return true;
  }
  function prepare(input){const candidates=new Map();if(!eligible(input))return candidates;const b=baselines.get(input.conversation_id);if(!b||input.query_generation<=b.generation||Date.parse(input.server_time_utc)<Date.parse(b.server)||Date.parse(input.server_time_utc)-Date.parse(b.server)>86400000)return candidates;
   if(!input.rows.some(r=>r.id===b.anchorId&&sig(r)===b.anchor))return candidates;
   let prior=b.lastSent;
   for(const r of input.rows){const stamp=Date.parse(r.sent_at);if(stamp<=Math.max(Date.parse(b.server),Date.parse(b.watermark)))continue;if(stamp<=prior)continue;prior=stamp;
    const event={account:'Example',conversation_id:input.conversation_id,message_id:r.id,role:r.role,sent_at:r.sent_at,observed_at:input.server_time_utc,history:false,probe_version:VERSION,live_provenance:{schema:SCHEMA,mode:'native_poll_after_baseline',epoch_uuid:b.epoch,baseline_server_time_utc:b.server,baseline_watermark_sent_at:b.watermark,observed_seq:++b.seq}};
    if(r.role==='buyer'){try{proof(event);candidates.set(r.id,event);}catch(_){/* old/future/ambiguous rows remain archives */}}
   }
   b.lastSent=prior;return candidates;
  }
  return Object.freeze({baseline,prepare,reset:()=>baselines.clear(),size:()=>baselines.size});
 }
 return Object.freeze({VERSION,SCHEMA,MODE,proof,create,createPoll});
});
