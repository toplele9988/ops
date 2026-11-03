import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bot, RefreshCw, TriangleAlert } from "lucide-react";
import { useEffect, useRef } from "react";
import { Link } from "react-router";
import { get, postForm } from "../api";
import { ago } from "../format";
import { Alert, BriefOut, Run, RoleInfo, SecretaryOut, SEVERITY, riskText } from "../roles";
import { Chip } from "./bits";
import { useToast } from "./Toast";

const SOURCE = (r: Run<unknown>) => (r.source === "ai" ? "AI 整理" : r.status === "fallback" ? "按规则整理（AI 暂时没成功）" : "按规则整理");

/** Deal secretary: "where is this deal, who is it waiting on, what next". */
export function SecretaryPanel({ dealId }: { dealId: number }) {
  const toast = useToast();
  const client = useQueryClient();
  const q = useQuery({ queryKey: ["secretary", dealId], queryFn: () => get<Run<SecretaryOut>>(`/api/v1/deals/${dealId}/secretary`), retry: false });
  const silent = useRef(false);
  const run = useMutation({
    mutationFn: () => postForm<Run<SecretaryOut>>(`/api/v1/deals/${dealId}/secretary/run`, {}),
    onSuccess: (r) => { client.setQueryData(["secretary", dealId], r); if (!silent.current) toast(r.reused ? "没有新情况，还是上次的结论" : "秘书看完了"); },
    onError: (e: Error) => { if (!silent.current) toast(e.message, "error"); },
    onSettled: () => { silent.current = false; },
  });
  const auto = useRef(false);
  useEffect(() => {
    // First time anyone opens this deal: let the secretary read it (silently; the button is there if it fails).
    if (q.isError && !auto.current) { auto.current = true; silent.current = true; run.mutate(); }
  }, [q.isError, run]);
  const out = q.data?.output_json;
  return (
    <div className="block">
      <h2 className="group-title"><Bot size={15} aria-hidden="true" style={{ verticalAlign: -2, marginRight: 4 }} />案子秘书{q.data && <span className="count">{SOURCE(q.data)} · {ago(q.data.created_at)}</span>}</h2>
      <div className="group">
        {!out && <div className="row"><div className="row-main"><div className="row-title">{q.isLoading ? "读取中…" : "秘书还没看过这个案子"}</div><div className="row-sub" style={{ whiteSpace: "normal" }}>它会读完这个案子的所有会话、待办和报价，告诉你现在卡在谁手上、下一步做什么。</div></div></div>}
        {out && (
          <div className="row" style={{ alignItems: "flex-start" }}>
            <div className="row-main" style={{ whiteSpace: "normal" }}>
              <div className="row-title" style={{ whiteSpace: "normal" }}>{out.headline}</div>
              {out.waiting_on.length > 0 && <ul className="plain-list">{out.waiting_on.map((w, i) => <li key={i}><b>{w.who}</b>：{w.what}{w.elapsed ? `（${w.elapsed}）` : ""}</li>)}</ul>}
              {out.next_steps.length > 0 && <><div className="t-foot" style={{ marginTop: 8 }}>建议下一步</div><ol className="plain-list">{out.next_steps.map((s, i) => <li key={i}><b>{s.who}</b>：{s.what}<span className="t-cap">　{s.why}</span></li>)}</ol></>}
              {out.risks.length > 0 && <><div className="t-foot" style={{ marginTop: 8 }}>要留意</div><ul className="plain-list">{out.risks.map((r, i) => <li key={i}>{riskText(r)}</li>)}</ul></>}
              {out.suggested_stage_label && <div className="t-cap" style={{ marginTop: 8 }}>秘书建议阶段：{out.suggested_stage_label}（由你确认，不会自动改）</div>}
            </div>
          </div>
        )}
        <div className="row"><button type="button" className="btn" disabled={run.isPending} onClick={() => run.mutate()}><RefreshCw size={16} aria-hidden="true" />{run.isPending ? "秘书在看…" : out ? "让秘书重新看一遍" : "让秘书看看这个案子"}</button></div>
      </div>
    </div>
  );
}

