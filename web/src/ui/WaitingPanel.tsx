import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router";
import { copyText, get, postForm } from "../api";
import { ROLE } from "../deals";
import { formatTime } from "../format";
import { Chip, Skeleton } from "./bits";
import { useToast } from "./Toast";
import { WorkflowError } from "./WorkflowError";

interface Conversation { key: string; title: string; state: string; availability_text: string; since: number | null; last_reminded_at: number | null }
interface Participant { party_id: number; name: string; role: string; state: string; availability_text: string; conversations: Conversation[] }
interface WaitingItem { id: number; party_id: number; party_name: string; role: string; conversation_key: string; title: string; waiting_on: "us" | "them"; since: number; last_reminded_at: number | null; status: string; elapsed: string; availability_text: string; linked: boolean }
interface Waiting { participants: Participant[]; items: WaitingItem[]; products: { id: number; name: string; qty: number | null }[] }
interface Draft {
  text: string; source_text: string; back_translation: string; native_language: string;
  needs_confirmation: string[]; warnings: { kind: string; message: string; fragments?: string[] }[]; message: string;
  contact?: { locked?: boolean }; validation?: { ok: boolean }; back_validation?: { ok: boolean };
}
const direction = (state: string) => state === "us" ? "等我方处理" : state === "them" ? "等对方回复" : "没有明确的待回复方向";

function WaitingEntry({ item, dealId, refresh }: { item: WaitingItem; dealId: number; refresh: () => void }) {
  const toast = useToast();
  const [supplement, setSupplement] = useState("");
  const [draft, setDraft] = useState<Draft | null>(null);
  const [text, setText] = useState("");
  const [reminderAt, setReminderAt] = useState("");
  const [requestId, setRequestId] = useState(() => `manual-${Date.now()}-${Math.random().toString(36).slice(2)}`);
  const [recording, setRecording] = useState(false);
  const action = useMutation({
    mutationFn: ({ op, fields }: { op: string; fields: Record<string, string> }) => postForm<Draft>(`/api/v1/deals/${dealId}/waiting/${item.id}/${op}`, fields),
    retry: false,
    onSuccess: (result, variables) => { if (variables.op === "draft") { setDraft(result); setText(result.text); toast("草稿已生成，请核对原稿和回译"); } else { if (variables.op === "remind") setRequestId(`manual-${Date.now()}-${Math.random().toString(36).slice(2)}`); setRecording(false); refresh(); toast("已记录"); } },
    onError: (e: Error) => toast(e.message, "error"),
  });
  const active = item.status === "open";
  const warning = !!draft && (draft.warnings?.some((w) => w.kind === "error") || draft.validation?.ok === false || draft.back_validation?.ok === false);
  return <div style={{ padding: 16, borderTop: "1px solid var(--line)", display: "grid", gap: 10 }}>
    <div><Chip tone={!active ? "green" : item.waiting_on === "us" ? "red" : "orange"}>{!active ? "已办完" : direction(item.waiting_on)}</Chip> <strong>{item.title}</strong></div>
    <div className="t-foot">{ROLE[item.role] ?? "原参与方"} · {item.party_name} · <Link to={`/inbox?conversation=${encodeURIComponent(item.conversation_key)}`}>查看具体会话</Link></div>
    <div className="t-foot">从 {formatTime(item.since)} 开始 · {item.elapsed} · 上次催问：{item.last_reminded_at ? formatTime(item.last_reminded_at) : "尚未登记"}</div>
    <div className="t-cap">{item.availability_text}</div>
    {active && <>
      <label className="t-foot">补充给对方的内容<textarea className="textarea" rows={2} value={supplement} maxLength={1000} onChange={(e) => setSupplement(e.target.value)} placeholder="只写对方可以知道、且已经确认的事实；还不清楚的请写成问题" /></label>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
        <button type="button" className="btn" disabled={action.isPending || !item.linked || item.waiting_on === "us"} onClick={() => action.mutate({ op: "draft", fields: { supplement } })}>按对方母语起草催问</button>
        <button type="button" className="btn" disabled={action.isPending} onClick={() => setRecording(!recording)}>人工记录已催问</button>
        <button type="button" className="btn" disabled={action.isPending} onClick={() => { if (window.confirm("这件事已经办完？确认后结束该项等待。")) action.mutate({ op: "resolve", fields: {} }); }}>这件事已办完</button>
      </div>
      {recording && <div style={{ display: "grid", gap: 8 }}><p className="t-foot">仅在你已经自行联系对方后登记。这里只记时间，不发送；原事项的等待起点保持不变。</p><label className="t-foot">实际催问时间（北京时间）<input aria-label="实际催问时间" type="datetime-local" value={reminderAt} onChange={(e) => setReminderAt(e.target.value)} /></label><button type="button" className="btn" disabled={action.isPending || !reminderAt} onClick={() => action.mutate({ op: "remind", fields: { request_id: requestId, at: new Date(reminderAt + "+08:00").toISOString() } })}>确认登记实际催问时间</button></div>}
    </>}
    {draft && <div className="group" style={{ padding: 12, display: "grid", gap: 10 }}>
      <strong>催问草稿 · {draft.native_language} · {draft.contact?.locked ? "语言已人工锁定" : "请核对识别出的语言"}</strong>
      <p className="t-foot">{draft.message}</p>
      <details><summary>查看中文起草原稿</summary><p style={{ whiteSpace: "pre-wrap" }}>{draft.source_text}</p></details>
      <label className="t-foot">对方母语草稿<textarea className="textarea" rows={5} value={text} onChange={(e) => setText(e.target.value)} /></label>
      <p className="t-foot" style={{ whiteSpace: "pre-wrap" }}>回译核对稿：{draft.back_translation}</p>
      {draft.needs_confirmation?.map((n) => <p className="t-foot" key={n}>待确认：{n}</p>)}
      {warning && <p role="alert" style={{ color: "var(--red)" }}>译文中的数字、型号或语义需要核对。请核对原稿、母语版和回译，再自行发送。</p>}
      {draft.warnings?.map((w, i) => <p className="t-foot" key={i} style={{ color: w.kind === "error" ? "var(--red)" : undefined }}>{w.message}</p>)}
      {text !== draft.text && <p className="t-foot">你修改了母语草稿，下面的回译仍对应生成时的版本，请重新核对。</p>}
      <button type="button" className="btn" onClick={async () => toast(await copyText(text) ? "已复制草稿，请核对后自行发送" : "复制未成功，请手工选择草稿复制")}>复制草稿</button>
    </div>}
  </div>;
}

