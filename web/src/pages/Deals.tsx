import { PageIntro } from "./overview/PageIntro";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useSearchParams } from "react-router";
import { get, qs } from "../api";
import { DealRow, Participant, ROLE_SHORT, STAGES, STATUS_TEXT } from "../deals";
import { ago } from "../format";
import { Chip, Empty, SearchField, Skeleton } from "../ui/bits";
import { Segmented } from "../ui/Segmented";
import { NewDeal } from "./deals/NewDeal";
import { useDealAction } from "./deals/actions";

interface Summary { open: number; won: number; lost: number; paused: number; by_stage: { stage: string; label: string; count: number }[]; waiting_on_us: number; overdue: number; due_tasks: number }
interface List { items: DealRow[]; total: number }

export function dot(p: Participant) {
  const tone = p.state === "us" ? "red" : p.state === "them" ? "orange" : "plain";
  const text = p.state === "us" ? "要我回复" : p.state === "them" ? "在等" : "未关联会话";
  return <Chip key={p.party_id} tone={tone}>{ROLE_SHORT[p.role] ?? p.role} · {text}</Chip>;
}

export function Deals() {
  const action = useDealAction();
  const [params, setParams] = useSearchParams();
  const [page, setPage] = useState(1);
  const status = params.get("status") ?? "open";
  const stage = params.get("stage") ?? "";
  const waiting = params.get("waiting") ?? "";
  const q = params.get("q") ?? "";
  const set = (changes: Record<string, string>) => {
    const next = new URLSearchParams(params);
    for (const [key, value] of Object.entries(changes)) (value ? next.set(key, value) : next.delete(key));
    setParams(next, { replace: true });
    setPage(1);
  };
  const summary = useQuery({ queryKey: ["deals-summary"], queryFn: () => get<Summary>("/api/v1/deals/summary"), retry: false });
  const list = useQuery({
    queryKey: ["deals", status, stage, waiting, q, page],
    queryFn: () => get<List>(`/api/v1/deals${qs({ status, stage, waiting, q, page, page_size: 50 })}`),
    placeholderData: keepPreviousData, retry: false,
  });
  const s = summary.data;
  const pages = list.data ? Math.max(1, Math.ceil(list.data.total / 50)) : 1;
  const grouped = STAGES.map((st) => ({ ...st, items: (list.data?.items ?? []).filter((d) => d.stage === st.key) }));

  return (
    <>
      <div className="page-head">
        <div>
          <h1 className="t-large">案子</h1>
          <p>按阶段跟进每笔生意，先处理等你回复的客户和到期事项。</p>
        </div>
        <NewDeal />
      </div>
      <PageIntro page="deals" />
      <div className="kpis">
        <button type="button" className={`kpi${status === "open" && !waiting ? " on" : ""}`} onClick={() => set({ status: "open", waiting: "" })}><span className="t-foot">进行中</span><span className="kpi-n num">{s?.open ?? "—"}</span><span className="t-cap">正在谈的生意</span></button>
        <button type="button" className={`kpi${waiting === "us" ? " on" : ""}`} onClick={() => set({ status: "open", waiting: "us" })}><span className="t-foot">要我处理</span><span className="kpi-n num" style={{ color: s?.waiting_on_us ? "var(--red)" : undefined }}>{s?.waiting_on_us ?? "—"}</span><span className="t-cap">对方在等你回复，或有待办到期</span></button>
        <button type="button" className={`kpi${status === "won" ? " on" : ""}`} onClick={() => set({ status: "won", waiting: "" })}><span className="t-foot">已成交</span><span className="kpi-n num" style={{ color: "var(--green)" }}>{s?.won ?? "—"}</span><span className="t-cap">客户已付款</span></button>
        <button type="button" className={`kpi${status === "lost" ? " on" : ""}`} onClick={() => set({ status: "lost", waiting: "" })}><span className="t-foot">已丢单</span><span className="kpi-n num">{s?.lost ?? "—"}</span><span className="t-cap">记录了原因，可回看</span></button>
      </div>
      <div className="toolbar">
        <Segmented label="状态" value={status} onChange={(value) => set({ status: value, waiting: "" })}
          options={[{ value: "open", label: "进行中" }, { value: "won", label: "已成交" }, { value: "lost", label: "已丢单" }, { value: "paused", label: "暂停" }]} />
        <div className="grow"><SearchField label="搜索案子" placeholder="搜案子名、客户…" value={q} onSearch={(value) => set({ q: value })} /></div>
        {list.data && <span className="t-foot num">共 {list.data.total} 个</span>}
      </div>

      {action.message && <p className="ops-feedback" role="status">{action.message}</p>}
      {action.error && <p className="callout" role="alert">{action.error}<button className="btn sm" onClick={() => list.refetch()}>刷新核对</button></p>}
      {list.isLoading && <Skeleton lines={5} />}
      {list.isError && <div className="group"><Empty title="没能读到案子" action={<button className="btn" onClick={() => list.refetch()}>重新读取案子</button>}>{(list.error as Error).message}</Empty></div>}
      {list.data && list.data.items.length === 0 && (
        <div className="group"><Empty title={q || waiting ? "没有符合条件的案子" : "还没有案子"} action={<Link className="btn primary" to="/inbox?state=waiting">去商机沟通，从会话建一个案子</Link>}>
          打开一个客户会话，点右上角「案子」→「新建案子」，再把对应的供应商、货代会话关联进来。</Empty></div>
      )}
      {list.data && <>
        <p className="t-foot board-hint">按阶段查看当前页案子，可横向滚动。阶段数量仅统计当前页；点击卡片继续跟进。</p>
        <div className="deal-board" role="region" aria-label="销售管道看板" tabIndex={0}>
          {grouped.map((g) => <section className="deal-column" key={g.key} aria-labelledby={`stage-${g.key}`}>
            <div className="ops-column-title"><h2 className="group-title" id={`stage-${g.key}`}>{g.label}<span className="count num">{g.items.length}</span></h2><NewDeal stage={g.key} compact /></div>
            {g.items.length === 0 && <p className="t-foot">本页暂无案子</p>}
            {g.items.map((d) => <article key={d.id} className="deal-card ops-card" aria-label={d.title}><Link to={`/deals/${d.id}`}><h3 className="t-headline">{d.title}</h3></Link>
              <p className="t-foot">{d.customer?.name ?? "还没有客户"}{d.value ? ` · ${d.currency} ${d.value}` : " · 金额待定"}</p>
              {d.needs_attention && <Chip tone="red">需要跟进</Chip>}
              <p className="t-foot">{d.waiting_on_us ? "卡在我方：请先回复" : d.waiting_on.length ? `在等${d.waiting_on.map((r) => ROLE_SHORT[r] ?? r).join("、")}` : "请确认下一步安排"}</p>
              <div className="deal-participants">{d.participants.map(dot)}</div>
              {<p className="t-foot">下一步：{d.next_action || "核对本阶段待办"}{d.next_action_due ? `（${d.next_action_due}）` : ""}</p>}
              <p className="t-cap">{d.open_tasks} 个待办{d.last_activity_at ? ` · ${ago(d.last_activity_at)}` : ""}</p>
              {d.status !== "open" && <Chip tone={STATUS_TEXT[d.status]?.tone}>{STATUS_TEXT[d.status]?.text}</Chip>}
              <div className="ops-actions"><Link className="btn sm" to={`/deals/${d.id}`}>打开跟进</Link>{(() => {
                const next = STAGES[STAGES.findIndex((st) => st.key === d.stage) + 1];
                if (d.status === "open" && next && ["sourcing", "quoting", "negotiating", "awaiting_payment"].includes(next.key)) return <button className="btn sm primary" disabled={action.busy} onClick={() => action.run(`deals/${d.id}/update`, { stage: next.key }, `已进入「${next.label}」，阶段待办已生成并记入经过`)}>推进到{next.label}</button>;
                if (d.status === "open" && next?.key === "paid") return <Link className="btn sm primary" to={`/deals/${d.id}#deal-payments`}>核对并确认收款</Link>;
                if (d.status === "won") return <Link className="btn sm primary" to={`/deals/${d.id}#deal-fulfil`}>处理备货发货</Link>;
                return null;
              })()}</div>
            </article>)}
          </section>)}
        </div>
      </>}
      {list.data && pages > 1 && (
        <div className="pager">
          <button type="button" className="btn sm" disabled={page <= 1} onClick={() => setPage(page - 1)}>上一页</button><span className="num">{page} / {pages}</span>
          <button type="button" className="btn sm" disabled={page >= pages} onClick={() => setPage(page + 1)}>下一页</button>
        </div>
      )}
    </>
  );
}
