/* REFERENCE ONLY: anonymized example; never install into a real browser. */
/* Example operations executor. Adapter touches only the currently visible conversation.
 * No navigation, credentials, selectors or platform network requests in this module.
 * A persisted before-click marker and server claim prevent replay after uncertainty.
 */
(function(root,factory){if(typeof module==='object'&&module.exports)module.exports=factory();else root.ICBUOperationsClient=factory();})(typeof globalThis!=='undefined'?globalThis:this,function(){
 'use strict';
 const PREFIX='icbu.example.operations.v1.command.';
 const receiptText=x=>String(x||'').replace(/\s+/g,' ').trim();
 const norm=x=>String(x||'').trim().replace(/\s+/g,' ').toLocaleLowerCase();
 const error=code=>Object.assign(new Error(code),{code});
 const ensure=(v,c)=>{if(!v)throw error(c);};
 async function sha(text){const bytes=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(text));return [...new Uint8Array(bytes)].map(b=>b.toString(16).padStart(2,'0')).join('');}
 function create({adapter,transport,store,clientId,hash=sha,now=()=>Date.now()/1000,wait=ms=>new Promise(r=>setTimeout(r,ms)),onStatus=()=>{},human={active:()=>false,version:()=>0}}){
  ensure(/^[A-Za-z0-9_-]{12,100}$/.test(clientId),'operations_client_id_invalid');
  let busy=false,stopped=false,lastState='idle';
  const update=state=>{lastState=state;onStatus({state});};
  const quiet=version=>ensure(!human.active()&&human.version()===version,'operations_user_active');
  function identity(){
   const x=adapter.identity();ensure(x&&x.account==='Example'&&/^tm:[A-Za-z0-9_-]{1,128}$/.test(x.conversation_id)&&typeof x.buyer_name==='string'&&x.buyer_name.trim(),'operations_identity_unverified');
   return {account:'Example',conversation_id:x.conversation_id,buyer_name:x.buyer_name.trim()};
  }
  function same(a,b){ensure(a.account===b.account&&a.conversation_id===b.conversation_id&&norm(a.buyer_name)===norm(b.buyer_name),'operations_conversation_changed');}
  const key=id=>PREFIX+id;
  async function persist(record){await store.set(key(record.command.id),record);}
  async function acknowledge(record){
   ensure(record.result,'operations_no_receipt');
   try{
    const r=await transport('ack',record.result);
    const preservedUnknown=r&&r.ok===true&&r.state==='unknown'&&r.reason==='unknown_fenced'&&record.result.state==='blocked';
    ensure(r&&r.ok===true&&(r.state===record.result.state||preservedUnknown),'operations_ack_mismatch');
    // Keep the original local assertion for audit while respecting the stronger
    // server-side uncertainty fence; no new send or repeated ACK is performed.
    if(preservedUnknown)record.server_final_state='unknown';
    record.acked=true;await persist(record);update(preservedUnknown?'unknown':record.result.state);return true;
   }catch(_){update('ack_pending');return false;}
  }
  function receipt(cmd,state,platformId,detail,ownerClient=clientId){return {account:'Example',id:cmd.id,client_id:ownerClient,lease_token:cmd.lease_token,state,platform_message_id:platformId||'',detail};}
  async function execute(cmd,observed,activityVersion=human.version()){
   ensure(cmd&&/^[0-9a-f-]{36}$/.test(cmd.id)&&['fill','send'].includes(cmd.action),'operations_command_invalid');
   ensure(typeof cmd.text==='string'&&cmd.text.length>0&&cmd.text.length<=6000&&typeof cmd.lease_token==='string'&&cmd.lease_token.length>=32,'operations_command_invalid');
   ensure(typeof cmd.execute_before==='number'&&cmd.execute_before>now()&&cmd.execute_before-now()<=125,'operations_command_expired');
   ensure(await hash(cmd.text)===cmd.text_sha256,'operations_text_hash_mismatch');same(cmd,observed);same(cmd,identity());
   const existing=await store.get(key(cmd.id));
   if(existing){
    // Even a transport accidentally returning an old command may never click again.
    if(existing.result)return acknowledge(existing);
    existing.result=receipt(cmd,'unknown','','local_before_click_record_exists',existing.clientId);await persist(existing);return acknowledge(existing);
   }
   const record={command:cmd,clientId,phase:'received',created_at:now(),acked:false,result:null};
   await persist(record);
   let clicked=false;
   try{
    ensure(!stopped,'operations_stopped');quiet(activityVersion);same(cmd,identity());
    const current=adapter.readInput();ensure(typeof current==='string','operations_input_unreadable');
    ensure(current.trim()===''||current===cmd.text,'operations_existing_composer_text');
    quiet(activityVersion);await adapter.fill(cmd.text);quiet(activityVersion);same(cmd,identity());
    ensure(adapter.readInput()===cmd.text,'operations_fill_not_exact');
    if(cmd.action==='fill'){
     record.phase='finished';record.result=receipt(cmd,'filled','','exact_text_in_verified_conversation');await persist(record);return acknowledge(record);
    }
    ensure(!stopped&&now()<cmd.execute_before,'operations_command_expired');quiet(activityVersion);same(cmd,identity());
    const before=new Set(adapter.readMessages().map(x=>x.id).filter(Boolean));
    record.phase='before_click';record.before_ids=[...before];record.before_click_at=now();await persist(record);
    // Revalidate after asynchronous durable storage, immediately before the only click.
    ensure(!stopped&&now()<cmd.execute_before,'operations_command_expired');quiet(activityVersion);same(cmd,identity());ensure(adapter.readInput()===cmd.text,'operations_composer_changed');
    clicked=true;await adapter.clickSend();
    const until=now()+15;
    while(now()<until){
     same(cmd,identity());
     const found=adapter.readMessages().filter(x=>x.id&&!before.has(x.id)&&x.role==='seller'&&receiptText(x.content)===receiptText(cmd.text));
     if(found.length===1){
      record.phase='finished';record.result=receipt(cmd,'sent',found[0].id,'new_exact_seller_message_in_verified_conversation');await persist(record);return acknowledge(record);
     }
     if(found.length>1)throw error('operations_receipt_ambiguous');
     await wait(300);
    }
    throw error('operations_receipt_timeout');
   }catch(e){
    record.phase='finished';record.result=receipt(cmd,clicked?'unknown':'blocked','',/^operations_[a-z_]+$/.test(e&&e.code||'')?e.code:'operations_execution_failed');
    await persist(record);return acknowledge(record);
   }
  }
  async function recoverAcknowledgements(){
   for(const k of await store.list()){
    if(!k.startsWith(PREFIX))continue;
    const r=await store.get(k);
    if(r&&r.command&&r.result&&!r.acked)await acknowledge(r);
   }
  }
  async function tick(){
   if(busy||stopped)return;busy=true;
   try{
    await recoverAcknowledgements();if(human.active()){update('user_active');return;}const activityVersion=human.version(),observed=identity();
    const r=await transport('poll',{...observed,client_id:clientId});
    ensure(r&&r.ok===true,'operations_poll_failed');
    if(r.command)await execute(r.command,observed,activityVersion);else update('idle');
   }catch(_){update('waiting_for_verified_conversation');}
   finally{busy=false;}
  }
  return Object.freeze({tick,execute,recoverAcknowledgements,stop:()=>{stopped=true;},resume:()=>{stopped=false;},status:()=>({state:lastState,busy,stopped}),PREFIX});
 }
 return Object.freeze({create,PREFIX});
});
