import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { Link, useNavigate } from "react-router";
import { get, postForm, qs } from "../api";
import type { DealRow } from "../deals";
import { ISSUE_STATUS, fromFen, NODE_STATUS, type Node, type ReorderLead } from "../fulfil";
import { Chip, Empty, Skeleton } from "../ui/bits";
import { ReadFailure, WriteRecovery, useWriteRecovery } from "../ui/ReadState";
import { useToast } from "../ui/Toast";

import { NodeEditor } from "./deals/Fulfilment";
import "./deals/deals.css";

interface Row { deal_id: number; title: string; stage: string; done: number; total: number; next_due: string | null; overdue: boolean; items: Node[] }
const STAGE: Record<string, string> = { paid: "已成交·备货", inspection: "验货", shipped: "已发货", delivered: "已签收", aftersales: "售后", reorder: "返单" };

export function FulfilBoardPage() {
  const [editing, setEditing] = useState<{ dealId: number; node: Node } | null>(null);
  const navigate = useNavigate();
  const toast = useToast();
  const client = useQueryClient();
  const q = useQuery({ queryKey: ["fulfil-board"], queryFn: () => get<{ items: Row[]; total: number }>("/api/v1/fulfilment/board"), retry: false });
  const leads = useQuery({ queryKey: ["reorder-leads"], queryFn: () => get<{ items: ReorderLead[] }>("/api/v1/reorder/leads"), retry: false });
  const source = useRef<ReorderLead | null>(null);
  const [candidates, setCandidates] = useState<DealRow[]>([]);
  const recovery = useWriteRecovery(async () => {
    const customerId = source.current?.customer.id;
    const results = await Promise.allSettled([
      leads.refetch({ throwOnError: true }),
      customerId ? get<{ items: DealRow[] }>(`/api/v1/deals${qs({ status: "all", party: customerId, page_size: 100 })}`).then((result) => setCandidates(result.items)) : Promise.resolve(),
    ]);
    for (const result of results) if (result.status === "rejected") throw result.reason;
  });
  const copy = useMutation({
    mutationFn: (lead: ReorderLead) => postForm<{ id: number }>(`/api/v1/deals/${lead.deal_id}/reorder`, {}),
    retry: false,
    onSuccess: (result) => { client.invalidateQueries({ queryKey: ["reorder-leads"] }); client.invalidateQueries({ queryKey: ["deals"] }); toast("已创建返单草稿，请重新核对价格和交期"); navigate(`/deals/${result.id}`); },
    onError: (error: Error) => recovery.fail(error),
  });
  const items = q.isSuccess ? q.data.items : [];
  return (
    <>
      <div className="page-head"><div><h1 className="t-large">备货与发货</h1><p>已经成交的案子，现在走到哪一步、哪个延期了。延期的排在最前面。</p></div></div>
      {q.isPending && <Skeleton lines={4} />}
      {q.isError && <ReadFailure subject="履约进度" error={q.error} retry={() => q.refetch()} busy={q.isFetching} />}
      {q.isSuccess && items.length === 0 && <div className="group"><Empty title="还没有已成交的案子" action={<Link className="btn primary" to="/deals?stage=awaiting_payment">去案子核对收款</Link>}>客户付款、你在案子里点“确认收款”后，案子会出现在这里。</Empty></div>}
      {items.length > 0 && (
        <div className="group">
          {items.map((r) => {
            const cur = r.items.find((n) => n.status === "doing") ?? r.items.find((n) => n.status !== "done");
            return (
              <section key={r.deal_id} className="row" style={{ display: "block" }}><Link to={`/deals/${r.deal_id}`} className="btn sm">打开案子</Link>
                <div className="row-main">
                  <div className="row-title">{r.title} <Chip tone="plain">{STAGE[r.stage] ?? r.stage}</Chip>{r.overdue && <Chip tone="red">有延期</Chip>}</div>
                  <div className="row-sub num">已完成 {r.done}/{r.total}{cur ? ` · 现在：${cur.label}` : " · 全部完成"}{r.next_due ? ` · 下个期限 ${r.next_due}` : ""}</div>
                </div>
                {r.items.map((n) => <div className={`ops-node${n.overdue ? " overdue" : ""}`} key={n.node}><div className="ops-node-main">{n.label} <Chip tone={n.overdue ? "red" : NODE_STATUS[n.status].tone}>{n.overdue ? "已超期" : NODE_STATUS[n.status].text}</Chip><p className="t-foot">{n.planned_on ? `计划 ${n.planned_on}` : "尚未排期"}{n.completed_on ? ` · 完成 ${n.completed_on}` : ""}</p></div><button className="btn" onClick={() => setEditing({ dealId: r.deal_id, node: n })}>记录{n.label}</button></div>)}
              </section>
            );
          })}
        </div>
      )}
      {editing && <NodeEditor key={`${editing.dealId}-${editing.node.node}`} dealId={editing.dealId} node={editing.node} close={() => setEditing(null)} />}
      <div className="block" style={{ marginTop: 24 }}>
        <h2 className="group-title">返单线索<span className="count num">{leads.isSuccess ? leads.data.items.length : ""}</span></h2>
        <WriteRecovery recovery={recovery} subject="创建返单草稿" />
        {recovery.blocked && candidates.length > 0 && <div className="group"><div className="row t-sub">同一客户的案子，请先打开核对，确认有没有刚创建的返单草稿。</div>{candidates.map((d) => <Link className="row" to={`/deals/${d.id}`} key={d.id}>案子 #{d.id} · {d.title}</Link>)}</div>}
        {leads.isPending && <Skeleton lines={2} />}
        {leads.isError && <ReadFailure subject="返单线索" error={leads.error} retry={() => leads.refetch()} busy={leads.isFetching} />}
        {leads.isSuccess && <div className="group">
          {leads.data.items.length === 0 && <div className="row t-sub">暂时没有达到跟进天数的返单线索。新建过后续案子的客户也不会重复列出。</div>}
          {leads.data.items.map((l) => (
            <div className="row" key={l.deal_id} style={{ display: "block" }}>
              <Link className="row-title" to={`/deals/${l.deal_id}`}>{l.customer.name} <span className="t-cap">{l.title}</span></Link>
              <p className="t-foot">{l.suggestion}</p>
              <details style={{ margin: "10px 0" }}>
                <summary>预览上次售价、反馈和供应商</summary>
                <div className="t-foot" style={{ display: "grid", gap: 6, marginTop: 8 }}>
                  <b>上次客户接受的售价（只供核对，不带入新案子）</b>
                  {l.products.length === 0 && <span>没有可预览的产品与成交售价。</span>}
                  {l.products.map((p, index) => <span key={index}>{p.name} · 数量 {p.qty ?? "待确认"} · {p.sell_unit_minor == null ? "售价待核对" : `${p.currency} ${fromFen(p.sell_unit_minor)}（单价，单位请在原报价核对）`}</span>)}
                  <b>供应商</b>
                  {l.suppliers.length === 0 ? <span>没有可核对的供应商关联。</span> : l.suppliers.map((s) => <span key={s.id}>{s.name} · {s.source_label}</span>)}
                  <b>客户反馈与售后</b>
                  {l.feedback.length === 0 ? <span>没有记录过反馈与售后问题。</span> : l.feedback.map((f) => <span key={f.id}>{f.kind_label} · {ISSUE_STATUS[f.status]?.text ?? f.status}：{f.description}{f.resolution ? `；结论：${f.resolution}` : ""}</span>)}
                </div>
              </details>
              <button type="button" className="btn" disabled={copy.isPending || recovery.blocked || leads.isFetching} onClick={() => { if (window.confirm("创建一份返单草稿？只带客户和产品，价格、收款、履约与供应商都要重新确认。")) { source.current = l; setCandidates([]); copy.mutate(l); } }}>创建返单草稿</button>
            </div>
          ))}
        </div>}
      </div>
    </>
  );
}
