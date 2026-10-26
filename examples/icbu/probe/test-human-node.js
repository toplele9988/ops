'use strict';
// Simulates events in a Node VM: does not attest real browser isTrusted semantics.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
(async()=>{
 let time=0,opts;const listeners={},menus=new Map(),win={};win.top=win;win.self=win;win.addEventListener=()=>{};
 const ctx={location:{origin:'https://messenger.fixture.invalid',pathname:'/message/weblitePWA.htm'},window:win,document:{visibilityState:'visible',addEventListener:(k,f)=>{listeners[k]=f;}},performance:{now:()=>time},navigator:{onLine:true,locks:{request:async(_,__,f)=>f({})}},crypto:{randomUUID:()=> '00000000-0000-4000-8000-000000000001'},TextEncoder,Date,Promise,Uint8Array,console:{info(){},error(){}},setInterval:()=>1,clearInterval(){},setTimeout,clearTimeout,alert(){},GM_listValues:()=>[],GM_getValue:(_,d)=>d,GM_setValue(){},GM_deleteValue(){},GM_registerMenuCommand:(k,f)=>menus.set(k,f),GM_xmlhttpRequest(){throw Error('network disabled')},ICBUPWADOM:{create:()=>({identity:()=>({account:'Example',conversation_id:'tm:ali_101',buyer_name:'Synthetic'})})},ICBUPWACore:{createGMTransport(){}},ICBUPWAFreshGate:{},ICBULiveCapture:{createHeartbeatTransport(){}},ICBUPWACapture:{create:()=>({start:async()=>{},stop(){},tick(){},status(){return{}},queueStatus:async()=>({})})},ICBUOperationsClient:{create:o=>{opts=o;return{stop(){},tick(){},status(){return{}}};}}};
 vm.runInNewContext(fs.readFileSync(path.join(__dirname,'pwa-entry.js'),'utf8'),ctx);
 menus.get('ICBU实时聊天：启用监听与221指定任务')();await new Promise(setImmediate);
 assert(opts);const human=opts.human;
 assert.equal(human.active(),false);listeners.pointermove({isTrusted:false});assert.equal(human.version(),0);
 listeners.pointermove({isTrusted:true});assert.equal(human.version(),1);assert.equal(human.active(),true);
 time=4999;assert.equal(human.active(),true);time=5000;assert.equal(human.active(),false);
 listeners.keydown({isTrusted:true});assert.equal(human.active(),true);assert.equal(human.version(),2);
 console.log(JSON.stringify({status:'PASS',checks:5,scope:'Node simulated trusted flags only; no browser or network'}));
})().catch(e=>{console.error(e);process.exitCode=1;});
