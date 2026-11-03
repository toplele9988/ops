import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Copy, Plus } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router";
import { copyText, get, postForm } from "../api";
import type { QuoteSummary } from "../quotes";
import type { Participant } from "../deals";
import { STAGES } from "../deals";
import { ago } from "../format";
import { Fulfilment, ISSUE_KIND, ISSUE_STATUS, Issue, NODE_STATUS, PAY_KIND, PAY_METHOD, Payment, PaySummary, RESP, SEVERITY_TEXT, fromFen, toFen } from "../fulfil";
import { Chip, Sheet, Skeleton } from "./bits";
import { useToast } from "./Toast";
import { ReadFailure, WriteRecovery, useWriteRecovery } from "./ReadState";

const today = () => new Date().toISOString().slice(0, 10);

function useAct(invalidate: string[], readback: () => Promise<unknown>) {
  const toast = useToast();
  const client = useQueryClient();
  const recovery = useWriteRecovery(readback);
  const m = useMutation({
    mutationFn: ({ path, fields }: { path: string; fields: Record<string, string> }) => postForm<Record<string, unknown>>(`/api/v1/${path}`, fields),
    retry: false, onError: (e: Error) => recovery.fail(e),
  });
  const run = (path: string, fields: Record<string, string>, ok: string, after?: () => void) =>
    !recovery.blocked && m.mutate({ path, fields }, { onSuccess: () => { toast(ok); invalidate.forEach((k) => client.invalidateQueries({ queryKey: [k] })); client.invalidateQueries({ queryKey: ["deal"] }); client.invalidateQueries({ queryKey: ["deals"] }); client.invalidateQueries({ queryKey: ["deals-summary"] }); after?.(); } });
  return { run, pending: m.isPending || recovery.blocked, recovery };
}

