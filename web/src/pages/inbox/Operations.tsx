import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { Link } from "react-router";
import { get, qs } from "../../api";
import { uuid } from "../../lib_uuid";
import { formatTime } from "../../format";
import { Empty, Sheet, Skeleton } from "../../ui/bits";
import { NativeComposePanel } from "./LanguagePanels";
import { inboxAction, supplierDraft, type Context, type Result } from "../../ops/inbox";

export function useInboxAction(key: string, onResult: (result: Result) => void) {
  const client = useQueryClient();
  return useMutation({ mutationFn: (fields: Record<string, string>) => inboxAction(key, fields), retry: false,
    onSuccess: (result) => { void client.invalidateQueries({ queryKey: ["inbox"] }); void client.invalidateQueries({ queryKey: ["inbox-summary"] }); void client.invalidateQueries({ queryKey: ["deals"] }); onResult(result); } });
}

export function Reply({ convKey, name, reply, setReply, onResult }: { convKey: string; name: string; reply: string; setReply: (v: string) => void; onResult: (r: Result) => void }) {
  const [confirming, setConfirming] = useState(false);
  const [uncertain, setUncertain] = useState(false);
  const request = useRef(uuid());
  const send = useInboxAction(convKey, (r) => { setConfirming(false); setReply(""); request.current = uuid(); onResult(r); });
  const invalid = !reply.trim() || reply.includes("【待确认");
  const error = send.error ? "草稿保存结果未能确认，已停止发送。请先刷新记录并人工核对，避免重复操作。" : "";
  return <div className="composer">
    <label className="inbox-field"><span>回复内容（可编辑）</span><textarea className="textarea" rows={3} value={reply} onChange={(e) => setReply(e.target.value)} placeholder="写回复，或点 AI 起草回复" /></label>
    <NativeComposePanel convKey={convKey} reply={reply} onAdopt={setReply} />
    <div className="composer-row"><span className="t-cap">演练：只保存草稿，不会发给对方。</span><button className="btn primary" disabled={invalid || uncertain || send.isPending} onClick={() => setConfirming(true)}>确认发送…</button></div>
    {reply.includes("【待确认") && <p className="t-foot">请先核对并修改【待确认】内容。</p>}
    {error && <div role="alert" className="callout">{error}<button className="btn sm" onClick={() => { setUncertain(false); send.reset(); request.current = uuid(); }}>我已人工核对，重新编辑</button></div>}
    <Sheet open={confirming} title="确认发送" onClose={() => { if (!send.isPending) setConfirming(false); }}>
      <p>收件人：<b>{name}</b></p><div className="quote">{reply}</div><p className="t-foot">确认后只进入演练发送队列，不会对外发送。</p>
      <div className="sheet-actions"><button className="btn" disabled={send.isPending} onClick={() => setConfirming(false)}>取消</button><button className="btn primary" disabled={invalid || send.isPending} onClick={() => send.mutate({ action: "send", request_id: request.current, text: reply.trim(), dry_run: "1", confirm: "1" }, { onError: () => { setConfirming(false); setUncertain(true); } })}>{send.isPending ? "正在保存…" : "确认发送"}</button></div>
    </Sheet>
  </div>;
}

