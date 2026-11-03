import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router";
import { get, postForm } from "../api";
import { formatTime } from "../format";
import { Chip, Skeleton } from "./bits";
import { useToast } from "./Toast";
import { WorkflowError } from "./WorkflowError";

interface Candidate {
  id: number; status: string; name: string; qty: number | null; destination: string | null;
  specifications: string | null; accessories: string | null;
  source: { conversation_key: string; text: string; sent_at?: number | string }; accepted_item_id?: number | null;
}
interface Candidates { items: Candidate[]; total: number; conversations: { key: string; title: string }[] }
const FIELDS = { name: "产品或型号", qty: "数量", destination: "目的地", specifications: "规格要求", accessories: "配套要求" };

function ReviewDemand({ candidate: c, dealId }: { candidate: Candidate; dealId: number }) {
  const client = useQueryClient();
  const toast = useToast();
  const [fields, setFields] = useState(Object.fromEntries(Object.keys(FIELDS).map((key) => [key, String(c[key as keyof Candidate] ?? "")])));
  const mutation = useMutation({
    mutationFn: (action: "accept" | "dismiss") => postForm(`/api/v1/deals/${dealId}/demands/${c.id}/${action}`, action === "accept" ? fields : {}),
    retry: false,
    onSuccess: () => { client.invalidateQueries({ queryKey: ["deal-demands", dealId] }); client.invalidateQueries({ queryKey: ["deal", String(dealId)] }); client.invalidateQueries({ queryKey: ["deal-offers", dealId] }); client.invalidateQueries({ queryKey: ["deal-waiting", dealId] }); toast("已记录你的确认"); },
    onError: (e: Error) => toast(e.message, "error"),
  });
  return <div style={{ padding: 16, borderBottom: "1px solid var(--line)", display: "grid", gap: 10 }}>
    <div><Chip tone={c.status === "accepted" ? "green" : "orange"}>{c.status === "accepted" ? "已入产品清单" : c.status === "dismissed" ? "已忽略" : "待你确认"}</Chip> <Link to={`/inbox?conversation=${encodeURIComponent(c.source.conversation_key)}`}>查看来源会话</Link> <span className="t-cap">{formatTime(c.source.sent_at)}</span></div>
    {c.source.text && <blockquote style={{ margin: 0, whiteSpace: "pre-wrap" }}>{c.source.text}</blockquote>}
    {c.status === "pending" ? <>
      <div className="form-grid">{Object.entries(FIELDS).map(([key, label]) => <label key={key}><span className="t-foot">{label}</span><input aria-label={label} autoComplete="off" value={fields[key]} onChange={(e) => setFields({ ...fields, [key]: e.target.value })} placeholder="原文没说清的先留空" /></label>)}</div>
      <div style={{ display: "flex", gap: 8 }}><button type="button" className="btn primary" disabled={mutation.isPending || !fields.name.trim()} onClick={() => mutation.mutate("accept")}>核对好了，加入产品清单</button><button type="button" className="btn" disabled={mutation.isPending} onClick={() => mutation.mutate("dismiss")}>忽略这条</button></div>
    </> : <div>{c.name}{c.qty ? ` · ${c.qty} 件` : " · 数量待确认"}{c.specifications ? ` · ${c.specifications}` : ""}</div>}
  </div>;
}

export function DemandCandidates({ dealId }: { dealId: number }) {
  const toast = useToast();
  const client = useQueryClient();
  const [conversation, setConversation] = useState("");
  const q = useQuery({ queryKey: ["deal-demands", dealId], queryFn: () => get<Candidates>(`/api/v1/deals/${dealId}/demands`), retry: false });
  const scan = useMutation({ mutationFn: () => postForm(`/api/v1/deals/${dealId}/demands/scan`, conversation ? { conversation_key: conversation } : {}), retry: false,
    onSuccess: () => { client.invalidateQueries({ queryKey: ["deal-demands", dealId] }); toast("已提取候选，请逐条核对"); }, onError: (e: Error) => toast(e.message, "error") });
  return <div className="block"><h2 className="group-title">询盘里的产品需求</h2>
    <p className="t-foot">从客户原文提取，核对后才加入产品清单。没说清的字段留空；不会生成价格或包装承诺。</p>
    {q.isLoading ? <Skeleton /> : q.isError ? <WorkflowError message={`需求候选没读到：${q.error.message}`} refresh={() => q.refetch()} /> : <div className="group">
      <div style={{ padding: 16, display: "flex", flexWrap: "wrap", gap: 8 }}><label>客户来源 <select aria-label="需求来源会话" value={conversation} onChange={(e) => setConversation(e.target.value)}><option value="">本案来源询盘</option>{q.data?.conversations.map((c) => <option value={c.key} key={c.key}>{c.title}</option>)}</select></label><button type="button" className="btn" disabled={scan.isPending} onClick={() => scan.mutate()}>提取待确认需求</button></div>
      {q.data?.items.map((c) => <ReviewDemand key={c.id} candidate={c} dealId={dealId} />)}
      {q.data?.items.length === 0 && <p style={{ padding: "0 16px 16px" }} className="t-sub">还没有候选。先关联有客户身份的询盘会话，再提取；也可以手工填写产品清单。</p>}
    </div>}
  </div>;
}