/** 收款：只有人点“确认收款”才算数；确认后案子进入已成交。 */
export function PaymentsPanel({ dealId, currency, dealStatus, quotes = [] }: { dealId: number; currency?: string; dealStatus?: string; quotes?: QuoteSummary[] }) {
  const q = useQuery({ queryKey: ["payments", dealId], queryFn: () => get<{ items: Payment[]; summary: PaySummary[] }>(`/api/v1/deals/${dealId}/payments`), retry: false });
  const client = useQueryClient();
  const { run, pending, recovery } = useAct(["payments", "fulfilment"], async () => { await q.refetch({ throwOnError: true }); const deal = await get(`/api/v1/deals/${dealId}`); client.setQueryData(["deal", String(dealId)], deal); });
  const [adding, setAdding] = useState(false);
  const [confirming, setConfirming] = useState<Payment | null>(null);
  const [f, setF] = useState({ kind: "deposit", amount: "", currency: currency ?? "USD", received_on: today(), method: "wire", note: "", quote_round_id: "auto", currency_note: "" });
  const [qualifies, setQualifies] = useState(false);
  const [currencyNote, setCurrencyNote] = useState("");
  const set = (k: string) => (e: { target: { value: string } }) => setF({ ...f, [k]: e.target.value });
  const fen = toFen(f.amount);
  const items = q.data?.items ?? [];
  const earlier = items.some((p) => p.status === "confirmed" && (p.kind === "deposit" || p.kind === "full" || p.qualifies === 1));
  const firstPayment = confirming != null && (confirming.kind === "deposit" || confirming.kind === "full") && !earlier;
  if (q.isError) return <div className="block" id="deal-payments"><h2 className="group-title">收款</h2><ReadFailure subject="收款记录" error={q.error} retry={() => q.refetch()} busy={q.isFetching} /><WriteRecovery recovery={recovery} subject="收款操作" /></div>;
  if (q.isPending) return <div className="block" id="deal-payments"><h2 className="group-title">收款</h2><Skeleton lines={2} /></div>;
  return (
    <div className="block" id="deal-payments">
      <h2 className="group-title">收款<span className="count">客户付款，才算成交</span></h2>
      <WriteRecovery recovery={recovery} subject="收款登记或确认" />
      {dealStatus === "won" && !items.some((p) => p.confirmed_at && p.kind !== "refund") && <p className="callout">此案保留历史成交状态，但没有可核对的确认收款记录。请先核对原始到账依据，不要因这里没有记录就重复登记。</p>}
      <div className="group">
        {(q.data?.summary ?? []).filter((s) => s.received_fen || s.receivable_fen).map((s) => (
          <div className="row" key={s.currency}><div className="row-main num"><div className="row-title">{s.currency}　已收 {fromFen(s.received_fen)}</div><div className="row-sub">{s.receivable_fen != null ? `应收 ${fromFen(s.receivable_fen)} · 还差 ${fromFen(s.unreceived_fen)}` : "应收金额要等有客户接受的报价后才能算"}</div></div></div>
        ))}
        {items.length === 0 && dealStatus !== "won" && <div className="row"><div className="row-main t-sub">还没有收款记录。客户付了款，点下面的按钮记一笔，核对无误后再点“确认收款”。</div></div>}
        {items.map((p) => (
          <div className="row" key={p.id} style={{ alignItems: "flex-start" }}>
            <div className="row-main">
              <div className="row-title">{PAY_KIND[p.kind]} <span className="num">{p.currency} {fromFen(p.amount_fen)}</span> <Chip tone={p.status === "confirmed" ? "green" : p.status === "pending" ? "orange" : "plain"}>{p.status === "confirmed" ? "已确认" : p.status === "pending" ? "待核对" : "已作废"}</Chip></div>
              <div className="row-sub">{p.received_on} · {PAY_METHOD[p.method] ?? p.method}{p.note ? ` · ${p.note}` : ""}{p.confirmed_at ? ` · ${p.confirmed_by ?? "操作人待核对"}确认于 ${ago(p.confirmed_at)}` : ""}</div>
              <div className="row-sub">关联报价：{p.quote_round_id ? <Link to={`/quotes/${p.quote_round_id}`}>报价 #{p.quote_round_id}</Link> : "未关联"}{p.currency_note ? ` · 币种差异说明：${p.currency_note}` : ""}</div>
            </div>
            {p.status === "pending" && <button type="button" className="btn sm primary" disabled={pending} onClick={() => { setQualifies(false); setCurrencyNote(p.currency_note ?? ""); setConfirming(p); }}>{p.kind === "refund" ? "确认退款" : "确认收款"}</button>}
            {p.status !== "void" && <button type="button" className="btn sm plain" disabled={pending} onClick={() => { if (window.confirm("作废这笔收款记录？作废后不再计入已收。")) run(`payments/${p.id}/void`, {}, "已作废"); }}>作废</button>}
          </div>
        ))}
        <div className="row"><button type="button" className="btn" disabled={pending} onClick={() => setAdding(true)}><Plus size={16} aria-hidden="true" />记一笔收款</button></div>
      </div>
      <Sheet open={adding} title="记一笔收款" onClose={() => setAdding(false)}>
        <WriteRecovery recovery={recovery} subject="收款登记" />
        <div className="form-grid" style={{ gridTemplateColumns: "1fr 1fr" }}>
          <label><span>类型</span><select name="kind" value={f.kind} onChange={set("kind")}>{Object.entries(PAY_KIND).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></label>
          <label><span>收款方式</span><select name="method" value={f.method} onChange={set("method")}>{Object.entries(PAY_METHOD).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></label>
          <label><span>金额</span><input name="amount" inputMode="decimal" autoComplete="off" placeholder="例如 1200.50…" value={f.amount} onChange={set("amount")} /></label>
          <label><span>币种</span><input name="currency" autoComplete="off" value={f.currency} onChange={(e) => setF({ ...f, currency: e.target.value.toUpperCase() })} /></label>
          <label><span>收到日期</span><input name="received_on" type="date" value={f.received_on} onChange={set("received_on")} /></label>
          <label className="span2"><span>关联报价轮</span><select name="quote_round_id" value={f.quote_round_id} onChange={set("quote_round_id")}><option value="auto">默认关联最新一轮客户接受的报价</option><option value="">不关联报价</option>{quotes.map((r) => <option key={r.id} value={r.id}>第 {r.round_no} 轮 · {r.currency}{r.status === "accepted" ? " · 客户接受" : ""}</option>)}</select></label>
          <label className="span2"><span>币种差异说明（与报价币种不同时必填，不自动换汇）</span><input name="currency_note" maxLength={1000} value={f.currency_note} onChange={set("currency_note")} /></label>
          <label className="span2"><span>备注（比如银行流水后四位）</span><input name="note" autoComplete="off" value={f.note} onChange={set("note")} /></label>
        </div>
        {f.amount && !fen && <p className="t-foot" style={{ color: "var(--red)" }}>金额只能是数字，最多两位小数。</p>}
        <div className="sheet-actions"><button type="button" className="btn" onClick={() => setAdding(false)}>取消</button>
          <button type="button" className="btn primary" disabled={!fen || pending || f.currency.length !== 3} onClick={() => run(`deals/${dealId}/payments`, { kind: f.kind, amount_fen: fen!, currency: f.currency, received_on: f.received_on, method: f.method, ...(f.quote_round_id !== "auto" ? { quote_round_id: f.quote_round_id } : {}), ...(f.currency_note ? { currency_note: f.currency_note } : {}), ...(f.note ? { note: f.note } : {}) }, "已记下，等你核对后确认", () => { setAdding(false); setF({ ...f, amount: "", note: "" }); })}>记下</button></div>
      </Sheet>
      <Sheet open={confirming != null} title={confirming?.kind === "refund" ? "确认已经退回这笔款？" : "确认已经收到这笔款？"} onClose={() => setConfirming(null)}>
        <WriteRecovery recovery={recovery} subject="收款确认" />
        {confirming && <>
          <p>{PAY_KIND[confirming.kind]} <b className="num">{confirming.currency} {fromFen(confirming.amount_fen)}</b>，{confirming.received_on}，{PAY_METHOD[confirming.method]}。</p>
          <p className="t-foot">请对着银行或支付平台的记录核对。确认收款才计入已收；确认退款会扣减已收，不会让案子成交。</p>
          {firstPayment && <p className="callout">这是首笔达到规则的定金或全款。确认到账后就算成交；已成交案子保留当前履约阶段。</p>}
          <p className="t-foot">关联报价：{confirming.quote_round_id ? <Link to={`/quotes/${confirming.quote_round_id}`}>报价 #{confirming.quote_round_id}（请打开核对币种）</Link> : "未关联报价"}。收款币种：{confirming.currency}。</p>
          <label className="form-grid" style={{ display: "grid" }}><span>币种差异说明（与报价币种不同时必填）</span><input name="currency_note" maxLength={1000} value={currencyNote} onChange={(e) => setCurrencyNote(e.target.value)} /></label>
          {confirming.kind !== "refund" && !firstPayment && <label style={{ display: "flex", gap: 8, alignItems: "center", margin: "10px 0" }}><input type="checkbox" checked={qualifies} onChange={(e) => setQualifies(e.target.checked)} />这笔款达到成交条件（案子变成“已成交”）</label>}
        </>}
        <div className="sheet-actions"><button type="button" className="btn" onClick={() => setConfirming(null)}>再核对一下</button>
          <button type="button" className="btn primary" disabled={pending} onClick={() => confirming && run(`payments/${confirming.id}/confirm`, { confirm: "1", ...(confirming.kind !== "refund" ? { qualifies: firstPayment || qualifies ? "1" : "0" } : {}), currency_note: currencyNote }, "已确认，请核对收款记录和案子状态", () => setConfirming(null))}>{confirming?.kind === "refund" ? "确认退款" : "确认收到"}</button></div>
      </Sheet>
    </div>
  );
}

const FIELD: Record<string, [string, string][]> = {
  supplier_confirmation: [["promised_on", "供应商承诺交期"]], inspection: [["inspector", "验货人/公司"]],
  booking: [["carrier", "船司/航班"], ["tracking_number", "提单/运单号"], ["etd", "预计离港"], ["eta", "预计到达"], ["departed_on", "实际离港"]], signoff: [["signed_on", "签收日期"]],
};
const DATE_FIELDS = new Set(["promised_on", "etd", "eta", "departed_on", "signed_on"]);

/** 成交之后：下单、交期、验货、订舱、在途、签收。 */
export function FulfilmentPanel({ dealId, participants = [] }: { dealId: number; participants?: Participant[] }) {
  const q = useQuery({ queryKey: ["fulfilment", dealId], queryFn: () => get<Fulfilment>(`/api/v1/deals/${dealId}/fulfilment`), retry: false });
  const client = useQueryClient();
  const { run, pending, recovery } = useAct(["fulfilment"], async () => { await q.refetch({ throwOnError: true }); const deal = await get(`/api/v1/deals/${dealId}`); client.setQueryData(["deal", String(dealId)], deal); });
  const [edit, setEdit] = useState<import("../fulfil").Node | null>(null);
  const [f, setF] = useState<Record<string, string>>({});
  const d = q.data;
  if (q.isError) return <div className="block"><h2 className="group-title">备货与发货</h2><ReadFailure subject="履约进度" error={q.error} retry={() => q.refetch()} busy={q.isFetching} /><WriteRecovery recovery={recovery} subject="履约操作" /></div>;
  if (q.isPending) return <div className="block"><h2 className="group-title">备货与发货</h2><Skeleton lines={2} /></div>;
  if (!d || !d.eligible) return null;
  const open = (n: import("../fulfil").Node) => { setEdit(n); setF({ status: n.status, responsible: n.responsible, party_id: n.party_id == null ? "" : String(n.party_id), completed_on: n.completed_on ?? "", planned_on: n.planned_on ?? "", note: n.note ?? "", inspection_result: n.inspection_result ?? "", ...Object.fromEntries((FIELD[n.node] ?? []).map(([k]) => [k, String((n as unknown as Record<string, unknown>)[k] ?? "")])) }); };
  const save = () => {
    if (!edit) return;
    const fields: Record<string, string> = {};
    for (const [k, v] of Object.entries(f)) if (v !== "" || k === "note" || k === "party_id") fields[k] = v;
    run(`deals/${dealId}/fulfilment/${edit.node}/update`, fields, "已保存", () => setEdit(null));
  };
  return (
    <div className="block">
      <WriteRecovery recovery={recovery} subject="履约记录或阶段确认" />
      <h2 className="group-title">备货与发货<span className="count num">已完成 {d.done}/{d.total}{d.overdue ? " · 有延期" : ""}</span></h2>
      <div className="group">
        {d.items.map((n) => {
          const st = NODE_STATUS[n.status];
          return (
            <button type="button" className="row" key={n.node} disabled={pending} onClick={() => open(n)} style={{ alignItems: "flex-start", textAlign: "left" }}>
              <div className="row-main">
                <div className="row-title">{n.label} <Chip tone={n.overdue ? "red" : st.tone}>{n.overdue ? "已延期" : st.text}</Chip></div>
                <div className="row-sub num">{RESP[n.responsible] ?? n.responsible}{n.party_id ? ` · ${participants.find((p) => p.party_id === n.party_id)?.name ?? `单位 #${n.party_id}`}` : " · 未指定单位"}{n.due_on ? ` · 期限 ${n.due_on}` : ""}{n.completed_on ? ` · 完成 ${n.completed_on}` : ""}{n.tracking_number ? ` · 单号 ${n.tracking_number}` : ""}{n.inspection_result ? ` · 验货${n.inspection_result === "pass" ? "通过" : n.inspection_result === "fail" ? "没通过" : "要整改"}` : ""}</div>
              </div>
            </button>
          );
        })}
        {d.suggestions?.map((s, i) => <div className="row" key={i}><div className="row-main t-sub">建议：{s.reason}</div><button type="button" className="btn sm" disabled={pending} onClick={() => { const label = STAGES.find((stage) => stage.key === s.stage)?.label ?? s.stage; if (window.confirm(`${s.reason}。请核对实际进展，确认进入「${label}」？`)) run(`deals/${dealId}/update`, { stage: s.stage }, `已进入「${label}」`); }}>确认进入「{STAGES.find((stage) => stage.key === s.stage)?.label ?? s.stage}」</button></div>)}
      </div>
      <Sheet open={edit != null} wide title={edit?.label ?? ""} onClose={() => setEdit(null)}>
        <WriteRecovery recovery={recovery} subject="履约记录" />
        <div className="form-grid" style={{ gridTemplateColumns: "1fr 1fr" }}>
          <label><span>进展</span><select name="status" value={f.status} onChange={(e) => setF({ ...f, status: e.target.value })}>{Object.entries(NODE_STATUS).map(([k, v]) => <option key={k} value={k}>{v.text}</option>)}</select></label>
          <label><span>负责方</span><select name="responsible" value={f.responsible} onChange={(e) => setF({ ...f, responsible: e.target.value, party_id: "" })}>{["us", "supplier", "forwarder", "vendor"].map((k) => <option key={k} value={k}>{RESP[k]}</option>)}</select></label>
          <label><span>具体单位</span><select name="party_id" value={f.party_id} disabled={f.responsible === "us"} onChange={(e) => setF({ ...f, party_id: e.target.value })}><option value="">{f.responsible === "us" ? "我方负责" : "暂未指定，请先在案子中添加参与方"}</option>{participants.filter((p) => p.role === f.responsible).map((p) => <option key={p.party_id} value={p.party_id}>{p.name}</option>)}</select></label>
          <label><span>计划完成日</span><input name="planned_on" type="date" value={f.planned_on} onChange={(e) => setF({ ...f, planned_on: e.target.value })} /></label>
          {f.status === "done" && <label><span>实际完成日（不填就是今天）</span><input name="completed_on" type="date" value={f.completed_on} onChange={(e) => setF({ ...f, completed_on: e.target.value })} /></label>}
          {edit?.node === "inspection" && <label><span>验货结果</span><select name="inspection_result" value={f.inspection_result} onChange={(e) => setF({ ...f, inspection_result: e.target.value })}><option value="">还没结果</option><option value="pass">通过</option><option value="needs_fix">要整改</option><option value="fail">没通过</option></select></label>}
          {(FIELD[edit?.node ?? ""] ?? []).map(([k, label]) => <label key={k}><span>{label}</span><input name={k} type={DATE_FIELDS.has(k) ? "date" : "text"} autoComplete="off" value={f[k] ?? ""} onChange={(e) => setF({ ...f, [k]: e.target.value })} /></label>)}
          <label className="span2"><span>备注</span><input name="note" autoComplete="off" value={f.note} onChange={(e) => setF({ ...f, note: e.target.value })} /></label>
        </div>
        <p className="t-foot">这里只是记录进度，不会给供应商或货代发消息，也不会自动改案子阶段。</p>
        <div className="sheet-actions"><button type="button" className="btn" onClick={() => setEdit(null)}>取消</button><button type="button" className="btn primary" disabled={pending} onClick={save}>保存</button></div>
      </Sheet>
    </div>
  );
}

/** 售后问题与客户意见：记录、跟进、转给厂家/货代的说明草稿。 */
export function IssuesPanel({ dealId }: { dealId: number }) {
  const toast = useToast();
  const q = useQuery({ queryKey: ["issues", dealId], queryFn: () => get<{ items: Issue[] }>(`/api/v1/deals/${dealId}/issues`), retry: false });
  const { run, pending, recovery } = useAct(["issues"], async () => { await q.refetch({ throwOnError: true }); });
  const [adding, setAdding] = useState(false);
  const [f, setF] = useState({ kind: "quality", responsible: "supplier", severity: "medium", description: "" });
  const [draft, setDraft] = useState<string | null>(null);
  const [draftIssue, setDraftIssue] = useState<Issue | null>(null);
  const [draftError, setDraftError] = useState<unknown>(null);
  const [draftBusy, setDraftBusy] = useState(false);
  const [res, setRes] = useState<Issue | null>(null);
  const [resText, setResText] = useState("");
  const [note, setNote] = useState("");
  const items = q.data?.items ?? [];
  const showDraft = async (i: Issue) => {
    setDraftIssue(i); setDraft(null); setDraftError(null); setDraftBusy(true);
    try { const r = await get<{ text: string }>(`/api/v1/issues/${i.id}/draft?target=${i.responsible === "forwarder" ? "forwarder" : "supplier"}`); setDraft(r.text); } catch (e) { setDraftError(e); } finally { setDraftBusy(false); }
  };
  if (q.isError) return <div className="block"><h2 className="group-title">售后问题与客户意见</h2><ReadFailure subject="问题与意见记录" error={q.error} retry={() => q.refetch()} busy={q.isFetching} /><WriteRecovery recovery={recovery} subject="问题记录" /></div>;
  if (q.isPending) return <div className="block"><h2 className="group-title">售后问题与客户意见</h2><Skeleton lines={2} /></div>;
  return (
    <div className="block">
      <WriteRecovery recovery={recovery} subject="问题记录" />
      <h2 className="group-title">售后问题与客户意见<span className="count num">{items.filter((i) => i.status === "open" || i.status === "waiting_party").length ? `${items.filter((i) => i.status === "open" || i.status === "waiting_party").length} 个没解决` : ""}</span></h2>
      <div className="group">
        {items.length === 0 && <div className="row"><div className="row-main t-sub">没有记录。客户反映质量、少发、破损、延误，或者提了意见，都记在这里，再转给厂家或货代。</div></div>}
        {items.map((i) => (
          <div className="row" key={i.id} style={{ alignItems: "flex-start" }}>
            <div className="row-main" style={{ whiteSpace: "normal" }}>
              <div className="row-title" style={{ whiteSpace: "normal" }}>{ISSUE_KIND[i.kind]} · {SEVERITY_TEXT[i.severity]} <Chip tone={ISSUE_STATUS[i.status].tone}>{ISSUE_STATUS[i.status].text}</Chip> <span className="t-cap">责任：{RESP[i.responsible]}</span></div>
              <div className="row-sub" style={{ whiteSpace: "normal" }}>{i.description}</div>
              {i.resolution && <div className="row-sub" style={{ whiteSpace: "normal" }}>结论：{i.resolution}</div>}
              {i.notes.map((n) => <div className="row-sub" key={n.id} style={{ whiteSpace: "normal" }}>· {n.note} <span className="t-cap">{ago(n.at)}</span></div>)}
              <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginTop: 8 }}>
                {i.status === "open" && <button type="button" className="btn sm" disabled={pending} onClick={() => run(`issues/${i.id}/update`, { status: "waiting_party" }, "已标为等对方回复")}>已转给对方</button>}
                {(i.status === "open" || i.status === "waiting_party") && <button type="button" className="btn sm" onClick={() => { setRes(i); setResText(""); }}>已解决…</button>}
                {i.status === "resolved" && <button type="button" className="btn sm" disabled={pending} onClick={() => run(`issues/${i.id}/update`, { status: "closed", resolution: i.resolution ?? "" }, "已关闭")}>关闭</button>}
                {i.status !== "closed" && <button type="button" className="btn sm" onClick={() => showDraft(i)}>转给厂家/货代的说明</button>}
                {i.status !== "closed" && <input className="textarea" style={{ minHeight: 30, padding: "0 8px", flex: 1, minWidth: 120 }} aria-label="追加处理记录" placeholder="追加一条处理记录…" value={note} onChange={(e) => setNote(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter" && note.trim()) run(`issues/${i.id}/note`, { note }, "已记录", () => setNote("")); }} />}
              </div>
            </div>
          </div>
        ))}
        <div className="row"><button type="button" className="btn" disabled={pending} onClick={() => setAdding(true)}><Plus size={16} aria-hidden="true" />记一个问题或意见</button></div>
      </div>
      <Sheet open={adding} title="记一个问题或意见" onClose={() => setAdding(false)}>
        <WriteRecovery recovery={recovery} subject="问题登记" />
        <div className="form-grid" style={{ gridTemplateColumns: "1fr 1fr" }}>
          <label><span>类型</span><select name="kind" value={f.kind} onChange={(e) => setF({ ...f, kind: e.target.value })}>{Object.entries(ISSUE_KIND).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></label>
          <label><span>严重程度</span><select name="severity" value={f.severity} onChange={(e) => setF({ ...f, severity: e.target.value })}>{Object.entries(SEVERITY_TEXT).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></label>
          <label className="span2"><span>责任在谁</span><select name="responsible" value={f.responsible} onChange={(e) => setF({ ...f, responsible: e.target.value })}>{Object.entries(RESP).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></label>
          <label className="span2"><span>发生了什么</span><textarea className="textarea" name="description" rows={4} value={f.description} onChange={(e) => setF({ ...f, description: e.target.value })} /></label>
        </div>
        <div className="sheet-actions"><button type="button" className="btn" onClick={() => setAdding(false)}>取消</button><button type="button" className="btn primary" disabled={!f.description.trim() || pending} onClick={() => run(`deals/${dealId}/issues`, f, "已记录", () => { setAdding(false); setF({ ...f, description: "" }); })}>记录</button></div>
      </Sheet>
      <Sheet open={res != null} title="问题怎么解决的？" onClose={() => setRes(null)}>
        <WriteRecovery recovery={recovery} subject="问题处理" />
        <label><span className="sr-only">解决结论</span><textarea className="textarea" name="resolution" rows={4} placeholder="例如：厂家补发 2 台，已发出…" value={resText} onChange={(e) => setResText(e.target.value)} /></label>
        <div className="sheet-actions"><button type="button" className="btn" onClick={() => setRes(null)}>取消</button><button type="button" className="btn primary" disabled={!resText.trim() || pending} onClick={() => res && run(`issues/${res.id}/update`, { status: "resolved", resolution: resText }, "已标为解决", () => setRes(null))}>标为已解决</button></div>
      </Sheet>
      <Sheet open={draftIssue != null} wide title="转给厂家/货代的说明（草稿）" onClose={() => { setDraft(null); setDraftIssue(null); }}>
        {draftBusy && <p>正在读取说明草稿…</p>}
        {draftError != null && <ReadFailure subject="说明草稿" error={draftError} retry={() => draftIssue && showDraft(draftIssue)} busy={draftBusy} />}
        <p className="callout" role="note">这是模板草稿，里面标了【待补充】的地方要你自己补上产品型号、现象、数量和日期。系统不会替你发出去。</p>
        <textarea className="textarea" name="draft" rows={10} value={draft ?? ""} onChange={(e) => setDraft(e.target.value)} spellCheck={false} />
        <div className="sheet-actions"><button type="button" className="btn" onClick={() => { setDraft(null); setDraftIssue(null); }}>关闭</button><button type="button" className="btn primary" disabled={draftBusy || draftError != null || draft == null} onClick={async () => toast((await copyText(draft ?? "")) ? "已复制" : "复制没成功，请手动选择复制")}><Copy size={16} aria-hidden="true" />复制</button></div>
      </Sheet>
    </div>
  );
}
