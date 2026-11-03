import { useQueryClient } from "@tanstack/react-query";
import { Plus } from "lucide-react";
import { useRef, useState } from "react";
import { Link, useNavigate } from "react-router";
import { get, postForm, qs } from "../api";
import { toFen } from "../fulfil";
import { Sheet } from "./bits";
import { useToast } from "./Toast";
import { ReadFailure, WriteRecovery, useWriteRecovery } from "./ReadState";

const SOURCES = ["阿里国际站", "微信", "WhatsApp", "邮箱", "独立站", "其他"];
const today = () => new Date().toISOString().slice(0, 10);

/** 不从会话出发，直接把一笔生意（比如阿里国际站已经下单的）录进来；已收款可以一并确认。 */
export function ManualDealButton() {
  const toast = useToast();
  const navigate = useNavigate();
  const client = useQueryClient();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [f, setF] = useState({ source: "阿里国际站", customer: "", order_no: "", title: "", value: "", currency: "USD", notes: "", paid: false, pay_kind: "full", pay_amount: "", pay_date: today(), pay_method: "other", pay_qualifies: false });
  const [items, setItems] = useState<{ name: string; qty: string }[]>([{ name: "", qty: "1" }]);
  const set = (k: string) => (e: { target: { value: string } }) => setF({ ...f, [k]: e.target.value });
  const valueOk = !f.value || /^\d+(\.\d{1,2})?$/.test(f.value);
  const payFen = toFen(f.pay_amount);
  const ready = f.customer.trim() && (f.title.trim() || f.order_no.trim()) && valueOk && (!f.paid || payFen);

  const [readError, setReadError] = useState<unknown>(null);
  const [matches, setMatches] = useState<{ id: number; title: string }[]>([]);
  const pendingRead = useRef<() => Promise<unknown>>(async () => {});
  const recovery = useWriteRecovery(() => pendingRead.current());
  const customerUrl = `/api/v1/archive/parties?kind=customer&page_size=100&q=${encodeURIComponent(f.customer.trim())}`;
  const readCustomers = async () => {
    const found = await get<{ items: { id: number; name: string }[] }>(customerUrl);
    setReadError(null);
    return found;
  };
  const submit = async () => {
    if (busy || recovery.blocked || readError != null) return;
    setBusy(true);
    let dealId: number | null = null;
    let writing = false;
    const write = async <T,>(path: string, fields: Record<string, string>, read: () => Promise<unknown>): Promise<T> => { pendingRead.current = read; writing = true; const result = await postForm<T>(path, fields); writing = false; return result; };
    try {
      // reuse an existing customer with exactly this name instead of creating a duplicate
      const found = await readCustomers();
      let partyId = found.items.find((p) => p.name === f.customer.trim())?.id;
      if (!partyId) partyId = (await write<{ id: number }>("/api/v1/archive/parties", { kind: "customer", name: f.customer.trim() }, readCustomers)).id;
      const title = f.title.trim() || `${f.source}订单 ${f.order_no.trim()}`;
      dealId = (await write<{ id: number }>("/api/v1/deals", { title, customer_party_id: String(partyId) }, async () => {
        const result = await get<{ items: { id: number; title: string }[] }>(`/api/v1/deals${qs({ status: "all", party: partyId, q: title, page_size: 100 })}`);
        setMatches(result.items);
      })).id;
      const readDeal = async () => { await get(`/api/v1/deals/${dealId}`); await get(`/api/v1/deals/${dealId}/payments`); };
      const notes = [`来源：${f.source}`, f.order_no.trim() && `订单号：${f.order_no.trim()}`, f.notes.trim()].filter(Boolean).join("；");
      await write(`/api/v1/deals/${dealId}/update`, { notes, ...(f.value ? { value: f.value, currency: f.currency } : {}) }, readDeal);
      for (const it of items) if (it.name.trim()) await write(`/api/v1/deals/${dealId}/items`, { name: it.name.trim(), qty: it.qty || "1", ...(f.currency ? { currency: f.currency } : {}) }, readDeal);
      if (f.paid && payFen) {
        const pay = await write<{ id: number }>(`/api/v1/deals/${dealId}/payments`, { kind: f.pay_kind, amount_fen: payFen, currency: f.currency, received_on: f.pay_date, method: f.pay_method, note: `${f.source}订单 ${f.order_no.trim()}`.trim() }, readDeal);
        await write(`/api/v1/payments/${pay.id}/confirm`, { confirm: "1", qualifies: f.pay_kind !== "balance" || f.pay_qualifies ? "1" : "0" }, readDeal);
      }
      client.invalidateQueries({ queryKey: ["deals"] }); client.invalidateQueries({ queryKey: ["deals-summary"] });
      toast(f.paid ? "已录入并确认收款，请核对案子状态" : "已录入案子");
      setOpen(false);
      navigate(`/deals/${dealId}`);
    } catch (e) {
      if (writing) await recovery.fail(e as Error);
      else setReadError(e);
      if (dealId) {
        toast(`案子 #${dealId} 已经建好，录入流程中断。请先核对收款与产品，再补完。`, "error");
        client.invalidateQueries({ queryKey: ["deals"] }); setOpen(false); navigate(`/deals/${dealId}?check=manual`);
      }
    } finally { setBusy(false); }
  };

  return (
    <>
      <button type="button" className="btn primary" onClick={() => setOpen(true)}><Plus size={16} aria-hidden="true" />录入一笔生意</button>
      <Sheet open={open} wide title="录入一笔生意" onClose={() => setOpen(false)}>
        <WriteRecovery recovery={recovery} subject="录入案子" />
        {recovery.blocked && matches.length > 0 && <div className="callout">请先打开核对已有案子：{matches.map((d) => <p key={d.id}><Link to={`/deals/${d.id}`} onClick={() => setOpen(false)}>案子 #{d.id} · {d.title}</Link></p>)}</div>}
        {readError != null && <ReadFailure subject="录入前的客户记录" error={readError} retry={async () => { try { await readCustomers(); } catch (error) { setReadError(error); } }} />}
        <p className="t-foot">不用先有会话：阿里国际站已经下单、微信里谈成的，直接录进来。录好后可以继续在案子里加供应商、货代、记报价和发货。</p>
        <div className="form-grid" style={{ gridTemplateColumns: "1fr 1fr" }}>
          <label><span>来源</span><select name="source" value={f.source} onChange={set("source")}>{SOURCES.map((s) => <option key={s}>{s}</option>)}</select></label>
          <label><span>订单号（没有可不填）</span><input name="order_no" autoComplete="off" value={f.order_no} onChange={set("order_no")} /></label>
          <label className="span2"><span>客户名称</span><input name="customer" autoComplete="off" placeholder="例如：买家公司或姓名…" value={f.customer} onChange={set("customer")} /></label>
          <label className="span2"><span>案子标题（不写就用“来源+订单号”）</span><input name="title" autoComplete="off" value={f.title} onChange={set("title")} /></label>
          <label><span>订单金额（可不填）</span><input name="value" inputMode="decimal" autoComplete="off" value={f.value} onChange={set("value")} /></label>
          <label><span>币种</span><input name="currency" autoComplete="off" value={f.currency} onChange={(e) => setF({ ...f, currency: e.target.value.toUpperCase() })} /></label>
        </div>
        <div className="t-foot" style={{ margin: "12px 0 4px" }}>买了什么</div>
        {items.map((it, i) => (
          <div key={i} style={{ display: "flex", gap: 8, marginBottom: 6 }}>
            <input className="textarea" style={{ minHeight: 36, padding: "0 10px", flex: 1 }} name={`item${i}`} aria-label={`第 ${i + 1} 个产品`} autoComplete="off" placeholder="产品名称或型号…" value={it.name} onChange={(e) => setItems(items.map((x, j) => (j === i ? { ...x, name: e.target.value } : x)))} />
            <input className="textarea" style={{ minHeight: 36, padding: "0 10px", width: 80 }} name={`qty${i}`} aria-label="数量" inputMode="numeric" autoComplete="off" value={it.qty} onChange={(e) => setItems(items.map((x, j) => (j === i ? { ...x, qty: e.target.value.replace(/\D/g, "") } : x)))} />
          </div>
        ))}
        <button type="button" className="btn sm" onClick={() => setItems([...items, { name: "", qty: "1" }])}><Plus size={14} aria-hidden="true" />再加一个产品</button>
        <label style={{ display: "block", marginTop: 12 }}><span className="t-foot">备注</span><textarea className="textarea" name="notes" rows={2} value={f.notes} onChange={set("notes")} /></label>
        <label style={{ display: "flex", gap: 8, alignItems: "center", margin: "14px 0 6px" }}><input type="checkbox" checked={f.paid} onChange={(e) => setF({ ...f, paid: e.target.checked, pay_amount: e.target.checked && !f.pay_amount ? f.value : f.pay_amount })} />客户已经付款，我已经核对到账（录入时人工确认收款）</label>
        {f.paid && (
          <div className="form-grid" style={{ gridTemplateColumns: "1fr 1fr" }}>
            <label><span>这笔是</span><select name="pay_kind" value={f.pay_kind} onChange={set("pay_kind")}><option value="full">全款</option><option value="deposit">定金</option><option value="balance">尾款</option></select></label>
            <label><span>到账金额（{f.currency}）</span><input name="pay_amount" inputMode="decimal" autoComplete="off" value={f.pay_amount} onChange={set("pay_amount")} /></label>
            <label><span>到账日期</span><input name="pay_date" type="date" value={f.pay_date} onChange={set("pay_date")} /></label>
            <label><span>方式</span><select name="pay_method" value={f.pay_method} onChange={set("pay_method")}><option value="other">平台收款/其他</option><option value="wire">电汇</option><option value="paypal">PayPal</option><option value="lc">信用证</option></select></label>
          </div>
        )}
        {f.paid && (f.pay_kind === "balance" ? <label style={{ display: "flex", gap: 8, marginTop: 10 }}><input type="checkbox" checked={f.pay_qualifies} onChange={(e) => setF({ ...f, pay_qualifies: e.target.checked })} />这笔尾款已达到成交条件（不勾选则只记已收，不记成交）</label> : <p className="t-foot">新案首笔确认定金或全款就算成交，进入备货阶段。</p>)}
        {f.paid && f.pay_amount && !payFen && <p className="t-foot" style={{ color: "var(--red)" }}>到账金额只能是数字，最多两位小数。</p>}
        <div className="sheet-actions"><button type="button" className="btn" onClick={() => setOpen(false)}>取消</button><button type="button" className="btn primary" disabled={!ready || busy || recovery.blocked || readError != null} onClick={submit}>{busy ? "正在录入…" : f.paid ? "录入并确认收款" : "录入"}</button></div>
      </Sheet>
    </>
  );
}
