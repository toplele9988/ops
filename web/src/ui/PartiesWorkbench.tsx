import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router";
import { get, postForm, qs } from "../api";
import { formatTime } from "../format";
import { Empty, Sheet, Skeleton } from "./bits";
import { useToast } from "./Toast";
import { PageIntro } from "../pages/overview/PageIntro";
import { CounterpartTime } from "./CounterpartTime";
import "./parties.css";

type Kind = "customer" | "supplier" | "forwarder" | "vendor";
const KINDS: Record<Kind, string> = { customer: "客户", supplier: "供应商", forwarder: "货代", vendor: "配套供应商" };
const TERMS: Record<string, string> = { lead_time: "交货周期", moq: "起订量", payment: "付款方式", shipping_origin: "发货地", warranty: "质保", contact_note: "联系备注" };
const CHANNELS: Record<string, string> = { alibaba: "阿里国际站", wechat: "微信", whatsapp: "WhatsApp", website: "独立站", email: "邮箱" };
interface Contact { id: number; kind: Kind; name: string; country: string; timezone?: string; notes: string; tags: string; next_followup: string | null; last_contact: number | null; channels: string[]; todo_count: number; open_deals: number }
interface Listing { items: Contact[]; total: number; countries: string[]; channels: string[] }
interface Conversation { key: string; channel: string; name: string; party_id?: number | null }
interface Draft { id: number; conversation_key: string; text: string; reviewed_at: string | null; dry_run: number; state: string }
interface Product { id: number; name: string; model?: string; carton_l_cm?: number; carton_w_cm?: number; carton_h_cm?: number; gross_kg?: number; units_per_carton?: number; packing_ready: boolean; verified: boolean; current_offer?: { currency: string; price: string; quoted_on: string; valid_until?: string; validity: string } }
interface Detail extends Contact { terms: { id: number; field: string; value: string; status: string }[]; identities: { key: string; channel: string; name: string }[]; timeline: { id: string; at: number | string; summary: string; channel: string; conversation_key?: string; role?: string }[]; deals: { id: number; title: string; status: string; stage: string }[]; products: Product[]; pending_questions: string[]; merges: { id: number; source_name: string; at: string; undone_at: string | null }[]; merged_profiles: { name: string; notes: string; tags: string }[]; drafts: Draft[] }
interface Result { id: number; message: string; draft_id?: number; text?: string; deal_id?: number }
const API = "/api/v1/parties-ops";

function Field({ label, value, onChange, type = "text", maxLength = 200 }: { label: string; value: string; onChange: (v: string) => void; type?: string; maxLength?: number }) {
  return <label className="parties-field"><span>{label}</span><input type={type} maxLength={maxLength} value={value} onChange={e => onChange(e.target.value)} /></label>;
}
function Pick({ label, value, onChange, options }: { label: string; value: string; onChange: (v: string) => void; options: [string, string][] }) {
  return <label className="parties-field"><span>{label}</span><select aria-label={label} value={value} onChange={e => onChange(e.target.value)}>{options.map(([key, text]) => <option key={key} value={key}>{text}</option>)}</select></label>;
}

