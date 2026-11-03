import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { get } from "../../api";
import { todayIso } from "../../archive";
import type { Participant } from "../../deals";
import { STAGES } from "../../deals";
import { fromFen, toFen, NODE_STATUS, PAY_KIND, PAY_METHOD, RESP, type Fulfilment, type Node, type Payment } from "../../fulfil";
import { Chip, Sheet, Skeleton } from "../../ui/bits";
import { useDealAction } from "./actions";
import "./deals.css";

export function Payments({ dealId }: { dealId: number }) {
  const q = useQuery({ queryKey: ["payments", dealId], queryFn: () => get<{ items: Payment[] }>(`/api/v1/deals/${dealId}/payments`), retry: false });
  const action = useDealAction();
  const [adding, setAdding] = useState(false);
  const [confirming, setConfirming] = useState<Payment | null>(null);
  const [f, setF] = useState({ kind: "full", amount: "", currency: "USD", received_on: todayIso(), method: "wire", note: "", currency_note: "" });
  return <section className="block" id="deal-payments"><h2 className="group-title">收款：到账后由你确认</h2>
    {action.message && <p role="status" className="ops-feedback">{action.message}</p>}{action.error && <p role="alert" className="callout">{action.error}</p>}
    {q.isPending && <Skeleton lines={2} />}{q.isError && <p role="alert">收款记录没能读到。<button className="btn" onClick={() => q.refetch()}>重新读取收款</button></p>}
    {q.isSuccess && <div className="group">{q.data.items.length === 0 && <p className="row">还没有登记收款。先核对到账依据，再记一笔。</p>}{q.data.items.map((p) => <div className="ops-node" key={p.id}><div className="ops-node-main">{PAY_KIND[p.kind]} {p.currency} {fromFen(p.amount_fen)}<p className="t-foot">{p.received_on} · {PAY_METHOD[p.method]}</p><Chip tone={p.status === "confirmed" ? "green" : "orange"}>{p.status === "confirmed" ? "已确认到账" : p.status === "void" ? "已作废" : "待核对到账"}</Chip></div>{p.status === "pending" && <button className="btn primary" disabled={action.busy} onClick={() => setConfirming(p)}>{p.kind === "refund" ? "核对退款" : "确认收款"}</button>}</div>)}<div className="row"><button className="btn" onClick={() => setAdding(true)}>记一笔收款</button></div></div>}
    <Sheet open={adding} title="记一笔收款" onClose={() => setAdding(false)}><form className="ops-form" onSubmit={async (e) => { e.preventDefault(); const amount = toFen(f.amount); if (amount && await action.run(`deals/${dealId}/payments`, { kind: f.kind, amount_fen: amount, currency: f.currency, received_on: f.received_on, method: f.method, note: f.note, currency_note: f.currency_note }, "已登记待核对收款；案子尚未成交")) setAdding(false); }}>
      <label>类型<select value={f.kind} onChange={(e) => setF({ ...f, kind: e.target.value })}>{Object.entries(PAY_KIND).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></label>
      <label>金额<input required inputMode="decimal" value={f.amount} onChange={(e) => setF({ ...f, amount: e.target.value })} /></label><label>币种<input required maxLength={3} value={f.currency} onChange={(e) => setF({ ...f, currency: e.target.value.toUpperCase() })} /></label>
      <label>收到日期<input required type="date" max={todayIso()} value={f.received_on} onChange={(e) => setF({ ...f, received_on: e.target.value })} /></label><label>收款方式<select value={f.method} onChange={(e) => setF({ ...f, method: e.target.value })}>{Object.entries(PAY_METHOD).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></label>
      <label>到账依据或备注<input value={f.note} onChange={(e) => setF({ ...f, note: e.target.value })} /></label><label>币种差异说明<input value={f.currency_note} onChange={(e) => setF({ ...f, currency_note: e.target.value })} /></label>
      {action.error && <p role="alert">{action.error}</p>}<div className="sheet-actions"><button type="button" className="btn" onClick={() => setAdding(false)}>取消</button><button className="btn primary" disabled={!toFen(f.amount) || !/^[A-Z]{3}$/.test(f.currency) || action.busy}>登记待核对</button></div>
    </form></Sheet>
    <Sheet open={confirming != null} title="再次核对到账" onClose={() => setConfirming(null)}><p>请核对银行或支付平台记录：{confirming?.currency} {fromFen(confirming?.amount_fen)}。</p><p className="callout">{confirming?.kind === "refund" ? "确认退款后将扣减已收款。" : "确认到账后，案子会标记为已成交并生成备货待办；这一步必须由你核实后点击。"}</p>
      <label className="ops-form">币种差异说明<input value={f.currency_note} onChange={(e) => setF({ ...f, currency_note: e.target.value })} /></label>{action.error && <p role="alert">{action.error}</p>}
      <div className="sheet-actions"><button className="btn" onClick={() => setConfirming(null)}>返回核对</button><button className="btn primary" disabled={action.busy} onClick={async () => { if (confirming && await action.run(`payments/${confirming.id}/confirm`, { confirm: "1", qualifies: confirming.kind === "refund" ? "0" : "1", currency_note: f.currency_note }, confirming.kind === "refund" ? "退款已确认，已收款已扣减" : "已确认到账，案子已成交，备货待办已生成")) setConfirming(null); }}>我已核对，{confirming?.kind === "refund" ? "确认退款" : "确认到账并成交"}</button></div>
    </Sheet>
  </section>;
}

const special: Record<string, [string, string, string][]> = { supplier_confirmation: [["promised_on", "供应商承诺交期", "date"]], inspection: [["inspector", "验货人", "text"]], booking: [["carrier", "船司或航班", "text"], ["tracking_number", "提单或运单号", "text"], ["etd", "预计离港", "date"], ["eta", "预计到达", "date"], ["departed_on", "实际离港", "date"]], signoff: [["signed_on", "实际签收日", "date"]] };

export function NodeEditor({ dealId, node, close, participants = [] }: { dealId: number; node: Node | null; close: () => void; participants?: Participant[] }) {
  const action = useDealAction();
  const [f, setF] = useState<Record<string, string>>({ status: node?.status ?? "todo", responsible: node?.responsible ?? "us", party_id: String(node?.party_id ?? ""), planned_on: node?.planned_on ?? "", completed_on: node?.completed_on ?? "", note: node?.note ?? "", inspection_result: node?.inspection_result ?? "", ...Object.fromEntries((special[node?.node ?? ""] ?? []).map(([k]) => [k, String((node as unknown as Record<string, unknown>)?.[k] ?? "")])) });
  return <Sheet open={!!node} title={node?.label ?? "记录进度"} onClose={close}><form className="ops-form" onSubmit={async (e) => { e.preventDefault(); if (!node) return; const fields = Object.fromEntries(Object.entries(f).filter(([k, v]) => v !== "" || ["note", "party_id", "planned_on"].includes(k)).filter(([k]) => k !== "inspection_result" || node.node === "inspection")); if (await action.run(`deals/${dealId}/fulfilment/${node.node}/update`, fields, "进度已保存并记入案子经过")) close(); }}>
    <label>进展<select aria-label="进展" value={f.status} onChange={(e) => setF({ ...f, status: e.target.value })}>{Object.entries(NODE_STATUS).map(([k, v]) => <option key={k} value={k}>{v.text}</option>)}</select></label>
    <label>计划完成日<input type="date" value={f.planned_on} onChange={(e) => setF({ ...f, planned_on: e.target.value })} /></label>
    {f.status === "done" && <label>实际完成日<input type="date" max={todayIso()} value={f.completed_on} onChange={(e) => setF({ ...f, completed_on: e.target.value })} /></label>}
    <label>负责方<select value={f.responsible} onChange={(e) => setF({ ...f, responsible: e.target.value, party_id: "" })}>{["us", "supplier", "forwarder", "vendor"].map((k) => <option key={k} value={k}>{RESP[k]}</option>)}</select></label>
    <label>具体单位<select value={f.party_id} onChange={(e) => setF({ ...f, party_id: e.target.value })}><option value="">暂不指定</option>{participants.filter((p) => p.role === f.responsible).map((p) => <option key={p.party_id} value={p.party_id}>{p.name}</option>)}</select></label>
    {node?.node === "inspection" && <label>验货结果<select aria-label="验货结果" required={f.status === "done"} value={f.inspection_result} onChange={(e) => setF({ ...f, inspection_result: e.target.value })}><option value="">待核对</option><option value="pass">通过</option><option value="fail">未通过</option><option value="needs_fix">需要整改</option></select></label>}
    {(special[node?.node ?? ""] ?? []).map(([key, label, type]) => <label key={key}>{label}<input type={type} value={f[key]} onChange={(e) => setF({ ...f, [key]: e.target.value })} /></label>)}
    <label>进度备注<textarea value={f.note} onChange={(e) => setF({ ...f, note: e.target.value })} /></label>
    {action.error && <p role="alert">{action.error}</p>}<p className="t-foot">保存只记录实际进度。采购单另存演练草稿，由人核对。</p><div className="sheet-actions"><button type="button" className="btn" onClick={close}>取消</button><button className="btn primary" disabled={action.busy}>保存节点</button></div>
  </form></Sheet>;
}

export function FulfilmentActions({ dealId, participants }: { dealId: number; participants: Participant[] }) {
  const q = useQuery({ queryKey: ["fulfilment", dealId], queryFn: () => get<Fulfilment>(`/api/v1/deals/${dealId}/fulfilment`), retry: false });
  const [node, setNode] = useState<Node | null>(null);
  const [party, setParty] = useState("");
  const action = useDealAction();
  if (q.isPending) return <Skeleton lines={2} />;
  if (q.isError) return <p role="alert">备货进度没能读到。<button className="btn" onClick={() => q.refetch()}>重读备货进度</button></p>;
  if (!q.data.eligible) return null;
  return <section className="block" id="deal-fulfil"><h2 className="group-title">备货与发货 · 已完成 {q.data.done}/{q.data.total}</h2>
    {action.message && <p role="status" className="ops-feedback">{action.message}</p>}{action.error && <p role="alert">{action.error}</p>}
    <div className="group">{q.data.items.map((n) => <div className={`ops-node${n.overdue ? " overdue" : ""}`} key={n.node}><div className="ops-node-main"><b>{n.label}</b> <Chip tone={n.overdue ? "red" : NODE_STATUS[n.status].tone}>{n.overdue ? "已超期" : NODE_STATUS[n.status].text}</Chip><p className="t-foot">{n.planned_on ? `计划 ${n.planned_on}` : "请安排日期"}{n.completed_on ? ` · 完成 ${n.completed_on}` : ""}</p></div><button className="btn" onClick={() => setNode(n)}>记录{n.label}</button></div>)}</div>
    <div className="ops-actions"><label>采购供应商<select aria-label="采购供应商" value={party} onChange={(e) => setParty(e.target.value)}><option value="">请选择本案供应商</option>{participants.filter((p) => p.role === "supplier").map((p) => <option key={p.party_id} value={p.party_id}>{p.name}</option>)}</select></label><button className="btn" disabled={!party || action.busy} onClick={() => action.run(`deals/${dealId}/drafts`, { kind: "supplier_order", party_id: party }, "采购单已存演练草稿，未发送")}>起草采购单</button></div>
    {q.data.suggestions.map((s) => <div className="ops-actions" key={s.stage}><p>{s.reason}</p><button className="btn" disabled={action.busy} onClick={() => { if (window.confirm(`确认实际进展已达到「${STAGES.find((st) => st.key === s.stage)?.label}」？`)) void action.run(`deals/${dealId}/update`, { stage: s.stage }, "阶段已更新，待办已生成"); }}>确认进入{STAGES.find((st) => st.key === s.stage)?.label}</button></div>)}
    {node && <NodeEditor key={node.node} dealId={dealId} node={node} close={() => setNode(null)} participants={participants} />}
  </section>;
}
