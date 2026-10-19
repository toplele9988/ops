import {createBasket} from './basket.js';
import * as drawerModel from './drawer-model.js';
import * as costChart from './cpq-cost-chart.js';
import {initDrawer} from './cpq-drawer.js';
import {initActions} from './cpq-actions.js';
import '../../strobe/gallery.js';

const DEFAULT_TARGET_MARGIN=0.18;
const pending=[];
let ready=false,starting=null,basket=null,drawer=null,wantsOpen=false;
function report(error){const el=document.getElementById('cpq-verify');if(el){el.textContent=error.message||String(error);el.classList.remove('text-success');el.classList.add('text-danger');}}
async function initialMargin(){
  let targetMargin=null;
  const reply=await fetch('/api/cpq/context',{credentials:'same-origin'});
  if(reply.status===400)return DEFAULT_TARGET_MARGIN;
  let data;try{data=await reply.json();}catch{throw Error(`初始化失败：HTTP ${reply.status}`);}
  if(!reply.ok)throw Error(typeof data?.error?.code==='string'?data.error.code:`HTTP ${reply.status}`);
  targetMargin=data?.thresholds?.min_margin;
  if(typeof targetMargin!=='number'||!Number.isFinite(targetMargin)||targetMargin<=-1||targetMargin>=1)throw Error('服务端初始毛利待确认');
  return targetMargin;
}
function drain(){
  let added=false;
  while(pending.length){const item=pending.shift();try{basket.add(item.model_id,item.qty);added=true;}catch(error){report(error);}}
  if(added||wantsOpen){wantsOpen=false;drawer.open();}
}
function start(){
  if(!ready)return Promise.resolve();
  if(basket){drain();return Promise.resolve();}
  if(starting)return starting;
  starting=(async()=>{
    const target_margin=await initialMargin();
    const next=createBasket(undefined,{target_margin});
    const nextDrawer=initDrawer({root:document.getElementById('cpq-drawer'),basket:next});
    initActions({root:document.getElementById('cpq-drawer')});
    basket=next;drawer=nextDrawer;
    window.XL=window.XL||{};
    Object.assign(window.XL,{basket,cpqDrawer:drawer});
    window.XL.cpq=Object.assign(window.XL.cpq||{},{basket,drawer,model:drawerModel,chart:costChart});
    drain();
  })().catch(report).finally(()=>{starting=null;});
  return starting;
}
document.addEventListener('xl:add-to-basket',event=>{
  const item=event.detail;
  if(typeof item?.model_id!=='string'||!item.model_id||item.model_id.trim()!==item.model_id||!Number.isSafeInteger(item.qty)||item.qty<=0){report(Error('型号或数量无效'));return;}
  pending.push({model_id:item.model_id,qty:item.qty});void start();
});
function boot(){
  ready=true;
  document.getElementById('cpq-drawer')?.addEventListener('show.bs.offcanvas',()=>{if(!basket){wantsOpen=true;void start();}});
  void start();
}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',boot,{once:true});else boot();
