'use strict';
// Pure Node synthetic DOM; does not launch Chromium or make requests.
const assert=require('node:assert/strict');
const dom=require('./pwa-dom.js');
const core=require('./pwa-core.js');

function node(overrides={}) {
 const attrs={...(overrides.attrs||{})}, query={...(overrides.query||{})}, single={...(overrides.single||{})};
 return {isConnected:true,getClientRects:()=>[1],getAttribute:k=>attrs[k]??null,
  querySelectorAll:k=>query[k]||[],querySelector:k=>single[k]||null,
  closest:()=>null,matches:()=>false,...overrides};
}
function fixture(metaOverride) {
 const data={messageId:'1001',sendTime:'1700000000000',senderId:'101',receiverId:'201',...metaOverride};
 const rich=node({innerText:'Synthetic inquiry',textContent:'Synthetic inquiry'});
 const name=node({textContent:'Synthetic Buyer'});
 const line=node({attrs:{'data-expinfo':JSON.stringify(data)},query:{'.content-with-translation.text-content .session-rich-content.text':[rich]},single:{'.item-content.item-content-left':node(),'.item-base-info .name':name},matches:s=>s==='.item-left:not(.item-right)'});
 const container=node();line.parentElement=container;
 const card=node({query:{'.alicrm-customer-detail-card-header-name-container .name-text':[name]}});
 const header=node({textContent:'Synthetic Buyer',attrs:{title:'Synthetic Buyer'}});
 const selected=node({attrs:{'data-ali-id':'101'}});
 const menu=node({query:{'.icon-tip p':[node({textContent:'登录账号: Example Operator'})]}});
 const avatar=node({closest:()=>menu});
 const area=node({query:{'.message-item-wrapper':[line]}});
 const document=node({query:{'.im-seller-nav-menu-container .session-avatar':[avatar],'.contact-item-container.selected':[selected],'.messenger-content-container .content-header .contact-name':[header],'.contact-base-info .alicrm-customer-detail-card':[card],'.messenger-content-container':[area]}});
 return {document,line,selected};
}
const location={origin:'https://messenger.fixture.invalid',pathname:'/message/weblitePWA.htm'};
let count=0;
function check(name,fn){fn();count++;console.log('PASS '+name);}
check('identity_exact_participants',()=>{const x=fixture();const v=dom.identity(x.document,location);assert.equal(v.account,'Example');assert.equal(v.conversation_id,'tm:ali_101');assert.equal(v.buyer_name,'Synthetic Buyer');});
check('missing_message_id_blocks',()=>{const x=fixture({messageId:undefined});assert.throws(()=>dom.identity(x.document,location),e=>e.code==='pwa_message_metadata_invalid');});
check('nonnumeric_message_id_blocks',()=>{const x=fixture({messageId:'bad-id'});assert.throws(()=>dom.identity(x.document,location),e=>e.code==='pwa_message_metadata_invalid');});
check('short_timestamp_blocks',()=>{const x=fixture({sendTime:'1700000000'});assert.throws(()=>dom.identity(x.document,location),e=>e.code==='pwa_message_metadata_invalid');});
check('invalid_json_blocks',()=>{const x=fixture();x.line.getAttribute=()=>'{';assert.throws(()=>dom.identity(x.document,location),e=>e.code==='pwa_message_metadata_invalid');});
check('participant_mismatch_blocks',()=>{const x=fixture({senderId:'999'});assert.throws(()=>dom.identity(x.document,location),e=>e.code==='pwa_buyer_anchor_unverified');});
check('url_query_never_copied',()=>{const data=core.normalize({account:'Example',conversation_id:'tm:ali_101',buyer_name:'Synthetic Buyer',content:'Synthetic',history:true,role:'buyer',message_id:'1001',url:'https://messenger.fixture.invalid/message/weblitePWA.htm?secret=fake'},()=> 'synthetic-id',()=>1700000000000);assert.equal(data.url,'https://messenger.fixture.invalid/message/weblitePWA.htm');});
console.log(JSON.stringify({status:'PASS',checks:count,scope:'Node synthetic DOM only; no browser or network'}));
