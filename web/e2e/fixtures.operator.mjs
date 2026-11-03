// Fictional browser state only. Unknown API calls never reach a backend.
const at = Date.parse('2030-01-01T10:00:00Z') / 1000;
export async function installOperatorFixtures(page) {
  const profiles = {
    'fictional-supplier': {name:'虚构供应商',type:'supplier_product',channel:'wechat',timezone:'Asia/Shanghai',language:'zh',tone:'professional',hourly_limit:6,daily_limit:20,interval_seconds:30},
    'fictional-buyer': {name:'虚构客户',type:'customer_wholesale',channel:'whatsapp',timezone:'America/Los_Angeles',language:'en',tone:'professional',hourly_limit:6,daily_limit:20,interval_seconds:30},
  };
  const make = (id, kind, key, parts) => ({id,event_id:'fictional-event',kind,contact_key:key,level:kind==='ask_supplier'?1:2,state:'dry_run',reason:'master_off',due_at:null,created_at:at,dry_run:1,rehearsal:{who:profiles[key].name,type:profiles[key].type,trigger:"新入站消息",why:"虚构验收：资料缺口",sources:[{label:"触发消息",ref:"fictional-message",conversation_key:key}],would_send_at:at,timing_reason:"authorized"},
    data:{parts,fact_ids:[],context:{contacts:structuredClone(profiles),deal:{id:1,title:'虚构灯具询盘'}}}});
  const state = {settings:{enabled:false,dry_run:true,levels:{},types:{},contacts:{}},profiles,
    actions:[make(1,'ask_supplier','fictional-supplier',['您好，请核对本次询盘的产品规格、装箱资料、现价、报价有效期与交期。','4 × 虚构灯具']),
      make(2,'reply_customer','fictional-buyer',['Thank you for your message. I am checking the details and will follow up.']),
      {...make(3,'reply_customer','fictional-buyer',['Fictional uncertain send']),state:'unknown',reason:'bridge_unknown'},
      {...make(4,'ask_supplier','fictional-supplier',['您好，麻烦补充产品规格与装箱资料。']),reason:'dossier_rehearsal',rehearsal:{who:'虚构档案厂家',type:'supplier_product',trigger:'供应商高优先级资料缺口',why:'规格和装箱资料缺失',sources:[{label:'档案引用',ref:'fictional-sheet'}],would_send_at:null,timing_reason:'contact_binding_required'},data:{parts:['您好，麻烦补充产品规格与装箱资料。'],fact_ids:[],rehearsal_only:true,payload:{gaps:[{id:'g1',kind:'规格',impact:'缺少规格资料'},{id:'g2',kind:'装箱',impact:'缺少装箱资料'}]},context:{contacts:structuredClone(profiles)}}}],
    decisions:[{id:1,event_id:'fictional-event',action_id:1,at,phase:'decision',data:{trigger:{id:'fictional-event',kind:'inbound'},context:{source:'fictional-product-sheet'},model:{actual_model:'fictional-qwen'},output:{intent:'inquiry'},policy:'all off'}}],
    unresolved_events:[],transport:'fake',types:['customer_project','customer_wholesale','customer_end','customer_other','supplier_product','supplier_logistics','supplier_support'],
    posts:[],fakeBridge:[],fail:false,receipts:new Map()};
  await page.route('**/api/**',async route=>{
    const request=route.request(),url=new URL(request.url()),path=url.pathname;
    const reply=(data,status=200)=>route.fulfill({status,json:structuredClone(data)});
    if(path==='/api/v1/inbox/summary')return reply({ready:true,waiting_total:0,by_channel:[]});
    if(path!=='/api/v1/operator'&&!path.startsWith('/api/v1/operator/'))throw Error('Unexpected API '+path);
    if(request.method()==='GET') {
      if(state.fail)return reply({error:'虚构读取失败'},503);
      const {posts,fakeBridge,fail,receipts,...view}=state;void posts;void fakeBridge;void fail;void receipts;
      return reply(view);
    }
    const fields=Object.fromEntries(new URLSearchParams(request.postData()??''));
    state.posts.push({path,fields});
    if(!fields.operation_key)return reply({error:'missing receipt'},400);
    if(state.receipts.has(fields.operation_key))return reply(state.receipts.get(fields.operation_key));
    let result={ok:true};
    if(path.endsWith('/settings')) {
      const enabled=fields.enabled==='1';
      if(fields.scope==='master')state.settings[fields.key]=enabled;
      else state.settings[fields.scope][fields.key]=enabled;
      if(!state.settings.enabled||state.settings.dry_run)for(const a of state.actions)if(['ready','scheduled','pending','dry_run'].includes(a.state)){a.state='dry_run';a.reason='master_stopped';}
    } else if(path.endsWith('/profiles')) {
      const {conversation_key,operation_key,...values}=fields;void operation_key;
      state.profiles[conversation_key]={...state.profiles[conversation_key],...values};
    } else {
      const match=path.match(/actions\/(\d+)\/(approve|edit|reject)$/);
      if(!match)return reply({error:'unsupported'},404);
      const a=state.actions.find(a=>a.id===Number(match[1]));
      if(match[2]==='reject'){a.state='rejected';a.reason='owner_rejected';}
      if(match[2]==='edit'){a.data.parts=fields.text.split('\n\n');a.state='pending';a.reason='owner_edited';}
      if(match[2]==='approve') {
        if(!state.settings.enabled||state.settings.dry_run)return reply({error:'演练模式不能发送'},409);
        if(a.state==='unknown')return reply({error:'结果未知不可重试'},409);
        state.fakeBridge.push({action_id:a.id,channel:state.profiles[a.contact_key].channel,text:a.data.parts.join('\n')});
        a.state='sent';a.reason='bridge_sent';result={ok:true,action:structuredClone(a)};
      }
    }
    state.decisions.unshift({id:state.decisions.length+1,event_id:null,action_id:null,at,phase:'owner',data:{path,fields}});
    state.receipts.set(fields.operation_key,result);
    return reply(result);
  });
  return state;
}