/** Patrol alerts (optionally for one deal). */
export function AlertList({ dealId, limit = 6, empty, framed }: { dealId?: number; limit?: number; empty?: string; framed?: boolean }) {
  const toast = useToast();
  const client = useQueryClient();
  const q = useQuery({ queryKey: ["alerts", dealId ?? "all"], queryFn: () => get<{ items: Alert[] }>(`/api/v1/patrol/alerts${dealId ? `?deal_id=${dealId}` : ""}`), retry: false });
  const dismiss = useMutation({
    mutationFn: (id: number) => postForm(`/api/v1/patrol/alerts/${id}/dismiss`, {}),
    onSuccess: () => client.invalidateQueries({ queryKey: ["alerts"] }),
    onError: (e: Error) => toast(e.message, "error"),
  });
  const patrol = useQuery({ queryKey: ["roles"], queryFn: () => get<{ items: RoleInfo[] }>("/api/v1/roles"), retry: false });
  const lastPatrol = patrol.data?.items.find((r) => r.key === "patrol");
  const items = (q.data?.items ?? []).slice(0, limit);
  if (q.isLoading) return null;
  const Wrap = framed ? ({ children }: { children: React.ReactNode }) => <div className="block"><h2 className="group-title">提醒<span className="count num">{items.length}</span></h2><div className="group">{children}</div></div> : ({ children }: { children: React.ReactNode }) => <>{children}</>;
  return (
    <Wrap>
      <div className="row"><div className="row-main t-sub">{lastPatrol?.last_run_at ? `规则巡检生成于 ${lastPatrol.last_run_at}` : "还没有巡检记录"}。有新消息或新收款后，请更新巡检和简报。</div></div>
      {items.length === 0 && <div className="row"><div className="row-main t-sub">{empty ?? "当前没有提醒，请按需更新巡检。"}</div></div>}
      {items.map((a) => (
        <div className="row" key={a.id} style={{ alignItems: "flex-start" }}>
          <TriangleAlert size={18} aria-hidden="true" style={{ flex: "none", color: a.severity === "high" ? "var(--red)" : "var(--orange)" }} />
          <div className="row-main" style={{ whiteSpace: "normal" }}>
            <div className="row-title" style={{ whiteSpace: "normal" }}>{a.message}</div>
            <div className="row-sub">{SEVERITY[a.severity].text}{a.deal_id && !dealId ? <> · <Link to={`/deals/${a.deal_id}`}>打开案子</Link></> : null}</div>
          </div>
          <button type="button" className="btn sm plain" onClick={() => dismiss.mutate(a.id)}>知道了</button>
        </div>
      ))}
    </Wrap>
  );
}

const money = (amounts: Record<string, string>) => { const e = Object.entries(amounts); return e.length ? e.map(([c, v]) => `${c} ${v}`).join("、") : null; };

/** The owner's one-page daily brief. */
export function BriefCard() {
  const toast = useToast();
  const client = useQueryClient();
  const q = useQuery({ queryKey: ["brief"], queryFn: () => get<Run<BriefOut>>("/api/v1/brief/today"), retry: false });
  const run = useMutation({
    mutationFn: async () => {
      await postForm("/api/v1/patrol/run", {});
      try { return await postForm<Run<BriefOut>>("/api/v1/brief/run", {}); }
      catch { throw new Error("巡检已更新，简报未能更新，请稍后手动更新。"); }
    },
    onSuccess: (r) => { client.setQueryData(["brief"], r); toast(r.reused ? "巡检已完成，简报沿用上次结果" : "巡检和简报已更新"); },
    onSettled: () => { client.invalidateQueries({ queryKey: ["alerts"] }); client.invalidateQueries({ queryKey: ["roles"] }); },
    onError: (e: Error) => toast(e.message, "error"),
  });
  const b = q.data?.output_json;
  const rows: [string, "today" | "week" | "month"][] = [["今天成交", "today"], ["本周成交", "week"], ["本月成交", "month"]];
  return (
    <div className="block">
      <h2 className="group-title">今日成交简报{q.data && <span className="count">{SOURCE(q.data)} · 生成于 {q.data.created_at}</span>}</h2>
      <div className="group">
        <div className="row"><div className="row-main t-sub" style={{ whiteSpace: "normal" }}>有新消息或新收款后，请更新巡检和简报。金额是期间新成交案子的当前已确认收款净额；没有已确认收款时用已接受报价兜底，缺资料时待补。不同币种分别列示，不是订单额或期间现金流。</div></div>
        {q.data?.error && <div className="row"><div className="row-main t-sub">{q.data.error}</div></div>}
        {!b ? (
          <div className="row"><div className="row-main"><div className="row-title">{q.isLoading ? "读取中…" : "今天还没有生成简报"}</div><div className="row-sub" style={{ whiteSpace: "normal" }}>点右边按钮，系统会算出今天的成交、在谈案子和最该处理的事，并用一小段话讲给你听。</div></div></div>
        ) : (
          <>
            <div className="brief-stats">
              {rows.map(([label, k]) => (
                <div key={k}><span className="t-foot">{label}</span><span className="kpi-n num" style={{ fontSize: 26, lineHeight: "30px" }}>{b.results[k].count}<span className="t-cap"> 单</span></span><span className="t-cap num">{money(b.results[k].amounts) ?? (b.results[k].amount_pending ? "金额待补" : "—")}</span></div>
              ))}
              <div><span className="t-foot">在谈案子</span><span className="kpi-n num" style={{ fontSize: 26, lineHeight: "30px" }}>{b.negotiating}</span><span className="t-cap">本周报价发出 {b.quotes_this_week.sent} · 接受 {b.quotes_this_week.accepted}</span></div>
            </div>
            <div className="row"><div className="row-main" style={{ whiteSpace: "normal" }}>{b.narrative}</div></div>
            {b.priorities.slice(0, 5).map((p, i) => (
              <div className="row" key={i}><Chip tone={p.severity === "high" ? "red" : p.severity === "medium" ? "orange" : "plain"}>{SEVERITY[p.severity as "high"]?.text ?? "提示"}</Chip><div className="row-main" style={{ whiteSpace: "normal" }}>{p.message}</div>{p.deal_id && <Link to={`/deals/${p.deal_id}`} className="btn sm">打开</Link>}</div>
            ))}
          </>
        )}
        <div className="row"><button type="button" className="btn" disabled={run.isPending} onClick={() => run.mutate()}><RefreshCw size={16} aria-hidden="true" />{run.isPending ? "正在更新…" : "更新巡检和简报"}</button></div>
      </div>
    </div>
  );
}
