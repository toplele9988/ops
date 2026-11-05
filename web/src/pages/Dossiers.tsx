import { useMutation, useQuery } from "@tanstack/react-query";
import { FileText, Image as ImageIcon, ArrowRight, Package, AlertCircle, Factory } from "lucide-react";
import { useState } from "react";
import { Link, useParams, useSearchParams } from "react-router";
import { ApiError, copyText, get, postForm, qs } from "../api";
import { type Configuration, type Evidence, type Family, type FamilyDetail, type Gap, type Offer, type QuestionDraft, type Source, type Supplier, type SupplierDetail, gapTypes, known, priceKind, quoteStates, taxBases } from "../dossiers";
import { Chip, Empty, Pick, SearchField, Sheet, Skeleton } from "../ui/bits";
import "./Dossiers.css";

const API = "/api/v1/dossiers";
const read = <T,>(url: string) => get<T>(API + url);
function Failure({error, retry}: {error: Error | null; retry: () => void}) {
  return <div role="alert" className="ds-error"><AlertCircle size={20}/><div><b>{error instanceof ApiError && error.status === 404 ? "档案接口尚未部署或记录不存在" : "读取失败"}</b><p>{error?.message}。请重试。</p><button className="btn sm" onClick={retry}>重试</button></div></div>;
}
function Heading({title, text}: {title:string; text:string}) {
  return <><div className="page-head"><div><div className="ds-eyebrow">采购资料 · 带来源的档案</div><h1>{title}</h1><p>{text}</p></div></div><nav className="ds-tabs" aria-label="档案导航"><Link to="/supplier-dossiers">供应商档案</Link><Link to="/product-dossiers">产品档案</Link><Link to="/dossier-gaps">缺资料</Link></nav></>;
}
function useFilters() {
  const [params, setParams] = useSearchParams();
  const set = (key:string, value:string) => setParams(prev=>{const p=new URLSearchParams(prev);value?p.set(key,value):p.delete(key);if(key!=="page")p.delete("page");return p;});
  return {params,set};
}
function OcrBadge({candidate}: {candidate:boolean}) {
  return candidate?<Chip tone="orange" dot={false}>图片识别，待核对</Chip>:null;
}
function SourceEvidence({items,locator}: {items:Evidence[];locator?:string}) {
  const matching=locator?items.filter(e=>e.locator===locator):[];
  const selected=(matching.length?matching:items).slice(0,8),ids=new Set(selected.map(e=>e.evidence_id)),other=items.filter(e=>!ids.has(e.evidence_id));
  const row=(e:Evidence)=><div className={`ds-evidence${locator===e.locator?" selected":""}`} key={e.evidence_id}><b>{e.field_path}</b><p>{e.locator} · {e.evidence_state==="ocr_candidate"?<OcrBadge candidate/>:e.evidence_state}</p><blockquote>{e.excerpt}</blockquote></div>;
  return <>{selected.map(row)}{other.length>0&&<details><summary>展开该来源的其他 {other.length} 条字段证据</summary>{other.map(row)}</details>}</>;
}
function SourceTag({id, locator, label="来源", ocrCandidate=false}: {id:string; locator?:string; label?:string; ocrCandidate?:boolean}) {
  const [open,setOpen]=useState(false);
  const q=useQuery({queryKey:["dossier-source",id],queryFn:()=>read<Source>(`/sources/${encodeURIComponent(id)}`),enabled:open,retry:false});
  const [imageFailed, setImageFailed]=useState(false);
  return <><button className="ds-source" onClick={()=>setOpen(true)} aria-label={`查看${label} ${locator||id}`}><FileText size={12}/>{label}{locator && locator!=="text" ? ` · ${locator.split(":bbox")[0]}` : ""}</button><OcrBadge candidate={ocrCandidate}/>
    <Sheet open={open} title="核对来源" onClose={()=>setOpen(false)}>
      <button className="btn ds-source-close" onClick={()=>setOpen(false)}>关闭来源</button>
      {q.isLoading&&<Skeleton/>}{q.isError&&<Failure error={q.error} retry={()=>q.refetch()}/>}
      {q.data&&<div className="ds-source-body"><Chip>{q.data.kind}</Chip><OcrBadge candidate={q.data.evidence_state==="ocr_candidate"||q.data.evidence.some(e=>e.evidence_state==="ocr_candidate")}/><h3>{q.data.filename||"归档消息与证据"}</h3><p>来源时间：{known(q.data.source_at)}</p><p>位置：{locator||q.data.locator||"未知"}</p>
        {q.data.availability_note&&<div className="ds-note">{q.data.availability_note}</div>}
        {q.data.conversation_url&&<><p>会话：{q.data.conversation_id} · 时间：{q.data.message_at ? new Date(q.data.message_at*1000).toLocaleString("zh-CN",{timeZone:"Asia/Shanghai"}) : "未知"}（北京时间）</p><a className="btn sm" href={q.data.conversation_url}>打开原会话</a></>}
        {q.data.original_text&&<blockquote>{q.data.original_text}</blockquote>}
        {q.data.media_url&&q.data.mime?.startsWith("image/")&&!imageFailed&&<a href={q.data.media_url} target="_blank" rel="noreferrer"><img className="ds-original" src={q.data.media_url} alt="来源原图" onError={()=>setImageFailed(true)}/></a>}
        {imageFailed&&<p role="alert">原图加载失败，请重新核对来源。</p>}
        {q.data.media_url&&<a className="btn sm" href={q.data.media_url+(q.data.mime==="application/pdf"?`#page=${(locator||q.data.locator||"").match(/page:(\d+)/)?.[1]||1}`:"")} target="_blank" rel="noreferrer">{q.data.mime?.startsWith("image/")?"打开原图":q.data.mime==="application/pdf"?"打开 PDF 原文件对应页":"下载原文件，按上述页码或单元格核对"}</a>}
        <h3>逐字段证据</h3>{q.data.evidence.length ? <SourceEvidence items={q.data.evidence} locator={locator}/>:<p>尚无逐字段片段，请核对原件。</p>}
        <p className="t-cap">解析：{q.data.parse_state} · 整理：{q.data.extract_state}{q.data.failure_reason&&` · ${q.data.failure_reason}`}</p></div>}
    </Sheet></>;
}
function EvidenceTags({items}: {items:Evidence[]}) {
  return <span className="ds-tags">{items.map(e=><SourceTag key={e.evidence_id} id={e.source_id} locator={e.locator} label={e.field_path} ocrCandidate={e.evidence_state==="ocr_candidate"}/>)}</span>;
}
function Thumbnail({id}: {id:string|null}) {
  const [enabled,setEnabled]=useState(false),[failed,setFailed]=useState(false);
  const q=useQuery({queryKey:["dossier-source",id],queryFn:()=>read<Source>(`/sources/${encodeURIComponent(id!)}`),enabled:!!id&&enabled,retry:false});
  return <div className="ds-thumb" ref={node=>{if(!node||enabled)return;const observer=new IntersectionObserver(entries=>{if(entries.some(e=>e.isIntersecting)){setEnabled(true);observer.disconnect();}});observer.observe(node);return()=>observer.disconnect();}}>
    {q.data?.media_url&&q.data.mime?.startsWith("image/")&&!failed?<img src={q.data.media_url} alt="产品原图缩略" loading="lazy" onError={()=>setFailed(true)}/>:<><ImageIcon size={30}/><span>{id?"原图待核对":"暂无原图"}</span></>}
  </div>;
}
function Quote({offer}: {offer:Offer}) {
  return <article className="ds-quote"><div className="ds-quote-top"><b className="ds-price">{offer.amount_decimal!==null?offer.amount_decimal:"未报价"}</b><Chip tone="plain">{priceKind[offer.price_kind]||offer.price_kind}</Chip></div>
    {offer.amount_decimal===null&&<p>金额原文：{offer.amount_raw}</p>}
    <p className="ds-terms">币种 {known(offer.currency)} · 税制 {taxBases[offer.tax_basis]||known(offer.tax_basis)}{offer.tax_rate_text&&` / ${offer.tax_rate_text}`} · 单位 {known(offer.unit)}</p>
    <p className="ds-terms">报价日期 {known(offer.quoted_on)}{offer.document_period&&` · 文档期间 ${offer.document_period}`}<br/>有效期 {known(offer.valid_until)} · 状态 {quoteStates[offer.quote_state]||known(offer.quote_state)}</p>
    <SourceTag id={offer.source_id} locator={offer.locator} ocrCandidate={offer.evidence.some(e=>e.evidence_state==="ocr_candidate")}/>
    <details><summary>报价条件与字段证据</summary><p>金额原文：{offer.amount_raw}</p><p>MOQ {known(offer.moq_text)} · 数量条件 {known(offer.quantity_condition)} · 交期 {known(offer.lead_time_text)}</p><dl className="ds-config">{Object.entries(offer.conditions).map(([k,v])=><div key={k}><dt>{k}</dt><dd>{typeof v==="string"?v:JSON.stringify(v)}</dd></div>)}</dl><EvidenceTags items={offer.evidence}/></details>
  </article>;
}
function Config({item}: {item:Configuration}) {
  const conflicts=item.gaps.filter(g=>/conflict|correction|misfiled/.test(g.gap_type));
  return <div className="ds-config-cell"><Link to={`/product-dossiers/${item.family_key}`}><b>{item.canonical_model||item.display_name}</b></Link><p>{item.variant_label}</p><Chip tone={item.evidence_state==="原文明确"?"plain":"orange"}>{item.evidence_state}</Chip>{conflicts.length>0&&<p className="ds-conflict">冲突 / 待核实：{conflicts.map(g=>g.impact).join("；")}</p>}
    <details><summary>查看配置与来源</summary><dl className="ds-config">{Object.entries(item.config).map(([k,v])=><div key={k}><dt>{k}</dt><dd>{typeof v==="string"?v:JSON.stringify(v)}</dd></div>)}</dl><EvidenceTags items={item.evidence}/></details></div>;
}
function GapCards({gaps, limit=30}: {gaps:Gap[];limit?:number}) {
  const [all,setAll]=useState(false);
  return <><div className="ds-gap-list">{gaps.slice(0,all?gaps.length:limit).map(g=><article className="ds-gap" key={g.gap_id} data-testid="dossier-gap"><header><Chip tone={g.priority<=5?"orange":"plain"}>优先级 {g.priority}</Chip><span>{gapTypes[g.gap_type]||g.gap_type}</span></header>{g.supplier_name&&<Link className="ds-gap-supplier" to={`/supplier-dossiers/${g.supplier_id}`}>该问 {g.supplier_name}</Link>}{g.product_name&&<p className="t-cap">{g.product_name}</p>}<h3>{g.ask_text}</h3><p><b>为什么重要：</b>{g.impact}</p><div className="ds-tags">{g.source_refs.map(id=><SourceTag key={id} id={id}/>)}</div></article>)}</div>{gaps.length>limit&&!all&&<button className="btn" onClick={()=>setAll(true)}>展开其余 {gaps.length-limit} 项</button>}</>;
}
export function SupplierDossiers() {
  const {id}=useParams();
  return id?<SupplierView id={id}/>:<SupplierCards/>;
}
function SupplierCards() {
  const {params,set}=useFilters(),q=params.get("q")||"",type=params.get("type")||"";
  const list=useQuery({queryKey:["dossier-suppliers",q,type],queryFn:()=>read<{items:Supplier[];total:number}>(`/suppliers${qs({q,type})}`),retry:false});
  return <div className="ds-page"><Heading title="供应商档案" text="先选一家，核对它能供什么、报价条件和还缺的资料。"/><div className="ds-toolbar"><SearchField label="搜索供应商档案" placeholder="搜索供应商、主营品类" value={q} onSearch={v=>set("q",v)}/><Pick label="供应商类型" value={type} onChange={v=>set("type",v)} options={[{value:"",label:"全部类型"},...['产品','物流','配套'].map(v=>({value:v,label:v}))]}/><Link to="/suppliers">旧供应商资料与往来单位</Link></div>
    {list.isLoading&&<Skeleton/>}{list.isError&&<Failure error={list.error} retry={()=>list.refetch()}/>}{list.data&&<><p className="t-foot">{list.data.total} 家供应商 · 有价 / 缺价按配置统计，报价可用性请打开核对</p><div className="ds-card-grid">{list.data.items.map(s=><Link key={s.supplier_id} className="ds-supplier-card" to={`/supplier-dossiers/${s.supplier_id}`}><div className="ds-card-title"><span className="ds-factory"><Factory size={22}/></span><Chip tone="plain">{s.supplier_type}供应商</Chip></div><h2>{s.name}</h2><p className="ds-categories">{s.categories.join(" · ")||"主营品类待补"}</p><dl className="ds-stats"><div><dt>产品族</dt><dd>{s.family_count}</dd></div><div><dt>有价配置</dt><dd>{s.priced_count}</dd></div><div><dt>缺价配置</dt><dd>{s.unpriced_count}</dd></div></dl><div className="ds-card-bottom"><span>最新报价 {known(s.latest_quote)}</span><span className="ds-gap-count">缺资料 {s.gap_count} 项 <ArrowRight size={14}/></span></div></Link>)}</div>{!list.data.total&&<Empty title="没有符合条件的供应商">调整类型或搜索词后重试。</Empty>}</>}</div>;
}
function SupplierView({id}: {id:string}) {
  const q=useQuery({queryKey:["dossier-supplier",id],queryFn:()=>read<SupplierDetail>(`/suppliers/${encodeURIComponent(id)}`),retry:false});
  return <div className="ds-page"><Heading title={q.data?.name||"供应商档案"} text="先看配置与报价，点来源核实，再把缺资料合成问话草稿。"/><Link className="btn sm" to="/supplier-dossiers">返回供应商档案</Link>{q.isLoading&&<Skeleton/>}{q.isError&&<Failure error={q.error} retry={()=>q.refetch()}/>}{q.data&&<><div className="ds-detail-top"><div><Chip>{q.data.supplier_type}供应商</Chip><p>{q.data.categories.join(" · ")}</p></div><Link className="btn primary" to={`/dossier-gaps?supplier=${id}`}>补资料 / 生成问话草稿</Link></div><aside className="ds-note"><b>可靠性备注</b><p>{q.data.reliability_notes||"尚无可靠性备注，使用前请核对来源。"}</p><EvidenceTags items={q.data.evidence}/></aside><h2>产品族与配置 <span className="t-cap">{q.data.products.length} 个配置</span></h2>
      <div className="ds-table-wrap" tabIndex={0} aria-label="供应商配置与报价"><table className="ds-table"><thead><tr><th>产品与配置</th><th>报价 / 条件 / 来源</th><th>缺资料</th></tr></thead><tbody>{q.data.products.map(p=><tr key={p.product_id}><td><Config item={p}/></td><td>{p.offers.length?p.offers.map(o=><Quote key={o.offer_id} offer={o}/>):<span className="ds-unquoted">未报价</span>}</td><td><Link to={`/dossier-gaps?supplier=${id}`}>{p.gaps.length} 项待补</Link></td></tr>)}</tbody></table></div>
      {q.data.other_offers.length>0&&<section><h2>运费、费用与尚未关联配置的报价</h2><div className="ds-card-grid">{q.data.other_offers.map(o=><Quote key={o.offer_id} offer={o}/>)}</div></section>}<h2>缺资料清单 <span className="t-cap">{q.data.gaps.length} 项</span></h2><GapCards gaps={q.data.gaps} limit={6}/></>}</div>;
}
export function ProductDossiers() {
  const {id}=useParams();
  return id?<FamilyView id={id}/>:<FamilyCards/>;
}
function FamilyCards() {
  const {params,set}=useFilters(),q=params.get("q")||"",category=params.get("category")||"",page=Math.max(1,Number(params.get("page"))||1);
  const list=useQuery({queryKey:["dossier-families",q,category],queryFn:()=>read<{items:Family[];total:number;categories:string[]}>(`/families${qs({q,category})}`),retry:false});
  const rows=list.data?.items.slice((page-1)*24,page*24)||[];
  return <div className="ds-page"><Heading title="产品档案" text="按品类找产品，打开同款配置，逐家对照价格、条件和出处。"/><div className="ds-toolbar"><SearchField label="搜索产品档案" placeholder="搜索名称、型号、配置" value={q} onSearch={v=>set("q",v)}/><Pick label="产品品类" value={category} onChange={v=>set("category",v)} options={[{value:"",label:"全部品类"},...(list.data?.categories||[]).map(v=>({value:v,label:v}))]}/><Link to="/products">旧产品候选与核准工具</Link></div><p className="ds-note">仅有同款证据的配置才共用一行。币种、税制、数量条件未知时，价格仅供逐条核对。</p>{list.isLoading&&<Skeleton/>}{list.isError&&<Failure error={list.error} retry={()=>list.refetch()}/>}{list.data&&<>{[...new Set(rows.map(r=>r.category))].map(cat=><section key={cat}><h2>{cat||"品类待补"}</h2><div className="ds-card-grid">{rows.filter(r=>r.category===cat).map(f=><Link className="ds-product-card" to={`/product-dossiers/${f.family_key}`} key={f.family_key}><Thumbnail id={f.thumbnail_source_id}/><div className="ds-product-copy"><h3>{f.display_name}</h3><p>{f.configuration_count} 个配置 · {f.supplier_ids.length} 家供应商</p>{f.states.some(s=>s!=="原文明确")&&<Chip tone="orange">含待核实资料</Chip>}<p className="t-cap">缺资料 {f.gap_count} 项</p><span className="ds-next">查看配置与价格 <ArrowRight size={14}/></span></div></Link>)}</div></section>)}{!list.data.total&&<Empty title="没有符合条件的产品">换个品类或搜索词。</Empty>}<Pager page={page} total={list.data.total} size={24} set={v=>set("page",String(v))}/></>}</div>;
}
function Pager({page,total,size,set}: {page:number;total:number;size:number;set:(v:number)=>void}) {return <div className="pager"><button className="btn sm" disabled={page<=1} onClick={()=>set(page-1)}>上一页</button><span>{page} / {Math.max(1,Math.ceil(total/size))} · 共 {total} 项</span><button className="btn sm" disabled={page*size>=total} onClick={()=>set(page+1)}>下一页</button></div>;}
function FamilyView({id}: {id:string}) {
  const q=useQuery({queryKey:["dossier-family",id],queryFn:()=>read<FamilyDetail>(`/families/${encodeURIComponent(id)}`),retry:false});
  return <div className="ds-page"><Heading title={q.data?.display_name||"产品档案"} text="同一配置一行，每家报价一列。展开条件后再判断是否可比。"/><Link className="btn sm" to="/product-dossiers">返回产品档案</Link>{q.isLoading&&<Skeleton/>}{q.isError&&<Failure error={q.error} retry={()=>q.refetch()}/>}{q.data&&<><p className="ds-note">{q.data.category} · {q.data.configurations.length} 个配置 · {q.data.suppliers.length} 家供应商。未关联该配置的供应商显示“未确认供货”；历史报价不代表当前可下单。</p><div className="ds-table-wrap ds-comparison" tabIndex={0} aria-label="跨供应商配置价格对比"><table className="ds-table"><thead><tr><th>标准配置 / 待核实项</th>{q.data.suppliers.map(s=><th key={s.supplier_id}><Link to={`/supplier-dossiers/${s.supplier_id}`}>{s.name}</Link></th>)}</tr></thead><tbody>{q.data.configurations.map(p=><tr key={p.product_id}><td><Config item={p}/></td>{q.data.suppliers.map(s=><td key={s.supplier_id} data-supplier={s.supplier_id}><div className="ds-mobile-label">{s.name}</div>{p.offers.filter(o=>o.supplier_id===s.supplier_id).length?p.offers.filter(o=>o.supplier_id===s.supplier_id).map(o=><Quote key={o.offer_id} offer={o}/>):<p className="ds-unquoted">{p.suppliers.some(x=>x.supplier_id===s.supplier_id)?"未报价":"未确认供货"}</p>}</td>)}</tr>)}</tbody></table></div><h2>冲突与待补资料</h2><GapCards gaps={q.data.configurations.flatMap(p=>p.gaps)} limit={6}/></>}</div>;
}
function DraftPanel({supplier,priority,type}: {supplier:string;priority:string;type:string}) {
  const q=useQuery({queryKey:["dossier-drafts",supplier],queryFn:()=>read<{items:QuestionDraft[]}>(`/suppliers/${encodeURIComponent(supplier)}/drafts`),retry:false});
  const [saved,setSaved]=useState<QuestionDraft|null>(null),[notice,setNotice]=useState("");
  const mutation=useMutation({mutationFn:()=>postForm<QuestionDraft>(API+`/suppliers/${encodeURIComponent(supplier)}/drafts`,{priority,type}),onSuccess:d=>{setSaved(d);void q.refetch();setNotice("草稿已存档，未发送。");}});
  return <aside className="ds-draft-panel"><div><h2>向这家供应商补资料</h2><p>合并当前供应商、优先级和类型筛选下的全部缺口（包含其余分页），存为一条问话草稿。</p></div><button className="btn primary" disabled={mutation.isPending} onClick={()=>mutation.mutate()}>{mutation.isPending?"正在存档…":"生成问话草稿"}</button>{mutation.isError&&<p role="alert">{mutation.error.message}，请重试。</p>}<p role="status">{notice}</p>{saved&&<div className="ds-draft-result"><label htmlFor="ds-draft-text">已存档问话草稿 · {JSON.parse(saved.gap_ids_json).length} 项缺口</label><textarea id="ds-draft-text" rows={10} readOnly value={saved.text}/><button className="btn sm" onClick={async()=>setNotice(await copyText(saved.text)?"已复制草稿，未发送。":"复制失败，请手动选择文字。")}>复制草稿</button></div>}<details><summary>查看已存档草稿</summary>{q.isLoading&&<Skeleton/>}{q.isError&&<Failure error={q.error} retry={()=>q.refetch()}/>}{q.data?.items.length===0&&<p>尚无存档草稿。</p>}{q.data?.items.map(d=><details className="ds-archive-draft" key={d.draft_id}><summary>{new Date(d.created_at).toLocaleString("zh-CN")} · {JSON.parse(d.gap_ids_json).length} 项 · 未发送</summary><pre>{d.text}</pre></details>)}</details></aside>;
}
export function DossierGaps() {
  const {params,set}=useFilters(),supplier=params.get("supplier")||"",priority=params.get("priority")||"",type=params.get("type")||"",page=Math.max(1,Number(params.get("page"))||1);
  const suppliers=useQuery({queryKey:["dossier-suppliers","",""],queryFn:()=>read<{items:Supplier[]}>("/suppliers"),retry:false});
  const q=useQuery({queryKey:["dossier-gaps",supplier,priority,type,page],queryFn:()=>read<{items:Gap[];total:number;types:string[];priorities:number[]}>(`/gaps${qs({supplier,priority,type,page,page_size:30})}`),retry:false});
  return <div className="ds-page"><Heading title="缺资料" text="看清该问哪家、要什么、为什么重要；选一家生成问话草稿。"/><div className="ds-toolbar"><Pick label="该问哪家" value={supplier} onChange={v=>set("supplier",v)} options={[{value:"",label:"全部供应商"},...(suppliers.data?.items||[]).map(s=>({value:s.supplier_id,label:s.name}))]}/><Pick label="缺口优先级" value={priority} onChange={v=>set("priority",v)} options={[{value:"",label:"全部优先级"},...(q.data?.priorities||[]).map(p=>({value:String(p),label:`优先级 ${p}`}))]}/><Pick label="缺口类型" value={type} onChange={v=>set("type",v)} options={[{value:"",label:"全部缺口类型"},...(q.data?.types||[]).map(t=>({value:t,label:gapTypes[t]||t}))]}/></div>{suppliers.isError&&<Failure error={suppliers.error} retry={()=>suppliers.refetch()}/>}{supplier?<DraftPanel key={`${supplier}:${priority}:${type}`} supplier={supplier} priority={priority} type={type}/>:<div className="ds-note"><Package size={20}/> 先按“该问哪家”选一家，即可合并缺口、生成并存档问话草稿。</div>}{q.isLoading&&<Skeleton/>}{q.isError&&<Failure error={q.error} retry={()=>q.refetch()}/>}{q.data&&<><p className="t-foot">当前筛选 {q.data.total} 项 · 优先级数字越小越先问</p><GapCards gaps={q.data.items}/>{!q.data.total&&<Empty title="当前筛选没有缺资料项">可调整供应商或筛选条件。</Empty>}<Pager page={page} total={q.data.total} size={30} set={v=>set("page",String(v))}/></>}</div>;
}
