import {createLightbox, addToBasket} from './lightbox.js';
window.XL = window.XL || {};
const root = document.getElementById('sg-root');
if (root && !root.dataset.ready) {
  let items;
  try { items = JSON.parse(document.getElementById('sg-data').textContent); } catch { items = null; }
  if (Array.isArray(items)) init(items);
}
function init(items) {
  root.dataset.ready = 'true';
  const $ = id => document.getElementById(id), keys = ['ip','source','segments','power','dmx','fc','completeness'];
  const chips = [...root.querySelectorAll('[data-filter][data-value]')], checks = [...root.querySelectorAll('[data-column-toggle]')];
  const allowed = Object.fromEntries(keys.map(k => [k,new Set(chips.filter(e=>e.dataset.filter===k).map(e=>e.dataset.value))]));
  const columns = new Set(checks.map(e=>e.dataset.columnToggle));
  const sorts = new Set(['default','power','leds','segments','weight','sku','name','ip','rgb','white','dmx','carton','fc','completeness']);
  const primary = new Set(['default','power','leds','segments','weight']), managed = ['view','q','sort','dir','hide',...keys];
  const grid=$('sg-grid'), body=root.querySelector('#sg-table tbody'), search=$('sg-search'), select=$('sg-sort');
  const cards=new Map([...grid.children].map(e=>[e.dataset.modelId,e])), rows=new Map([...body.children].map(e=>[e.dataset.modelId,e]));
  const indexes=new Map(items.map((v,i)=>[v.model_id,i])), lb=createLightbox(items), collator=new Intl.Collator('zh-CN',{numeric:true,sensitivity:'base'});
  const number=v=>typeof v==='number'&&Number.isFinite(v)?v:null;
  const positive=v=>number(v)!==null&&v>0?v:null;
  const norm=v=>String(v??'').toLowerCase().replace(/[\s\p{Dash_Punctuation}\u2212]+/gu,'');
  const texts=new Map(items.map(i=>[i.model_id,norm(`${i.internal_sku??''} ${i.product_name??''}`)]));
  function clean(raw) {
    raw=raw&&typeof raw==='object'?raw:{};
    return {view:raw.view==='table'?'table':'grid',q:typeof raw.q==='string'?raw.q.slice(0,200):'',sort:sorts.has(raw.sort)?raw.sort:'default',dir:raw.dir==='desc'?'desc':'asc',hidden:Array.isArray(raw.hidden)?[...new Set(raw.hidden.filter(k=>columns.has(k)))]:[],filters:Object.fromEntries(keys.map(k=>[k,Array.isArray(raw.filters?.[k])?[...new Set(raw.filters[k].filter(v=>allowed[k].has(v)))]:[]]))};
  }
  function read(useStorage) {
    const p=new URLSearchParams(location.search);
    if(managed.some(k=>p.has(k))) return clean({view:p.get('view'),q:p.get('q'),sort:p.get('sort'),dir:p.get('dir'),hidden:(p.get('hide')||'').split(','),filters:Object.fromEntries(keys.map(k=>[k,(p.get(k)||'').split(',')]))});
    try { if(useStorage) return clean(JSON.parse(window.localStorage.getItem('xl.strobe.gallery.v1'))); } catch {}
    return clean(null);
  }
  let state=read(true);
  function persist(mode) {
    try { window.localStorage.setItem('xl.strobe.gallery.v1',JSON.stringify(state)); } catch {}
    if(!mode)return;
    try {
      const u=new URL(location.href); managed.forEach(k=>u.searchParams.delete(k)); u.searchParams.set('view',state.view);
      for(const k of ['q','sort','dir']) if(state[k]&&!['default','asc'].includes(state[k]))u.searchParams.set(k,state[k]);
      keys.forEach(k=>{if(state.filters[k].length)u.searchParams.set(k,state.filters[k].join(','));});
      if(state.hidden.length)u.searchParams.set('hide',state.hidden.join(','));
      const target=u.pathname+'?'+u.searchParams.toString().replace(/%2C/gi,',')+u.hash;
      if(target!==location.pathname+location.search+location.hash)history[mode==='push'?'pushState':'replaceState'](null,'',target);
    } catch {}
  }
  const dmx=i=>{const a=(i.dmx_modes||[]).filter(v=>positive(v)!==null);return a.length?Math.max(...a):null;};
  const bucket=(v,a,b)=>positive(v)===null?'unknown':v<=a?'small':v<=b?'medium':'large';
  function source(i){const r=number(i.light_source?.rgb_count),w=number(i.light_source?.white_count);return r>0&&w>0?'RGB+W':r>0&&w===0?'RGB':w>0&&r===0?'W':'unknown';}
  function match(i,k,v){
    const fc=i.flight_case?.source,score=number(i.spec_completeness?.score);
    if(k==='fc')return v==='yes'?Boolean(fc&&fc!=='none'):v==='no'?fc==='none':v==='estimated'?['estimate','estimated'].includes(fc):!fc;
    return ({ip:i.ip_rating||'unknown',source:source(i),segments:bucket(i.pixel_segments,16,48),power:bucket(i.power_w,500,1000),dmx:bucket(dmx(i),16,64),completeness:score===null?'unknown':score>=1&&!(i.spec_completeness?.missing||[]).length?'complete':'incomplete'})[k]===v;
  }
  function value(i){const r=number(i.light_source?.rgb_count),w=number(i.light_source?.white_count);return ({power:positive(i.power_w),weight:positive(i.net_weight_kg),segments:positive(i.pixel_segments),leds:r!==null&&w!==null?positive(r+w):null,sku:i.internal_sku||null,name:i.product_name||null,ip:i.ip_rating||null,rgb:positive(r),white:positive(w),dmx:dmx(i),carton:positive(i.carton?.units_per_pkg),fc:i.flight_case?.source||null,completeness:number(i.spec_completeness?.score)})[state.sort]??null;}
  function compare(a,b){const stable=indexes.get(a.model_id)-indexes.get(b.model_id);if(state.sort==='default')return stable;const av=value(a),bv=value(b);if(av===null||bv===null)return av===bv?stable:av===null?1:-1;const n=typeof av==='number'&&typeof bv==='number'?av-bv:collator.compare(String(av),String(bv));return n*(state.dir==='desc'?-1:1)||stable;}
  function render(mode='push'){
    root.dataset.view=state.view;grid.hidden=state.view!=='grid';$('sg-table-wrap').hidden=state.view!=='table';$('sg-columns').hidden=state.view!=='table';search.value=state.q;
    const custom=select.querySelector('[value=column]');custom.hidden=primary.has(state.sort);select.value=primary.has(state.sort)?state.sort:'column';
    $('sg-direction').textContent=state.dir==='asc'?'升序 ↑':'降序 ↓';$('sg-direction').setAttribute('aria-pressed',String(state.dir==='desc'));$('sg-direction').disabled=state.sort==='default';
    chips.forEach(e=>e.setAttribute('aria-pressed',String(state.filters[e.dataset.filter].includes(e.dataset.value))));
    root.querySelectorAll('#sg-view-toggle [data-view]').forEach(e=>e.setAttribute('aria-pressed',String(e.dataset.view===state.view)));
    root.querySelectorAll('#sg-table [data-sort]').forEach(e=>e.closest('th').setAttribute('aria-sort',e.dataset.sort===state.sort?(state.dir==='asc'?'ascending':'descending'):'none'));
    checks.forEach(e=>{e.checked=!state.hidden.includes(e.dataset.columnToggle);});
    root.querySelectorAll('#sg-table [data-column]').forEach(e=>{e.hidden=state.hidden.includes(e.dataset.column);});
    let count=0;const gf=document.createDocumentFragment(),tf=document.createDocumentFragment();
    [...items].sort(compare).forEach(i=>{const shown=texts.get(i.model_id).includes(norm(state.q))&&keys.every(k=>!state.filters[k].length||state.filters[k].some(v=>match(i,k,v)));if(shown)count++;const card=cards.get(i.model_id),row=rows.get(i.model_id);if(card){card.hidden=!shown;gf.append(card);}if(row){row.hidden=!shown;tf.append(row);}});
    grid.append(gf);body.append(tf);$('sg-count').textContent=`${count} / ${items.length} 款`;$('sg-empty').hidden=count>0;persist(mode);
  }
  root.addEventListener('click',e=>{
    const add=e.target.closest('.sg-add[data-model-id]');if(add&&!add.closest('#sg-lightbox')){addToBasket(add.dataset.modelId);return;}
    const open=e.target.closest('[data-lb-open]');if(open&&!open.disabled){lb.open(open.dataset.modelId,open.dataset.assetUid,open);return;}
    const chip=e.target.closest('[data-filter][data-value]');if(chip){const a=state.filters[chip.dataset.filter],v=chip.dataset.value;state.filters[chip.dataset.filter]=a.includes(v)?a.filter(x=>x!==v):[...a,v];render();return;}
    const view=e.target.closest('#sg-view-toggle [data-view]');if(view){state.view=view.dataset.view;render();return;}
    const header=e.target.closest('#sg-table [data-sort]');if(header){state.dir=state.sort===header.dataset.sort&&state.dir==='asc'?'desc':'asc';state.sort=header.dataset.sort;render();}
  });
  search.addEventListener('input',()=>{state.q=search.value;render('replace');});
  select.addEventListener('change',()=>{if(select.value==='column')return;state.sort=select.value;state.dir='asc';render();});
  $('sg-direction').addEventListener('click',()=>{state.dir=state.dir==='asc'?'desc':'asc';render();});
  $('sg-reset').addEventListener('click',()=>{state.q='';keys.forEach(k=>state.filters[k]=[]);render();});
  checks.forEach(e=>e.addEventListener('change',()=>{state.hidden=checks.filter(c=>!c.checked).map(c=>c.dataset.columnToggle);render();}));
  window.addEventListener('popstate',()=>{state=read(false);render(null);});
  root.querySelectorAll('.sg-img img').forEach(img=>{
    const shell=img.closest('.sg-img'),secondary=img.classList.contains('sg-secondary');
    const done=()=>{if(secondary)shell.classList.toggle('sg-has-second',img.naturalWidth>0);else{shell.classList.toggle('is-error',!img.naturalWidth);shell.classList.toggle('is-ready',img.naturalWidth>0);}};
    img.addEventListener('load',done);img.addEventListener('error',done);if(img.complete)done();
  });
  root.classList.add('sg-enhanced');window.XL.strobeGallery={open:lb.open};render('replace');
}