export function PartiesWorkbench({ initialKind }: { initialKind: "customer" | "supplier" }) {
  const client = useQueryClient();
  const toast = useToast();
  const [params, setParams] = useSearchParams();
  const { id: routeId } = useParams();
  const navigate = useNavigate();
  const latestParams = useRef(params);
  latestParams.current = params;
  const kind = (initialKind === "supplier" && ["forwarder", "vendor"].includes(params.get("kind") ?? "") ? params.get("kind") : initialKind) as Kind;
  const selectedKey = initialKind === "customer" ? "c" : "party";
  const chosen = params.get(selectedKey) ?? routeId ?? "";
  const [adding, setAdding] = useState(false);
  const [createKind, setCreateKind] = useState<Kind>(kind);
  const [name, setName] = useState("");
  const [country, setCountry] = useState("");
  const [conversation, setConversation] = useState("");
  const [feedback, setFeedback] = useState("");
  const [error, setError] = useState("");
  const [lastDeal, setLastDeal] = useState<number>();
  const request = useRef<{ payload: string; token: string } | null>(null);
  const set = (changes: Record<string, string>) => {
    const next = new URLSearchParams(latestParams.current);
    for (const [key, value] of Object.entries(changes)) value ? next.set(key, value) : next.delete(key);
    latestParams.current = next;
    if (routeId && selectedKey in changes) navigate(`${initialKind === "customer" ? "/customers" : "/suppliers"}?${next}`);
    else setParams(next);
  };
  const filters = Object.fromEntries(["q", "country", "channel", "contact", "open", "sort", "page"].map(key => [key, params.get(key) ?? ""]));
  const list = useQuery({ queryKey: ["parties-ops", "list", kind, filters], queryFn: () => get<Listing>(API + qs({ ...filters, kind, page_size: 40 })), retry: false });
  const convs = useQuery({ queryKey: ["parties-ops", "conversations"], queryFn: () => get<{ items: Conversation[] }>(API + "/conversations"), retry: false });
  const action = useMutation({
    mutationFn: async ({ path, fields }: { path: string; fields: Record<string, string> }) => {
      const payload = JSON.stringify([path, fields]);
      if (request.current?.payload !== payload) request.current = { payload, token: crypto.randomUUID() };
      return postForm<Result>(API + path, { ...fields, request_id: request.current.token });
    },
    onMutate: () => { setError(""); setFeedback(""); },
    onSuccess: async result => {
      request.current = null;
      setFeedback(result.message);
      toast(result.message);
      if (result.deal_id) setLastDeal(result.deal_id);
      await client.invalidateQueries({ queryKey: ["parties-ops"] });
    },
    onError: (e: Error) => { setError(e.message); toast(e.message, "error"); },
  });
  const mutate = (path: string, fields: Record<string, string>, success?: (r: Result) => void) => action.mutate({ path, fields }, { onSuccess: success });
  const filter = (key: string, value: string) => set({ [key]: value, page: "" });
  const changeKind = (next: Kind) => {
    const dest = next === "customer" ? "/customers" : "/suppliers";
    return `${dest}?${next === "customer" ? "" : `kind=${next}&`}${next === "customer" ? "c" : "party"}=`;
  };
  return <div className="parties-workbench">
    <div className="page-head"><div><h1 className="t-large">{KINDS[kind]}</h1><p>把会话归到同一家，安排跟进、核对资料，再把生意推进到案子。</p></div><button className="btn primary" onClick={() => { setCreateKind(kind); setAdding(true); }}>新建往来单位</button></div>
    <PageIntro page={initialKind === "customer" ? "customers" : "suppliers"} />
    <p className="parties-notice">消息均保存为演练草稿（dry-run），由人核对，尚未发送。</p>
    {feedback && <div role="status" className="parties-feedback">{feedback}{lastDeal && <Link to={`/deals/${lastDeal}`}>查看新案子</Link>}</div>}
    {error && <div role="alert" className="parties-error">{error}<button className="btn sm" onClick={() => { action.reset(); setError(""); }}>收起提示</button></div>}
    <div className="split" data-detail={Boolean(chosen)}>
      <div className="split-list parties-list">
        <div className="parties-filters">
          {initialKind === "supplier" && <Pick label="单位类型" value={kind} onChange={v => set({ kind: v, party: "", page: "" })} options={(["supplier", "forwarder", "vendor"] as Kind[]).map(k => [k, KINDS[k]])} />}
          <Field label={`搜索${KINDS[kind]}`} value={filters.q} onChange={v => filter("q", v)} />
          <Pick label="国家" value={filters.country} onChange={v => filter("country", v)} options={[["", "全部国家"], ...(list.data?.countries ?? []).map(c => [c, c] as [string, string])]} />
          <Pick label="来源渠道" value={filters.channel} onChange={v => filter("channel", v)} options={[["", "全部渠道"], ...[...new Set([...(list.data?.channels ?? []), "alibaba", "wechat", "whatsapp", "website"])].map(c => [c, CHANNELS[c] ?? c] as [string, string])]} />
          <Pick label="最近联系" value={filters.contact} onChange={v => filter("contact", v)} options={[["", "不限时间"], ["7", "最近7天"], ["30", "最近30天"], ["stale", "超过30天未联系"], ["never", "尚无联系记录"]]} />
          <Pick label="未结案子" value={filters.open} onChange={v => filter("open", v)} options={[["", "全部"], ["yes", "有未结案子"], ["no", "无未结案子"]]} />
          <Pick label="排序" value={filters.sort || "recent"} onChange={v => filter("sort", v)} options={[["recent", "最近联系优先"], ["oldest", "最久未联系优先"], ["tasks", "待办最多优先"], ["name", "按名称"], ["followup", "下次跟进优先"]]} />
          <button className="btn" onClick={() => set({ q: "", country: "", channel: "", contact: "", open: "", sort: "", page: "" })}>清除筛选</button>
        </div>
        {list.isLoading && <Skeleton />}
        {list.isError && <Empty title="没能读到往来单位" action={<button className="btn" onClick={() => list.refetch()}>重新读取</button>}>{list.error.message}</Empty>}
        {list.data?.items.length === 0 && <Empty title={Object.values(filters).some(Boolean) ? "没有符合条件的往来单位" : kind === "customer" ? "这里还没有客户：去商机沟通把会话归到客户" : `这里还没有${KINDS[kind]}`} action={<Link className="btn" to="/inbox">去商机沟通</Link>}>可以清除筛选，或新建往来单位并关联已有会话。</Empty>}
        <div className="group">{list.data?.items.map(c => <div className="row parties-row" key={c.id}>
          <div className="row-main"><div className="row-title">{c.name}</div><div className="row-sub">{c.country || "国家未填"} · {c.channels.map(ch => CHANNELS[ch] ?? ch).join("、") || "尚未关联会话"}</div><div className="row-sub">最后联系：{formatTime(c.last_contact) || "暂无"}</div><div className="row-sub">待办 {c.todo_count} 项 · 未结案子 {c.open_deals} 个{c.next_followup ? ` · 跟进 ${c.next_followup}` : ""}</div></div>
          <button className="btn sm" aria-label={`处理${c.name}`} onClick={() => { set({ [selectedKey]: String(c.id) }); setFeedback(""); setError(""); setLastDeal(undefined); }}>查看并处理</button>
        </div>)}</div>
        {list.data && list.data.total > 40 && <div className="pager"><button className="btn" disabled={Number(filters.page || 1) <= 1} onClick={() => set({ page: String(Number(filters.page || 1) - 1) })}>上一页</button><span>{filters.page || 1} / {Math.ceil(list.data.total / 40)}</span><button className="btn" disabled={Number(filters.page || 1) * 40 >= list.data.total} onClick={() => set({ page: String(Number(filters.page || 1) + 1) })}>下一页</button></div>}
      </div>
      <div className="split-detail">{chosen ? <ContactDetail key={chosen} id={chosen} onBack={() => set({ [selectedKey]: "" })} mutate={mutate} pending={action.isPending} error={error} conversations={convs.data?.items ?? []} /> : <Empty title={`选择一位${KINDS[kind]}开始处理`} action={<button className="btn" onClick={() => setAdding(true)}>建立新资料</button>}>点“查看并处理”，可跟进、关联会话或核对资料。</Empty>}</div>
    </div>
    <Sheet open={adding} title="新建往来单位" onClose={() => setAdding(false)}>
      <div className="parties-form"><Pick label="往来单位类型" value={createKind} onChange={v => setCreateKind(v as Kind)} options={(Object.keys(KINDS) as Kind[]).map(k => [k, KINDS[k]])} /><Field label="单位名称" value={name} onChange={setName} /><Field label="所在国家" value={country} onChange={setCountry} maxLength={100} />
        <Pick label="关联现有会话" value={conversation} onChange={setConversation} options={[["", "暂不关联，稍后补充"], ...(convs.data?.items ?? []).filter(c => !c.party_id).map(c => [c.key, `${CHANNELS[c.channel] ?? c.channel} · ${c.name}`] as [string, string])]} />
        {convs.isError && <p role="alert">会话读取失败，暂可建档，稍后重新关联。<button className="btn" onClick={() => convs.refetch()}>重读会话</button></p>}
        <p>同名客户不会自动合并；已属于其他单位的会话不会被抢走。</p>
        {error && <p role="alert">{error}</p>}<div className="sheet-actions"><button className="btn" onClick={() => setAdding(false)}>取消</button><button className="btn primary" disabled={!name.trim() || action.isPending} onClick={() => mutate("", { kind: createKind, name, country, conversation_key: conversation }, r => { setAdding(false); setName(""); setCountry(""); setConversation(""); if ((createKind === "customer") === (initialKind === "customer")) set({ [selectedKey]: String(r.id), kind: createKind }); else window.location.assign("/app" + changeKind(createKind) + r.id); })}>保存并打开资料</button></div>
        {error && <p role="alert">{error}</p>}
      </div>
    </Sheet>
  </div>;
}

