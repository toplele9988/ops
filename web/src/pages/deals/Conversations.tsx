import { useQueries, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router";
import { get } from "../../api";
import type { DealDetail } from "../../deals";
import { formatTime } from "../../format";
import { Sheet } from "../../ui/bits";
import { useDealAction } from "./actions";

export function ConversationLink({ dealId, partyId }: { dealId: number; partyId: number }) {
  const [open, setOpen] = useState(false);
  const [key, setKey] = useState("");
  const action = useDealAction();
  const q = useQuery({ queryKey: ["link-conversations"], queryFn: () => get<{ items: { key: string; buyer_name: string; channel_label: string }[] }>("/api/v1/inbox/conversations?page_size=100"), enabled: open, retry: false });
  return <><button className="btn sm" onClick={() => setOpen(true)}>关联会话</button><Sheet open={open} title="把会话关联到这位参与方" onClose={() => setOpen(false)}>
    <p>核对会话里的人是否就是这位客户、供应商或货代。关联后会汇入本案经过。</p>
    <div className="ops-form"><label>选择会话<select value={key} onChange={(e) => setKey(e.target.value)}><option value="">请选择</option>{q.data?.items.map((c) => <option key={c.key} value={c.key}>{c.channel_label} · {c.buyer_name}</option>)}</select></label></div>
    {q.isError && <p role="alert">会话没能读到。<button className="btn" onClick={() => q.refetch()}>重新读取</button></p>}
    {action.error && <p role="alert">{action.error}</p>}
    <div className="sheet-actions"><button className="btn" onClick={() => setOpen(false)}>取消</button><button className="btn primary" disabled={!key || action.busy} onClick={async () => { if (await action.run(`deals/${dealId}/conversations`, { conversation_key: key, party_id: String(partyId) }, "会话已关联，经过已更新")) setOpen(false); }}>确认关联</button></div>
  </Sheet>{action.message && <p role="status">{action.message}</p>}</>;
}

export function CrossTimeline({ deal }: { deal: DealDetail }) {
  const links = deal.conversations;
  const queries = useQueries({ queries: links.map((link) => ({ queryKey: ["deal-messages", link.key ?? link.conversation_key], queryFn: () => get<{ conversation: { channel_label?: string; buyer_name?: string }; messages: { id?: string; role: string; text?: string; sent_at: string }[] }>(`/api/v1/inbox/conversations/${encodeURIComponent(link.key ?? link.conversation_key ?? "")}`), retry: false })) });
  const entries = deal.events.map((e) => ({ key: `event-${e.id}`, at: e.at, text: e.summary, label: e.who ?? "案子记录", href: "" }));
  queries.forEach((q, i) => q.data?.messages.forEach((m, j) => entries.push({ key: `${i}-${j}`, at: m.sent_at, text: m.text || "图片或文件，请打开会话查看", label: `${q.data?.conversation.channel_label ?? "会话"} · ${m.role === "seller" ? "我方" : q.data?.conversation.buyer_name ?? "对方"}`, href: `/inbox?c=${encodeURIComponent(links[i].key ?? links[i].conversation_key ?? "")}` })));
  entries.sort((a, b) => Date.parse(b.at) - Date.parse(a.at));
  return <>
    {queries.some((q) => q.isPending) && <p className="t-foot">正在汇总各渠道经过…</p>}
    {queries.map((q, i) => q.isError ? <p key={i} role="alert">一条会话暂时读不到，已有案子记录保留。<button className="btn sm" onClick={() => q.refetch()}>重读会话</button></p> : null)}
    {links.length === 0 && <p className="t-foot">还没有关联会话，在参与方旁点“关联会话”即可汇总沟通。</p>}
    <ul className="timeline ops-timeline">{entries.map((e) => <li key={e.key}><time>{formatTime(e.at)}</time><span>{e.href ? <Link to={e.href}>{e.label}</Link> : <b>{e.label}</b>}：{e.text}</span></li>)}</ul>
  </>;
}
