import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, MessageSquare, Plus } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router";
import { get, postForm as rawPostForm, qs } from "../api";
import { Page, Party } from "../archive";
import { IssuesPanel } from "../ui/DealFulfil";
import { Payments, FulfilmentActions } from "./deals/Fulfilment";
import { ConversationLink, CrossTimeline } from "./deals/Conversations";
import { operationForm as postForm } from "./deals/actions";
import { AlertList, SecretaryPanel } from "../ui/RolePanels";
import { QSTATUS, QuoteSummary } from "../quotes";
import { DealDetail, DealTask, ROLE, STAGES, STATUS_TEXT } from "../deals";
import { ago } from "../format";
import { Chip, Sheet, Skeleton } from "../ui/bits";
import { useToast } from "../ui/Toast";
import { ReadFailure, WriteRecovery, useWriteRecovery } from "../ui/ReadState";
import { DealWorkflow } from "../ui/DealWorkflow";

const STATE_TEXT = { us: { text: "球在我们手上：对方最后说了话，等你回复", tone: "red" as const }, them: { text: "在等对方回复", tone: "orange" as const }, none: { text: "还没有关联会话", tone: "plain" as const } };

export function DealPage() {
  const { id } = useParams();
  const navigate = useNavigate();
  const [search] = useSearchParams();
  const toast = useToast();
  const client = useQueryClient();
  const q = useQuery({ queryKey: ["deal", id], queryFn: () => get<DealDetail>(`/api/v1/deals/${id}`), retry: false });
  const [adding, setAdding] = useState(false);
  const [lost, setLost] = useState(false);
  const [reason, setReason] = useState("");
  const [addKind, setAddKind] = useState("supplier");
  const [addParty, setAddParty] = useState("");
  const [newName, setNewName] = useState("");
  const [taskText, setTaskText] = useState("");
  const [note, setNote] = useState("");
  const [item, setItem] = useState({ name: "", qty: "" });
  const [edit, setEdit] = useState<Record<string, string>>({});
  const quotes = useQuery({ queryKey: ["quotes", id], queryFn: () => get<{ items: QuoteSummary[] }>(`/api/v1/deals/${id}/quotes`), retry: false });
  const newQuote = useMutation({
    mutationFn: () => postForm<{ id: number }>(`/api/v1/deals/${id}/quotes`, {}),
    onSuccess: (r) => { client.invalidateQueries({ queryKey: ["quotes", id] }); navigate(`/quotes/${r.id}`); },
    retry: false, onError: (e: Error) => recovery.fail(e),
  });
  const parties = useQuery({ queryKey: ["parties", addKind], queryFn: () => get<Page<Party>>(`/api/v1/archive/parties${qs({ kind: addKind, status: "active", page_size: 100 })}`), enabled: adding, retry: false });
  const recovery = useWriteRecovery(async () => { await q.refetch({ throwOnError: true }); await quotes.refetch({ throwOnError: true }); if (adding) await parties.refetch({ throwOnError: true }); });
  const refresh = () => { client.invalidateQueries({ queryKey: ["deal", id] }); client.invalidateQueries({ queryKey: ["deals"] }); client.invalidateQueries({ queryKey: ["deals-summary"] }); };
  const act = useMutation({
    mutationFn: ({ path, fields }: { path: string; fields: Record<string, string> }) => postForm(`/api/v1/${path}`, fields),
    retry: false, onSuccess: () => { refresh(); }, onError: (e: Error) => recovery.fail(e),
  });
  const run = (path: string, fields: Record<string, string>, ok = "已保存", after?: () => void) =>
    !recovery.blocked && act.mutate({ path, fields }, { onSuccess: () => { toast(ok); after?.(); } });
  const createParty = useMutation({
    mutationFn: () => rawPostForm<{ id: number }>("/api/v1/archive/parties", { kind: addKind, name: newName }),
    onSuccess: (r) => { toast("已添加"); setNewName(""); client.invalidateQueries({ queryKey: ["parties", addKind] }); if (r?.id) setAddParty(String(r.id)); },
    retry: false, onError: (e: Error) => recovery.fail(e),
  });

  if (q.isLoading) return <><h1 className="t-large">案子</h1><Skeleton lines={6} /></>;
  if (q.isError || !q.data) return <div className="group"><ReadFailure subject="案子详情" error={q.error} retry={() => q.refetch()} busy={q.isFetching} /><WriteRecovery recovery={recovery} subject="案子操作" /></div>;
  const d = q.data;
  const idx = d.stages.findIndex((s) => s.current);
  const next = d.stages[idx + 1];
  const paidIndex = STAGES.findIndex((s) => s.key === "paid");
  const canAdvance = next && ((d.status === "open" && idx < paidIndex && next.key !== "paid") || (d.status === "won" && idx >= paidIndex));
  const blocked = act.isPending || recovery.blocked;
  const allTasks = Object.values(d.tasks).flat();
  const openTasks = allTasks.filter((t) => t.status === "open");
  const doneTasks = allTasks.filter((t) => t.status !== "open");
  const byStage = (list: DealTask[]) => STAGES.map((s) => ({ s, items: list.filter((t) => t.stage === s.key) })).filter((g) => g.items.length);
  const field = (key: string, fallback = "") => edit[key] ?? fallback;
  const dirty = Object.keys(edit).length > 0;

  return (
    <>
      <button type="button" className="btn plain" style={{ marginLeft: -8, marginBottom: 6 }} onClick={() => navigate("/deals")}><ArrowLeft size={16} aria-hidden="true" />全部案子</button>
      <div className="page-head" style={{ marginBottom: 12 }}>
        <div>
          <h1 className="t-large">{d.title}</h1>
          <p>在这里跟进各方沟通、报价和收款，成交后继续安排备货发货。</p>
          <p>{d.customer ? `客户：${d.customer.name}` : "还没有客户"}{d.value ? ` · 金额 ${d.currency} ${d.value}` : ""}</p>
        </div>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
          <Chip tone={STATUS_TEXT[d.status]?.tone}>{STATUS_TEXT[d.status]?.text}</Chip>
          {canAdvance && <button type="button" className="btn primary" disabled={blocked} onClick={() => { if (window.confirm(`确认进入「${next.label}」？请先核对实际进展。`)) run(`deals/${d.id}/update`, { stage: next.key }, `已进入「${next.label}」`); }}>确认进入「{next.label}」</button>}
          {d.status === "open" && next?.key === "paid" && <a className="btn primary" href="#deal-payments">核对并确认收款</a>}
          {d.status === "open" && <button type="button" className="btn" onClick={() => setLost(true)}>标记丢单</button>}
          {(d.status === "lost" || d.status === "paused") && idx < paidIndex && <button type="button" className="btn" onClick={() => run(`deals/${d.id}/update`, { status: "open" }, "已重新打开")}>重新打开</button>}
        </div>
      </div>
      <div className="stagebar" aria-label="阶段进度">
        {d.stages.map((s) => <span key={s.key} className={s.current ? "current" : s.done ? "done" : ""} aria-current={s.current ? "step" : undefined}>{s.label}</span>)}
      </div>
      {d.status === "won" && <p className="t-foot" style={{ marginTop: 8, color: "var(--green)" }}>此案保留已成交记录，付款依据请在下方「收款」核对。</p>}
      {d.status === "lost" && d.lost_reason && <p className="t-foot" style={{ marginTop: 8 }}>丢单原因：{d.lost_reason}</p>}

      {search.get("check") === "manual" && <p className="callout">录入流程中断，案子已经建好。请先核对产品、收款记录和付款状态，再补完缺项，不要直接再登记同一笔款。</p>}
      <WriteRecovery recovery={recovery} subject="案子操作" />

      {(d.drafts?.length ?? 0) > 0 && <section className="block"><h2 className="group-title">演练草稿箱 · 等你核对</h2><div className="group">{d.drafts?.map((draft) => <details key={draft.id} className="row" style={{ display: "block" }}><summary>{draft.kind === "pi" ? "PI" : draft.kind === "quote" ? "报价单" : draft.kind === "supplier_order" ? "采购单" : "询价"} · 未发送 · dry-run</summary><pre className="ops-timeline">{draft.text}</pre></details>)}</div></section>}
      <div className="cols" style={{ marginTop: 24 }}>
        <div>
          <div className="block">
            <h2 className="group-title">各方现在的情况<span className="count">{d.unlinked_participants ? `${d.unlinked_participants} 方还没关联会话` : ""}</span></h2>
            <div className="group">
              {d.participants.length === 0 && <div className="row"><div className="row-main t-sub">还没有参与方。点下面「添加参与方」，加上客户、供应商、货代。</div></div>}
              {d.participants.map((p) => (
                <div className="row" key={p.party_id} style={{ alignItems: "flex-start" }}>
                  <div className="row-main">
                    <div className="row-title">{p.name} <span className="t-cap">{ROLE[p.role] ?? p.role}</span></div>
                    <div className="row-sub" style={{ color: p.state === "us" ? "var(--red)" : p.state === "them" ? "var(--orange)" : undefined }}>{STATE_TEXT[p.state].text}{p.last_at ? ` · ${ago(p.last_at)}` : ""}</div>
                    <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginTop: 6 }}><ConversationLink dealId={d.id} partyId={p.party_id} />
                      {(p.conversations ?? []).map((c) => (
                        <Link key={c.key} className="btn sm" to={`/inbox?c=${encodeURIComponent(c.key)}`}><MessageSquare size={14} aria-hidden="true" />{c.channel_label} · {(c.title ?? "打开会话").slice(0, 14)}</Link>
                      ))}
                    </div>
                  </div>
                  <button type="button" className="btn sm plain" onClick={() => { if (window.confirm(`把「${p.name}」从这个案子里移除？（不会删除这个往来单位）`)) run(`deals/${d.id}/participants/${p.id ?? p.party_id}/remove`, {}, "已移除"); }}>移除</button>
                </div>
              ))}
              <div className="row"><button type="button" className="btn" onClick={() => setAdding(true)}><Plus size={16} aria-hidden="true" />添加参与方</button></div>
            </div>
          </div>

          <DealWorkflow key={id} dealId={Number(id)} revision={JSON.stringify([d.items, d.participants, d.conversations])} />
          <SecretaryPanel dealId={Number(id)} />
          <AlertList dealId={Number(id)} framed />

          <div className="block">
            <h2 className="group-title">报价<span className="count num">{quotes.data?.items.length ? `${quotes.data.items.length} 轮` : ""}</span></h2>
            <div className="group">
              {quotes.isError && <ReadFailure subject="报价记录" error={quotes.error} retry={() => quotes.refetch()} busy={quotes.isFetching} />}
              {quotes.isPending && <Skeleton lines={2} />}
              {(!quotes.isError ? quotes.data?.items ?? [] : []).map((r) => (
                <Link key={r.id} className="row" to={`/quotes/${r.id}`}>
                  <div className="row-main">
                    <div className="row-title">第 {r.round_no} 轮 <Chip tone={QSTATUS[r.status]?.tone}>{QSTATUS[r.status]?.text ?? r.status}</Chip></div>
                    <div className="row-sub num">{r.incoterm} · {r.totals.revenue ? `${r.currency} ${r.totals.revenue}` : "未定价"}{r.totals.margin ? ` · 利润率 ${r.totals.margin}` : ""}{r.blocking ? ` · ${r.blocking} 件事要先解决` : ""}</div>
                  </div>
                </Link>
              ))}
              {quotes.isSuccess && quotes.data.items.length === 0 && <div className="row"><div className="row-main t-sub">还没有报价。先把客户要的产品记在右边的「产品清单」，再点下面的按钮。</div></div>}
              <div className="row"><button type="button" className="btn primary" disabled={newQuote.isPending || recovery.blocked || !quotes.isSuccess} onClick={() => newQuote.mutate()}><Plus size={16} aria-hidden="true" />新建{quotes.data?.items.length ? "下一轮" : ""}报价</button></div>
            </div>
          </div>

          <Payments dealId={Number(id)} />
          <FulfilmentActions dealId={Number(id)} participants={d.participants} />
          <IssuesPanel dealId={Number(id)} />

          <div className="block">
            <h2 className="group-title">待办<span className="count num">{openTasks.length} 个没完成</span></h2>
            <div className="group">
              {byStage(openTasks).map((g) => (
                <div key={g.s.key}>
                  <div className="t-cap" style={{ padding: "10px 16px 0" }}>{g.s.label}</div>
                  {g.items.map((t) => (
                    <label className="task" key={t.id}>
                      <input type="checkbox" disabled={blocked} checked={false} onChange={() => run(`deal-tasks/${t.id}/status`, { status: "done" }, "已完成")} aria-label={`完成：${t.title}`} />
                      <span><span className="task-title">{t.title}</span>{t.due && <span className="t-cap num">　{t.due}</span>}</span>
                    </label>
                  ))}
                </div>
              ))}
              {openTasks.length === 0 && <div className="row"><div className="row-main t-sub">没有待办了。</div></div>}
              <div className="row" style={{ gap: 8 }}>
                <input className="textarea" style={{ minHeight: 36, flex: 1, padding: "0 10px" }} aria-label="新增待办" name="task" autoComplete="off" placeholder="加一条待办，例如：问供应商 200W 外箱尺寸…" value={taskText} onChange={(e) => setTaskText(e.target.value)} />
                <button type="button" className="btn" disabled={!taskText.trim() || blocked} onClick={() => run(`deals/${d.id}/tasks`, { title: taskText }, "已添加", () => setTaskText(""))}>添加</button>
              </div>
              {doneTasks.length > 0 && <div className="t-cap" style={{ padding: "8px 16px 12px" }}>已完成 {doneTasks.length} 个</div>}
            </div>
          </div>

          <div className="block">
            <h2 className="group-title">跨渠道经过</h2>
            <div className="group" style={{ paddingTop: 12 }}>
              <CrossTimeline deal={d} />
              <div className="row" style={{ gap: 8 }}>
                <input className="textarea" style={{ minHeight: 36, flex: 1, padding: "0 10px" }} aria-label="记一笔" name="note" autoComplete="off" placeholder="记一笔，例如：客户还价 52 美元，供应商说最多让 1 块…" value={note} onChange={(e) => setNote(e.target.value)} />
                <button type="button" className="btn" disabled={!note.trim() || blocked} onClick={() => run(`deals/${d.id}/events`, { note }, "已记录", () => setNote(""))}>记录</button>
              </div>
            </div>
          </div>
        </div>

        <div>
          <div className="block">
            <h2 className="group-title">下一步</h2>
            <div className="group" style={{ padding: 16, display: "grid", gap: 10 }}>
              <label className="form-grid" style={{ display: "grid" }}><span className="t-foot">下一步要做什么</span><input name="next_action" autoComplete="off" value={field("next_action", d.next_action ?? "")} onChange={(e) => setEdit({ ...edit, next_action: e.target.value })} /></label>
              <label className="form-grid" style={{ display: "grid" }}><span className="t-foot">最晚什么时候</span><input name="next_action_due" type="date" value={field("next_action_due", d.next_action_due ?? "")} onChange={(e) => setEdit({ ...edit, next_action_due: e.target.value })} /></label>
              <label className="form-grid" style={{ display: "grid" }}><span className="t-foot">备注</span><textarea className="textarea" name="notes" rows={3} value={field("notes", d.notes ?? "")} onChange={(e) => setEdit({ ...edit, notes: e.target.value })} /></label>
              <div style={{ textAlign: "right" }}><button type="button" className="btn" disabled={!dirty || blocked} onClick={() => run(`deals/${d.id}/update`, edit, "已保存", () => setEdit({}))}>保存</button></div>
            </div>
          </div>
          <div className="block">
            <h2 className="group-title">产品清单</h2>
            <div className="group">
              {d.items.length === 0 && <div className="row"><div className="row-main t-sub">客户要买的产品记在这里。</div></div>}
              {d.items.map((it) => (
                <div className="row" key={it.id}>
                  <div className="row-main"><div className="row-title">{it.name}</div><div className="row-sub num">{it.qty ? `${it.qty} 件` : "数量未定"}{it.target_price ? ` · 目标价 ${it.currency ?? ""} ${it.target_price}` : ""}</div></div>
                  <button type="button" className="btn sm plain" onClick={() => run(`deals/${d.id}/items/${it.id}/remove`, {}, "已移除")}>移除</button>
                </div>
              ))}
              <div className="row" style={{ gap: 8 }}>
                <input className="textarea" style={{ minHeight: 36, flex: 1, padding: "0 10px" }} aria-label="产品名" name="item" autoComplete="off" placeholder="产品名或型号…" value={item.name} onChange={(e) => setItem({ ...item, name: e.target.value })} />
                <input className="textarea" style={{ minHeight: 36, width: 70, padding: "0 10px" }} aria-label="数量" name="qty" inputMode="numeric" autoComplete="off" placeholder="数量…" value={item.qty} onChange={(e) => setItem({ ...item, qty: e.target.value })} />
                <button type="button" className="btn" disabled={!item.name.trim() || blocked} onClick={() => run(`deals/${d.id}/items`, Object.fromEntries(Object.entries(item).filter(([, v]) => v)), "已添加", () => setItem({ name: "", qty: "" }))}>添加</button>
              </div>
            </div>
          </div>
        </div>
      </div>

      <Sheet open={adding} title="添加参与方" onClose={() => setAdding(false)}>
        <WriteRecovery recovery={recovery} subject="参与方操作" />
        <div className="form-grid" style={{ gridTemplateColumns: "1fr" }}>
          <label><span>他是谁</span><select name="kind" value={addKind} onChange={(e) => { setAddKind(e.target.value); setAddParty(""); }}>{Object.entries(ROLE).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></label>
          {parties.isError && <ReadFailure subject="可选参与方" error={parties.error} retry={() => parties.refetch()} busy={parties.isFetching} />}
          {parties.isPending && <p className="t-foot">正在读取可选参与方…</p>}
          <label><span>选一个已有的</span><select name="party" value={addParty} onChange={(e) => setAddParty(e.target.value)}><option value="">请选择…</option>{(!parties.isError ? parties.data?.items ?? [] : []).map((p) => <option key={p.id} value={p.id}>{p.short_name || p.name}</option>)}</select></label>
          <label><span>没有？直接新建</span><span style={{ display: "flex", gap: 6 }}><input name="newparty" style={{ flex: 1 }} autoComplete="off" placeholder="名称…" value={newName} onChange={(e) => setNewName(e.target.value)} /><button type="button" className="btn sm" disabled={!newName.trim() || createParty.isPending || recovery.blocked || !parties.isSuccess} onClick={() => createParty.mutate()}>新建</button></span></label>
        </div>
        <p className="t-foot" style={{ marginTop: 10 }}>加进来之后，去「商机沟通」里打开这个人的会话，点「案子」把会话关联过来，这里就能看到他最新说了什么。</p>
        <div className="sheet-actions"><button type="button" className="btn" onClick={() => setAdding(false)}>取消</button>
          <button type="button" className="btn primary" disabled={!addParty || blocked || !parties.isSuccess} onClick={() => run(`deals/${d.id}/participants`, { party_id: addParty, role: addKind }, "已添加", () => { setAdding(false); setAddParty(""); })}>加入案子</button></div>
      </Sheet>
      <Sheet open={lost} title="标记丢单" onClose={() => setLost(false)}>
        <p className="callout">确认后案子移入已丢单，不再作为进行中生意跟进；原因会保留在经过里。</p><label className="form-grid" style={{ display: "grid" }}><span className="t-foot">为什么丢了？（以后复盘用）</span><input name="reason" autoComplete="off" autoFocus placeholder="例如：客户嫌运费贵，选了别家…" value={reason} onChange={(e) => setReason(e.target.value)} /></label>
        <div className="sheet-actions"><button type="button" className="btn" onClick={() => setLost(false)}>取消</button>
          <button type="button" className="btn primary" disabled={!reason.trim() || blocked} onClick={() => run(`deals/${d.id}/update`, { status: "lost", lost_reason: reason }, "已标记丢单", () => { setLost(false); setReason(""); })}>确认丢单</button></div>
      </Sheet>
    </>
  );
}
