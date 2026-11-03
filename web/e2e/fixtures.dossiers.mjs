// All identities, content and prices in this file are fictional.
export const familyKey='ZmljdGlvbmFsLWZhbWlseQ';
const png=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aZWQAAAAASUVORK5CYII=','base64');
const evidence=(id,source='SF')=>({evidence_id:'E-'+id,source_id:source,field_path:'config.功率',locator:'page:2:cell:B7',excerpt:'虚构原文片段：配置需核对',evidence_state:'待核实'});
const offer=(id,supplier,amount,kind='unit_price')=>({offer_id:id,supplier_id:supplier,supplier_name:supplier==='S1'?'虚构甲灯具':'虚构乙灯具',amount_decimal:amount,amount_raw:amount??'约若干',currency:null,tax_basis:'unknown',tax_rate_text:null,unit:null,price_kind:kind,quote_state:'observed',quoted_on:null,document_period:'2030-03',valid_until:null,moq_text:null,quantity_condition:null,lead_time_text:null,conditions:{'验收说明':'此数据完全虚构'},source_id:'SF',locator:'page:2:cell:B7',evidence:[evidence(id)]});
export async function installDossierFixtures(page){
  const suppliers=[{supplier_id:'S1',name:'虚构甲灯具',supplier_type:'产品',categories:['虚构洗墙灯','虚构影视灯'],reliability_notes:'虚构验收：规格冲突未复核，报价条件未知。',family_count:1,configuration_count:2,priced_count:1,unpriced_count:1,latest_quote:null,gap_count:34},
    {supplier_id:'S2',name:'虚构乙灯具',supplier_type:'产品',categories:['虚构洗墙灯'],reliability_notes:'虚构验收',family_count:1,configuration_count:1,priced_count:1,unpriced_count:0,latest_quote:'2030-03-12',gap_count:1},
    {supplier_id:'S3',name:'虚构运达物流',supplier_type:'物流',categories:['虚构运费'],reliability_notes:'虚构验收',family_count:0,configuration_count:0,priced_count:0,unpriced_count:0,latest_quote:null,gap_count:0}];
  const gaps=Array.from({length:35},(_,i)=>({gap_id:'G'+(i+1),supplier_id:i===34?'S2':'S1',supplier_name:i===34?'虚构乙灯具':'虚构甲灯具',product_id:'P1',product_name:'虚构洗墙灯',gap_type:i===0?'power_conflict':'quote_terms',priority:i===0?1:8,ask_text:`请补虚构资料 ${i+1}：配置、装箱与报价条件`,impact:'虚构影响：配置与采购成本需核对',source_refs:['SF']}));
  const configurations=[{product_id:'P1',family_key:familyKey,display_name:'虚构洗墙灯',category:'虚构灯具',canonical_model:'DEMO-X',variant_label:'配置 alpha',evidence_state:'待核实：规格冲突',config:{功率:'虚构数值',配置:'alpha'},evidence:[evidence('P1')],suppliers:[{supplier_id:'S1',name:'虚构甲灯具',model_aliases:['DEMO-X']},{supplier_id:'S2',name:'虚构乙灯具',model_aliases:['DEMO-Y']}],offers:[offer('O1','S1','123.4500'),offer('O2','S2','120.00000001'),offer('O3','S1',null,'configuration_surcharge')],gaps:gaps.slice(0,1)},
    {product_id:'P2',family_key:familyKey,display_name:'虚构洗墙灯',category:'虚构灯具',canonical_model:'DEMO-X',variant_label:'配置 beta',evidence_state:'原文明确',config:{功率:'虚构另一数值'},evidence:[evidence('P2')],suppliers:[{supplier_id:'S1',name:'虚构甲灯具',model_aliases:[]}],offers:[],gaps:[]}];
  const family={family_key:familyKey,display_name:'虚构洗墙灯',category:'虚构灯具',configuration_count:2,supplier_ids:['S1','S2'],states:['原文明确','待核实'],gap_count:35,thumbnail_source_id:'SF'};
  const state={mode:'ok',posts:[],drafts:[],suppliers,gaps,configurations,family};
  state.summary={ready:true,total:gaps.length,items:gaps.slice(0,5).map(g=>({...g,to:'/dossier-gaps?supplier='+g.supplier_id}))};
  await page.route('**/api/v1/dossiers/**',async route=>{
    const req=route.request(),url=new URL(req.url()),path=url.pathname.slice('/api/v1/dossiers'.length);
    if(state.mode==='network')return route.abort('failed');
    if(state.mode==='missing')return route.fulfill({status:404,json:{error:'虚构：接口未部署'}});
    if(state.mode==='error')return route.fulfill({status:503,json:{error:'虚构读取失败'}});
    const reply=data=>route.fulfill({json:structuredClone(data)});
    if(path.startsWith('/media/'))return route.fulfill({contentType:'image/png',body:png});
    if(req.method()==='POST'){
      if(!/\/suppliers\/S[12]\/drafts$/.test(path))throw Error('unexpected fictional POST');
      const fields=Object.fromEntries(new URLSearchParams(req.postData()));state.posts.push({path,fields});
      const supplier=path.split('/')[2],rows=gaps.filter(g=>g.supplier_id===supplier&&(!fields.priority||String(g.priority)===fields.priority)&&(!fields.type||g.gap_type===fields.type));
      const text='您好，请核对以下虚构资料：\n'+rows.map(g=>g.ask_text).join('\n');
      let d=state.drafts.find(d=>d.text===text);
      if(!d){d={draft_id:'fictional-draft-'+state.drafts.length,supplier_id:supplier,text,state:'draft',gap_ids_json:JSON.stringify(rows.map(g=>g.gap_id)),created_at:'2030-03-17T12:00:00Z',created_by:'fictional-operator'};state.drafts.unshift(d);}
      return reply(d);
    }
    if(path==='/suppliers'){const rows=state.mode==='empty'?[]:suppliers.filter(s=>(!url.searchParams.get('q')||s.name.includes(url.searchParams.get('q')))&&(!url.searchParams.get('type')||s.supplier_type===url.searchParams.get('type')));return reply({items:rows,total:rows.length});}
    if(/^\/suppliers\/S[123]\/drafts$/.test(path))return reply({items:state.drafts.filter(d=>d.supplier_id===path.split('/')[2])});
    if(/^\/suppliers\/S[123]$/.test(path)){const s=suppliers.find(s=>s.supplier_id===path.split('/')[2]);return reply({...s,products:configurations.map(p=>({...p,offers:p.offers.filter(o=>o.supplier_id===s.supplier_id)})),gaps:gaps.filter(g=>g.supplier_id===s.supplier_id),evidence:[],other_offers:[]});}
    if(path==='/families')return reply({items:state.mode==='empty'?[]:[family],total:state.mode==='empty'?0:1,categories:['虚构灯具']});
    if(path==='/families/'+familyKey)return reply({...family,configurations,suppliers:suppliers.slice(0,2).map(s=>({supplier_id:s.supplier_id,name:s.name}))});
    if(path==='/sources/SF')return reply({source_id:'SF',kind:'nas_file',locator:'page:2:cell:B7',source_at:null,filename:'虚构资料.png',parse_state:'reviewed',extract_state:'reviewed',failure_reason:null,available:true,media_url:'/api/v1/dossiers/media/fictional-signed-token',mime:'image/png',evidence:[evidence('P1')]});
    if(path==='/gaps'){
      const p=url.searchParams,rows=state.mode==='empty'?[]:gaps.filter(g=>(!p.get('supplier')||p.get('supplier')===g.supplier_id)&&(!p.get('priority')||p.get('priority')===String(g.priority))&&(!p.get('type')||p.get('type')===g.gap_type)),page=Number(p.get('page')||1),size=Number(p.get('page_size')||30);
      return reply({items:rows.slice((page-1)*size,page*size),total:rows.length,page,page_size:size,types:['power_conflict','quote_terms'],priorities:[1,8]});
    }
    throw Error('unknown fictional dossier API '+path);
  });
  await page.route('**/api/v1/ops/queue',route=>route.fulfill({json:{generated_at:1900000000,items:[],total:0,complete:true,unavailable:[],counts:{},supplier_dossiers:state.summary}}));
  return state;
}
