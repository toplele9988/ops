/* REFERENCE ONLY: anonymized example; never install into a real browser. */
'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),{chromium}=require('playwright');
(async()=>{
 const browser=await chromium.launch({headless:true,...(process.env.CHROMIUM_BIN?{executablePath:process.env.CHROMIUM_BIN}:{})});
 let forbidden=0;
 try{
  const page=await browser.newPage({viewport:{width:400,height:300}});
  const target='https://messenger.fixture.invalid/message/weblitePWA.htm';
  await page.route('**/*',route=>route.request().url()===target?route.fulfill({contentType:'text/html',body:'<body><input id="field" /></body>'}):(forbidden++,route.abort()));
  await page.goto(target);
  await page.evaluate(()=>{
   window.__time=100;Object.defineProperty(performance,'now',{value:()=>window.__time});
   window.__menus=new Map();window.GM_listValues=()=>[];window.GM_getValue=(_,d)=>d;window.GM_setValue=()=>{};window.GM_deleteValue=()=>{};
   window.GM_registerMenuCommand=(k,v)=>window.__menus.set(k,v);window.GM_xmlhttpRequest=()=>{throw Error('No network')};
   window.ICBUPWADOM={create:()=>({identity:()=>({account:'Example',conversation_id:'tm:ali_100000000001',buyer_name:'Synthetic'})})};
   window.ICBUPWACore={createGMTransport(){}};window.ICBUPWAFreshGate={};window.ICBULiveCapture={createHeartbeatTransport(){}};
   window.ICBUPWACapture={create:()=>({start:async()=>{},stop(){},tick(){},status(){return{}},queueStatus:async()=>({})})};
   window.ICBUOperationsClient={create:o=>{window.__options=o;return{stop(){},tick(){},status(){return{}}};}};
  });
  await page.addScriptTag({content:fs.readFileSync(path.join(__dirname,'pwa-entry.js'),'utf8')});
  await page.evaluate(()=>window.__menus.get('ICBU实时聊天：启用监听与221指定任务')());
  await page.waitForFunction(()=>!!window.__options);
  let status=await page.evaluate(()=>({active:window.__options.human.active(),version:window.__options.human.version()}));
  assert.deepEqual(status,{active:false,version:0});
  await page.evaluate(()=>document.dispatchEvent(new Event('pointermove',{bubbles:true})));
  status=await page.evaluate(()=>({active:window.__options.human.active(),version:window.__options.human.version()}));
  assert.deepEqual(status,{active:false,version:0});
  await page.mouse.move(120,120);
  status=await page.evaluate(()=>({active:window.__options.human.active(),version:window.__options.human.version()}));
  assert.equal(status.active,true);assert.ok(status.version>=1);const afterPointer=status.version;
  await page.evaluate(()=>{window.__time+=5000});
  assert.equal(await page.evaluate(()=>window.__options.human.active()),false);
  await page.keyboard.press('A');
  status=await page.evaluate(()=>({active:window.__options.human.active(),version:window.__options.human.version()}));
  assert.equal(status.active,true);assert.ok(status.version>afterPointer);assert.equal(forbidden,0);
  const out={status:'PASS',checks:5,names:['synthetic_event_ignored','trusted_mouse_observed','five_second_cooldown','trusted_keyboard_observed','external_network_requests_zero'],scope:'isolated synthetic browser, intercepted fixture, no user session/customer messages'};
  fs.writeFileSync(path.join(__dirname,'test-human-browser-result.json'),JSON.stringify(out,null,2)+'\n');console.log(JSON.stringify(out));
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
