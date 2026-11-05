// Fictional stateful browser fixtures. No HTTP proxy, database or channel sender.
import { installBaseFixtures as installFixtures } from './fixtures.base.mjs';
export const stages = ['inquiry','sourcing','quoting','negotiating','awaiting_payment','paid','inspection','shipped','delivered','aftersales','reorder'];
const labels = ['询盘','询价核实','报价','谈判','待付款','已成交·备货','验货','已发货','已签收','售后','返单'];
const tasks = [['弄清需求'],['确认供应商成本','向货代要运价'],['核对成本报价有效期','检查倒挂'],['记录客户还价'],['确认收款'],['下单给供应商','确认交期','通知客户生产进度'],['安排验货'],['确认到达日'],['确认签收'],[],[]];
const nodeNames = {po:'下单给供应商',supplier_confirmation:'供应商确认交期',production:'生产中',inspection:'验货',booking:'出货订舱',transit:'物流在途',customs:'到港/清关',signoff:'客户签收'};
const at = '2030-01-01T08:00:00+08:00';
const pageOf = (items) => ({items,total:items.length,page:1,page_size:100});
const parties = [{id:11,kind:'customer',name:'虚构客户甲',country:'ZZ'},{id:12,kind:'supplier',name:'虚构供应商甲'},{id:13,kind:'forwarder',name:'虚构货代甲'}];
export const conversations = [{key:'fictional-alibaba',buyer_name:'虚构客户甲',channel_label:'阿里国际站',country:'ZZ'},{key:'fictional-wechat',buyer_name:'虚构供应商甲',channel_label:'微信',country:'ZZ'}];
const products = [1,2,3].map((id) => ({ id, name: id === 1 ? '虚构合格灯具' : id === 2 ? '虚构过期灯具' : '虚构缺有效期灯具', packing_ready:true, current_offer:{ id,validity:id === 1 ? 'valid' : id === 2 ? 'expired':'no_expiry'}, offers:[] }));
export async function installDealFixtures(page, problems=[]) {
  const state = {deals:[],quotes:[],payments:[],outbox:[],nodes:{},posts:[],receipts:new Map()};
  let sequence = 100;
  const next = () => ++sequence;
  const event = (d,text) => d.events.unshift({id:next(),kind:'system',summary:text,at,who:'虚构操作人'});
  const templates = (d) => { d.tasks[d.stage] ??= tasks[stages.indexOf(d.stage)].map((title)=>({id:next(),title,status:'open',auto:1,stage:d.stage})); };
  const advance = (d, stage) => { d.stage=stage; d.stage_label=labels[stages.indexOf(stage)]; templates(d); event(d,'进入'+d.stage_label); };
  const view = (d) => ({...d,drafts:state.outbox.filter((o)=>o.deal_id===d.id),stages:stages.map((key,i)=>({key,label:labels[i],current:key===d.stage,done:i<stages.indexOf(d.stage),index:i})),open_tasks:Object.values(d.tasks).flat().filter((t)=>t.status==='open').length});
  const getNodes = (d) => state.nodes[d.id] ??= Object.entries(nodeNames).map(([node,label])=>({id:next(),node,label,status:'todo',responsible:'us',party_id:null,planned_on:null,completed_on:null,inspection_result:null,overdue:false}));
  const progress = (d) => { const items=getNodes(d); return {eligible:d.status==='won',deal_id:d.id,title:d.title,stage:d.stage,items,done:items.filter((n)=>n.status==='done').length,total:8,overdue:items.some((n)=>n.overdue),suggestions:[],next_due:null}; };
  const calc = (q) => {
    q.blocking=[];
    if(!q.terms.confirmed) q.blocking.push({code:'terms',message:'请确认付款与交付范围'});
    if(!q.valid_until) q.blocking.push({code:'expiry',message:'请填写报价有效期'});
    if(q.lines.some((l)=>!l.offer_id)) q.blocking.push({code:'offer',message:'成本已过期或没写有效期，请向供应商要新价'});
    if(q.lines.some((l)=>!l.sell_unit_price)) q.blocking.push({code:'sell',message:'请填写销售单价'});
    if(q.incoterm==='DDP'&&!q.costs.length) q.blocking.push({code:'freight',message:'请添加有效运价'});
    const cost=q.lines.reduce((s,l)=>s+Number(l.cost_unit??0)*l.qty,0)+q.costs.reduce((s,c)=>s+Number(c.amount),0);
    const revenue=q.lines.reduce((s,l)=>s+Number(l.sell_unit_price??0)*l.qty,0);
    const profit=revenue*7-cost;
    q.inverted=q.lines.every((l)=>l.sell_unit_price!=null)&&profit<0;
    if(q.inverted) q.blocking.push({code:'inverted',message:'报价倒挂，销售额低于总成本'});
    q.totals={cost_cny:cost.toFixed(2),revenue:revenue.toFixed(2),revenue_cny:(revenue*7).toFixed(2),profit_cny:profit.toFixed(2),margin:revenue?(profit/(revenue*7)*100).toFixed(2)+'%':null,cost_complete:q.lines.every((l)=>l.offer_id)};
    q.per_line=q.lines.map((l,i)=>({line_id:l.id,line_no:i+1,landed_cost_cny:(100*l.qty).toFixed(2),min_price_for_target:'20.00'}));
    return q;
  };
  await installFixtures(page,problems);
  await page.route('**/api/**',async (route)=>{
    const request=route.request(),url=new URL(request.url()),path=url.pathname.replace(/^\/api\/v1\//,''),method=request.method();
    const f=Object.fromEntries(new URLSearchParams(request.postData()??''));
    const match=path.match(/^deals\/(\d+)(?:\/(.*))?$/), d=match?state.deals.find((d)=>d.id===Number(match[1])):null;
    const qm=path.match(/^quotes\/(\d+)(?:\/(.*))?$/), q=qm?state.quotes.find((q)=>q.id===Number(qm[1])):null;
    const reply=async (data,status=200)=>route.fulfill({status,json:structuredClone(data)});
    try {
      if(method==='GET') {
        if(path==='parties-ops') return reply(pageOf(parties.filter((p)=>p.kind===url.searchParams.get('kind'))));
        if(path==='archive/parties') return reply(pageOf(parties.filter((p)=>p.kind===url.searchParams.get('kind'))));
        if(path==='archive/products') return reply(pageOf(products));
        if(path==='inbox/conversations') return reply(pageOf(conversations));
        if(path.startsWith('inbox/conversations/fictional-')) {const c=conversations.find((c)=>path.endsWith(c.key));return reply({conversation:c,messages:[{role:'buyer',text:c.channel_label==='微信'?'虚构供应商：成本已确认':'虚构客户：请报价四件',sent_at:at}]});}
        if(path==='deals/summary') return reply({open:state.deals.filter((d)=>d.status==='open').length,won:state.deals.filter((d)=>d.status==='won').length,lost:state.deals.filter((d)=>d.status==='lost').length,paused:0,waiting_on_us:0,by_stage:stages.map((stage,i)=>({stage,label:labels[i],count:state.deals.filter(d=>d.status==='open'&&d.stage===stage).length})),overdue:0,due_tasks:0});
        if(path==='deals') {
          const params=url.searchParams;
          const filtered=state.deals.filter(d=>(!params.get('status')||params.get('status')==='all'||d.status===params.get('status'))&&(!params.get('stage')||d.stage===params.get('stage'))&&(!params.get('waiting')||(params.get('waiting')==='us'?d.needs_attention:d.waiting_on.length>0)));
          const number=Number(params.get('page')||1),size=Number(params.get('page_size')||30);
          return reply({items:filtered.slice((number-1)*size,number*size),total:filtered.length,page:number,page_size:size});
        }
        if(d) {
          if(!match[2]) return reply(view(d));
          if(match[2]==='quotes') return reply(pageOf(state.quotes.filter((q)=>q.deal_id===d.id).map((q)=>({...calc(q),blocking:q.blocking.length,warnings:0}))));
          if(match[2]==='payments') return reply({items:state.payments.filter((p)=>p.deal_id===d.id),summary:[]});
          if(match[2]==='fulfilment') return reply(progress(d));
          if(match[2]==='offers') return reply({customer:null,participants:[],items:[],draft_rounds:[]});
          if(match[2]==='demands') return reply({...pageOf([]),conversations:[]});
          if(match[2]==='waiting') return reply({participants:[],items:[],products:[]});
          if(match[2]==='secretary') return reply({id:1,source:'rules',status:'ok',created_at:at,output_json:{headline:'先核对产品再报价',waiting_on:[],next_steps:[],risks:[]}});
          if(match[2]==='issues') return reply(pageOf([]));
        }
        if(path==='fulfilment/board') return reply(pageOf(state.deals.filter((d)=>d.status==='won').map(progress)));
        if(path==='reorder/leads') return reply(pageOf([]));
        if(q) {
          if(!qm[2]) return reply(calc(q));
          if(qm[2]==='freight-options') return reply({destination_country:q.destination_country,items:[{id:13,forwarder_name:'虚构货代甲',origin:'虚构港',destination_country:'ZZ',mode:'air',service:'虚构到门服务',unit:'kg',currency:'CNY',unit_price:'2',amount_in_quote_currency:'40.00',amount_cny:'40.00',min_charge:null,valid_until:'2030-02-01',remaining_validity_days:31,calculable:true,calculation_reasons:[],recommendations:['cheapest'],channel:'forwarder'}]});
          if(qm[2]==='pi'||qm[2]==='text') return reply({text:`虚构报价四件 USD 200.00 ${q.incoterm}`,lang:'zh',ready:calc(q).blocking.length===0,source_hash:'a'.repeat(64),missing:[]});
        }
        return route.fallback();
      }
      if (!/^(deals(?:\/|$)|deal-tasks\/|quotes\/|payments\/)/.test(path)) return route.fallback();
      if(method!=='POST') throw Error('Unexpected method '+method);
      state.posts.push({path,fields:f});
      const signature=JSON.stringify([path,f]);
      if(f.operation_key&&state.receipts.has(f.operation_key)) {const old=state.receipts.get(f.operation_key);if(old.signature!==signature)return reply({error:'同次操作内容改变'},409);return reply(old.result);}
      if(q&&qm[2]?.startsWith('lines/')&&f.sell_unit_price==='1'&&state.priceGate) await state.priceGate.promise;
      let result={ok:true,id:next()};
      if(path==='deals') {
        const c=parties.find((p)=>String(p.id)===f.customer_party_id)??parties[0];
        const customer={id:c.id,name:f.customer_name||c.name};
        const deal={id:result.id,title:f.title,customer,destination_country:f.destination_country,stage:f.stage||'inquiry',status:'open',currency:'USD',value:null,participants:[{party_id:customer.id,id:next(),name:customer.name,role:'customer',state:'none',last_at:null,conversations:[]}],conversations:[],items:[{id:next(),name:f.product_name,qty:Number(f.qty)}],tasks:{},events:[],waiting_on_us:false,waiting_on:[],overdue:[],needs_attention:false,unlinked_participants:1,last_activity_at:1893456000,next_action:'核对需求'};
        deal.stage_label=labels[stages.indexOf(deal.stage)];templates(deal);event(deal,'创建案子');if(f.conversation_key)deal.conversations.push({id:next(),key:f.conversation_key,party_id:customer.id});state.deals.push(deal);
      } else if(d&&match[2]==='update') {if(f.stage)advance(d,f.stage);Object.assign(d,Object.fromEntries(Object.entries(f).filter(([k])=>k!=='operation_key'&&k!=='stage')));event(d,'更新案子资料');}
      else if(d&&match[2]==='participants') {const p=parties.find((p)=>String(p.id)===f.party_id);d.participants.push({...p,id:next(),party_id:p.id,role:f.role,state:'none',conversations:[],last_at:null});event(d,'添加参与方');}
      else if(d&&match[2]==='conversations') {const p=d.participants.find((p)=>String(p.party_id)===f.party_id),c=conversations.find((c)=>c.key===f.conversation_key);d.conversations.push({id:next(),key:c.key,party_id:p.party_id});p.conversations.push({...c,title:c.buyer_name});p.state='us';event(d,'关联会话');}
      else if(d&&match[2]==='items') {d.items.push({id:result.id,name:f.name,qty:Number(f.qty)});event(d,'添加产品');}
      else if(d&&match[2]==='tasks') {d.tasks[d.stage].push({id:result.id,title:f.title,status:'open',stage:d.stage});event(d,'添加待办');}
      else if(path.startsWith('deal-tasks/')) {const id=Number(path.split('/')[1]);const deal=state.deals.find((d)=>Object.values(d.tasks).flat().some((t)=>t.id===id));Object.values(deal.tasks).flat().find((t)=>t.id===id).status=f.status;event(deal,'完成待办');}
      else if(d&&match[2]==='events')event(d,f.note);
      else if(d&&match[2]==='quotes') {const quote={id:result.id,deal_id:d.id,round_no:state.quotes.filter((q)=>q.deal_id===d.id).length+1,status:'draft',currency:'USD',fx_rate:'7',fx_source:'虚构缓存',target_margin:'15%',incoterm:'DDP',destination_country:d.destination_country,valid_until:null,lines:d.items.map((i)=>({...i,qty:i.qty,offer_id:null,cost_unit:null,product_id:null,sell_unit_price:null,unit:'件'})),costs:[],warnings:[],terms:{},negotiations:[],parties,cost_sources:[],customer_conversations:[]};state.quotes.push(quote);calc(quote);event(d,'创建报价');}
      else if(q) {
        if(qm[2]==='update')Object.assign(q,f);
        else if(qm[2]==='terms')q.terms={...f,confirmed:true};
        else if(qm[2]?.match(/^lines\/\d+\/update$/)){const l=q.lines.find((l)=>String(l.id)===qm[2].split('/')[1]);if(f.product_id){const p=products.find((p)=>String(p.id)===f.product_id);Object.assign(l,{product_id:p.id,name:p.name,offer_id:p.id===1?1:null,cost_unit:p.id===1?'100.00':null,cost_validity:p.current_offer.validity,cost_valid_until:p.id===1?'2030-02-01':null});}Object.assign(l,f);if(f.qty)l.qty=Number(f.qty);}
        else if(qm[2]==='freight-options/choose')q.costs.push({id:result.id,kind:'freight',amount:'40.00',validity:'valid',valid_until:'2030-02-01',manual:0,calc:{mode:'air'},source_note:'虚构运价#13'});
        else if(qm[2]==='negotiations')q.negotiations.push({id:result.id,...f,party_id:Number(f.party_id),at});
        else if(qm[2]==='drafts'){if(calc(q).blocking.length)return reply({error:'成本过期或资料不全，拒绝起草'},409);result={...result,state:'draft',mode:'dry-run',text:`虚构${f.kind}：${q.currency} ${q.totals.revenue}`};state.outbox.push({...result,kind:f.kind,deal_id:q.deal_id,quote_id:q.id,created_at:at});const deal=state.deals.find((d)=>d.id===q.deal_id);deal.value=q.totals.revenue;deal.currency=q.currency;event(deal,'演练草稿已保存，未发送');}
        else throw Error('Missing quote mutation '+path);calc(q);
      }
      else if(d&&match[2]==='payments'){state.payments.push({id:result.id,deal_id:d.id,...f,amount_fen:Number(f.amount_fen),status:'pending'});event(d,'登记待核对收款');}
      else if(path.match(/^payments\/\d+\/confirm$/)){const p=state.payments.find((p)=>String(p.id)===path.split('/')[1]);if(f.confirm!=='1')return reply({error:'需要人工确认'},400);p.status='confirmed';const deal=state.deals.find((d)=>d.id===p.deal_id);deal.status='won';advance(deal,'paid');event(deal,'收到客户付款');}
      else if(d&&match[2]?.match(/^fulfilment\/[^/]+\/update$/)){const n=getNodes(d).find((n)=>n.node===match[2].split('/')[1]);Object.assign(n,f);if(f.status==='done')n.completed_on=f.completed_on||'2030-01-01';n.overdue=n.status!=='done'&&!!n.planned_on&&n.planned_on<'2030-01-01';event(d,'履约：'+n.label);}
      else if(d&&match[2]==='drafts'){result={...result,state:'draft',mode:'dry-run',text:'虚构采购单'};state.outbox.push({...result,kind:f.kind,deal_id:d.id,created_at:at});event(d,'采购单已存演练草稿');}
      else throw Error('Missing stateful mutation '+path);
      if(f.operation_key)state.receipts.set(f.operation_key,{signature,result});
      return reply(result);
    } catch(error){problems.push(error.message);return reply({error:error.message},501);}
  });
  return state;
}
