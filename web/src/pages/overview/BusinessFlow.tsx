import { useQuery } from "@tanstack/react-query";
import { ArrowRight } from "lucide-react";
import { Link } from "react-router";
import { ApiError, get } from "../../api";
import { readProfiles } from "../../profiles";
import "./flow.css";

interface Source { isPending: boolean; isError: boolean; error: Error | null }
interface Count { value: number | null; reason?: string }
interface StageCounts { by_stage: { stage: string; count: number | null }[] }
interface Shipping { total: number; items: { stage: string }[] }
const count = (value: unknown): value is number => typeof value === "number" && Number.isSafeInteger(value) && value >= 0;
const unavailable = (source: Source): Count | undefined => source.isPending ? { value: null, reason: "正在读取数量。" }
  : source.isError ? { value: null, reason: source.error instanceof ApiError && source.error.status === 404 ? "统计尚未接入，请打开列表核对。" : "数量读取失败，请刷新或打开列表核对。" } : undefined;
const known = (value: unknown, reason = "本次统计没有提供数量，请打开列表核对。"): Count => count(value) ? { value } : { value: null, reason };
const dealPath = (stage: string, status = "open") => `/deals?status=${status}&stage=${stage}`;

export const FLOW_QUERY_KEYS = [["inbox-summary"], ["triage"], ["deals-summary"], ["fulfil-board"], ["flow-aftersales"], ["flow-profiles"]];

function NumberLink({ metric, to, label }: { metric: Count; to: string; label: string }) {
  return <div className="business-flow-metric">
    <Link className="business-flow-number num" to={to} aria-label={`${label}：${metric.value == null ? "数量未知" : metric.value}，打开查看`}>
      {metric.value == null ? "—" : metric.value.toLocaleString("zh-CN")}
    </Link>
    <span>{label}</span>
    {metric.reason && <p className="business-flow-reason">{metric.reason}</p>}
  </div>;
}

