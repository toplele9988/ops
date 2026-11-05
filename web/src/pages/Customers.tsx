import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useId, useRef, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router";
import { MessageCircle, MessageSquare, Store } from "lucide-react";
import { ApiError, get, postForm, qs } from "../api";
import { readProfile, readProfiles, type ConversationProfile, type CustomerStage, type ProfileTime } from "../profiles";
import { Chip, Empty, Pick, SearchField } from "../ui/bits";
import { PartiesWorkbench } from "../ui/PartiesWorkbench";
import "./Customers.css";

const API = "/api/v1/parties-ops";
const WAITING = "AI 还在读聊天记录，读完会自动出现";
const STAGES: { key: CustomerStage; label: string; hint: string }[] = [
  { key: "potential", label: "潜在客户", hint: "问过价 / 问过产品" },
  { key: "quoted", label: "已报价", hint: "已给客户报过价" },
  { key: "customer", label: "老客户", hint: "成交过，有过生意" },
  { key: "after_sales", label: "售后中", hint: "正在处理售后" },
  { key: "dormant", label: "沉睡", hint: "90 天没往来" },
];
const RECORDS = ["customers", "records"];
const CONVERSATIONS = ["customers", "conversations"];
interface CustomerRecord {
  id: number; name: string; kind: string; country: string; last_contact: number | null;
  channels: string[]; notes: string; tags: string; next_followup: string | null; todo_count: number; open_deals: number;
}
interface Conversation { key: string; channel: string; name: string; party_id: number | null }
interface CustomerRow {
  key: string; name: string; record?: CustomerRecord; profiles: ConversationProfile[];
  stage: CustomerStage | null; last: ProfileTime; blocked?: string;
}

// Old archives may use conversation_ref. The real detail projects that into identities,
// while /conversations only projects rows in the identities table. Never guess by name.
async function resolveLegacyLinks(records: CustomerRecord[], profiles: ConversationProfile[], conversations: Conversation[]) {
  const links = new Map(conversations.map(c => [c.key, c]));
  const unresolved = profiles.filter(p => !links.get(p.conversation_key)?.party_id);
  if (!unresolved.length) return { items: conversations };
  const keys = new Set(unresolved.map(p => p.conversation_key));
  const candidates = records.filter(r => r.channels.some(channel => unresolved.some(p => p.channel === channel)));
  let cursor = 0;
  await Promise.all(Array.from({ length: Math.min(4, candidates.length) }, async () => {
    while (cursor < candidates.length) {
      const record = candidates[cursor++];
      const detail = await get<{ identities: { key: string; channel: string; name: string }[] }>(`${API}/${record.id}`);
      for (const identity of detail.identities) {
        if (keys.has(identity.key) && !links.get(identity.key)?.party_id) links.set(identity.key, { ...identity, party_id: record.id });
      }
    }
  }));
  return { items: [...links.values()] };
}

