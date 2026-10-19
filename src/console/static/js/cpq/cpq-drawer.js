import {actionStates,sliderZones,snapMargin,marginZone,freightLevel,incotermOptions,coerceIncoterm,laneState,lanesForMode,buildCalcRequest,formatUsd,formatPct,packingCompareRows,sortAlerts,SLIDER_MIN,SLIDER_MAX,SLIDER_STEP} from './drawer-model.js';
import {renderCostChart,disposeCostChart} from './cpq-cost-chart.js';
const instances=new WeakMap(),DEBOUNCE_MS=120,REVEAL_MS=30000;
const arr=x=>Array.isArray(x)?x:[],finite=x=>typeof x==='number'&&Number.isFinite(x);
const number=x=>finite(x)?String(x):'待确认',money=x=>finite(x)?formatUsd(x):'待确认';
const key=s=>JSON.stringify([...new Set(s.lines.map(x=>x.model_id))].sort());
function today(){const d=new Date();return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;}
async function read(reply){let data;try{data=await reply.json();}catch{throw Error(`HTTP ${reply.status}: JSON 无效`);}if(!reply.ok)throw Error(typeof data?.error?.code==='string'?data.error.code:`HTTP ${reply.status}`);if(!data||typeof data!=='object'||Array.isArray(data))throw Error('响应格式无效');return data;}