function ContactDetail({ id, onBack, mutate, pending, error, conversations }: { id: string; onBack: () => void; mutate: (path: string, fields: Record<string, string>, success?: (r: Result) => void) => void; pending: boolean; error: string; conversations: Conversation[] }) {
  const detail = useQuery({ queryKey: ["parties-ops", "detail", id], queryFn: () => get<Detail>(`${API}/${encodeURIComponent(id)}`), retry: false });
  const [editing, setEditing] = useState(false);
  const [form, setForm] = useState({ name: "", country: "", timezone: "", notes: "", tags: "", next_followup: "" });
  const [linkKey, setLinkKey] = useState("");
  const [draftKey, setDraftKey] = useState("");
  const [mergeOpen, setMergeOpen] = useState(false);
  const [source, setSource] = useState("");
  const [duplicateQuery, setDuplicateQuery] = useState("");
  const [dealOpen, setDealOpen] = useState(false);
  const [title, setTitle] = useState("");
  const [term, setTerm] = useState({ field: "lead_time", value: "" });
  const [draft, setDraft] = useState<{ id: number; text: string }>();
  const [draftOpen, setDraftOpen] = useState(false);
  const [draftError, setDraftError] = useState("");
  const duplicateList = useQuery({ queryKey: ["parties-ops", "merge-candidates", duplicateQuery], queryFn: () => get<Listing>(API + qs({ kind: "customer", page_size: 100, sort: "name", q: duplicateQuery })), enabled: mergeOpen, retry: false });
  if (detail.isLoading) return <Skeleton />;
  if (!detail.data) return <Empty title="没能打开资料" action={<><button className="btn" onClick={onBack}>返回列表</button><button className="btn" onClick={() => detail.refetch()}>重新读取</button></>}>{detail.error?.message}</Empty>;
  const p = detail.data;
  const actionKey = p.identities.some(i => i.key === draftKey) ? draftKey : p.identities[0]?.key || "";
  const act = (op: string, fields: Record<string, string>, success?: (r: Result) => void) => mutate(`/${id}/${op}`, fields, success);
  const makeDraft = (purpose: string) => {
    if (!actionKey) { setDraftError("请先关联会话，再起草消息。"); return; }
    setDraftError("");
    act("draft", { purpose, conversation_key: actionKey }, r => { setDraft({ id: r.draft_id!, text: r.text! }); setDraftOpen(true); });
  };
  return <article aria-label={`${p.name}资料`}>
    <button className="btn plain back" onClick={onBack}>返回{KINDS[p.kind]}列表</button>
    <section className="group parties-card"><h2 className="t-title">{p.name}</h2><CounterpartTime country={p.country} timezone={p.timezone} /><p>{p.country || "国家未填"} · 待办 {p.todo_count} 项 · 下次跟进：{p.next_followup || "待安排"}</p><p>标签：{p.tags || "暂无"}</p><p className="parties-wrap">备注：{p.notes || "暂无"}</p>
      {p.merged_profiles.map(m => <p key={m.name} className="parties-wrap">已合入 {m.name} · 原标签：{m.tags || "暂无"} · 原备注：{m.notes || "暂无"}</p>)}
      <div className="parties-actions"><button className="btn" onClick={() => { setForm({ name: p.name, country: p.country, timezone: p.timezone ?? "", notes: p.notes, tags: p.tags, next_followup: p.next_followup ?? "" }); setEditing(true); }}>编辑资料与跟进日期</button>
        {p.kind === "customer" && <><button className="btn" onClick={() => setMergeOpen(true)}>合并重复客户</button><button className="btn" onClick={() => { setTitle(`${p.name}的采购需求`); setDealOpen(true); }}>转为案子</button><Link className="btn" to={"/deals" + qs({ new: "1", customer: p.id, destination_country: p.country })}>新建案子（填写产品）</Link></>}
      </div>
    </section>
    <section className="block"><h3 className="group-title">渠道身份与现有会话</h3><div className="group parties-card">
      {p.identities.length === 0 && <p>还没有关联会话；先选择下方已有会话，再开始跟进。</p>}
      {p.identities.map(i => <div className="row parties-row" key={i.key}><div className="row-main parties-wrap">{CHANNELS[i.channel] ?? i.channel} · {i.name}</div><Link className="btn sm" to={`/inbox?conversation=${encodeURIComponent(i.key)}&role=all`}>查看会话</Link></div>)}
      <div className="parties-actions"><Pick label="待关联会话" value={linkKey} onChange={setLinkKey} options={[["", "选择一个现有会话"], ...conversations.filter(c => !c.party_id).map(c => [c.key, `${CHANNELS[c.channel] ?? c.channel} · ${c.name}`] as [string, string])]} /><button className="btn" disabled={!linkKey || pending} onClick={() => act("link", { conversation_key: linkKey }, () => setLinkKey(""))}>关联到本单位</button></div>
      {p.identities.length > 0 && <Pick label="草稿发往会话" value={actionKey} onChange={setDraftKey} options={p.identities.map(i => [i.key, `${CHANNELS[i.channel] ?? i.channel} · ${i.name}`])} />}
      <div className="parties-actions">{p.kind === "customer" ? <button className="btn primary" disabled={pending} onClick={() => makeDraft("followup")}>一键起草跟进</button> : <><button className="btn primary" disabled={pending} onClick={() => makeDraft("inquiry")}>一键询价</button><button className="btn" disabled={pending} onClick={() => makeDraft("materials")}>催资料</button></>}</div>
      {draftError && <p role="alert">{draftError}</p>}
    </div></section>
    {p.kind !== "customer" && <section className="block"><h3 className="group-title">产品、最近报价与包装核对</h3><div className="group">
      {p.products.length === 0 && <Empty title="还没有产品资料" action={<button className="btn" disabled={pending} onClick={() => makeDraft("materials")}>起草索取产品资料</button>}>先向对方索取产品与包装资料。</Empty>}
      {p.products.map(x => <div className="parties-product" key={x.id}><h4>{x.name} {x.model}</h4><p>外箱：{[x.carton_l_cm, x.carton_w_cm, x.carton_h_cm].every(v => v != null) ? `${x.carton_l_cm}×${x.carton_w_cm}×${x.carton_h_cm} cm` : "缺尺寸"} · 毛重：{x.gross_kg ?? "未填"} kg · 每箱 {x.units_per_carton ?? "未填"} 件</p><p>{x.current_offer ? `${x.current_offer.currency} ${x.current_offer.price} · 报价日 ${x.current_offer.quoted_on} · ${x.current_offer.valid_until ? `有效至 ${x.current_offer.valid_until}` : "未写有效期"}${["expired", "no_expiry"].includes(x.current_offer.validity) ? " · 需重新询价" : ""}` : "还没有报价"}</p><p role="status">包装核对：{x.verified ? "已核对" : x.packing_ready ? "资料齐全，待人工核对" : "资料缺失，待补齐"}</p>
        <div className="parties-actions"><Link className="btn sm" to={`/products?supplier=${p.id}&open=${x.id}`}>补充产品资料</Link><button className="btn sm" disabled={pending || !x.packing_ready || x.verified} onClick={() => { if (window.confirm(`确认已对照来源核对“${x.name}”的外箱三边、毛重和每箱数量？此结果将供核价使用。`)) act("verify", { product_id: String(x.id), confirm: "1" }); }}>标记资料已核</button><button className="btn sm" disabled={pending} onClick={() => makeDraft("inquiry")}>起草询价</button></div>
      </div>)}
    </div><h3 className="group-title">待询问事项</h3><div className="group parties-card">{p.pending_questions.length ? p.pending_questions.map((s, i) => <div className="row parties-row" key={i}><span className="row-main">{s}</span><button className="btn sm" disabled={pending} onClick={() => makeDraft("materials")}>起草追问</button></div>) : <p>当前产品没有待补的报价或包装资料。</p>}</div></section>}
    {p.kind !== "customer" && <section className="block"><h3 className="group-title">交期、起订量和付款条款</h3><div className="group parties-card">
      {(p.terms ?? []).map(t => <div className="row parties-row" key={t.id}><div className="row-main"><strong>{TERMS[t.field] ?? t.field}</strong><p>{t.value} · {t.status === "confirmed" ? "已核对" : t.status === "suggested" ? "待核对" : "已否决"}</p></div>{t.status === "suggested" && <div className="parties-actions"><button className="btn sm" disabled={pending} onClick={() => act("term-status", { term_id: String(t.id), action: "confirm" })}>采用条款</button><button className="btn sm" disabled={pending} onClick={() => act("term-status", { term_id: String(t.id), action: "reject" })}>否决条款</button></div>}</div>)}
      {!(p.terms ?? []).length && <p>还没有合作条款，向对方问清楚后记录在这里。</p>}
      <div className="parties-actions"><Pick label="条款类型" value={term.field} onChange={field => setTerm({ ...term, field })} options={Object.entries(TERMS)} /><Field label="条款内容" value={term.value} onChange={value => setTerm({ ...term, value })} maxLength={2000} /><button className="btn" disabled={pending || !term.value.trim()} onClick={() => act("term", term, () => setTerm({ ...term, value: "" }))}>记录合作条款</button></div>
    </div></section>}
    <section className="block"><h3 className="group-title">关联案子</h3><div className="group">{p.deals.map(d => <div className="row parties-row" key={d.id}><span className="row-main">{d.title} · {({ open: "进行中", paused: "暂停", won: "已成交", lost: "已丢单" } as Record<string, string>)[d.status] ?? d.status}</span><Link className="btn sm" to={`/deals/${d.id}`}>推进案子</Link></div>)}{p.deals.length === 0 && <Empty title="还没有关联案子" action={p.kind === "customer" ? <button className="btn" onClick={() => { setTitle(`${p.name}的采购需求`); setDealOpen(true); }}>新建采购案子</button> : <Link className="btn" to="/deals">去案子关联参与方</Link>} />}</div></section>
    <section className="block"><h3 className="group-title">演练发件箱草稿</h3><div className="group">{p.drafts.map(d => <div className="parties-product" key={d.id}><p className="parties-wrap">{d.text}</p><p>演练（dry-run） · {d.reviewed_at ? "人工已核对" : "待人工核对"} · 尚未发送</p><button className="btn sm" onClick={() => { setDraft({ id: d.id, text: d.text }); setDraftOpen(true); }}>编辑并人工核对草稿</button></div>)}{p.drafts.length === 0 && <p className="parties-card">还没有草稿，可点击上方跟进或询价按钮。</p>}</div></section>
    <section className="block" aria-label="往来时间线"><h3 className="group-title">全部往来时间线</h3><div className="group"><ol className="contact-timeline">{p.timeline.map(e => <li key={e.id}><time>{formatTime(e.at)}</time><span>{CHANNELS[e.channel] ?? e.channel} · {e.role === "buyer" ? "对方" : e.role === "seller" ? "我方" : "记录"}</span><p className="parties-wrap">{e.summary || "附件或空消息，请进入会话查看"}</p>{e.conversation_key && <Link className="btn sm" to={`/inbox?conversation=${encodeURIComponent(e.conversation_key)}`}>查看并回复</Link>}</li>)}{p.timeline.length === 0 && <li>还没有往来记录。<Link to="/inbox">去商机沟通关联会话</Link></li>}</ol></div></section>
    {p.merges.length > 0 && <section className="block"><h3 className="group-title">合并与撤销记录</h3><div className="group">{p.merges.map(m => <div className="row parties-row" key={m.id}><span className="row-main">{m.source_name} · {formatTime(m.at)} · {m.undone_at ? "已撤销" : "已合入"}</span>{!m.undone_at && <button className="btn sm" disabled={pending} onClick={() => { if (window.confirm("撤销后恢复两位客户独立展示；合并期间新增记录留在保留客户名下。确认撤销？")) act("undo", { merge_id: String(m.id), confirm: "1" }); }}>撤销这次合并</button>}</div>)}</div></section>}
    <Sheet open={editing} title="编辑资料与跟进日期" onClose={() => setEditing(false)}><div className="parties-form"><Field label="单位名称" value={form.name} onChange={name => setForm({ ...form, name })} /><Field label="所在国家" value={form.country} onChange={country => setForm({ ...form, country })} maxLength={100} /><Field label="对方时区（IANA，可留空）" value={form.timezone} onChange={timezone => setForm({ ...form, timezone })} maxLength={100} /><Field label="标签（逗号分隔）" value={form.tags} onChange={tags => setForm({ ...form, tags })} maxLength={300} /><Field label="下次跟进日期" type="date" value={form.next_followup} onChange={next_followup => setForm({ ...form, next_followup })} /><label className="parties-field">备注<textarea rows={4} maxLength={2000} value={form.notes} onChange={e => setForm({ ...form, notes: e.target.value })} /></label>{error && <p role="alert">{error}</p>}<div className="sheet-actions"><button className="btn" onClick={() => setEditing(false)}>取消</button><button className="btn primary" disabled={pending || !form.name.trim()} onClick={() => act("update", form, () => setEditing(false))}>保存资料</button></div></div></Sheet>
    <Sheet open={mergeOpen} title="合并重复客户" onClose={() => setMergeOpen(false)}><p>保留“{p.name}”。另一位客户的身份、往来与案子合并展示；原资料保留，可在合并记录中撤销。</p><p>合并期间新增记录归保留客户，撤销时仍留在保留客户。</p><Field label="搜索重复客户" value={duplicateQuery} onChange={v => { setDuplicateQuery(v); setSource(""); }} /><Pick label="要合入的客户" value={source} onChange={setSource} options={[["", "请选择重复客户"], ...(duplicateList.data?.items ?? []).filter(c => c.id !== p.id).map(c => [String(c.id), c.name] as [string, string])]} />{duplicateList.data && duplicateList.data.total > 100 && <p>符合条件的客户超过100位，请在上方缩小搜索范围再选择。</p>}{duplicateList.isError && <p role="alert">候选客户读取失败<button className="btn" onClick={() => duplicateList.refetch()}>重试</button></p>}{error && <p role="alert">{error}</p>}<div className="sheet-actions"><button className="btn" onClick={() => setMergeOpen(false)}>取消</button><button className="btn primary" disabled={!source || pending} onClick={() => { if (window.confirm(`将选中的重复客户合入“${p.name}”，会话和案子将统一展示，可撤销。确认合并？`)) act("merge", { source_id: source, confirm: "1" }, () => { setMergeOpen(false); setSource(""); }); }}>确认合并客户</button></div></Sheet>
    <Sheet open={dealOpen} title="转为案子" onClose={() => setDealOpen(false)}><Field label="案子名称" value={title} onChange={setTitle} /><p>会生成询盘待办，带入客户与已关联会话。</p>{error && <p role="alert">{error}</p>}<div className="sheet-actions"><button className="btn" onClick={() => setDealOpen(false)}>取消</button><button className="btn primary" disabled={!title.trim() || pending} onClick={() => act("deal", { title }, () => setDealOpen(false))}>创建采购案子</button></div></Sheet>
    <Sheet open={draftOpen} title="人工核对演练草稿" onClose={() => setDraftOpen(false)}><p>演练（dry-run）：只存草稿，确认后也不会外发。</p><label className="parties-field">消息内容<textarea rows={7} maxLength={2000} value={draft?.text ?? ""} onChange={e => setDraft(draft ? { ...draft, text: e.target.value } : undefined)} /></label>{error && <p role="alert">{error}</p>}<div className="sheet-actions"><button className="btn" onClick={() => setDraftOpen(false)}>稍后核对</button><button className="btn primary" disabled={pending || !draft?.text.trim()} onClick={() => { if (draft && window.confirm("确认已核对收件会话与内容？这里只记录人工核对，演练草稿尚未发送。")) act("review", { draft_id: String(draft.id), text: draft.text, confirm: "1" }, () => setDraftOpen(false)); }}>确认演练草稿</button></div></Sheet>
  </article>;
}