export function Operations({ convKey, context, onResult }: { convKey: string; context: Context; onResult: (r: Result) => void }) {
  const [mode, setMode] = useState("");
  const [title, setTitle] = useState("");
  const [target, setTarget] = useState("");
  const [customer, setCustomer] = useState(context.party?.id ? String(context.party.id) : "");
  const [when, setWhen] = useState("");
  const [supplier, setSupplier] = useState("");
  const [product, setProduct] = useState("");
  const [preview, setPreview] = useState<Result | null>(null);
  const [supplierText, setSupplierText] = useState("");
  const [blocked, setBlocked] = useState(false);
  const request = useRef(uuid());
  const client = useQueryClient();
  const action = useInboxAction(convKey, (r) => { setMode(""); onResult(r); });
  const customers = useQuery({ queryKey: ["inbox", "customers", title], queryFn: async () => { const data = await get<{ items: { id: number; name: string; country: string }[]; total: number }>(`/api/v1/parties-ops?kind=customer&page_size=100&q=${encodeURIComponent(title)}`); return { ...data, items: data.items.map(p => ({ key: String(p.id), buyer_name: p.name, country: p.country, channel: "已建档", account: "", n_conversations: 0, n_messages: 0, last_seen: "" })) }; }, enabled: mode === "assign", retry: false });
  const deals = useQuery({ queryKey: ["inbox", "open-deals", title], queryFn: () => get<{ items: { id: number; title: string }[]; total: number }>(`/api/v1/deals?status=open&page_size=100&q=${encodeURIComponent(title)}`), enabled: mode === "link_deal", retry: false });
  const draft = useMutation({ mutationFn: () => supplierDraft(convKey, { supplier_key: supplier, product, request_id: request.current }), retry: false,
    onSuccess: (r) => { setPreview(r); setSupplierText(r.message!.text); request.current = uuid(); void client.invalidateQueries({ queryKey: ["inbox"] }); } });
  const open = (next: string) => { setMode(next); request.current = uuid(); action.reset(); draft.reset(); setPreview(null); setBlocked(false); setTitle(""); setTarget(""); setCustomer(context.party?.id ? String(context.party.id) : ""); };
  const submit = (fields: Record<string, string>) => action.mutate({ ...fields, request_id: mode ? request.current : uuid() });
  const busy = action.isPending || draft.isPending;
  const titles: Record<string, string> = { create_deal: "转为案子", link_deal: "关联已有案子", remind: "稍后提醒", assign: "归到客户", supplier: "问供应商" };
  return <>
    <section aria-label="客户资料"><h3 className="t-headline">客户资料</h3>
      {context.customer ? <><b>{context.customer.buyer_name}</b><dl className="kv"><dt>国家</dt><dd>{context.customer.country || "未提供"}</dd><dt>往来</dt><dd>{context.customer.n_conversations} 个会话 · {context.customer.n_messages} 条消息</dd><dt>最近联系</dt><dd>{formatTime(context.customer.last_seen)}</dd></dl><Link className="btn sm" to={context.party?.id ? `/customers/${context.party.id}` : "/customers"}>打开客户资料</Link></> : <p className="t-foot">尚未归到客户，选择客户后可一起跟进。</p>}
      <button className="btn sm" onClick={() => open("assign")}>归到客户</button>
    </section>
    <section><h3 className="t-headline">下一步</h3><div className="inbox-actions">
      <button className="btn" disabled={busy} onClick={() => submit({ action: context.workflow_state === "done" ? "reopen" : "done" })}>{context.workflow_state === "done" ? "恢复跟进" : "标记已处理"}</button>
      <button className="btn" onClick={() => open("remind")}>稍后提醒</button>
      <button className="btn" onClick={() => open("supplier")}>问供应商</button>
    </div><p className="t-foot">{context.workflow_state === "done" ? "已处理，新消息到达会再次提醒。" : context.remind_at ? `提醒时间：${formatTime(context.remind_at)}${context.reminder_due ? "（已到时间）" : ""}` : "先核对问题，再回复或找供应商确认。"}</p>
      {action.isError && mode === "" && <p role="alert">{action.error.message}</p>}
    </section>
    <section aria-label="关联案子"><h3 className="t-headline">这笔生意</h3>
      {context.deals.length ? context.deals.map((d) => <Link key={d.id} className="btn inbox-deal" to={`/deals/${d.id}`}>{d.title} · {d.stage_label}</Link>) : <p className="t-foot">还没关联案子，可从这次询盘建一个。</p>}
      <div className="inbox-actions"><button className="btn primary" onClick={() => open("create_deal")}>转为案子</button><button className="btn" onClick={() => open("link_deal")}>关联已有案子</button><Link className="btn" to={"/deals" + qs({ new: "1", conversation: convKey, customer_name: context.party?.name ?? context.customer_name ?? "", destination_country: context.party?.country ?? context.customer?.country ?? "" })}>填写产品后建案</Link></div>
    </section>
    <Sheet open={Boolean(mode)} title={titles[mode] ?? "操作"} onClose={() => { if (!busy) setMode(""); }}>
      {mode === "create_deal" && <label className="inbox-field">案子名称<input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="可不填，按客户名称建案子" /></label>}
      {mode === "link_deal" && <><label className="inbox-field">搜索案子<input value={title} onChange={(e) => setTitle(e.target.value)} /></label>{deals.isLoading && <Skeleton />}{deals.isError && <Empty title="没能读到案子" action={<button className="btn" onClick={() => deals.refetch()}>重新读取</button>}>{deals.error.message}</Empty>}<label className="inbox-field">选择案子<select aria-label="选择案子" value={target} onChange={(e) => setTarget(e.target.value)}><option value="">请选择</option>{deals.data?.items.map((d) => <option key={d.id} value={d.id}>{d.title}</option>)}</select></label>{deals.data?.items.length === 0 && <p>没有匹配的案子，可取消后点“转为案子”。</p>}</>}
      {mode === "assign" && <><label className="inbox-field">搜索客户<input value={title} onChange={(e) => { setTitle(e.target.value); setCustomer(""); }} /></label>{customers.isLoading && <Skeleton />}{customers.isError && <Empty title="没能读到客户" action={<button className="btn" onClick={() => customers.refetch()}>重新读取</button>}>{customers.error.message}</Empty>}<label className="inbox-field">选择客户<select aria-label="选择客户" value={customer} onChange={(e) => setCustomer(e.target.value)}><option value="">请选择</option>{customers.data?.items.map((c) => <option key={c.key} value={c.key}>{c.buyer_name} · {c.country || "未提供国家"} · {c.channel}</option>)}</select></label><p className="callout">保存会更换这条会话的归属，并同步更新案子内的会话归属。请核对客户。</p>{customers.data?.items.length === 0 && <Link className="btn" to="/customers">去客户页查看</Link>}</>}
      {mode === "remind" && <><label className="inbox-field">提醒时间<input type="datetime-local" value={when} onChange={(e) => setWhen(e.target.value)} /></label><p>按北京时间选择，到时会回到“要我回复”。</p></>}
      {mode === "supplier" && <>
        {!preview ? <><p>带上当前客户的问题和案子里的产品，只起草，核对后再确认。</p><label className="inbox-field">供应商会话<select aria-label="供应商会话" value={supplier} onChange={(e) => setSupplier(e.target.value)}><option value="">请选择</option>{context.suppliers.map((s) => <option key={s.key} value={s.key}>{s.buyer_name} · {s.channel_label}</option>)}</select></label><label className="inbox-field">补充产品<input value={product} onChange={(e) => setProduct(e.target.value)} placeholder="未关联产品时必填" /></label>{!context.suppliers.length && <Empty title="尚无供应商会话" action={<Link className="btn" to="/suppliers">去供应商页查看</Link>}>先接入供应商会话，再来起草询问。</Empty>}</> : <><p role="status">{preview.notice}</p><p>收件人：{preview.recipient}</p><label className="inbox-field">供应商询问内容<textarea aria-label="供应商询问内容" rows={8} value={supplierText} onChange={(e) => setSupplierText(e.target.value)} /></label><p>确认后只进入演练发送队列，不会发给供应商。</p></>}
      </>}
      {action.isError && <p className="callout" role="alert">{action.error.message}{mode === "supplier" && " 请刷新记录并人工核对，不会自动重发。"}</p>}
      {draft.isError && <p className="callout" role="alert">{draft.error.message}</p>}
      <div className="sheet-actions"><button className="btn" disabled={busy} onClick={() => setMode("")}>取消</button>
        {mode === "supplier" ? !preview ? <button className="btn primary" disabled={!supplier || busy} onClick={() => draft.mutate()}>起草供应商询问</button> : <button className="btn primary" disabled={!supplierText.trim() || supplierText.includes("【待确认") || busy || blocked} onClick={() => { action.mutate({ action: "supplier_send", supplier_key: supplier, draft_id: String(preview.message!.id), text: supplierText, confirm: "1", dry_run: "1", request_id: request.current }, { onError: () => setBlocked(true) }); }}>确认发送给供应商（演练）</button>
        : <button className="btn primary" disabled={busy || (mode === "link_deal" && !target) || (mode === "assign" && !customer) || (mode === "remind" && (!when || !Number.isFinite(Date.parse(when))))} onClick={() => submit({ action: mode, ...(mode === "create_deal" ? { title } : mode === "link_deal" ? { deal_id: target } : mode === "assign" ? { customer_party_id: customer, confirm: "1" } : { remind_at: new Date(when + "+08:00").toISOString() }) })}>{busy ? "正在保存…" : mode === "assign" ? "确认归属" : "保存"}</button>}
      </div>
    </Sheet>
  </>;
}