// The existing list is paginated (maximum 100). Join identities after loading all pages.
async function readRecords(): Promise<CustomerRecord[]> {
  const items: CustomerRecord[] = [];
  for (let page = 1; ; page++) {
    const result = await get<{ items: CustomerRecord[]; total: number }>(API + qs({ kind: "customer", page_size: 100, page }));
    items.push(...result.items);
    if (items.length >= result.total) return items;
    if (!result.items.length) throw new Error("客户档案还没读完整，请重试");
  }
}
function timestamp(value: ProfileTime | undefined): number {
  if (value === null || value === undefined || value === "") return 0;
  if (typeof value === "number") return Number.isFinite(value) ? value < 1e12 ? value * 1000 : value : 0;
  const normalized = /^\d{4}-\d\d-\d\d[T ]\d\d:\d\d(:\d\d(\.\d+)?)?$/.test(value) ? value.replace(" ", "T") + "Z" : value;
  return Date.parse(normalized) || 0;
}
const dateFormat = new Intl.DateTimeFormat("zh-CN", { timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false });
function ContactTime({ at }: { at: ProfileTime }) {
  const time = timestamp(at);
  if (!time) return <span>最近往来时间未知</span>;
  const seconds = Math.max(0, (Date.now() - time) / 1000);
  const relative = seconds < 60 ? "刚刚" : seconds < 3600 ? `${Math.floor(seconds / 60)} 分钟前` : seconds < 86400 ? `${Math.floor(seconds / 3600)} 小时前` : `${Math.floor(seconds / 86400)} 天前`;
  return <time dateTime={new Date(time).toISOString()}>{dateFormat.format(time)} 北京时间 · {relative}</time>;
}
const regions = new Intl.DisplayNames(["zh-CN"], { type: "region" });
function Country({ profile, fallback }: { profile?: ConversationProfile; fallback?: string }) {
  const raw = profile?.country || fallback || "";
  const iso = /^[a-z]{2}$/i.test(raw) ? raw.toUpperCase() : "";
  const translated = iso ? regions.of(iso) : "";
  const name = profile?.country_name || (translated && translated !== iso ? translated : iso ? "" : raw) || "国家待确认";
  const flag = iso && !["ZZ", "XX"].includes(iso) ? String.fromCodePoint(...[...iso].map(c => c.charCodeAt(0) + 127397)) : "🌐";
  return <span><span aria-hidden="true">{flag}</span> {name}</span>;
}
function Channel({ channel }: { channel: string }) {
  const Icon = channel === "alibaba" ? Store : channel === "wechat" ? MessageSquare : MessageCircle;
  const label = ({ alibaba: "阿里国际站", wechat: "微信", whatsapp: "WhatsApp", email: "邮箱", website: "独立站" } as Record<string, string>)[channel] || "其他渠道";
  return <span className="customers-channel"><Icon size={15} aria-hidden="true" />{label}</span>;
}
function joinCustomers(profiles: ConversationProfile[], records: CustomerRecord[], conversations: Conversation[]): CustomerRow[] {
  const owners = new Map(conversations.map(c => [c.key, c.party_id]));
  const rows = new Map<string, CustomerRow>(records.map(record => [`party-${record.id}`, { key: `party-${record.id}`, name: record.name, record, profiles: [], stage: null, last: record.last_contact }]));
  for (const profile of profiles) {
    if (profile.kind !== "customer") continue;
    const owner = owners.get(profile.conversation_key);
    const existing = owner ? rows.get(`party-${owner}`) : undefined;
    const row: CustomerRow = existing || { key: `chat-${profile.conversation_key}`, name: profile.org_name || profile.people?.[0]?.name || profile.title || "待确认姓名", profiles: [], stage: null, last: null };
    row.profiles.push(profile);
    if (!existing) {
      if (owner) row.blocked = "会话已有归属，请到客户档案核对";
      else if (!owners.has(profile.conversation_key)) row.blocked = "会话暂不可用，请稍后刷新";
      rows.set(row.key, row);
    }
  }
  for (const row of rows.values()) {
    row.profiles.sort((a, b) => timestamp(b.last_at) - timestamp(a.last_at));
    const latest = Math.max(timestamp(row.last), ...row.profiles.map(p => timestamp(p.last_at)));
    row.last = latest || null;
    const usable = row.profiles.filter(p => p.state === "ready" || p.state === "partial");
    // A new inquiry alone does not undo a known purchase in another owned conversation.
    const knownStage = usable.find(p => p.customer_stage === "after_sales")?.customer_stage || usable.find(p => p.customer_stage === "customer")?.customer_stage || usable[0]?.customer_stage;
    row.stage = latest && Date.now() - latest >= 90 * 86400_000 ? "dormant" : STAGES.some(s => s.key === knownStage) ? knownStage! : null;
  }
  return [...rows.values()].sort((a, b) => timestamp(b.last) - timestamp(a.last));
}