export function initDrawer({root,basket}){
  const doc=root?.nodeType===9?root:root?.ownerDocument||document;
  const panel=root?.matches?.('#cpq-drawer')?root:(root||doc).querySelector?.('#cpq-drawer');
  if(panel&&instances.has(panel))return instances.get(panel);
  const $=s=>panel?.querySelector(s)||null,$$=s=>panel?[...panel.querySelectorAll(s)]:[],state=()=>JSON.parse(basket.serialize());
  const put=(el,value)=>{if(el)el.textContent=String(value??'待确认');};
  const rows=new Map(),catalog=new Map(),errors=new Map(),acks=new Set(),cleanup=[];
  let context=null,contextKey=null,loadingKey=null,contextTask=null,contextSeq=0,seq=0,accepted=-1;
  let result=null,request=null,timer=null,revealTimer=null,previousKey=key(state()),reason=$('#cpq-override-reason')?.value||'';
  let initialMargin=true,opened=false,dead=false,offcanvas=null;
  try{const raw=JSON.parse(doc.getElementById('sg-data')?.textContent||'null');for(const item of arr(Array.isArray(raw)?raw:raw?.items)){
    if(typeof item?.model_id!=='string')continue;
    const images=arr(item.images).filter(x=>x?.customer_safe===true),image=images.find(x=>x.role==='main')||images[0];let thumb=null;
    if(typeof image?.thumb_url==='string'&&/^\/(?!\/)/.test(image.thumb_url)&&!/[\\\s\u0000-\u001f\u007f]/u.test(image.thumb_url)){const u=new URL(image.thumb_url,doc.location.href);if(u.origin===doc.location.origin)thumb=u.pathname+u.search;}
    catalog.set(item.model_id,{sku:item.internal_sku||item.model_id,name:item.product_name||'产品名称待确认',thumb});
  }}catch{/* Optional catalog metadata. */}
  const ready=()=>context!==null&&loadingKey===null&&contextKey===key(state());
  const masked=()=>!context||context.cost_masked===true||result?.cost_masked===true;
  const permissions=()=>arr(context?.permissions).filter(x=>typeof x==='string');
  const alerts=()=>arr(result?.alerts).filter(x=>x&&typeof x.code==='string');
  function listen(el,event,fn){if(el){el.addEventListener(event,fn);cleanup.push(()=>el.removeEventListener(event,fn));}}
  function value(el,x){if(el&&doc.activeElement!==el&&!errors.has(el))el.value=String(x??'');}
  function verify(message,error=false,success=false){const el=$('#cpq-verify');put(el,message);el?.classList.toggle('text-danger',error);el?.classList.toggle('text-success',success);}
  function prune(){for(const el of errors.keys())if(!panel?.contains(el)||el.disabled){errors.delete(el);el.removeAttribute('aria-invalid');}}
  function actionValues(){
    prune();const fresh=result&&accepted===seq&&ready()&&!errors.size;
    const values=actionStates(fresh?result.gate:null,fresh?alerts():[],{acks:[...acks],overrideReason:reason,permissions:permissions()});
    for(const [action,v]of Object.entries(values)){if(!fresh)Object.assign(v,{disabled:true,title:'当前输入尚未校验'});else if(action!=='save'&&result.gate.can_export!==true)Object.assign(v,{disabled:true,title:v.title||'服务端未允许导出'});}return values;
  }
  function renderActions(){const values=actionValues();for(const el of $$('#cpq-actions [data-action]')){const v=values[el.getAttribute('data-action')]||{disabled:true,title:'不支持的操作'};el.disabled=v.disabled;el.setAttribute('title',v.title);}}
  function hideBasis(){clearTimeout(revealTimer);revealTimer=null;put($('#cpq-cost-basis'),'●●●●●');$('#cpq-cost-basis')?.setAttribute('data-masked','true');}
  function canReveal(){if(!ready()||masked())return false;const s=state(),models=new Map(context.models.map(m=>[m.model_id,m]));return s.lines.length>0&&s.lines.every(l=>finite(models.get(l.model_id)?.cost_basis_cny));}
  function reveal(){if(!$('#cpq-cost-basis')||!canReveal())return;const models=new Map(context.models.map(m=>[m.model_id,m]));const total=state().lines.reduce((sum,l)=>sum+models.get(l.model_id).cost_basis_cny*l.qty,0);hideBasis();if(!finite(total))return verify('成本基数合计无效',true);put($('#cpq-cost-basis'),`成本基数合计 = ${formatUsd(total)} CNY`);$('#cpq-cost-basis').setAttribute('data-masked','false');revealTimer=setTimeout(hideBasis,REVEAL_MS);}
  function invalidate(message='等待服务端计算'){++seq;accepted=-1;request=null;clearTimeout(timer);timer=null;hideBasis();verify(message);renderActions();return seq;}
  function schedule(){if(dead)return;const current=invalidate();timer=setTimeout(()=>{timer=null;void calculate(current);},DEBOUNCE_MS);}
  function bad(el,message){errors.set(el,message);el.setAttribute('aria-invalid','true');schedule();verify(message,true);}
  function edit(fn,el){if(el){errors.delete(el);el.removeAttribute('aria-invalid');}try{fn();}catch(e){if(el)bad(el,e.message);else{invalidate();verify(e.message,true);}}}
  function renderRows(s){
    for(const el of doc.querySelectorAll('#cpq-line-count, #cpq-basket-count, [data-cpq-line-count], [data-bs-target="#cpq-drawer"] .badge, [href="#cpq-drawer"] .badge'))put(el,s.lines.length);
    if($('#cpq-empty'))$('#cpq-empty').hidden=s.lines.length>0;
    const ids=new Set(s.lines.map(l=>l.model_id)),accessories=new Set(arr(context?.accessories).map(a=>a.accessory_id));
    for(const [id,row]of rows)if(!ids.has(id)){row.remove();rows.delete(id);}
    for(const line of s.lines){let row=rows.get(line.model_id);if(!row){
      const prototype=$('#cpq-line-prototype'),host=$('#cpq-lines');if(!prototype||!host)continue;
      row=prototype.cloneNode(true);row.removeAttribute('id');for(const el of row.querySelectorAll('[id]'))el.removeAttribute('id');row.hidden=false;row.setAttribute('data-line-model-id',line.model_id);
      const item=catalog.get(line.model_id);put(row.querySelector('[data-field="sku"]'),item?.sku||line.model_id);put(row.querySelector('[data-field="name"]'),item?.name||'产品名称待确认');
      const shell=row.querySelector('.cpq-line-image');if(shell&&item?.thumb){const image=doc.createElement('img');image.setAttribute('src',item.thumb);image.setAttribute('alt',item.name);image.setAttribute('loading','lazy');image.addEventListener('error',()=>{image.remove();shell.setAttribute('aria-label','图片待确认');},{once:true});shell.removeAttribute('aria-label');shell.append(image);}
      host.append(row);rows.set(line.model_id,row);
    }
    const qty=row.querySelector('.cpq-qty'),price=row.querySelector('[data-field="unit-price"]'),remove=row.querySelector('[data-line-action="remove"]');
    if(qty){qty.disabled=false;value(qty,line.qty);}if(remove)remove.disabled=false;
    if(price){price.disabled=s.pricing_mode!=='price';if(price.closest('label'))price.closest('label').hidden=price.disabled;value(price,line.unit_price_usd);}
    for(const el of row.querySelectorAll('.cpq-pack [data-mode]')){const mode=el.getAttribute('data-mode').replace('flightcase','flight_case');el.disabled=!['carton','flight_case'].includes(mode);el.setAttribute('aria-pressed',String(mode===line.packing_mode));}
    for(const checkbox of row.querySelectorAll('[data-accessory]')){const id=checkbox.getAttribute('data-accessory'),selected=line.accessories.find(a=>a.accessory_id===id),input=[...row.querySelectorAll('[data-accessory-qty]')].find(el=>el.getAttribute('data-accessory-qty')===id);checkbox.checked=Boolean(selected);checkbox.disabled=!ready()||!accessories.has(id);checkbox.setAttribute('title',checkbox.disabled?'配件编号待确认':'');if(input){input.disabled=checkbox.disabled;if(selected)value(input,selected.qty_per_unit);else if(!input.value)value(input,input.getAttribute('min'));}}
    }prune();
  }
  function render(){
    const s=state();renderRows(s);const mode=s.shipping.mode,terms=incotermOptions(mode);
    for(const el of $$('#cpq-mode [data-mode]')){el.disabled=!incotermOptions(el.getAttribute('data-mode')).length;el.setAttribute('aria-pressed',String(el.getAttribute('data-mode')===mode));}
    for(const el of $$('#cpq-incoterm [data-incoterm]')){el.disabled=!terms.includes(el.getAttribute('data-incoterm'));el.setAttribute('title',el.disabled?'运输方式不支持此术语':'');el.setAttribute('aria-pressed',String(el.getAttribute('data-incoterm')===s.shipping.incoterm));}
    const lanes=lanesForMode(arr(context?.lanes),mode),select=$('#cpq-lane');if(select){select.textContent='';const empty=doc.createElement('option');empty.setAttribute('value','');put(empty,ready()?'请选择航线':'航线待加载');select.append(empty);for(const lane of lanes){const status=laneState(lane,today()),option=doc.createElement('option'),why=status.reason==='expired'?'已过期':status.reason==='unpriced'?'运价待确认':status.reason;option.setAttribute('value',lane.lane_id);option.setAttribute('title',why);option.disabled=!status.usable;option.classList.toggle('text-muted',!status.usable);put(option,`${lane.name||lane.lane_id}${status.usable?'':`（${why}）`}`);select.append(option);}select.value=s.shipping.lane_id||'';select.disabled=!ready()||!lanes.length;}
    put($('#cpq-lane-validity'),lanes.find(l=>l.lane_id===s.shipping.lane_id)?.valid_until||'待确认');
    const slider=$('#cpq-margin'),range=slider?.closest('.cpq-range'),caption=`目标毛利 ${formatPct(s.target_margin)} · 实际毛利 ${formatPct(result?.totals?.actual_margin)}`;
    if(slider){slider.setAttribute('min',String(SLIDER_MIN));slider.setAttribute('max',String(SLIDER_MAX));slider.setAttribute('step',String(SLIDER_STEP));slider.disabled=s.pricing_mode==='price';value(slider,s.target_margin);slider.setAttribute('aria-valuetext',caption);}
    put($('#cpq-margin-value'),caption);put($('#cpq-actual-return'),formatPct(result?.totals?.actual_margin));const legend=slider?.closest('details')?.querySelector('p');
    try{const t=context.thresholds,z=sliderZones(t);range?.style.setProperty('--xl-fuse-pos',`${z.fuse_pct}%`);range?.style.setProperty('--xl-min-pos',`${z.min_pct}%`);range?.setAttribute('data-zone',marginZone(s.target_margin,t));put(legend,`红区 ≤${formatPct(t.fuse_margin)} · 琥珀区 <${formatPct(t.min_margin)} · 绿区 ≥${formatPct(t.min_margin)}`);}catch{range?.style.removeProperty('--xl-fuse-pos');range?.style.removeProperty('--xl-min-pos');range?.removeAttribute('data-zone');put(legend,'毛利阈值待加载');}
    const pricing=$('#cpq-pricing-mode');if(pricing){pricing.disabled=false;pricing.value=s.pricing_mode==='price'?'price':'target';}
    const override=$('#cpq-override-reason'),allowed=ready()&&Boolean(result?.gate?.requires_override)&&permissions().includes('cpq.override_margin');if(override){override.hidden=!allowed;override.disabled=!allowed;value(override,reason);}for(const el of $$('label[for="cpq-override-reason"]'))el.hidden=!allowed;
    const eye=$('#cpq-reveal');if(eye){eye.disabled=!canReveal();eye.setAttribute('title',eye.disabled?'成本不可见或基数待确认':'显示 30 秒');}
    put($('#cpq-fx-rate'),number(context?.fx?.usd_cny));put($('#cpq-fx-updated'),context?.fx?.updated_at||'待确认');
    const buffer=$('#cpq-fx-buffer'),bufferValue=context?.fx?.buffer_pct??context?.pricing?.fx_buffer_pct;if(buffer){buffer.disabled=true;value(buffer,finite(bufferValue)?bufferValue*100:'');buffer.setAttribute('title','由服务端配置，接口不接受修改');}
    put($('#cpq-packing-source'),s.lines.map(l=>`${catalog.get(l.model_id)?.sku||l.model_id}：${arr(context?.models).find(m=>m.model_id===l.model_id)?.packing?.flight_case?.source||'待确认'}`).join('；')||'待确认');renderActions();
  }
  function paint(){
    if(!result)return;const t=result.totals||{},hide=masked(),values={total:money(t.price_usd),packages:number(t.packages),cbm:number(t.cbm),'gross-weight':number(t.gw_kg),'chargeable-weight':number(t.chargeable_kg)};
    for(const el of $$('[data-result]'))put(el,values[el.getAttribute('data-result')]);
    const body=$('#cpq-result-lines');if(body){body.textContent='';for(const line of arr(result.lines)){const row=doc.createElement('tr'),product=catalog.get(line.model_id);for(const x of [product?`${product.sku} · ${product.name}`:line.model_id,money(line.unit_price_usd),money(line.amount_usd)]){const cell=doc.createElement('td');put(cell,x);row.append(cell);}body.append(row);}}
    const host=$('#cpq-alerts'),required=new Set(arr(result.gate?.requires_ack));if(host){host.textContent='';for(const alert of sortAlerts(alerts())){const row=doc.createElement('div'),message=doc.createElement('p');row.setAttribute('class','cpq-alert');row.setAttribute('data-code',alert.code);row.setAttribute('data-severity',alert.severity||'info');put(message,alert.message);row.append(message);
      if(required.has(alert.code)||alert.requires==='ack'||alert.requires===alert.code||arr(alert.requires).some(x=>x===alert.code||x==='ack')){const label=doc.createElement('label'),input=doc.createElement('input'),caption=doc.createElement('span');input.setAttribute('type','checkbox');input.setAttribute('data-ack-code',alert.code);input.checked=acks.has(alert.code);put(caption,' 我已知悉');label.append(input,caption);row.append(label);}
      if(!hide&&alert.code==='FREIGHT_EXCEEDS_GOODS'&&arr(alert.suggestions).length){const ul=doc.createElement('ul');for(const suggestion of alert.suggestions){const li=doc.createElement('li');put(li,typeof suggestion==='string'?suggestion:'建议内容待确认');ul.append(li);}row.append(ul);}host.append(row);
    }}
    const compare=$('#cpq-pack-compare')||$('.cpq-compare');if(compare){let table=compare;if(!['TABLE','TBODY'].includes(table.tagName)){table=compare.querySelector('table');if(!table){table=doc.createElement('table');compare.append(table);}}let tbody=table.tagName==='TBODY'?table:table.querySelector('tbody');if(!tbody){tbody=doc.createElement('tbody');table.append(tbody);}tbody.textContent='';for(const item of packingCompareRows(result.packing_compare,hide)){const row=doc.createElement('tr'),label=doc.createElement('th'),cell=doc.createElement('td');label.setAttribute('scope','row');put(label,item.label);put(cell,item.value===null?'待确认':hide?formatPct(item.value):`${formatUsd(item.value)} USD`);row.append(label,cell);tbody.append(row);}}
    put($('#cpq-packing-delta'),finite(result.packing_compare?.flight_case?.delta_pct)?formatPct(result.packing_compare.flight_case.delta_pct):'待确认');
    put($('#cpq-container-plan'),result.fcl_plan==null?'待计算':JSON.stringify(result.fcl_plan,(key,value)=>{const k=key.toLowerCase();if(k.includes('supplier')||k.startsWith('offer')||k.includes('cost_basis'))return undefined;if(hide&&(k.includes('cost')||k.startsWith('exw')||k==='unit_goods_value_cny'))return undefined;return value;}));
    const ratio=$('#cpq-freight-ratio'),meter=$('#cpq-freight-meter');if(ratio){ratio.classList.remove('ok','warn','heavy','fuse');try{const level=freightLevel(t.freight_ratio,context.thresholds.freight_ratio);ratio.classList.add(level);ratio.setAttribute('data-level',level);if(meter){const fr=context.thresholds.freight_ratio,scale=fr.inverted*1.5;meter.setAttribute('max',String(scale));meter.style.setProperty('--cpq-freight-warn-pos',`${fr.sensitive/scale*100}%`);meter.style.setProperty('--cpq-freight-fuse-pos',`${fr.inverted/scale*100}%`);meter.setAttribute('value',String(Math.max(0,t.freight_ratio)));}meter?.setAttribute('aria-label',formatPct(t.freight_ratio));put(ratio.querySelector('output'),formatPct(t.freight_ratio));put($('#cpq-freight-status'),{ok:'正常',warn:'敏感',heavy:'偏高',fuse:'运费超过货值'}[level]);}catch{ratio.removeAttribute('data-level');ratio.style.removeProperty('width');meter?.removeAttribute('value');put(ratio.querySelector('output'),'待确认');put($('#cpq-freight-status'),'');}}
    const chart=$('#cpq-cost-chart');if(chart)try{renderCostChart(chart,result.totals,{masked:!context||Boolean(result.cost_masked||context.cost_masked)});}catch{disposeCostChart(chart);put(chart,'成本图暂不可用');}
  }
  function refresh(force=false){
    if(dead)return Promise.resolve();const s=state(),k=key(s);if(loadingKey===k&&contextTask)return contextTask;if(!force&&ready())return Promise.resolve();
    const ids=[...new Set(s.lines.map(l=>l.model_id))].sort(),current=++contextSeq;context=null;contextKey=null;loadingKey=ids.length?k:null;invalidate(ids.length?'正在加载上下文':'报价篮为空。');render();paint();if(!ids.length){contextTask=null;return Promise.resolve();}
    contextTask=(async()=>{try{const query=new URLSearchParams({model_ids:ids.join(',')}),data=await read(await fetch(`/api/cpq/context?${query}`,{credentials:'same-origin'}));if(dead||current!==contextSeq||k!==key(state()))return;if(!Array.isArray(data.models)||!Array.isArray(data.lanes))throw Error('上下文格式无效');context=data;contextKey=k;loadingKey=null;const minimum=data.thresholds?.min_margin;if(initialMargin&&finite(minimum)&&minimum>-1&&minimum<1){initialMargin=false;if(state().target_margin!==minimum)basket.setMargin(minimum);}render();paint();schedule();}catch(e){if(dead||current!==contextSeq||k!==key(state()))return;context=null;contextKey=null;loadingKey=null;render();paint();verify(`上下文加载失败：${e.message}`,true);}finally{if(current===contextSeq)contextTask=null;}})();return contextTask;
  }
  async function calculate(current){
    if(dead||current!==seq)return;prune();if(errors.size)return verify(errors.values().next().value,true);if(!state().lines.length)return verify('报价篮为空。');if(!ready())return;
    const built=buildCalcRequest(state(),{lanes:context.lanes,todayISO:today()});if(!built.ok)return verify({EMPTY_BASKET:'报价篮为空。',NO_LANE:'请选择航线。',LANE_UNUSABLE:'航线不可用。',INVALID_BASKET:'请检查数量、包装和单价。'}[built.reason]||'输入待确认',built.reason==='INVALID_BASKET');
    const payload={...built.request,acks:[...acks],override_reason:reason},token=doc.querySelector('meta[name="csrf-token"]')?.getAttribute('content'),csrfHeaders=token?{'X-CSRF-Token':token}:{};verify('正在由服务端计算…');
    try{const reply=await fetch('/api/cpq/calculate',{
      method:'POST',credentials:'same-origin',
      headers:{'Content-Type':'application/json',...csrfHeaders},
      body:JSON.stringify(payload),
    });const data=await read(reply);if(dead||current!==seq||!ready())return;if(!data.gate||typeof data.gate.can_save!=='boolean'||typeof data.gate.can_export!=='boolean')throw Error('服务端门禁无效');result=data;request=payload;accepted=current;if(masked())hideBasis();paint();render();verify('已由服务端计算 ✓',false,true);}catch(e){if(dead||current!==seq)return;accepted=-1;request=null;renderActions();verify(`服务端计算失败：${e.message}`,true);}
  }
  const unsubscribe=basket.subscribe(()=>{if(dead)return;hideBasis();render();const k=key(state());if(k!==previousKey){previousKey=k;void refresh();}else schedule();});
  listen(panel,'input',event=>{
    const el=event.target;if(!el?.matches||el.disabled)return;if(el.matches('#cpq-override-reason')){reason=el.value;schedule();return;}const amount=el.value?.trim()?Number(el.value):NaN;
    if(el.matches('#cpq-margin')){if(!finite(amount))return bad(el,'毛利无效');initialMargin=false;edit(()=>basket.setMargin(snapMargin(amount)),el);return;}
    const row=el.closest('[data-line-model-id]');if(!row||row.hidden)return;const id=row.getAttribute('data-line-model-id');
    if(el.matches('.cpq-qty')){if(!Number.isSafeInteger(amount)||amount<=0)return bad(el,'数量须为正整数');edit(()=>basket.setQty(id,amount),el);}
    else if(el.matches('[data-field="unit-price"]')){if(!finite(amount)||amount<=0)return bad(el,'单价须大于零');edit(()=>basket.setUnitPrice(id,amount),el);}
    else if(el.matches('[data-accessory-qty]')){if(!finite(amount)||amount<=0)return bad(el,'配件数量须大于零');const accessory=el.getAttribute('data-accessory-qty'),checkbox=[...row.querySelectorAll('[data-accessory]')].find(x=>x.getAttribute('data-accessory')===accessory);errors.delete(el);el.removeAttribute('aria-invalid');if(checkbox?.checked)edit(()=>basket.setAccessory(id,accessory,amount),el);else schedule();}
  });
  listen(panel,'change',event=>{
    const el=event.target;if(!el?.matches||el.disabled)return;
    if(el.matches('#cpq-pricing-mode'))edit(()=>basket.setPricingMode(el.value==='price'?'price':'margin'));
    else if(el.matches('#cpq-lane'))edit(()=>basket.setShipping({lane_id:el.value||null}));
    else if(el.matches('[data-ack-code]')){const code=el.getAttribute('data-ack-code');if(!code)return;if(el.checked)acks.add(code);else acks.delete(code);schedule();}
    else if(el.matches('[data-accessory]')){const row=el.closest('[data-line-model-id]');if(!row||row.hidden)return;const accessory=el.getAttribute('data-accessory'),input=[...row.querySelectorAll('[data-accessory-qty]')].find(x=>x.getAttribute('data-accessory-qty')===accessory),amount=input?.value?.trim()?Number(input.value):NaN;if(el.checked&&(!finite(amount)||amount<=0)){el.checked=false;if(input)bad(input,'请填写配件数量');return;}edit(()=>basket.setAccessory(row.getAttribute('data-line-model-id'),accessory,el.checked?amount:0),input);}
  });
  listen(panel,'click',event=>{
    const el=event.target?.closest?.('button');if(!el||el.disabled)return;const row=el.closest('[data-line-model-id]');
    if(el.matches('#cpq-reveal'))return reveal();
    if(el.matches('#cpq-actions [data-action]')){const action=el.getAttribute('data-action');if(!request||actionValues()[action]?.disabled!==false)return;const copy=JSON.parse(JSON.stringify(request));doc.dispatchEvent(new CustomEvent('xl:cpq-action',{detail:{action,request:copy,acks:[...copy.acks],override_reason:copy.override_reason}}));return;}
    if(row&&!row.hidden&&el.matches('[data-line-action="remove"]'))return edit(()=>basket.remove(row.getAttribute('data-line-model-id')));
    if(row&&!row.hidden&&el.matches('.cpq-pack [data-mode]'))return edit(()=>basket.setPacking(row.getAttribute('data-line-model-id'),el.getAttribute('data-mode').replace('flightcase','flight_case')));
    if(el.matches('#cpq-mode [data-mode]')){const mode=el.getAttribute('data-mode'),shipping=state().shipping,lane=lanesForMode(arr(context?.lanes),mode).find(l=>l.lane_id===shipping.lane_id&&laneState(l,today()).usable);return edit(()=>basket.setShipping({mode,incoterm:coerceIncoterm(mode,shipping.incoterm),lane_id:lane?.lane_id||null}));}
    if(el.matches('#cpq-incoterm [data-incoterm]'))return edit(()=>basket.setShipping({incoterm:el.getAttribute('data-incoterm')}));
    if(el.matches('[data-bs-dismiss="offcanvas"]')&&!window.bootstrap?.Offcanvas)close();
  });
  function onOpen(){if(dead)return;const first=!opened;opened=true;if(first||(!ready()&&!loadingKey))void refresh(first);}
  function controller(){const C=window.bootstrap?.Offcanvas;if(!C||!panel)return null;if(!offcanvas)offcanvas=C.getOrCreateInstance?C.getOrCreateInstance(panel):new C(panel);return offcanvas;}
  function open(){if(dead||!panel)return;onOpen();const instance=controller();if(instance)instance.show();else{panel.hidden=false;panel.classList.add('show');panel.setAttribute('aria-modal','true');panel.removeAttribute('aria-hidden');panel.focus();}}
  function close(){hideBasis();const instance=controller();if(instance)instance.hide();else{panel?.classList.remove('show');panel?.setAttribute('aria-hidden','true');panel?.removeAttribute('aria-modal');}}
  listen(panel,'show.bs.offcanvas',onOpen);listen(panel,'shown.bs.offcanvas',paint);
  listen(panel,'hidden.bs.offcanvas',()=>{hideBasis();const chart=$('#cpq-cost-chart');if(chart)disposeCostChart(chart);});
  listen(doc,'visibilitychange',()=>{if(doc.hidden)hideBasis();});
  listen(doc,'click',event=>{if(!window.bootstrap?.Offcanvas&&event.target?.closest?.('[data-bs-target="#cpq-drawer"], [href="#cpq-drawer"]')){event.preventDefault();open();}});
  const api={open,close,refresh:()=>refresh(true),destroy(){if(dead)return;dead=true;++contextSeq;++seq;clearTimeout(timer);hideBasis();unsubscribe();cleanup.forEach(fn=>fn());const chart=$('#cpq-cost-chart');if(chart)disposeCostChart(chart);context=null;result=null;request=null;acks.clear();errors.clear();rows.clear();if(panel)instances.delete(panel);}};
  if(panel)instances.set(panel,api);hideBasis();render();if(panel?.classList.contains('show'))onOpen();return api;
}
