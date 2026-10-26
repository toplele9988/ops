/* REFERENCE ONLY: anonymized example; never install into a real browser. */
/* Example PWA DOM adapter. Selectors verified through existing signed-in UI on 2026-10-25.
 * No cookies, URL queries, platform fetch, navigation or platform internal globals. */
(function(root,factory){if(typeof module==='object'&&module.exports)module.exports=factory();else root.ICBUPWADOM=factory();})(typeof globalThis!=='undefined'?globalThis:this,function(){
 'use strict';
 const URL_CANONICAL='https://messenger.fixture.invalid/message/weblitePWA.htm';
 const ORIGINAL='.content-with-translation.text-content .session-rich-content.text';
 const tidy=v=>String(v||'').normalize('NFKC').replace(/\s+/g,' ').trim();
 const fail=code=>Object.assign(new Error(code),{code});
 const visible=e=>!!e&&e.isConnected&&e.getClientRects().length>0;
 function one(root,sel,code){const a=[...root.querySelectorAll(sel)].filter(visible);if(a.length!==1)throw fail(code);return a[0];}
 function route(location){if(location.origin!=='https://messenger.fixture.invalid'||location.pathname!=='/message/weblitePWA.htm')throw fail('pwa_wrong_route');}
 function account(document){
  const avatar=one(document,'.im-seller-nav-menu-container .session-avatar','pwa_account_avatar_not_unique');
  const menu=avatar.closest('.menu-icon');const labels=menu?[...menu.querySelectorAll('.icon-tip p')]:[];
  if(labels.length!==1||!/^登录账号\s*[:：]?\s*Example Operator$/.test(tidy(labels[0].textContent)))throw fail('pwa_account_not_example');
  return 'Example';
 }
 function identity(document,location){
  const s=inspect(document,location);
  return {account:s.account,conversation_id:s.conversation_id,buyer_name:s.buyer_name,url:URL_CANONICAL,country:''};
 }
 function inspect(document,location){
  route(location);const bound=account(document);
  const selected=one(document,'.contact-item-container.selected','pwa_selected_contact_not_unique');
  const buyerId=selected.getAttribute('data-ali-id');
  if(typeof buyerId!=='string'||!/^\d{1,30}$/.test(buyerId))throw fail('pwa_selected_id_invalid');
  const header=one(document,'.messenger-content-container .content-header .contact-name','pwa_contact_header_not_unique');
  const name=tidy(header.textContent),title=tidy(header.getAttribute('title'));
  if(!name||name.length>200||(title&&title!==name))throw fail('pwa_contact_header_invalid');
  // CRM is a display-name consistency check only. Never derive identity or company/country from it.
  const card=one(document,'.contact-base-info .alicrm-customer-detail-card','pwa_customer_card_not_unique');
  const customer=one(card,'.alicrm-customer-detail-card-header-name-container .name-text','pwa_customer_name_not_unique');
  if(tidy(customer.textContent)!==name)throw fail('pwa_customer_header_mismatch');
  const area=one(document,'.messenger-content-container','pwa_content_not_unique');
  const nodes=[...area.querySelectorAll('.message-item-wrapper')].filter(e=>!e.parentElement.closest('.message-item-wrapper')&&!e.closest('blockquote'));
  if(!nodes.length)throw fail('pwa_no_message_baseline');
  const container=nodes[0].parentElement;if(nodes.some(e=>e.parentElement!==container))throw fail('pwa_message_containers_ambiguous');
  const rows=nodes.map(node=>{
   const m=metadata(node),r=role(node,m);if(r==='unknown')throw fail('pwa_message_role_unverified');
   if(r!=='system'&&(typeof m.senderId!=='string'||!/^\d{1,30}$/.test(m.senderId)||typeof m.receiverId!=='string'||(m.receiverId!==''&&!/^\d{1,30}$/.test(m.receiverId))))throw fail('pwa_message_participant_invalid');
   const text=r==='system'?null:content(node);
   if(r==='buyer'&&tidy(node.querySelector('.item-base-info .name')?.textContent)!==name)throw fail('pwa_buyer_message_name_mismatch');
   return {...m,node,role:r,content:text,fingerprint:JSON.stringify([r,text,m.senderId,m.receiverId])};
  });
  if(new Set(rows.map(r=>r.id)).size!==rows.length)throw fail('pwa_message_ids_duplicate');
  const buyers=rows.filter(r=>r.role==='buyer');
  if(!buyers.length||buyers.some(r=>r.senderId!==buyerId||!/^\d{1,30}$/.test(r.receiverId)))throw fail('pwa_buyer_anchor_unverified');
  const sellers=new Set(buyers.map(r=>r.receiverId));
  if(sellers.size!==1||sellers.has(buyerId))throw fail('pwa_seller_anchor_unverified');
  const sellerId=[...sellers][0];
  if(rows.some(r=>r.role==='seller'&&(r.senderId!==sellerId||(r.receiverId!==''&&r.receiverId!==buyerId))))throw fail('pwa_seller_participant_mismatch');
  return {account:bound,conversation_id:'tm:ali_'+buyerId,buyer_name:name,url:URL_CANONICAL,country:'',container,rows};
 }
 function metadata(node){
  const raw=node.getAttribute('data-expinfo');if(!raw||raw.length>16384)throw fail('pwa_message_metadata_missing');
  let data;try{data=JSON.parse(raw);}catch(_){throw fail('pwa_message_metadata_invalid');}
  if(!data||typeof data!=='object'||Array.isArray(data)||typeof data.messageId!=='string'||!/^\d{1,30}(?:\.PNM)?$/.test(data.messageId)||!/^\d{13}$/.test(String(data.sendTime)))throw fail('pwa_message_metadata_invalid');
  const stamp=Number(data.sendTime);if(!Number.isSafeInteger(stamp)||stamp<946684800000||stamp>=4102444800000)throw fail('pwa_message_time_invalid');
  return {id:data.messageId,sent_at:new Date(stamp).toISOString(),senderId:data.senderId,receiverId:data.receiverId,messageType:data.messageType};
 }
 function role(node,m=metadata(node)){
  if(node.matches('.item-right:not(.item-left)')&&node.querySelector('.item-content.item-content-right'))return 'seller';
  if(node.matches('.item-left:not(.item-right)')&&node.querySelector('.item-content.item-content-left'))return 'buyer';
  const classes=[...node.classList].sort().join(' ');
  const wrap=node.children.length===1?node.firstElementChild:null;
  const notice=wrap&&wrap.children.length===1?wrap.firstElementChild:null;
  if(m.messageType===9999&&classes==='message-item-wrapper messenger'&&wrap?.tagName==='DIV'&&wrap.className==='item-content-wrap'&&notice?.tagName==='DIV'&&notice.className==='item-system-notice level-1'&&!node.querySelector('.session-rich-content,.content-with-translation,.item-content,.item-base-info')&&!node.querySelector('.item-left,.item-right,.item-content-left,.item-content-right'))return 'system';
  return 'unknown';
 }
 function content(node){
  const a=[...node.querySelectorAll(ORIGINAL)].filter(e=>!e.closest('.session-translate')&&!e.closest('blockquote'));
  if(a.length!==1)return null;const e=a[0];
  if(e.querySelector('blockquote,iframe,table,img,video,audio,canvas,object,embed,.session-translate'))return null;
  const s=(e.innerText||e.textContent||'').trim();return s&&s.length<=16000?s:null;
 }
 function snapshot(document,location){return inspect(document,location);}
 function input(document){return one(document,'.messenger-content-container .send-box-container textarea.send-textarea','pwa_editor_not_unique');}
 function available(document){
  const area=one(document,'.messenger-content-container .send-box-container','pwa_send_box_not_unique');
  if([...area.querySelectorAll('.disable-tip')].some(e=>visible(e)&&!e.classList.contains('hide')))throw fail('pwa_send_disabled_tip');
  const box=input(document);if(box.disabled||box.readOnly||box.getAttribute('aria-disabled')==='true')throw fail('pwa_editor_disabled');return box;
 }
 function create(document,location){
  return Object.freeze({
   identity:()=>identity(document,location),snapshot:()=>snapshot(document,location),
   readMessages:()=>snapshot(document,location).rows.map(({id,role,content,sent_at})=>({id,role,content,sent_at})),
   readInput:()=>{identity(document,location);return available(document).value;},
   fill(text){
    identity(document,location);const box=available(document);if(box.value===text)return;if(box.value)throw fail('pwa_existing_user_draft');
    if(typeof text!=='string'||!text.trim()||text.length>16000)throw fail('pwa_invalid_fill_text');
    const view=document.defaultView;const setter=Object.getOwnPropertyDescriptor(view.HTMLTextAreaElement.prototype,'value').set;
    setter.call(box,text);box.dispatchEvent(new view.Event('input',{bubbles:true}));box.dispatchEvent(new view.Event('change',{bubbles:true}));
    if(box.value!==text)throw fail('pwa_fill_not_reflected');
   },
   clickSend(){
    identity(document,location);const box=available(document);if(!box.value.trim())throw fail('pwa_empty_editor');
    const button=one(document,'.messenger-content-container .send-box-container .send-btn-wrapper button.send-tool-button','pwa_send_button_not_unique');
    if(button.disabled||button.getAttribute('aria-disabled')==='true'||button.classList.contains('gray')||tidy(button.textContent)!=='发送')throw fail('pwa_send_button_disabled');
    button.click();
   }
  });
 }
 return Object.freeze({URL_CANONICAL,identity,snapshot,create,metadata,role,content});
});
