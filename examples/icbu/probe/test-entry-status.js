/* REFERENCE ONLY: anonymized example; never install into a real browser. */
'use strict';
const vm=require('node:vm'),fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
(async()=>{
 const menus=new Map(),gm=new Map([['icbu.example.v1.probe_token','synthetic']]),alarms=[],requests=[];let options,response={status:200,responseText:'{"ok":true,"command":null}'};
 const window={};window.top=window;window.self=window;window.addEventListener=()=>{};
 const adapter={identity:()=>({account:'Example',conversation_id:'tm:ali_100000000001',buyer_name:'Synthetic'})};
 const ctx={location:{origin:'https://messenger.fixture.invalid',pathname:'/message/weblitePWA.htm'},window,document:{visibilityState:'visible',addEventListener:()=>{}},navigator:{onLine:true,locks:{request:async(_,__,fn)=>fn({})}},crypto:{randomUUID:()=> '00000000-0000-4000-8000-000000000001'},performance:{now:()=>10000},TextEncoder,Date,Promise,Uint8Array,console:{info:()=>{},error:()=>{}},setInterval:()=>1,clearInterval:()=>{},setTimeout,clearTimeout,alert:x=>alarms.push(JSON.parse(x)),GM_listValues:()=>[...gm.keys()],GM_getValue:(k,d)=>gm.has(k)?gm.get(k):d,GM_setValue:(k,v)=>gm.set(k,v),GM_deleteValue:k=>gm.delete(k),GM_registerMenuCommand:(k,f)=>menus.set(k,f),GM_xmlhttpRequest:o=>{requests.push(o);queueMicrotask(()=>o.onload({...response,finalUrl:o.url}));return {abort(){}};},ICBUPWADOM:{create:()=>adapter,URL_CANONICAL:'https://messenger.fixture.invalid/message/weblitePWA.htm'},ICBUPWACore:{createGMTransport:()=>{}},ICBUPWAFreshGate:{},ICBULiveCapture:{createHeartbeatTransport:()=>{}},ICBUPWACapture:{create:()=>({start:async()=>{},stop(){},tick:async()=>{},status:()=>({baseline:true}),queueStatus:async()=>({pending:0,lastAck:null})})},ICBUOperationsClient:{create:o=>{options=o;return{stop(){},tick:async()=>{},status:()=>({state:'idle'})};}}};
 vm.runInNewContext(fs.readFileSync(path.join(__dirname,'pwa-entry.js'),'utf8'),ctx);
 menus.get('ICBU实时聊天：启用监听与221指定任务')();await new Promise(setImmediate);assert(options);
 await options.transport('poll',{account:'Example'});menus.get('ICBU实时聊天：查看状态（不显示正文或令牌）')();await new Promise(setImmediate);
 assert.equal(alarms.at(-1).last_poll.http_status,200);assert.equal(alarms.at(-1).last_poll.empty_command,true);assert.equal(alarms.at(-1).last_poll.ok,true);assert(!JSON.stringify(alarms).includes('synthetic'));assert(requests.every(x=>x.url==='http://gateway.invalid:8791/api/operations/alibaba/poll'));
 response={status:200,responseText:'null'};assert.equal(await options.transport('poll',{}),null);menus.get('ICBU实时聊天：查看状态（不显示正文或令牌）')();await new Promise(setImmediate);assert.equal(alarms.at(-1).last_poll.ok,false);assert.equal(alarms.at(-1).last_poll.empty_command,false);
 response={status:401,responseText:'{}'};await assert.rejects(options.transport('poll',{}),/pwa_http_401/);
 console.log(JSON.stringify({status:'PASS',checks:4,scope:'synthetic Node VM only; no UI/network/customer token',names:['poll200_empty_receipt','receipt_excludes_token','null_json_is_not_success','http401_rejected']}));
})().catch(e=>{console.error(e);process.exitCode=1;});