function Evidence({ profile }: { profile: ConversationProfile }) {
  const detail = useQuery({ queryKey: ["profiles", "detail", profile.conversation_key], queryFn: () => readProfile(profile.conversation_key), retry: false });
  if (detail.isPending) return <p role="status">正在找出判断依据…</p>;
  if (detail.isError) return <p role="alert">依据暂时没读到。<button className="btn" onClick={() => detail.refetch()}>重试读取依据</button></p>;
  const p = detail.data;
  if (!p) return <p>{WAITING}</p>;
  return <section className="customers-evidence-group" aria-label={`${profile.title}的依据`}>
    <div className="customers-meta"><Channel channel={p.channel} /><span>{p.title}</span></div>
    <p className="t-foot">{p.state === "partial" ? `已读 ${p.messages_read} / ${p.messages_total} 条，AI 还在继续读。` : p.state === "failed" ? "这段聊天暂时没读完，稍后会继续尝试。" : p.state === "skipped" ? "这段聊天暂未参与分析。" : `已读 ${p.messages_read} / ${p.messages_total} 条聊天记录。`}</p>
    {p.evidence?.length ? <ul className="customers-evidence">{p.evidence.map((e, index) => <li key={`${e.message_index}-${index}`}>
      <strong>{e.claim || "判断依据"}</strong>
      <Link className="customers-quote" to={`/inbox${qs({ conversation: p.conversation_key, role: "all", state: "all" })}`}><q>{e.quote}</q><span>打开对应会话 ↗</span></Link>
      <span className="t-foot"><ContactTime at={e.sent_at} /></span>
    </li>)}</ul> : <p>还没有可展示的原话，等 AI 继续读取。<Link to={`/inbox${qs({ conversation: p.conversation_key, role: "all", state: "all" })}`}>查看会话</Link></p>}
  </section>;
}

function CustomerCard({ row, selected, onSelect, onCreate, busy, canCreate }: { row: CustomerRow; selected: boolean; onSelect: () => void; onCreate: () => void; busy: boolean; canCreate: boolean }) {
  const [expanded, setExpanded] = useState(false);
  const evidenceId = useId();
  const p = row.profiles[0];
  const country = row.profiles.find(profile => profile.country || profile.country_name) || p;
  const products = [...new Set(row.profiles.flatMap(profile => profile.products || []))];
  const channels = [...new Set([...(row.record?.channels || []), ...row.profiles.map(profile => profile.channel)])];
  const summary = row.profiles.find(profile => profile.summary)?.summary;
  const eligible = !row.record && !row.blocked && canCreate && Boolean(row.stage);
  return <article className="customers-card" aria-label={row.name}>
    <div className="customers-card-main">
      {!row.record && <input className="customers-checkbox" type="checkbox" aria-label={`选择${row.name}`} disabled={!eligible || busy} checked={selected} onChange={onSelect} />}
      <div className="customers-card-content">
        <div className="customers-name"><h3>{p ? <button className="customers-name-button" aria-expanded={expanded} aria-controls={evidenceId} onClick={() => setExpanded(!expanded)}>{row.name}</button> : row.name}</h3><Chip tone={row.record ? "green" : "orange"}>{row.record ? "已建档客户" : "AI 发现 · 未建档"}</Chip>{row.stage && <Chip>{STAGES.find(s => s.key === row.stage)?.label}</Chip>}</div>
        <div className="customers-meta"><Country profile={country} fallback={row.record?.country} />{channels.map(channel => <Channel key={channel} channel={channel} />)}</div>
        <div className="customers-products" aria-label="问过的产品">{products.length ? products.slice(0, 3).map(product => <span key={product}>{product}</span>) : <span className="t-foot">问过的产品待确认</span>}</div>
        <p className="customers-summary">{summary || "AI 还在整理这位客户的聊天记录。"}</p>
        <p className="t-foot"><ContactTime at={row.last} /></p>
        {p?.state === "partial" && <p className="t-foot">聊天还没读完，判断可能继续补充。</p>}
        {row.blocked && <p className="t-foot">{row.blocked}</p>}
      </div>
      <div className="customers-card-actions">
        {row.record ? <Link className="btn" to={`/customers?c=${row.record.id}`}>查看客户资料</Link> : <button className="btn" disabled={!eligible || busy} onClick={onCreate}>{busy && selected ? "正在建档…" : "建为客户"}</button>}
        {p && <button className="btn plain" aria-expanded={expanded} aria-controls={evidenceId} onClick={() => setExpanded(!expanded)}>{expanded ? "收起依据" : "查看依据"}</button>}
      </div>
    </div>
    {expanded && <div className="customers-evidence-panel" id={evidenceId}>{row.profiles.map(profile => <Evidence key={profile.conversation_key} profile={profile} />)}</div>}
  </article>;
}

