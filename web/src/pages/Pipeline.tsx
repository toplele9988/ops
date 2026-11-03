import { useQuery } from "@tanstack/react-query";
import { RefreshCw } from "lucide-react";
import { Link } from "react-router";
import { get } from "../api";
import type { Tone } from "../labels";
import type { PipelineState as Status } from "../types/pipeline";
import { Chip, Empty, Skeleton } from "../ui/bits";
import "./Pipeline.css";

interface Hour { start_at: number; count: number }
interface Mix { count_24h: number; ratio_24h: number | null; last_arrival_at: number | null }
interface Source {
  key: string; channel: string; label: string; status: Status; status_label: string;
  last_seen_at: number | null; time_basis: "arrival" | "message_time" | "session_status" | "service_event";
  count_1h: number | null; count_24h: number | null; hourly: Hour[] | null;
  reason: string; next_step: string; notes: string[];
  mix: { live: Mix; history: Mix; unknown: Mix } | null;
  script_version?: string | null;
  session_status?: string | null; session_status_label?: string; has_me?: boolean | null; last_working_at?: number | null;
  failed_count_1h?: number | null; failed_count_24h?: number | null; restart_storm?: boolean | null;
  thresholds?: { slow_seconds: number; interrupted_seconds: number; day?: { start: string; end: string; night_multiplier: number } };
}
interface Coverage {
  key: string; label: string; status: "ready" | "not_connected" | "unavailable";
  numerator: number | null; denominator: number | null; ratio: number | null; reason: string;
}
interface Data {
  generated_at: number; summary: string; notes: string[]; sources: Source[];
  channels: { channel: string; label: string; status: Status; status_label: string; coverage: Coverage[] }[];
  scorecard: { channel: string; label: string; result: Result; result_label: string; metrics: Metric[] }[];
  failed_jobs: { status: "ready" | "not_connected" | "unavailable"; count: number | null; reason: string; items: { unit: string; failed_at: number | null; channel: string | null; result: string | null; exit_code: number | null }[] };
}
type Result = "pass" | "fail" | "unknown";
interface Metric { key: string; name: string; target: string; actual: string; result: Result; result_label: string; source: string; method: string; required: boolean }