export function WaitingPanel({ dealId }: { dealId: number }) {
  const toast = useToast();
  const client = useQueryClient();
  const [target, setTarget] = useState("");
  const [title, setTitle] = useState("");
  const [since, setSince] = useState("");
  const [waiting, setWaiting] = useState("them");
  const [product, setProduct] = useState("");
  const q = useQuery({ queryKey: ["deal-waiting", dealId], queryFn: () => get<Waiting>(`/api/v1/deals/${dealId}/waiting`), retry: false });
  const refresh = () => { client.invalidateQueries({ queryKey: ["deal-waiting", dealId] }); client.invalidateQueries({ queryKey: ["deal", String(dealId)] }); };
  const targets = q.data?.participants.flatMap((p) => p.conversations.map((c) => ({ party: p, conv: c, value: `${p.party_id}:${c.key}` }))) ?? [];
  const selected = targets.find((t) => t.value === target);
  const create = useMutation({ mutationFn: () => postForm(`/api/v1/deals/${dealId}/waiting`, {
    party_id: String(selected!.party.party_id), conversation_key: selected!.conv.key, title, waiting_on: waiting,
    ...(since ? { since: new Date(since).toISOString() } : {}),
    ...(product ? { item_id: product } : {}),
  }), retry: false, onSuccess: () => { refresh(); setTitle(""); setSince(""); toast("已登记这件事的等待"); }, onError: (e: Error) => toast(e.message, "error") });
  return <div className="block"><h2 className="group-title">现在在等谁、等哪件事</h2><p className="t-foot">会话方向只说明谁最后说了话。具体事项请单独登记；其他会话的新消息、起草催问、登记催问都不会重置原等待。</p>
    {q.isLoading ? <Skeleton /> : q.isError ? <WorkflowError message={`等待情况没读到：${q.error.message}`} refresh={() => q.refetch()} /> : <div className="group">
      {q.data?.participants.map((p) => <div key={p.party_id} style={{ padding: 16, borderBottom: "1px solid var(--line)" }}><strong>{ROLE[p.role]} · {p.name}</strong><div className="t-foot">{p.availability_text}</div>{p.conversations.map((c) => <div key={c.key} className="t-foot" style={{ marginTop: 8 }}><Link to={`/inbox?conversation=${encodeURIComponent(c.key)}`}>{c.title}</Link> · {direction(c.state)} · {c.availability_text}{c.since ? ` · 起点 ${formatTime(c.since)}` : ""}{c.last_reminded_at ? ` · 上次催问 ${formatTime(c.last_reminded_at)}` : ""}</div>)}</div>)}
      {q.data?.items.map((item) => <WaitingEntry key={item.id} item={item} dealId={dealId} refresh={refresh} />)}
      {q.data?.items.length === 0 && <p className="t-sub" style={{ padding: 16 }}>还没有登记具体事项。选择对象和会话，把正在等的报价、交期、包装或付款说明写下来。</p>}
      <div style={{ padding: 16, display: "grid", gap: 10 }}>
        <label className="t-foot">具体对象与会话<select aria-label="等待对象与会话" value={target} onChange={(e) => setTarget(e.target.value)}><option value="">请选择本案已关联的会话</option>{targets.map((t) => <option key={t.value} value={t.value}>{ROLE[t.party.role]} · {t.party.name} · {t.conv.title}</option>)}</select></label>
        <label className="t-foot">正在等什么<input autoComplete="off" value={title} maxLength={500} onChange={(e) => setTitle(e.target.value)} placeholder="例如：等这家供应商确认 ZX-200 的包装尺寸" /></label>
        <label className="t-foot">涉及本案哪项产品<select aria-label="等待事项涉及的产品" value={product} onChange={(e) => setProduct(e.target.value)}><option value="">不指定产品，仅按事项与人工补充起草</option>{q.data?.products.map((p) => <option value={p.id} key={p.id}>{p.name}{p.qty ? ` · ${p.qty} 件` : ""}</option>)}</select></label>
        <label className="t-foot">等哪一方<select value={waiting} onChange={(e) => setWaiting(e.target.value)}><option value="them">等对方处理</option><option value="us">等我方处理</option></select></label>
        <label className="t-foot">等待起点（留空按现在登记）<input type="datetime-local" value={since} onChange={(e) => setSince(e.target.value)} /></label>
        <button type="button" className="btn" disabled={create.isPending || !selected || !title.trim()} onClick={() => create.mutate()}>登记这项等待</button>
      </div>
      <p className="t-cap" style={{ padding: "0 16px 16px" }}>操作结果不明确时先点「重新读取」核对；页面不会自动重试登记或发送。<button type="button" className="btn sm plain" onClick={() => q.refetch()}>重新读取</button></p>
    </div>}
  </div>;
}
