import { useQuery } from "@tanstack/react-query";
import { ArrowRight, RefreshCw } from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router";
import { ApiError, get } from "../api";
import type { Tone } from "../labels";
import type { OverviewChannel, OverviewResponse, OverviewStage, OverviewStageKey, OverviewTime } from "../types/overview";
import type { PipelineState } from "../types/pipeline";
import { Chip, Empty, Skeleton } from "../ui/bits";
import "./Overview.css";
import { Workbench } from "./Workbench";
import { PageIntro } from "./overview/PageIntro";
import { TodayQueue } from "./overview/TodayQueue";
import { Welcome } from "./overview/Welcome";

const STATES: Record<PipelineState, { text: string; tone: Tone; priority: number }> = {
  interrupted: { text: "已中断", tone: "red", priority: 0 },
  slow: { text: "偏慢", tone: "orange", priority: 1 },
  unavailable: { text: "未知 · 暂时读不到", tone: "orange", priority: 2 },
  not_connected: { text: "未接入", tone: "plain", priority: 3 },
  never: { text: "尚无记录", tone: "plain", priority: 4 },
  ok: { text: "正常", tone: "green", priority: 5 },
};
const STAGES: { key: OverviewStageKey; label: string }[] = [
  { key: "connected", label: "接入" }, { key: "collecting", label: "实时采集" },
  { key: "stored", label: "保存" }, { key: "knowledge", label: "进知识库" },
];
const DATE = new Intl.DateTimeFormat("zh-CN", {
  timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit",
  hour: "2-digit", minute: "2-digit", hourCycle: "h23",
});
const number = (value: number | null | undefined) => value == null ? "未知" : value.toLocaleString("zh-CN");
const milliseconds = (value: OverviewTime) => value == null ? NaN : typeof value === "number" ? value * 1000 : Date.parse(value);

function relative(value: number, now: number) {
  const seconds = Math.abs(now - value) / 1000;
  if (seconds < 60) return value > now ? "不到 1 分钟后" : "不到 1 分钟前";
  const [amount, unit] = seconds < 3600 ? [Math.floor(seconds / 60), "分钟"]
    : seconds < 86400 ? [Math.floor(seconds / 3600), "小时"] : [Math.floor(seconds / 86400), "天"];
  return `${amount} ${unit}${value > now ? "后" : "前"}`;
}

function Time({ value, now }: { value: OverviewTime; now: number }) {
  const ms = milliseconds(value);
  if (!Number.isFinite(ms)) return <>未知</>;
  return <time dateTime={new Date(ms).toISOString()}>{DATE.format(ms)} 北京时间 <span>· {relative(ms, now)}</span></time>;
}

function Expiry({ value, now }: { value: string | null; now: number }) {
  if (!value) return <>未知</>;
  const dateOnly = /^\d{4}-\d{2}-\d{2}$/.test(value);
  const ms = Date.parse(dateOnly ? `${value}T00:00:00+08:00` : value);
  if (!Number.isFinite(ms)) return <>未知</>;
  return <time dateTime={value}>{dateOnly ? value : DATE.format(ms)} 北京时间 · {relative(ms, now)}{ms < now && "（已到期）"}</time>;
}

function Status({ state }: { state: PipelineState }) {
  return <Chip tone={STATES[state].tone}>{STATES[state].text}</Chip>;
}

function channelState(channel: OverviewChannel) {
  return [channel.state, ...STAGES.map(({ key }) => channel.stages.find((stage) => stage.key === key)?.state ?? "unavailable")]
    .sort((a, b) => STATES[a].priority - STATES[b].priority)[0];
}

function Metric({ label, value }: { label: string; value: number | null }) {
  return <div><dt>{label}</dt><dd className="num">{number(value)}</dd></div>;
}

function Stage({ stage, now, index }: { stage: OverviewStage; now: number; index: number }) {
  return <li className="overview-stage" data-state={stage.state} data-stage={stage.key}>
    <div className="overview-stage-heading"><span className="overview-step num">0{index + 1}</span><h4>{stage.label}</h4><ArrowRight size={14} aria-hidden="true" /></div>
    <Status state={stage.state} />
    <p className="overview-stage-detail">{stage.detail}</p>
    <dl className="overview-metrics">
      {stage.key === "collecting" && <Metric label="近 24 小时采集" value={stage.count_24h} />}
      {stage.key === "stored" && <><Metric label="累计保存" value={stage.total} /><Metric label="近 24 小时保存" value={stage.count_24h} /></>}
      {stage.key === "knowledge" && <><Metric label="已处理" value={stage.processed} /><Metric label="待处理" value={stage.pending} /></>}
    </dl>
    <p className="overview-stage-time t-foot">{stage.key === "knowledge" ? "最近处理" : stage.key === "connected" ? "最近上报" : "最近到达"}<br />
      <Time value={stage.key === "knowledge" ? stage.last_processed_at : stage.last_arrival_at} now={now} />
    </p>
    {stage.key !== "knowledge" && stage.last_arrival_at == null && stage.age_seconds != null && <p className="t-foot">接口记录距上次到达 {number(Math.floor(stage.age_seconds / 60))} 分钟（截至本次检查）</p>}
  </li>;
}

