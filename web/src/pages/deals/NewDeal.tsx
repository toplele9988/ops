import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate, useSearchParams } from "react-router";
import { get } from "../../api";
import { Sheet } from "../../ui/bits";
import { useDealAction } from "./actions";
import "./deals.css";

export function NewDeal({ stage = "inquiry", compact = false }: { stage?: string; compact?: boolean }) {
  const [params] = useSearchParams();
  const [open, setOpen] = useState(!compact && params.get("new") === "1");
  const [source, setSource] = useState(params.get("conversation") ? "conversation" : params.get("customer") ? "customer" : "manual");
  const [f, setF] = useState({ customer: params.get("customer") ?? "", customer_name: params.get("customer_name") ?? "", conversation: params.get("conversation") ?? "", product_name: "", qty: "", destination_country: params.get("destination_country") ?? "" });
  const navigate = useNavigate();
  const action = useDealAction();
  const customers = useQuery({ queryKey: ["new-deal-customers"], queryFn: () => get<{ items: { id: number; name: string; country?: string }[] }>("/api/v1/archive/parties?kind=customer&status=active&page_size=100"), enabled: open && source === "customer", retry: false });
  const conversations = useQuery({ queryKey: ["new-deal-conversations"], queryFn: () => get<{ items: { key: string; buyer_name: string; country?: string; channel_label: string }[] }>("/api/v1/inbox/conversations?page_size=100"), enabled: open && source === "conversation", retry: false });
  const customer = source === "customer" ? customers.data?.items.find((c) => String(c.id) === f.customer)?.name : f.customer_name;
  const ready = customer?.trim() && f.product_name.trim() && /^[1-9]\d{0,5}$/.test(f.qty) && f.destination_country.trim() && (source !== "conversation" || f.conversation);
  const submit = async () => {
    const result = await action.run("deals", { ...(source === "customer" ? { customer_party_id: f.customer } : { customer_name: f.customer_name.trim() }), ...(source === "conversation" ? { conversation_key: f.conversation } : {}), title: `${customer} · ${f.product_name}`, product_name: f.product_name, qty: f.qty, destination_country: f.destination_country, stage: ["paid", "inspection", "shipped", "delivered", "aftersales", "reorder"].includes(stage) ? "inquiry" : stage }, "案子已建立，产品和阶段待办已记好");
    if (result) { setOpen(false); navigate(`/deals/${result.id}`); }
  };
  return <>
    <button type="button" className={`btn ${compact ? "sm" : "primary"}`} onClick={() => setOpen(true)}>{compact ? "新建案子" : "＋ 新建案子"}</button>
    <Sheet open={open} title="新建案子" onClose={() => !action.busy && setOpen(false)}>
      <p className="t-foot">只需记下客户、产品、数量和目的国，其他资料可以后补。</p>
      {["paid", "inspection", "shipped", "delivered", "aftersales", "reorder"].includes(stage) && <p className="callout">先建立询盘案子，人工核对收款后再进入成交和发货阶段。</p>}
      <div className="ops-actions" role="group" aria-label="建案方式">{[["manual", "手动建案"], ["customer", "从客户建案"], ["conversation", "从会话建案"]].map(([key, label]) => <button key={key} className={`btn sm${source === key ? " primary" : ""}`} onClick={() => setSource(key)}>{label}</button>)}</div>
      <form className="ops-form" onSubmit={(e) => { e.preventDefault(); if (ready) void submit(); }}>
        {source === "customer" ? <label>客户<select aria-label="客户" required value={f.customer} onChange={(e) => { const c = customers.data?.items.find((p) => String(p.id) === e.target.value); setF({ ...f, customer: e.target.value, destination_country: c?.country ?? f.destination_country }); }}><option value="">请选择客户</option>{customers.data?.items.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}</select></label> : <>
          {source === "conversation" && <label>客户会话<select aria-label="客户会话" required value={f.conversation} onChange={(e) => { const c = conversations.data?.items.find((p) => p.key === e.target.value); setF({ ...f, conversation: e.target.value, customer_name: c?.buyer_name ?? "", destination_country: c?.country ?? "" }); }}><option value="">请选择会话</option>{conversations.data?.items.map((c) => <option key={c.key} value={c.key}>{c.channel_label} · {c.buyer_name}</option>)}</select></label>}
          <label>客户名称<input required maxLength={200} value={f.customer_name} onChange={(e) => setF({ ...f, customer_name: e.target.value })} /></label>
        </>}
        <label>产品<input required maxLength={200} value={f.product_name} onChange={(e) => setF({ ...f, product_name: e.target.value })} /></label>
        <label>数量<input required inputMode="numeric" value={f.qty} onChange={(e) => setF({ ...f, qty: e.target.value })} /></label>
        <label>目的国<input required maxLength={200} value={f.destination_country} onChange={(e) => setF({ ...f, destination_country: e.target.value })} /></label>
        {(customers.isError || conversations.isError) && <p role="alert">可选资料没能读到。<button type="button" className="btn sm" onClick={() => source === "customer" ? customers.refetch() : conversations.refetch()}>重新读取</button></p>}
        {action.error && <p role="alert">{action.error}</p>}
        <div className="sheet-actions"><button type="button" className="btn" onClick={() => setOpen(false)}>取消</button><button type="submit" className="btn primary" disabled={!ready || action.busy}>{action.busy ? "正在建立…" : "建立案子"}</button></div>
      </form>
    </Sheet>
  </>;
}