export function Customers() {
  const [params] = useSearchParams();
  const { id } = useParams();
  // Preserve the existing 360°, manual creation and reversible merge flows and deep links.
  if (params.has("c") || id || params.get("view") === "records") return <><p className="customers-back"><Link className="btn" to="/customers">返回客户分层</Link></p><PartiesWorkbench initialKind="customer" /></>;
  return <CustomersList />;
}

function CustomersList() {
  const client = useQueryClient();
  const [params, setParams] = useSearchParams();
  const stage = STAGES.find(s => s.key === params.get("stage"))?.key || "potential";
  const q = params.get("q") || "";
  const channel = params.get("channel") || "";
  const [selection, setSelection] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const locked = useRef(false);
  const requests = useRef(new Map<string, { fields: Record<string, string>; token: string }>());
  const [feedback, setFeedback] = useState("");
  const [saveError, setSaveError] = useState("");
  const profiles = useQuery({ queryKey: ["profiles", "customers"], queryFn: () => readProfiles(), retry: false, refetchInterval: 20000 });
  const records = useQuery({ queryKey: RECORDS, queryFn: readRecords, retry: false });
  const conversations = useQuery({ queryKey: CONVERSATIONS, queryFn: () => get<{ items: Conversation[] }>(API + "/conversations"), retry: false });
  const resolveEnabled = Boolean(records.data && profiles.data && conversations.data);
  const legacy = useQuery({
    queryKey: ["customers", "legacy-links", records.data, profiles.data?.items.map(p => [p.conversation_key, p.channel]), conversations.data],
    queryFn: () => resolveLegacyLinks(records.data!, profiles.data!.items, conversations.data!.items),
    enabled: resolveEnabled, retry: false, staleTime: 60000,
  });
  const setFilters = (changes: Record<string, string>) => {
    const next = new URLSearchParams(params);
    for (const [key, value] of Object.entries(changes)) value ? next.set(key, value) : next.delete(key);
    setSelection(new Set()); setFeedback(""); setSaveError("");
    setParams(next, { replace: true });
  };
  const rows = joinCustomers(profiles.data?.items || [], records.data || [], legacy.data?.items || conversations.data?.items || []);
  const filtered = rows.filter(row => (!channel || row.profiles.some(p => p.channel === channel) || row.record?.channels.includes(channel)) && (!q || [row.name, row.record?.country, row.record?.tags, row.record?.notes, ...row.profiles.flatMap(p => [p.org_name, p.title, p.country_name, p.summary, ...(p.people || []).map(person => person.name), ...(p.products || [])])].filter(Boolean).join(" ").toLocaleLowerCase().includes(q.toLocaleLowerCase())));
  const visible = filtered.filter(row => row.stage === stage);
  const waiting = filtered.filter(row => !row.stage);
  const canCreate = Boolean(records.data && conversations.data) && !records.isError && !conversations.isError && (!resolveEnabled || legacy.isSuccess);
  const eligible = visible.filter(row => !row.record && !row.blocked && canCreate);
  const chosen = eligible.filter(row => selection.has(row.key));
  const isLoading = profiles.isPending || records.isPending || conversations.isPending || (resolveEnabled && legacy.isPending);
  const countsKnown = !isLoading && !profiles.isError && !records.isError && !conversations.isError && !(resolveEnabled && legacy.isError);
  const refresh = () => { void profiles.refetch(); void records.refetch(); void conversations.refetch(); if (resolveEnabled) void legacy.refetch(); };

  async function createCustomers(targets: CustomerRow[]) {
    if (locked.current || !canCreate || !targets.length) return;
    locked.current = true; setBusy(true); setFeedback(""); setSaveError("");
    let saved = 0;
    const failed: string[] = [];
    try {
      for (const row of targets) {
        const p = row.profiles[0];
        if (!p || row.record || row.blocked) continue;
        const fields = { kind: "customer", name: row.name.slice(0, 200), country: (p.country || p.country_name || "").slice(0, 100), conversation_key: p.conversation_key };
        let request = requests.current.get(p.conversation_key);
        if (!request) { request = { fields, token: crypto.randomUUID() }; requests.current.set(p.conversation_key, request); }
        try {
          // Existing create is transactional: it creates the party AND links the identity.
          const result = await postForm<{ id: number }>(API, { ...request.fields, request_id: request.token });
          const record: CustomerRecord = { id: result.id, name: request.fields.name, kind: "customer", country: request.fields.country, last_contact: timestamp(p.last_at) / 1000 || null, channels: [p.channel], notes: "", tags: "", next_followup: null, todo_count: 0, open_deals: 0 };
          client.setQueryData<CustomerRecord[]>(RECORDS, old => [...(old || []).filter(r => r.id !== result.id), record]);
          client.setQueryData<{ items: Conversation[] }>(CONVERSATIONS, old => ({ items: (old?.items || []).map(c => c.key === p.conversation_key ? { ...c, party_id: result.id } : c) }));
          setSelection(old => new Set([...old].filter(key => key !== row.key)));
          requests.current.delete(p.conversation_key);
          saved++;
        } catch (error) {
          // Retain the exact fields and token for a safe retry after a lost response.
          failed.push(row.name);
          if (error instanceof ApiError && error.status === 409) {
            setSaveError("客户资料或会话归属有变化，请刷新客户后再核对。同名客户可到管理客户档案里关联会话。");
            break;
          }
        }
      }
      if (saved) setFeedback(`已建档 ${saved} 位客户，会话已关联到客户资料。`);
      if (failed.length) setSaveError(old => old || `“${failed.join("、")}”的建档结果暂未确认。请刷新核对后重试，已成功的客户会保留。`);
      await Promise.allSettled([client.invalidateQueries({ queryKey: RECORDS }), client.invalidateQueries({ queryKey: CONVERSATIONS }), client.invalidateQueries({ queryKey: ["parties-ops"] })]);
    } finally { locked.current = false; setBusy(false); }
  }

  return <div className="customers-page">
    <header className="page-head"><div><h1 className="t-large">客户</h1><p className="customers-intro">这里是所有客户。AI 读过聊天记录后，自动分成潜在客户和老客户；你确认后建档。</p></div><Link className="btn" to="/customers?view=records">管理客户档案</Link></header>
    <div className="customers-stages" role="radiogroup" aria-label="客户阶段">{STAGES.map((s, index) => <button type="button" role="radio" key={s.key} aria-checked={stage === s.key} tabIndex={stage === s.key ? 0 : -1} onClick={() => setFilters({ stage: s.key })} onKeyDown={e => {
      const step = e.key === "ArrowRight" || e.key === "ArrowDown" ? 1 : e.key === "ArrowLeft" || e.key === "ArrowUp" ? -1 : 0;
      if (!step) return;
      e.preventDefault();
      const next = (index + step + STAGES.length) % STAGES.length;
      setFilters({ stage: STAGES[next].key });
      (e.currentTarget.parentElement?.children[next] as HTMLButtonElement)?.focus();
    }}><span>{s.label}<strong className="num">{countsKnown ? filtered.filter(row => row.stage === s.key).length : "—"}</strong></span><small>{s.hint}</small></button>)}</div>
    <div className="customers-toolbar"><SearchField label="搜索客户" placeholder="搜索客户、国家或产品" value={q} onSearch={value => setFilters({ q: value })} /><Pick label="客户来源渠道" value={channel} onChange={value => setFilters({ channel: value })} options={[{ value: "", label: "全部渠道" }, { value: "alibaba", label: "阿里国际站" }, { value: "wechat", label: "微信" }, { value: "whatsapp", label: "WhatsApp" }]} /><button className="btn" disabled={busy || profiles.isFetching || records.isFetching || conversations.isFetching} onClick={refresh}>刷新客户</button>{(q || channel) && <button className="btn plain" onClick={() => setFilters({ q: "", channel: "" })}>清除筛选</button>}</div>
    {feedback && <p className="customers-notice success" role="status">{feedback}</p>}
    {saveError && <p className="customers-notice error" role="alert">{saveError}</p>}
    {isLoading && <div className="customers-loading" role="status" aria-busy="true">正在整理客户和聊天记录，请稍等…</div>}
    {profiles.isError && <div className="customers-notice error" role="alert">客户聊天分析暂时没读到，请稍后再试。{profiles.data && "先显示上次读到的结果。"}<button className="btn" onClick={() => profiles.refetch()}>重读聊天分析</button></div>}
    {(records.isError || conversations.isError || (resolveEnabled && legacy.isError)) && <div className="customers-notice error" role="alert">客户档案或会话归属暂时没读到。刷新后再建档，避免重复。<button className="btn" onClick={refresh}>重新读取客户</button></div>}
    {!isLoading && profiles.data === null && <div className="customers-notice" role="status">{WAITING}</div>}
    {!isLoading && <>
      <div className="customers-list-head"><div><h2 className="t-headline">{STAGES.find(s => s.key === stage)?.label}</h2><p className="t-foot">点客户名字或“查看依据”，可查看判断的原话。</p></div><div className="customers-batch"><label><input type="checkbox" aria-label="全选当前分段未建档客户" disabled={!eligible.length || busy} checked={eligible.length > 0 && chosen.length === eligible.length} onChange={e => setSelection(e.target.checked ? new Set(eligible.map(row => row.key)) : new Set())} />全选未建档</label><button className="btn primary" disabled={!chosen.length || busy} onClick={() => { if (window.confirm(`将为选中的 ${chosen.length} 位客户建立档案，并关联对应会话。确认全部建档？`)) void createCustomers(chosen); }}>{busy ? "正在建档…" : `全部建档${chosen.length ? `（${chosen.length}）` : ""}`}</button></div></div>
      <div className="customers-list" aria-label="当前阶段客户">{visible.map(row => <CustomerCard key={row.key} row={row} selected={selection.has(row.key)} onSelect={() => setSelection(old => { const next = new Set(old); next.has(row.key) ? next.delete(row.key) : next.add(row.key); return next; })} onCreate={() => void createCustomers([row])} busy={busy} canCreate={canCreate} />)}</div>
      {!visible.length && !profiles.isError && <Empty title={q || channel ? "没有找到符合条件的客户" : profiles.data === null || !profiles.data?.items.length ? WAITING : "这个分段暂时没有客户"} action={(q || channel) ? <button className="btn" onClick={() => setFilters({ q: "", channel: "" })}>清除筛选</button> : <Link className="btn" to="/inbox">去看聊天记录</Link>}>{q || channel ? "换个名字、产品或渠道试试。" : "客户档案会保留；AI 读完聊天后，会继续补充分层。"}</Empty>}
      {waiting.length > 0 && <section className="customers-waiting" aria-label="待分层客户"><h2 className="t-headline">等待读取聊天 · {waiting.length}</h2><p className="t-foot">这里保留还没有分层依据的客户，不计入上面的分段。</p><div className="customers-list">{waiting.map(row => <CustomerCard key={row.key} row={row} selected={false} onSelect={() => {}} onCreate={() => {}} busy={busy} canCreate={false} />)}</div></section>}
    </>}
  </div>;
}
