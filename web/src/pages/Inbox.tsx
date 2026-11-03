import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router";
import { get, postForm, qs } from "../api";
import { ago, formatTime } from "../format";
import { uuid } from "../lib_uuid";
import { Chip, Empty, SearchField, Skeleton } from "../ui/bits";
import { Segmented } from "../ui/Segmented";
import { InboxBlock } from "./inbox/Attachment";
import { MessageTranslationControl, TranslationToolbar, useConversationTranslation } from "../ui/LanguagePanels";
import { inboxContext, type Conv, type Detail, type Draft, type Result } from "../ops/inbox";
import { Operations, Reply } from "./inbox/Operations";
import { PageIntro } from "./overview/PageIntro";
import { CounterpartTime } from "../ui/CounterpartTime";
import "./inbox/inbox.css";

function DraftAdvice({ convKey, draft, onUse, onResult }: { convKey: string; draft: Draft | null; onUse: (v: string) => void; onResult: (r: Result) => void }) {
  const client = useQueryClient();
  const request = useRef(uuid());
  const make = useMutation({ mutationFn: () => postForm<{ draft: Draft }>(`/api/v1/inbox/conversations/${convKey}/draft`, { request_id: request.current }), retry: false,
    onSuccess: (r) => { request.current = uuid(); onUse(r.draft.text); void client.invalidateQueries({ queryKey: ["inbox", "detail", convKey] }); onResult({ notice: "AI 草稿已放入回复框，请核对后编辑" }); } });
  return <section aria-label="AI 建议"><h3 className="t-headline">AI 建议</h3><p className="t-foot">先核对产品、数量和交期，AI 只写草稿，发前由你确认。</p>
    {draft && <><div className="quote">{draft.text}</div>{draft.needs_confirmation.length > 0 && <div className="callout">发前请核对：<ul>{draft.needs_confirmation.map((s) => <li key={s}>{s}</li>)}</ul></div>}{draft.sources?.map((s, i) => <p className="t-cap" key={i}>参考：{s.title} {s.date ?? ""}</p>)}<button className="btn" onClick={() => { onUse(draft.text); onResult({ notice: "已放入回复框，可继续编辑" }); }}>使用这份草稿</button></>}
    {make.isError && <p className="callout" role="alert">{make.error.message}</p>}
    <button className="btn primary" disabled={make.isPending} onClick={() => make.mutate()}>{make.isPending ? "正在起草…" : "AI 起草回复"}</button>
  </section>;
}