export function BusinessFlow() {
  const options = { retry: false, refetchInterval: 60_000 } as const;
  const inbox = useQuery({ queryKey: ["inbox-summary"], queryFn: () => get<{ ready: boolean; waiting_total: number | null }>("/api/v1/inbox/summary"), ...options });
  const triage = useQuery({ queryKey: ["triage"], queryFn: () => get<{ items: unknown[] }>("/api/v1/triage?status=pending"), ...options });
  const deals = useQuery({ queryKey: ["deals-summary"], queryFn: () => get<StageCounts>("/api/v1/deals/summary"), ...options });
  const shipping = useQuery({ queryKey: ["fulfil-board"], queryFn: () => get<Shipping>("/api/v1/fulfilment/board"), ...options });
  // summary.by_stage only counts open deals. Aftersales and paid stages can be won.
  const aftersales = useQuery({ queryKey: ["flow-aftersales"], queryFn: () => get<{ total: number | null }>("/api/v1/deals?status=all&stage=aftersales&page=1&page_size=1"), ...options });
  // AI conversation profiles: customers found in chat history, before any deal exists.
  const profiles = useQuery({ queryKey: ["flow-profiles"], queryFn: () => readProfiles({}), ...options });
  const profileStage = (stage: "potential" | "quoted") => unavailable(profiles) ?? (profiles.data == null
    ? { value: null, reason: "AI 还在读聊天记录，读完会自动出现。" }
    : known(profiles.data.counts?.stage?.[stage], "本次统计没有提供这个阶段的数量。"));
  const messages = unavailable(inbox) ?? (inbox.data?.ready === true ? known(inbox.data.waiting_total) : { value: null, reason: "消息尚未就绪，请先检查渠道接入。" });
  const identities = unavailable(triage) ?? (Array.isArray(triage.data?.items) ? known(triage.data.items.length) : known(null));
  const stageCount = (stage: string) => unavailable(deals) ?? known(Array.isArray(deals.data?.by_stage) ? deals.data.by_stage.find((row) => row?.stage === stage)?.count : null, "本次统计没有提供这个阶段的数量。");
  const shippingCount = (stage: string) => unavailable(shipping) ?? (
    Array.isArray(shipping.data?.items) && count(shipping.data.total) && shipping.data.items.length === shipping.data.total && shipping.data.items.every((row) => row && typeof row.stage === "string")
      ? known(shipping.data.items.filter((row) => row.stage === stage).length)
      : known(null, "备货发货记录不完整，请打开列表核对。"));
  const payment = stageCount("awaiting_payment");
  const paid = shippingCount("paid");
  const inspection = shippingCount("inspection");
  const support = unavailable(aftersales) ?? known(aftersales.data?.total);
  const next = messages.value != null && messages.value > 0 ? { text: "先回复客户消息", to: "/inbox?state=waiting&role=customer" }
    : identities.value != null && identities.value > 0 ? { text: "先确认新会话归属", to: "/triage?status=pending" }
    : payment.value != null && payment.value > 0 ? { text: "核对待收款的案子", to: dealPath("awaiting_payment") }
    : (paid.value ?? 0) + (inspection.value ?? 0) > 0 ? { text: "跟进备货与发货", to: "/fulfilment" }
    : { text: "打开待我处理，安排今天的事", to: "/workbench" };

  return <section className="business-flow" aria-labelledby="business-flow-title">
    <header className="business-flow-heading"><h2 className="t-headline" id="business-flow-title">一笔生意，走到哪了</h2><span className="t-foot">点数字处理 · 每 60 秒更新</span></header>
    <p className="business-flow-hint t-foot" id="business-flow-hint">从左到右跟进；手机可左右滑动，也可用键盘选中数字。</p>
    <div className="business-flow-scroll" role="region" aria-label="生意流程，可横向滚动" tabIndex={0} aria-describedby="business-flow-hint">
      <ol className="business-flow-line">
        <li data-flow-step="messages"><h3>新消息<ArrowRight aria-hidden="true" size={16} /></h3><NumberLink metric={messages} to="/inbox?state=waiting&role=customer" label="待回复会话" /></li>
        <li data-flow-step="identity"><h3>待认人<ArrowRight aria-hidden="true" size={16} /></h3><NumberLink metric={identities} to="/triage?status=pending" label="待确认归属" /></li>
        <li data-flow-step="prospects"><h3>潜在客户<ArrowRight aria-hidden="true" size={16} /></h3><div className="business-flow-pair"><NumberLink metric={profileStage("potential")} to="/customers?stage=potential" label="问过价的客户" /><NumberLink metric={stageCount("inquiry")} to={dealPath("inquiry")} label="询盘案子" /></div></li>
        <li data-flow-step="quoted"><h3>已报价<ArrowRight aria-hidden="true" size={16} /></h3><NumberLink metric={profileStage("quoted")} to="/customers?stage=quoted" label="报过价的客户" /><div className="business-flow-links"><Link to={dealPath("negotiating")}>查看谈判阶段</Link></div></li>
        <li data-flow-step="payment"><h3>待收款<ArrowRight aria-hidden="true" size={16} /></h3><NumberLink metric={payment} to={dealPath("awaiting_payment")} label="等客户付款" /></li>
        <li data-flow-step="shipping"><h3>待发货<ArrowRight aria-hidden="true" size={16} /></h3><div className="business-flow-pair"><NumberLink metric={paid} to={dealPath("paid", "all")} label="备货案子" /><NumberLink metric={inspection} to={dealPath("inspection", "all")} label="验货案子" /></div><Link className="business-flow-action" to="/fulfilment">跟进出货节点</Link></li>
        <li data-flow-step="support"><h3>售后</h3><NumberLink metric={support} to={dealPath("aftersales", "all")} label="售后案子" /></li>
      </ol>
    </div>
    <p className="business-flow-scope t-foot">前两步按会话和归属建议计数；“问过价、报过价的客户”是 AI 读聊天记录后的判断，每小时更新；其余按案子当前阶段计数；备货、验货不含已发货，实际出货请核对节点。</p>
    <div className="business-flow-next"><span>下一步</span><Link className="btn primary" to={next.to}>{next.text}<ArrowRight size={16} aria-hidden="true" /></Link><Link className="btn" to="/guide">照着流程学怎么用</Link></div>
  </section>;
}
