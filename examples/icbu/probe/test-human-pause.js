/* REFERENCE ONLY: anonymized example; never install into a real browser. */
'use strict';
const assert=require('node:assert/strict'),crypto=require('node:crypto'),vm=require('node:vm'),fs=require('node:fs'),path=require('node:path');
const Client=require('./icbu-operations-client.js');
const hash=async s=>crypto.createHash('sha256').update(s).digest('hex');
const names=[];
async function test(name,fn){await fn();names.push(name);}
function setup(hooks={}) {
 let time=100,version=0,active=false,input='',polls=0,clicks=0;
 const map=new Map(),acks=[],sent=[],state=[];
 const id={account:'Example',conversation_id:'tm:ali_100000000001',buyer_name:'Synthetic Buyer'};
 const activity=()=>{version++;active=true;};
 const cmd={id:'11111111-2222-4333-8444-555555555555',...id,action:'send',text:'Synthetic reply',text_sha256:crypto.createHash('sha256').update('Synthetic reply').digest('hex'),lease_token:'a'.repeat(64),execute_before:220};
 const human={active:()=>active,version:()=>version};
 const store={list:async()=>[...map.keys()],get:async k=>map.get(k)||null,set:async(k,r)=>{map.set(k,structuredClone(r));if(hooks.onSave)await hooks.onSave(r,{activity,setInput:s=>input=s});}};
 const adapter={identity:()=>id,readInput:()=>input,readMessages:()=>sent,
  fill:async t=>{input=t;if(hooks.onFill)await hooks.onFill({activity,setInput:s=>input=s});},
  clickSend:async()=>{clicks++;if(hooks.onClick)await hooks.onClick({activity});if(hooks.deliver)sent.push({id:'synthetic-123',role:'seller',content:cmd.text});}};
 const transport=async(kind,body)=>{if(kind==='ack'){acks.push(body);return hooks.onAck?hooks.onAck(body):{ok:true,state:body.state};}
  polls++;if(hooks.onPoll)await hooks.onPoll({activity,release:()=>active=false});return {ok:true,command:polls===1?cmd:null};};
 const c=Client.create({adapter,transport,store,clientId:'synthetic-client-12345',hash,human,now:()=>time,wait:async ms=>time+=ms/1000,onStatus:x=>state.push(x.state)});
 return {c,map,acks,state,activity,release:()=>active=false,get polls(){return polls},get clicks(){return clicks},get input(){return input}};
}
async function main(){
 await test('active_operator_no_claim_then_resumes',async()=>{const f=setup({deliver:true});f.activity();await f.c.tick();assert.equal(f.polls,0);assert.equal(f.clicks,0);assert.equal(f.c.status().state,'user_active');f.release();await f.c.tick();assert.equal(f.polls,1);assert.equal(f.clicks,1);assert.equal(f.acks[0].state,'sent');});
 await test('activity_during_poll_blocks_even_if_quiet_after',async()=>{const f=setup({onPoll:async x=>{x.activity();x.release();}});await f.c.tick();assert.equal(f.clicks,0);assert.equal(f.input,'');assert.equal(f.acks[0].state,'blocked');assert.equal(f.acks[0].detail,'operations_user_active');});
 await test('activity_during_first_persist_never_fills',async()=>{const f=setup({onSave:async(r,x)=>{if(r.phase==='received')x.activity();}});await f.c.tick();assert.equal(f.clicks,0);assert.equal(f.input,'');assert.equal(f.acks[0].state,'blocked');});
 await test('activity_during_fill_preserves_user_text_and_blocks_send',async()=>{const f=setup({onFill:async x=>{x.activity();x.setInput('Human draft');}});await f.c.tick();assert.equal(f.clicks,0);assert.equal(f.input,'Human draft');assert.equal(f.acks[0].state,'blocked');assert.equal(f.acks[0].detail,'operations_user_active');});
 await test('activity_during_before_click_persist_prevents_click',async()=>{const f=setup({onSave:async(r,x)=>{if(r.phase==='before_click')x.activity();}});await f.c.tick();assert.equal(f.clicks,0);assert.equal(f.input,'Synthetic reply');assert.equal(f.acks[0].state,'blocked');});
 await test('activity_after_click_timeout_is_unknown_and_never_reissued',async()=>{const f=setup({onClick:async x=>x.activity()});await f.c.tick();assert.equal(f.clicks,1);assert.equal(f.acks[0].state,'unknown');f.release();await f.c.tick();assert.equal(f.clicks,1);assert.equal(f.acks.length,1);});
 await test('activity_after_click_with_receipt_remains_sent',async()=>{const f=setup({onClick:async x=>x.activity(),deliver:true});await f.c.tick();assert.equal(f.clicks,1);assert.equal(f.acks[0].state,'sent');assert.equal(f.acks[0].platform_message_id,'synthetic-123');});
 await test('pending_ack_flushed_even_while_operator_active',async()=>{const f=setup();f.map.set(Client.PREFIX+'old',{command:{id:'old'},clientId:'synthetic-client-12345',result:{state:'unknown'},acked:false});f.activity();await f.c.tick();assert.equal(f.acks.length,1);assert.equal(f.acks[0].state,'unknown');assert.equal(f.polls,0);});
 await test('late_blocked_unknown_fence_is_acknowledged_without_replay',async()=>{const f=setup({onPoll:async x=>x.activity(),onAck:()=>({ok:true,state:'unknown',reason:'unknown_fenced'})});await f.c.tick();assert.equal(f.clicks,0);assert.equal(f.c.status().state,'unknown');const record=[...f.map.values()][0];assert.equal(record.acked,true);assert.equal(record.result.state,'blocked');assert.equal(record.server_final_state,'unknown');f.release();await f.c.tick();assert.equal(f.acks.length,1);assert.equal(f.clicks,0);});
 await test('unexpected_unknown_response_never_acknowledges',async()=>{const f=setup({onPoll:async x=>x.activity(),onAck:()=>({ok:true,state:'unknown'})});await f.c.tick();assert.equal(f.clicks,0);assert.equal(f.c.status().state,'ack_pending');assert.equal([...f.map.values()][0].acked,false);});
 await test('entry_trusted_events_only_and_five_second_cooldown',async()=>{
  let clock=100,options;const listeners={},menus=new Map();const win={};win.top=win;win.self=win;win.addEventListener=()=>{};
  const ctx={location:{origin:'https://messenger.fixture.invalid',pathname:'/message/weblitePWA.htm'},window:win,document:{visibilityState:'visible',addEventListener:(type,cb)=>{listeners[type]=cb;}},performance:{now:()=>clock},navigator:{onLine:true,locks:{request:async(_,__,fn)=>fn({})}},crypto:{randomUUID:()=> '00000000-0000-4000-8000-000000000001'},TextEncoder,Date,Promise,Uint8Array,console:{info(){},error(){}},setInterval:()=>1,clearInterval(){},setTimeout,clearTimeout,alert(){},GM_listValues:()=>[],GM_getValue:(_,d)=>d,GM_setValue(){},GM_deleteValue(){},GM_registerMenuCommand:(k,f)=>menus.set(k,f),GM_xmlhttpRequest(){throw Error('no network')},ICBUPWADOM:{create:()=>({identity:()=>({account:'Example',conversation_id:'tm:ali_1',buyer_name:'Synthetic'})})},ICBUPWACore:{createGMTransport(){}},ICBUPWAFreshGate:{},ICBULiveCapture:{createHeartbeatTransport(){}},ICBUPWACapture:{create:()=>({start:async()=>{},stop(){},tick(){},status(){return{}},queueStatus:async()=>({})})},ICBUOperationsClient:{create:o=>{options=o;return{stop(){},tick(){},status(){return{}}};}}};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'pwa-entry.js'),'utf8'),ctx);menus.get('ICBU实时聊天：启用监听与221指定任务')();await new Promise(setImmediate);assert(options);
  const human=options.human;assert.equal(human.active(),false);listeners.pointermove({isTrusted:false});assert.equal(human.version(),0);assert.equal(human.active(),false);
  listeners.pointermove({isTrusted:true});assert.equal(human.version(),1);assert.equal(human.active(),true);clock+=4999;assert.equal(human.active(),true);clock++;assert.equal(human.active(),false);
  listeners.keydown({isTrusted:true});assert.equal(human.version(),2);assert.equal(human.active(),true);
 });
 const out={status:'PASS',checks:names.length,names,scope:'offline synthetic tests; no user browser, network, real token or sends'};
 fs.writeFileSync(path.join(__dirname,'test-human-pause-result.json'),JSON.stringify(out,null,2)+'\n');console.log(JSON.stringify(out));
}
main().catch(e=>{console.error(e);process.exitCode=1;});