function Conversation({ convKey, onBack }: { convKey: string; onBack: () => void }) {
  const detail = useQuery({ queryKey: ["inbox", "detail", convKey], queryFn: () => get<Detail>(`/api/v1/inbox/conversations/${convKey}`), retry: false, refetchInterval: 15000 });
  const context = useQuery({ queryKey: ["inbox", "context", convKey], queryFn: () => inboxContext(convKey), retry: false, refetchInterval: 15000 });
  const translation = useConversationTranslation(convKey, detail.data?.messages ?? []);
  const [reply, setReply] = useState("");
  const [showProfile, setShowProfile] = useState(false);
  const [notice, setNotice] = useState("");
  const onResult = (r: Result) => setNotice(r.notice);
  if (detail.isLoading) return <section className="pane thread"><Skeleton lines={6} /></section>;
  if (!detail.data || detail.isError) return <section className="pane thread"><Empty title="没能打开会话" action={<><button className="btn" onClick={() => detail.refetch()}>重新读取</button><button className="btn" onClick={onBack}>返回会话列表</button></>}>{detail.error?.message}</Empty></section>;
  const { conversation: c, messages, draft } = detail.data;
  return <>
    <section className="pane thread" aria-label="对话">
      <header className="pane-head"><button className="btn icon plain back-btn" aria-label="返回会话列表" onClick={onBack}><ArrowLeft size={20} /></button><div><b>{c.buyer_name}</b><p className="t-foot">{c.channel_label} · 已收录 {messages.length} 条消息</p></div><button className="btn sm" onClick={() => { void detail.refetch(); void context.refetch(); }}>刷新对话</button></header>
      <p className="inbox-purpose">核对客户需求并回复；这里显示已收录的消息。</p>
      {notice && <div className="inbox-feedback" role="status">{notice}</div>}
      {detail.isError && <p role="alert">对话更新失败，请点刷新对话。</p>}
      <TranslationToolbar translation={translation} />
      <div className="pane-body thread-body" tabIndex={0} aria-label="消息记录">
        {!messages.length && <Empty title="还没有消息" action={<button className="btn" onClick={() => detail.refetch()}>刷新对话</button>}>收到消息后就能开始回复。</Empty>}
        {messages.map((m, i) => { const t = translation.message(i); return <article key={i} className={`bubble ${m.role === "seller" ? "me" : "them"}`}>
          {m.sender_label && <span className="t-cap">{m.sender_label}</span>}
          <div className="bubble-text">{m.blocks?.length ? m.blocks.map((block, n) => <InboxBlock key={n} block={block} />) : m.text}</div>
          <span className="t-cap bubble-time">{formatTime(m.sent_at)}</span>
          {m.text.trim() && <MessageTranslationControl state={t} busy={translation.busy} />}
          {t.showing && t.item?.text && <div className="quote">{t.item.text}</div>}
        </article>; })}
        {context.data?.outbox.map((row) => <article key={row.id} className="inbox-queue" data-testid="dry-run-record"><b>{row.state === "draft" ? "供应商询问草稿（演练）" : "已进入发送队列（演练）"}</b><p>{row.text}</p>{row.conversation_key !== convKey && <Link className="btn sm" to={`/inbox?role=all&conversation=${row.conversation_key}`}>打开供应商会话</Link>}</article>)}
      </div>
      <Reply convKey={convKey} name={c.buyer_name} reply={reply} setReply={setReply} onResult={onResult} />
    </section>
    <aside className="pane side" aria-label="客户资料与 AI 建议" data-expanded={showProfile}>
      <button className="btn plain profile-toggle" aria-expanded={showProfile} onClick={() => setShowProfile(!showProfile)}>{showProfile ? "收起客户资料和操作" : "客户资料与 AI 建议 · 展开操作"}</button>
      <p className="inbox-purpose inbox-side-purpose">查看客户、关联案子，把下一步安排好。</p>
      {showProfile && notice && <div className="inbox-feedback" role="status">{notice}</div>}
      <div id="conversation-profile" className="pane-body profile-body">
        {context.isLoading && <Skeleton />}
        {context.isError && <Empty title="没能读到客户与案子" action={<button className="btn" onClick={() => context.refetch()}>重新读取</button>}>{context.error.message}</Empty>}
        <CounterpartTime country={context.data?.party?.country ?? c.country} timezone={context.data?.party?.timezone} />
        {context.data && <Operations convKey={convKey} context={context.data} onResult={onResult} />}
        <DraftAdvice convKey={convKey} draft={draft} onUse={(v) => { setReply(v); setShowProfile(false); }} onResult={onResult} />
      </div>
    </aside>
  </>;
}

