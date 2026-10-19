export function addToBasket(model_id){if(model_id)document.dispatchEvent(new CustomEvent('xl:add-to-basket',{detail:{model_id,qty:1},bubbles:true}));}
function localURL(raw){try{if(typeof raw!=='string'||!raw.startsWith('/media/a/')||raw.includes('\\'))return null;const u=new URL(raw,location.href);return u.origin===location.origin?u.href:null;}catch{return null;}}
export function createLightbox(items){
  const $=id=>document.getElementById(id),modal=$('sg-lightbox'),stage=$('sg-lb-stage'),img=stage.querySelector('img'),thumbs=$('sg-lb-thumbs'),specs=$('sg-lb-specs'),message=$('sg-lb-message');
  const models=new Map(items.map(i=>[i.model_id,i])),points=new Map();
  let item=null,images=[],index=0,opened=false,trigger=null,backend=null,scale=1,x=0,y=0,bw=0,bh=0,expected='',original=false,preloads=[],inert=[],gesture=null,start=null,multi=false,moved=false,suppress=0,lastTap=null;
  const clamp=(v,a,b)=>Math.max(a,Math.min(b,v));
  const positive=v=>typeof v==='number'&&Number.isFinite(v)&&v>0;
  function focusable(){return [...modal.querySelectorAll('button:not([disabled]),a[href],input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[tabindex="0"]')].filter(e=>!e.closest('[hidden]')&&e.getClientRects().length);}
  function isolate(){let node=modal;while(node.parentElement){for(const sibling of node.parentElement.children){if(sibling!==node&&!['SCRIPT','STYLE','LINK'].includes(sibling.tagName)){inert.push([sibling,sibling.inert]);sibling.inert=true;}}node=node.parentElement;if(node===document.body)break;}}
  function paint(){const mx=Math.max(0,(bw*scale-stage.clientWidth)/2),my=Math.max(0,(bh*scale-stage.clientHeight)/2);x=clamp(x,-mx,mx);y=clamp(y,-my,my);img.style.transform=`translate(${x}px, ${y}px) scale(${scale})`;stage.classList.toggle('sg-zoomed',scale>1);$('sg-lb-reset').textContent=scale.toFixed(1)+'×';$('sg-lb-minus').disabled=scale<=1;$('sg-lb-plus').disabled=scale>=5;}
  function fit(){if(!opened||!img.naturalWidth||img.hidden)return;const ratio=Math.min(stage.clientWidth/img.naturalWidth,stage.clientHeight/img.naturalHeight);bw=img.naturalWidth*ratio;bh=img.naturalHeight*ratio;img.style.width=bw+'px';img.style.height=bh+'px';paint();}
  function position(cx,cy){const r=stage.getBoundingClientRect();return {x:cx-r.left-r.width/2,y:cy-r.top-r.height/2};}
  function zoom(next,ax=0,ay=0){if(img.hidden)return;next=clamp(next,1,5);const r=next/scale;x=ax-(ax-x)*r;y=ay-(ay-y)*r;scale=next;paint();}
  function reset(){scale=1;x=0;y=0;paint();}
  function loaded(){if(!opened||img.currentSrc!==expected||!img.naturalWidth)return;img.hidden=false;message.hidden=true;fit();}
  img.addEventListener('load',loaded);
  img.addEventListener('error',()=>{if(!opened)return;img.hidden=true;message.textContent='暂无图片：加载失败，可切换图片或尝试原图。';message.hidden=false;});
  function load(full=false){original=full;const raw=full?images[index].orig_url:images[index].large_url;expected=localURL(raw)||'';img.hidden=true;message.hidden=false;message.textContent=full?'正在加载原图…':'正在加载图片…';$('sg-lb-original').disabled=full||!localURL(images[index].orig_url);img.alt=`${item.internal_sku||''} ${item.product_name||''} · ${index+1}`;img.src=expected;if(img.complete&&img.naturalWidth)loaded();}
  function show(next){
    index=(next+images.length)%images.length;points.clear();gesture=null;lastTap=null;reset();
    $('sg-lb-counter').textContent=`${index+1} / ${images.length}`;$('sg-lb-prev').disabled=$('sg-lb-next').disabled=images.length<2;
    [...thumbs.children].forEach((b,i)=>{b.setAttribute('aria-current',String(i===index));});
    thumbs.children[index]?.scrollIntoView({block:'nearest',inline:'nearest'});
    const mark=$('sg-lb-mark');if(mark)mark.dataset.assetUid=images[index].asset_uid;
    load();preloads=[];const adjacent=new Set([(index+images.length-1)%images.length,(index+1)%images.length]);
    adjacent.forEach(i=>{if(i!==index){const p=new Image();p.src=localURL(images[i].large_url);preloads.push(p);}});
  }
  function buildSpecs(){
    const dl=specs.querySelector('dl');dl.replaceChildren();const s=item.light_source||{},dmx=(item.dmx_modes||[]).filter(positive);
    const rows=[['对客 SKU',item.internal_sku],['产品名',item.product_name],['IP',item.ip_rating],['RGB 颗数',positive(s.rgb_count)?s.rgb_count:null],['白光颗数',positive(s.white_count)?s.white_count:null],['像素分段',positive(item.pixel_segments)?item.pixel_segments:null],['DMX',dmx.length?dmx.join('/')+' ch':null],['功率',positive(item.power_w)?item.power_w+' W':null],['净重',positive(item.net_weight_kg)?item.net_weight_kg+' kg':null]];
    rows.forEach(([label,value])=>{if(value===null||value===undefined||value==='')return;const dt=document.createElement('dt'),dd=document.createElement('dd');dt.textContent=label;dd.textContent=String(value);dl.append(dt,dd);});
  }
  function finish(){if(!opened)return;opened=false;points.clear();gesture=null;modal.hidden=true;modal.classList.remove('sg-visible','show');modal.setAttribute('aria-hidden','true');document.body.classList.remove('sg-lb-open');inert.forEach(([el,value])=>{el.inert=value;});inert=[];preloads=[];if(trigger?.isConnected)trigger.focus({preventScroll:true});}
  function close(){if(!opened)return;if(backend){try{backend.hide();}catch{}}finish();}
  modal.addEventListener('hidden.bs.modal',finish);
  function open(modelId,assetUid,element=document.activeElement){
    const next=models.get(modelId);if(!next)return;
    const available=(next.images||[]).filter(im=>localURL(im.large_url));if(!available.length)return;
    item=next;images=available;trigger=element;thumbs.replaceChildren();
    images.forEach((im,i)=>{const b=document.createElement('button'),t=document.createElement('img');b.type='button';b.dataset.index=String(i);b.setAttribute('aria-label',`第 ${i+1} 张图片`);t.alt='';t.loading='lazy';t.decoding='async';const url=localURL(im.thumb_url);if(url)t.src=url;b.append(t);thumbs.append(b);});
    buildSpecs();$('sg-lb-title').textContent=`${item.internal_sku||''} ${item.product_name||''}`.trim();$('sg-lb-add').dataset.modelId=item.model_id;
    if(!opened){opened=true;modal.hidden=false;modal.removeAttribute('aria-hidden');modal.classList.add('sg-visible');document.body.classList.add('sg-lb-open');isolate();
      try{if(window.bootstrap?.Modal){backend=backend||new window.bootstrap.Modal(modal,{backdrop:false,keyboard:false,focus:false});backend.show();}}catch{backend=null;}
    }
    show(Math.max(0,images.findIndex(im=>im.asset_uid===assetUid)));$('sg-lb-close').focus({preventScroll:true});
  }
  function info(){specs.hidden=!specs.hidden;$('sg-lb-info').setAttribute('aria-pressed',String(!specs.hidden));}
  $('sg-lb-close').addEventListener('click',close);$('sg-lb-prev').addEventListener('click',()=>show(index-1));$('sg-lb-next').addEventListener('click',()=>show(index+1));$('sg-lb-info').addEventListener('click',info);
  $('sg-lb-original').addEventListener('click',()=>{if(!original)load(true);});$('sg-lb-reset').addEventListener('click',reset);$('sg-lb-plus').addEventListener('click',()=>zoom(scale*1.25));$('sg-lb-minus').addEventListener('click',()=>zoom(scale/1.25));$('sg-lb-add').addEventListener('click',()=>addToBasket(item?.model_id));
  thumbs.addEventListener('click',e=>{const b=e.target.closest('[data-index]');if(b)show(Number(b.dataset.index));});
  modal.addEventListener('click',e=>{if(!opened||scale>1||performance.now()<suppress)return;if(e.target===modal){close();return;}if(e.target===stage){const r=img.getBoundingClientRect();if(img.hidden||e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)close();}});
  stage.addEventListener('wheel',e=>{if(!opened)return;e.preventDefault();const p=position(e.clientX,e.clientY),delta=e.deltaY*(e.deltaMode===1?16:e.deltaMode===2?stage.clientHeight:1);zoom(scale*Math.exp(-delta*.002),p.x,p.y);},{passive:false});
  stage.addEventListener('dblclick',e=>{if(e.pointerType==='touch'||e.target!==img)return;e.preventDefault();const p=position(e.clientX,e.clientY);zoom(scale>1?1:2.5,p.x,p.y);suppress=performance.now()+400;});
  function rebase(){const a=[...points.values()];if(a.length>=2){const p=position((a[0].x+a[1].x)/2,(a[0].y+a[1].y)/2);gesture={type:'pinch',distance:Math.max(1,Math.hypot(a[0].x-a[1].x,a[0].y-a[1].y)),scale,x,y,cx:p.x,cy:p.y};}else if(a.length)gesture={type:'pan',px:a[0].x,py:a[0].y,x,y};else gesture=null;}
  stage.addEventListener('pointerdown',e=>{if(!opened||e.button!==0||img.hidden)return;if(points.size===0){start={x:e.clientX,y:e.clientY,image:e.target===img};multi=false;moved=false;}points.set(e.pointerId,{x:e.clientX,y:e.clientY});if(points.size>1){multi=true;moved=true;}rebase();try{stage.setPointerCapture(e.pointerId);}catch{}});
  stage.addEventListener('pointermove',e=>{
    if(!points.has(e.pointerId)||!gesture)return;points.set(e.pointerId,{x:e.clientX,y:e.clientY});if(start&&Math.hypot(e.clientX-start.x,e.clientY-start.y)>6)moved=true;
    const a=[...points.values()];if(a.length>=2&&gesture.type==='pinch'){const p=position((a[0].x+a[1].x)/2,(a[0].y+a[1].y)/2),next=clamp(gesture.scale*Math.hypot(a[0].x-a[1].x,a[0].y-a[1].y)/gesture.distance,1,5);x=p.x-(gesture.cx-gesture.x)*next/gesture.scale;y=p.y-(gesture.cy-gesture.y)*next/gesture.scale;scale=next;paint();}else if(a.length===1&&gesture.type==='pan'&&scale>1){x=gesture.x+e.clientX-gesture.px;y=gesture.y+e.clientY-gesture.py;paint();}
  });
  function release(e,cancel=false){
    if(!points.has(e.pointerId))return;points.delete(e.pointerId);
    if(points.size){rebase();return;}
    if(moved||multi||cancel)suppress=performance.now()+400;
    if(!cancel&&start&&!multi&&scale===1){const dx=e.clientX-start.x,dy=e.clientY-start.y;if(Math.abs(dx)>60&&Math.abs(dx)>Math.abs(dy)*1.4){show(index+(dx<0?1:-1));suppress=performance.now()+400;}}
    if(!cancel&&!multi&&!moved&&start?.image&&e.pointerType==='touch'){
      const now=performance.now();if(lastTap&&now-lastTap.time<320&&Math.hypot(e.clientX-lastTap.x,e.clientY-lastTap.y)<30){const p=position(e.clientX,e.clientY);zoom(scale>1?1:2.5,p.x,p.y);lastTap=null;suppress=now+500;}else lastTap={time:now,x:e.clientX,y:e.clientY};
    }else lastTap=null;
    gesture=null;start=null;
  }
  stage.addEventListener('pointerup',e=>release(e));stage.addEventListener('pointercancel',e=>release(e,true));stage.addEventListener('lostpointercapture',e=>release(e,true));
  document.addEventListener('keydown',e=>{
    if(!opened)return;
    if(e.key==='Tab'){const a=focusable(),first=a[0]||modal,last=a[a.length-1]||modal;if(e.shiftKey&&(document.activeElement===first||!modal.contains(document.activeElement))){e.preventDefault();last.focus();}else if(!e.shiftKey&&(document.activeElement===last||!modal.contains(document.activeElement))){e.preventDefault();first.focus();}return;}
    if(e.ctrlKey||e.altKey||e.metaKey)return;
    if(['Escape','ArrowLeft','ArrowRight','i','I'].includes(e.key)){e.preventDefault();e.stopPropagation();if(e.key==='Escape')close();else if(e.key==='ArrowLeft')show(index-1);else if(e.key==='ArrowRight')show(index+1);else info();}
  },true);
  document.addEventListener('focusin',e=>{if(opened&&!modal.contains(e.target))($('sg-lb-close')).focus({preventScroll:true});});
  window.addEventListener('resize',fit);if(window.ResizeObserver)new ResizeObserver(fit).observe(stage);
  return {open,close};
}