const TONE: Record<Status, Tone> = { ok: "green", slow: "orange", interrupted: "red", never: "plain", not_connected: "plain", unavailable: "orange" };
const RESULT_TONE: Record<Result, Tone> = { pass: "green", fail: "red", unknown: "plain" };
const DATE = new Intl.DateTimeFormat("zh-CN", { timeZone: "Asia/Shanghai", month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
const time = (value: number | null) => value == null ? "暂无记录" : DATE.format(new Date(value * 1000));
const percent = (value: number | null) => value == null ? "暂无占比" : `${(value * 100).toFixed(1)}%`;
const duration = (seconds: number) => seconds >= 3600 ? `${+(seconds / 3600).toFixed(1)} 小时` : `${+(seconds / 60).toFixed(1)} 分钟`;

function Hourly({ source }: { source: Source }) {
  const hours = source.hourly;
  if (!hours) return <p className="t-foot pipeline-muted">暂无小时统计。{source.key === "alibaba_probe" ? "心跳只保留最新状态，不能还原历史次数。" : source.key === "whatsapp_session" ? "会话状态单独统计，不算消息到达。" : "数据来源恢复后会显示。"}</p>;
  const maximum = Math.max(1, ...hours.map((hour) => hour.count));
  const total = hours.reduce((sum, hour) => sum + hour.count, 0);
  return (
    <figure className="pipeline-chart">
      <figcaption className="t-foot">最近 48 个小时格 · 共 {total} 条（含当前小时）</figcaption>
      <svg viewBox="0 0 480 64" width="100%" height="64" role="img" aria-label={`${source.label}最近 48 个小时格共 ${total} 条，逐小时数量可在下方展开查看`}>
        {hours.map((hour, index) => {
          const height = hour.count / maximum * 60;
          return <rect key={hour.start_at} x={index * 10 + 1} y={64 - Math.max(1, height)} width="8" height={Math.max(1, height)} fill={hour.count ? "currentColor" : "var(--fill-2)"}>
            <title>{time(hour.start_at)} 起：{hour.count} 条</title>
          </rect>;
        })}
      </svg>
      <div className="pipeline-axis t-cap"><span>{time(hours[0].start_at)}</span><span>当前小时</span></div>
      {total === 0 && <p className="t-foot">这些小时内没有记录。</p>}
      <details className="t-foot">
        <summary>查看逐小时数量</summary>
        <div className="pipeline-hour-table" tabIndex={0} aria-label="逐小时数量，可滚动">
          <table><thead><tr><th scope="col">小时开始（北京时间）</th><th scope="col">条数</th></tr></thead>
            <tbody>{hours.map((hour) => <tr key={hour.start_at}><td>{time(hour.start_at)}</td><td className="num">{hour.count}</td></tr>)}</tbody>
          </table>
        </div>
      </details>
    </figure>
  );
}

function SourceCard({ source }: { source: Source }) {
  const threshold = source.thresholds;
  return (
    <section className={`group pipeline-card pipeline-${source.status}`} aria-labelledby={`pipeline-${source.key}`}>
      <div className="pipeline-card-head"><h2 className="t-title" id={`pipeline-${source.key}`}>{source.label}</h2><Chip tone={TONE[source.status]}>{source.status_label}</Chip></div>
      <p className="pipeline-reason">{source.reason}</p>
      <dl className="pipeline-counts">
        <div><dt>近 1 小时</dt><dd className="num">{source.count_1h ?? "暂无统计"}{source.count_1h != null && " 条"}</dd></div>
        <div><dt>近 24 小时</dt><dd className="num">{source.count_24h ?? "暂无统计"}{source.count_24h != null && " 条"}</dd></div>
        <div className="pipeline-last"><dt>{source.time_basis === "message_time" ? "最近消息发生时间" : source.time_basis === "session_status" ? "最近会话状态记录" : source.time_basis === "service_event" ? "最近桥接服务事件" : "最后到达时间"}</dt><dd>{time(source.last_seen_at)}</dd></div>
      </dl>
      {source.mix && <div className="pipeline-mix t-foot">
        <p>近 24 小时：实时 {source.mix.live.count_24h} 条（{percent(source.mix.live.ratio_24h)}） · 历史 {source.mix.history.count_24h} 条（{percent(source.mix.history.ratio_24h)}） · 未标明 {source.mix.unknown.count_24h} 条（{percent(source.mix.unknown.ratio_24h)}）</p>
        <p>最后实时到达：{time(source.mix.live.last_arrival_at)}</p>
      </div>}
      {source.key === "alibaba_probe" && <p className="t-foot">最后上报的脚本版本：{source.script_version ?? "未知，请在 Win10 核对"}</p>}
      {source.key === "whatsapp_session" && <div className="pipeline-mix t-foot">
        <p>当前记录状态：{source.session_status_label ?? "未知"}</p>
        <p>最后一次已登录可用：{time(source.last_working_at ?? null)}</p>
        <p>近 1 小时登录失败：{source.failed_count_1h ?? "未知"} 次 · 近 24 小时：{source.failed_count_24h ?? "未知"} 次</p>
        <p>有登录身份：{source.has_me == null ? "未知" : source.has_me ? "有" : "没有"} · 重启风暴：{source.restart_storm == null ? "未知" : source.restart_storm ? "是" : "否"}</p>
      </div>}
      <Hourly source={source} />
      <div className="pipeline-next"><b>下一步</b><p>{source.next_step}</p></div>
      <div className="pipeline-muted t-foot">
        {source.notes.map((note) => <p key={note}>{note}</p>)}
        {threshold && <details><summary>查看判定阈值</summary>
          <p>{source.key === "whatsapp_session" ? `会话尚未登录可用时立即提示；持续超过 ${duration(threshold.interrupted_seconds)} 算断流，重启风暴立即算断流。` : `超过 ${duration(threshold.slow_seconds)} 算变慢，超过 ${duration(threshold.interrupted_seconds)} 算断流。`}</p>
          {threshold.day && <p>北京时间 {threshold.day.start}–{threshold.day.end} 按正常时长计算；夜间每 {threshold.day.night_multiplier} 小时按 1 小时计算。</p>}
        </details>}
      </div>
    </section>
  );
}

export function Pipeline() {
  const q = useQuery({ queryKey: ["pipeline-health"], queryFn: () => get<Data>("/api/v1/pipeline/health"), retry: false, refetchInterval: 60_000, refetchOnWindowFocus: false });
  return (
    <div className="pipeline-page">
      <div className="page-head">
        <div><h1 className="t-large">数据管道健康</h1><p>看数据有没有持续进来、断在哪一步，以及下一步该做什么。</p></div>
        <button type="button" className="btn" disabled={q.isFetching} onClick={() => q.refetch()}><RefreshCw size={16} aria-hidden="true" />{q.isFetching ? "正在刷新…" : "刷新"}</button>
      </div>
      <p className="t-foot pipeline-muted">页面打开时每 60 秒自动刷新 · 时间均为北京时间 · <Link to="/system/channels">查看渠道接入</Link></p>
      {q.isLoading && <Skeleton lines={6} />}
      {q.isError && <div role="alert"><Empty title="没能读到管道健康状态">请点“刷新”再试；若仍失败，请重新登录或请管理员检查服务。{q.data && "下方保留的是上次结果，不能当作当前状态。"}</Empty></div>}
      {q.data && <>
        <div className="pipeline-summary callout" aria-live="polite"><strong>{q.data.summary}</strong><span className="t-foot">检查时间：{time(q.data.generated_at)}</span></div>
        <section className="block pipeline-scorecard" aria-labelledby="pipeline-acceptance">
          <h2 className="group-title" id="pipeline-acceptance">验收达标</h2>
          <p className="t-foot pipeline-muted">任一必验项不达标，渠道就不达标；没有不达标但还有未知项，渠道为未知。参考项不参与汇总，当前健康状态不等于完整验收。</p>
          {(q.data.scorecard ?? []).map((channel) => <section key={channel.channel} className="group pipeline-score-channel">
            <div className="pipeline-card-head"><h3 className="t-title">{channel.label}</h3><Chip tone={RESULT_TONE[channel.result]}>{channel.result_label}</Chip></div>
            <details open><summary className="t-foot">查看指标、目标与实测口径</summary>
              <div className="pipeline-score-metrics">{channel.metrics.map((metric) => <div className="pipeline-score-metric" key={metric.key}>
                <div className="pipeline-card-head"><strong>{metric.name}</strong><Chip tone={RESULT_TONE[metric.result]}>{metric.result_label}{!metric.required && " · 参考"}</Chip></div>
                <p>目标：{metric.target}</p><p>实测：{metric.actual}</p>
                <p className="t-foot pipeline-muted">来源：{metric.source}</p><p className="t-foot pipeline-muted">口径：{metric.method}</p>
              </div>)}</div>
            </details>
          </section>)}
          {!q.data.scorecard?.length && <Empty title="验收记分卡尚未接入">请管理员核对服务版本后刷新，缺少记录不能当作达标。</Empty>}
        </section>
        <div className="pipeline-grid">{q.data.sources.map((source) => <SourceCard key={source.key} source={source} />)}</div>
        {q.data.sources.length === 0 && <Empty title="暂时没有管道统计">请管理员核对来源配置，然后刷新。</Empty>}
        <section className="block pipeline-coverage"><h2 className="group-title">附件与翻译覆盖率</h2>
          <div className="group">{q.data.channels.map((channel) => <div key={channel.channel} className="row pipeline-coverage-row">
            <strong>{channel.label}</strong>
            {channel.coverage.map((metric) => <div key={metric.key}><div>{metric.label}：<b>{metric.status === "not_connected" ? "未接入" : metric.status === "unavailable" ? "暂时读不到" : percent(metric.ratio)}</b></div>
              {metric.denominator != null && <div className="t-foot num">{metric.numerator} / {metric.denominator}</div>}<p className="t-foot pipeline-muted">{metric.reason}</p></div>)}
          </div>)}</div>
        </section>
        <section className="block pipeline-jobs" aria-labelledby="pipeline-failed-jobs"><h2 className="group-title" id="pipeline-failed-jobs">后台作业失败</h2>
          <div className="group pipeline-card">
            <Chip tone={q.data.failed_jobs?.status === "ready" && q.data.failed_jobs.count ? "red" : "plain"}>{q.data.failed_jobs?.status === "ready" ? `失败 ${q.data.failed_jobs.count} 项` : q.data.failed_jobs?.status === "unavailable" ? "暂时读不到" : "未接入"}</Chip>
            <p>{q.data.failed_jobs?.reason ?? "请管理员接入只读的服务状态读取。"}</p>
            {[...q.data.channels, { channel: "other", label: "未归类" }].map((channel) => <div key={channel.channel}>
              <strong>{channel.label}</strong>
              {q.data.failed_jobs?.items.filter((item) => (item.channel ?? "other") === channel.channel).map((item) => <p key={item.unit}>{item.unit} · 失败时间：{time(item.failed_at)} · 结果：{item.result ?? "未知"} · 退出码：{item.exit_code ?? "未知"}</p>)}
            </div>)}
          </div>
        </section>
        <div className="t-foot pipeline-muted">{q.data.notes.map((note) => <p key={note}>{note}</p>)}</div>
      </>}
    </div>
  );
}