interface Summary { waiting_total: number; revision?: string; conversation_count?: number; by_channel: { channel: string; label: string; waiting: number }[] }
export function Inbox() {
  const [params, setParams] = useSearchParams();
  const [page, setPage] = useState(1);
  const [newMessage, setNewMessage] = useState(false);
  const previous = useRef<Summary | null>(null);
  const client = useQueryClient();
  const state = params.get("state") ?? "waiting";
  const channel = params.get("channel") ?? "";
  const q = params.get("q") ?? "";
  const role = params.get("role") ?? "customer";
  const selected = params.get("conversation") ?? params.get("c") ?? "";
  const set = (changes: Record<string, string>) => { const next = new URLSearchParams(params); if ("c" in changes) { changes = { ...changes, conversation: changes.c }; next.delete("c"); delete changes.c; } for (const [k, v] of Object.entries(changes)) v ? next.set(k, v) : next.delete(k); setParams(next, { replace: true }); };
  useEffect(() => setPage(1), [state, channel, q, role]);
  const summary = useQuery({ queryKey: ["inbox", "summary"], queryFn: () => get<Summary>("/api/v1/inbox/summary"), retry: false, refetchInterval: 10000 });
  useEffect(() => { const next = summary.data; if (!next) return; if (previous.current && next.revision && previous.current.revision !== next.revision) { setNewMessage(true); void client.invalidateQueries({ queryKey: ["inbox", "list"] }); } previous.current = next; }, [summary.data, client]);
  const list = useQuery({ queryKey: ["inbox", "list", state, channel, q, role, page], queryFn: () => get<{ items: Conv[]; total: number; ready: boolean }>(`/api/v1/inbox/conversations${qs({ state, channel, q, role, page, page_size: 40 })}`), retry: false, placeholderData: keepPreviousData, refetchInterval: 15000 });
  const refresh = () => { void client.invalidateQueries({ queryKey: ["inbox"] }); };
  return <><PageIntro page="inbox" /><p><Link to="/outbox">查看草稿未发送</Link></p><div className="inbox inbox-ops" data-open={Boolean(selected)}>
    <section className="pane listing" aria-label="会话列表">
      <header className="pane-head col"><p className="t-foot">收到询盘，在这里回复客户、找供应商核实，再跟进案子。</p>
        <Segmented label="渠道" value={channel} onChange={(v) => set({ channel: v, c: "" })} options={[{ value: "", label: "全部渠道" }, ...(summary.data?.by_channel ?? []).map((c) => ({ value: c.channel, label: c.label, count: c.waiting || undefined }))]} />
        <Segmented label="会话筛选" value={state} onChange={(v) => set({ state: v, c: "" })} options={[{ value: "waiting", label: "要我回复", count: summary.data?.waiting_total }, { value: "replied", label: "等对方" }, { value: "done", label: "已处理" }, { value: "all", label: "全部" }]} />
        <SearchField label="搜索会话" placeholder="搜客户、名称、消息…" value={q} onSearch={(v) => set({ q: v })} />
        <div className="inbox-actions"><label className="t-foot"><input type="checkbox" checked={role === "all"} onChange={(e) => set({ role: e.target.checked ? "all" : "customer" })} />同时显示供应商</label><button className="btn sm" onClick={refresh}>刷新会话</button></div>
        {summary.isError && <p role="alert">渠道概况读取失败，可点刷新会话重试。</p>}
        {newMessage && <button className="btn primary" aria-live="polite" onClick={() => { setNewMessage(false); set({ state: "waiting", c: "", q: "", channel: "" }); refresh(); }}>有新消息，查看待回复会话</button>}
      </header>
      <div className="pane-body" aria-busy={list.isFetching}>
        {list.isFetching && list.data && <p className="t-cap" role="status">正在更新会话…</p>}
        {list.isLoading && <Skeleton lines={6} />}
        {list.isError && <Empty title="没能读到会话" action={<button className="btn" onClick={() => list.refetch()}>重新读取</button>}>{list.error.message}</Empty>}
        {list.data?.items.length === 0 && <Empty title={q ? "没有找到匹配会话" : list.data.ready === false ? "还没收到渠道消息" : state === "waiting" ? "暂时没有要回复的会话" : "这里还没有会话"} action={<div className="inbox-actions"><button className="btn" onClick={() => set({ q: "", channel: "", state: "all", role: "all" })}>查看全部会话</button><button className="btn" onClick={refresh}>刷新会话</button>{list.data.ready === false && <Link className="btn" to="/system/channels">去接入渠道</Link>}</div>}>{list.data.ready === false ? "先接入渠道，收到的询盘会显示在这里。" : "当前筛选下没有会话，可查看全部或刷新新消息。"}</Empty>}
        <ul className="conv-list">{list.data?.items.map((c) => <li key={c.key}>
          <button className="conv" aria-current={c.key === selected ? "true" : undefined} onClick={() => set({ c: c.key })}><span className="avatar">{c.buyer_name[0] ?? "?"}</span><span className="conv-main"><span className="conv-top"><b className="conv-name">{c.buyer_name}</b><span className="t-cap">{ago(c.last.sent_at)}</span></span><span className="conv-snip">{c.last.text}</span><span className="t-foot">归属：{c.customer_name || "待归到客户"}</span><span className="conv-tags"><Chip>{c.channel_label}</Chip><Chip tone={c.workflow_state === "waiting" ? "orange" : "plain"}>{c.workflow_state === "done" ? "已处理" : c.workflow_state === "replied" ? "等对方" : "要我回复"}</Chip>{c.waiting_hours != null && <span className="t-cap">{c.waiting_hours < 1 ? "刚刚收到" : `等了 ${Math.round(c.waiting_hours)} 小时`}</span>}{c.remind_at && <span className="t-cap">提醒：{formatTime(c.remind_at)}</span>}</span></span></button>
          <button className="btn sm inbox-next" onClick={() => set({ c: c.key })}>处理询盘</button>
        </li>)}</ul>
        {list.data && list.data.total > 40 && <div className="pager"><button className="btn" disabled={page === 1} onClick={() => setPage(page - 1)}>上一页</button><span>{page} / {Math.ceil(list.data.total / 40)}</span><button className="btn" disabled={page * 40 >= list.data.total} onClick={() => setPage(page + 1)}>下一页</button></div>}
      </div>
    </section>
    {selected ? <Conversation key={selected} convKey={selected} onBack={() => set({ c: "" })} /> : <section className="pane thread empty-pane" aria-label="对话"><Empty title="选一个会话开始" action={<button className="btn" onClick={refresh}>刷新会话</button>}>点击会话旁的“处理询盘”，核对需求并安排下一步。</Empty></section>}
  </div></>;
}