function Channel({ channel, now, problems }: { channel: OverviewChannel; now: number; problems: OverviewResponse["summary"]["problems"] }) {
  const state = channelState(channel);
  const actions = problems.filter((problem) => problem.channel === channel.key || problem.channel === channel.label);
  return <section className="overview-channel" data-state={state} data-channel={channel.key} aria-labelledby={`overview-${channel.key}`}>
    <header className="overview-channel-heading">
      <h3 id={`overview-${channel.key}`}>{channel.label}</h3><Status state={state} /><p>{channel.headline}</p>
    </header>
    <p className="overview-scroll-hint t-foot">横向滑动查看四个环节 →</p>
    <div className="overview-flow-scroll" tabIndex={0} role="region" aria-label={`${channel.label}四个环节，可横向滚动`}>
      <ol className="overview-flow">{STAGES.map(({ key, label }, index) => <Stage key={key} index={index} now={now} stage={channel.stages.find((stage) => stage.key === key) ?? {
        key, label, state: "unavailable", detail: "本次接口未提供此环节，请检查统计服务。",
        last_arrival_at: null, age_seconds: null, count_24h: null, total: null, processed: null, pending: null, last_processed_at: null,
      }} />)}</ol>
    </div>
    {(actions.length > 0 || state !== "ok") && <div className="overview-channel-next"><b>下一步</b><div>{actions.length
      ? actions.map((problem, index) => <p key={index}>{problem.next_step || "请管理员检查该渠道的服务和统计记录，再刷新确认。"}</p>)
      : <p>请查看<Link to="/system/pipeline">数据管道健康</Link>，核对异常环节的服务和统计记录，再刷新确认。</p>}</div></div>}
    {channel.notes.length > 0 && <div className="overview-channel-notes t-foot">{channel.notes.map((note, index) => <p key={index}>{note}</p>)}</div>}
  </section>;
}

function Budget({ budget, now }: { budget: OverviewResponse["model_budget"]; now: number }) {
  const official = budget.deepseek_official;
  const balance = official?.balance_micros == null ? "未知" : (official.balance_micros / 1_000_000).toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 6 });
  return <section className="overview-budget" aria-labelledby="overview-budget">
    <div className="overview-section-heading"><h2 className="t-headline" id="overview-budget">模型预算</h2><span className="t-foot">余额与免费额度分别看</span></div>
    <div className="overview-official"><h3>官方 DeepSeek 余额</h3><p className="overview-balance num">{balance} <span>{official?.currency ?? "币种未知"}</span></p>
      <p className="t-foot">观测于 <Time value={official?.observed_at ?? null} now={now} /></p>
    </div>
    <h3 className="overview-pool-heading">百炼免费额度</h3>
    {budget.free_pools.length === 0 ? <p className="t-foot">未知 · 暂无免费额度记录，请在百炼控制台核对。</p> : <ul className="overview-pools">{budget.free_pools.map((pool, index) => <li key={`${pool.model}-${index}`}>
      <div className="overview-pool-title"><strong>{pool.model}</strong><span className="num">{number(pool.remaining_tokens)} <small>tokens 剩余</small></span></div>
      <p className="t-foot">观测于 <Time value={pool.observed_at} now={now} /></p>
      <p className="t-foot">到期日 <Expiry value={pool.expires_on} now={now} /></p>
    </li>)}</ul>}
    <p className="overview-budget-note t-foot">{budget.note}</p>
  </section>;
}

function History({ history, now }: { history: OverviewResponse["history"]["alibaba"]; now: number }) {
  const completeness = history.completeness == null ? "未知" : ({ partial: "部分覆盖", complete: "完整覆盖", unknown: "未知", incomplete: "覆盖不完整" } as Record<string, string>)[history.completeness] ?? history.completeness;
  const months = [...history.by_month].sort((a, b) => a.month.localeCompare(b.month));
  const maximum = Math.max(1, ...months.map((month) => month.count ?? 0));
  return <section className="overview-history" aria-labelledby="overview-history">
    <div className="overview-section-heading"><h2 className="t-headline" id="overview-history">阿里历史数据覆盖</h2><Chip tone={history.completeness === "complete" ? "green" : "plain"}>{completeness}</Chip></div>
    <dl className="overview-history-totals overview-metrics"><Metric label="已保存消息" value={history.messages} /><Metric label="覆盖会话" value={history.conversations} /></dl>
    <dl className="overview-range"><div><dt>最早消息</dt><dd><Time value={history.earliest_sent_at} now={now} /></dd></div><div><dt>最新消息</dt><dd><Time value={history.latest_sent_at} now={now} /></dd></div></dl>
    <details className="overview-months" open><summary>按月查看消息量（北京时间）</summary>
      {months.length === 0 ? <p className="t-foot">未知 · 暂无按月统计。</p> : <table><thead><tr><th scope="col">月份</th><th scope="col">消息量</th><th scope="col">条数</th></tr></thead><tbody>{months.map((month) => <tr key={month.month}><th scope="row">{month.month}</th><td><svg viewBox="0 0 200 6" width="100%" height="6" aria-hidden="true"><rect width="200" height="6" rx="3" fill="var(--fill)" />{month.count != null && <rect width={month.count / maximum * 200} height="6" rx="3" fill="var(--tint)" />}</svg></td><td className="num">{number(month.count)}</td></tr>)}</tbody></table>}
    </details>
    <p className="overview-budget-note t-foot">{history.detail}</p>
  </section>;
}

export function Overview() {
  const [now, setNow] = useState(Date.now);
  useEffect(() => { const timer = window.setInterval(() => setNow(Date.now()), 30_000); return () => window.clearInterval(timer); }, []);
  const q = useQuery({ queryKey: ["overview"], queryFn: () => get<OverviewResponse>("/api/v1/overview"), retry: false, refetchInterval: 60_000 });
  const data = q.data;
  const channels = data ? [...data.channels].sort((a, b) => STATES[channelState(a)].priority - STATES[channelState(b)].priority) : [];
  const problems = data ? [...data.summary.problems].sort((a, b) => {
    const priority = (key: string) => { const channel = channels.find((item) => item.key === key || item.label === key); return channel ? STATES[channelState(channel)].priority : 2; };
    return priority(a.channel) - priority(b.channel);
  }) : [];
  const notDeployed = q.error instanceof ApiError && q.error.status === 404;

  return <div className="overview-page">
    <div className="page-head"><div><p className="overview-eyebrow">运营台 / OVERVIEW</p><h1 className="t-large">全局总览</h1><p>消息有没有进来，资料有没有存好，知识有没有积累。</p></div><button type="button" className="btn" disabled={q.isFetching} onClick={() => q.refetch()}><RefreshCw size={16} aria-hidden="true" />{q.isFetching ? "正在刷新…" : "刷新"}</button></div>
    <PageIntro page="overview" />
    <Welcome />
    <TodayQueue />
    <p className="overview-freshness t-foot">每 60 秒自动刷新 · 时间均为北京时间{data && <> · 检查于 <Time value={data.generated_at} now={now} /></>}</p>
    {q.isLoading && <Skeleton lines={6} />}
    {q.isError && <div className="group overview-error" role="alert" data-overview-error={notDeployed ? "not-deployed" : "read-failed"}><Empty title={notDeployed ? "接口尚未部署" : "读取失败"}>
      {notDeployed ? "总览接口 /api/v1/overview 返回 404。请管理员部署接口后点击“刷新”。" : "无法读取总览数据，请点击“刷新”重试；仍失败时请检查网络、登录状态或联系管理员。"}
      {data && "下方保留上次读取结果，不能当作当前状态。"}
    </Empty></div>}
    {data && <>
      <section className="overview-summary" data-state={data.summary.state} aria-label="当前结论" aria-live="polite">
        <div className="overview-summary-title"><Status state={data.summary.state} /><h2 className="t-title">{data.summary.headline}</h2></div>
        {problems.length > 0 && <ol className="overview-problems">{problems.map((problem, index) => {
          const channel = channels.find((item) => item.key === problem.channel || item.label === problem.channel);
          return <li key={index} data-state={channel ? channelState(channel) : "unavailable"}><strong>{channel?.label ?? problem.channel}</strong><div><p>{problem.text}</p><p className="overview-action"><b>下一步：</b>{problem.next_step || "请管理员检查该渠道服务，再刷新确认。"}</p></div></li>;
        })}</ol>}
      </section>
      <section className="overview-channels" aria-labelledby="overview-channels"><div className="overview-section-heading"><h2 className="t-headline" id="overview-channels">渠道流水线</h2><Link className="t-foot" to="/system/pipeline">查看管道详情 →</Link></div>
        {channels.map((channel) => <Channel key={channel.key} channel={channel} now={now} problems={problems} />)}
        {channels.length === 0 && <Empty title="未知 · 暂无渠道状态">接口尚未提供渠道记录，请管理员检查统计服务后刷新。</Empty>}
      </section>
      <div className="overview-resources"><Budget budget={data.model_budget} now={now} /><History history={data.history.alibaba} now={now} /></div>
    </>}
    <section className="overview-daily" aria-labelledby="overview-daily"><h2 className="t-headline" id="overview-daily">今日运营</h2><p className="t-foot">待回复、案子、成交简报和资料提醒。</p><Workbench embedded /></section>
  </div>;
}
