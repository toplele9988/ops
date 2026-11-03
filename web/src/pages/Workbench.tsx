import { useQuery } from "@tanstack/react-query";
import { ChevronRight, Lightbulb, MessageSquareReply, PackageSearch, TriangleAlert } from "lucide-react";
import { Link } from "react-router";
import { get } from "../api";
import { ago, today } from "../format";
import { CHANNEL_NAME, dedupe } from "../notes";
import { dot } from "./Deals";
import type { DealRow } from "../deals";
import { Chip, Skeleton } from "../ui/bits";
import { AlertList, BriefCard } from "../ui/RolePanels";
import { PageIntro } from "./overview/PageIntro";
import { TodayQueue } from "./overview/TodayQueue";
import { Welcome } from "./overview/Welcome";

interface Conv { key: string; channel: string; channel_label?: string; account: string; buyer_name: string; country: string; n_messages: number; last: { role: string; text: string; sent_at: string }; waiting_hours: number | null; has_draft: boolean }
interface Inbox { ready: boolean; waiting_total: number; new_24h: number; draft_new: number; channels: { channel: string; account: string; conversations: number; waiting_reply: number; messages_24h: number; last_message_at: number }[] }
interface Packing { ready: boolean; generated_at: string; totals: { entries: number; ready: number; estimable: number; conflict: number; multi_model: number }; next_actions: { supplier: string; reason: string; count: number }[] }
interface Archive { products: { candidate: number; confirmed: number }; offers: { valid: number; expiring: number; expired: number; no_expiry: number; suggested: number }; freight: { valid: number; expiring: number; expired: number; no_expiry: number; suggested: number } }
interface Insp { counts: { total: number; relevance: Record<string, number>; status: Record<string, number> } }
interface Channels { notes: string[]; channels?: { channel: string; label: string; state?: string; diagnosis?: string | null }[] }

const waitText = (hours: number | null) => (hours == null ? "" : hours < 1 ? "刚刚" : hours < 48 ? `等了 ${Math.round(hours)} 小时` : `等了 ${Math.round(hours / 24)} 天`);

function Kpi({ to, label, value, hint, tone }: { to: string; label: string; value: number | string; hint: string; tone?: "red" | "orange" }) {
  return (
    <Link to={to} className="kpi">
      <span className="t-foot">{label}</span>
      <span className="kpi-n num" style={{ color: tone === "red" ? "var(--red)" : tone === "orange" ? "var(--orange)" : undefined }}>{value}</span>
      <span className="t-cap">{hint}</span>
    </Link>
  );
}

export function Workbench({ embedded = false }: { embedded?: boolean }) {
  const inbox = useQuery({ queryKey: ["inbox-summary"], queryFn: () => get<Inbox>("/api/v1/inbox/summary"), retry: false });
  const waiting = useQuery({ queryKey: ["inbox-waiting"], queryFn: () => get<{ items: Conv[] }>("/api/v1/inbox/conversations?state=waiting&page_size=8"), retry: false });
  const packing = useQuery({ queryKey: ["packing-summary"], queryFn: () => get<Packing>("/api/v1/packing/summary"), retry: false });
  const archive = useQuery({ queryKey: ["archive-summary"], queryFn: () => get<Archive>("/api/v1/archive/summary"), retry: false });
  const deals = useQuery({ queryKey: ["deals", "attention"], queryFn: () => get<{ items: DealRow[]; total: number }>("/api/v1/deals?status=open&waiting=us&page_size=6"), retry: false });
  const dealSummary = useQuery({ queryKey: ["deals-summary"], queryFn: () => get<{ open: number; waiting_on_us: number; overdue: number; won: number }>("/api/v1/deals/summary"), retry: false });
  const won = useQuery({ queryKey: ["deals", "won-recent"], queryFn: () => get<{ items: DealRow[]; total: number }>("/api/v1/deals?status=won&page_size=5"), retry: false });
  const triage = useQuery({ queryKey: ["triage"], queryFn: () => get<{ items: unknown[] }>("/api/v1/triage?status=pending"), retry: false });
  const audit = useQuery({ queryKey: ["audit"], queryFn: () => get<{ summary: string; attention_count: number; counts: Record<string, number> }>("/api/v1/audit"), retry: false });
  const insp = useQuery({ queryKey: ["inspiration-counts"], queryFn: () => get<Insp>("/api/v1/inspiration?page_size=1"), enabled: !embedded, retry: false });
  const channels = useQuery({ queryKey: ["system-channels"], queryFn: () => get<Channels>("/api/v1/system/channels"), enabled: !embedded, retry: false });

  const i = inbox.data?.ready ? inbox.data : undefined;
  const p = packing.data?.ready ? packing.data : undefined;
  const c = insp.data?.counts;
  const warn = dedupe([...(channels.data?.channels?.filter((c) => c.diagnosis).map((c) => c.diagnosis as string) ?? []), ...(channels.data?.notes ?? [])]);

  return (
    <>
      {!embedded && <div className="page-head">
        <div>
          <h1 className="t-large">工作台</h1>
          <p>{today()}。先处理上面的，再看下面的。</p>
        </div>
      </div>}

      {!embedded && <><PageIntro page="workbench" /><Welcome /><TodayQueue /></>}

      <div className="kpis">
        <Kpi to="/deals?waiting=us" label="要你跟进的案子" value={dealSummary.data ? dealSummary.data.waiting_on_us : "—"} hint={dealSummary.data ? `进行中共 ${dealSummary.data.open} 个` : "一笔笔生意"} tone={dealSummary.data && dealSummary.data.waiting_on_us > 0 ? "red" : undefined} />
        <Kpi to="/inbox?state=waiting" label="等你回复的买家" value={i ? i.waiting_total : "—"} hint="买家说完话，你还没回" tone={i && i.waiting_total > 0 ? "red" : undefined} />
        <Kpi to="/inbox" label="AI 起草待确认" value={i ? i.draft_new : "—"} hint="AI 写好的回复，等你看一眼" />
        <Kpi to="/packing" label="要催的供应商" value={p ? p.next_actions.length : "—"} hint="缺外箱尺寸、毛重的供应商" tone={p && p.next_actions.length > 0 ? "orange" : undefined} />
        <Kpi to="/triage" label="待整理的会话" value={triage.data ? triage.data.items.length : "—"} hint="AI 已看过，等你点确认" tone={triage.data && triage.data.items.length > 0 ? "orange" : undefined} />
      </div>

      <div className={embedded ? "daily-operations" : "cols"}>
        <div>
          {audit.data && audit.data.attention_count > 0 && (
            <Link to="/audit" className="callout" style={{ display: "block", marginBottom: 16, color: "inherit", textDecoration: "none" }}>
              <b>数据体检：{audit.data.summary}</b>（紧急 {audit.data.counts.high ?? 0}、要处理 {audit.data.counts.medium ?? 0}）。有的渠道可能漏收消息，点开看怎么补。
            </Link>
          )}
          <BriefCard />
          <AlertList framed />
          {won.data && won.data.total > 0 && (
            <div className="block">
              <h2 className="group-title">成交结果<span className="count num">共 {won.data.total} 个</span></h2>
              <div className="group">
                {won.data.items.map((d) => (
                  <Link key={d.id} className="row" to={`/deals/${d.id}`}>
                    <div className="row-main"><div className="row-title">{d.title}</div><div className="row-sub num">{d.customer?.name ?? "—"}{d.value ? ` · ${d.currency} ${d.value}` : ""}</div></div>
                    <Chip tone="green">已成交</Chip>
                    <ChevronRight size={16} className="chev" aria-hidden="true" />
                  </Link>
                ))}
              </div>
            </div>
          )}

          <div className="block">
            <h2 className="group-title">要你跟进的案子<span className="count num">{deals.data?.total ?? ""}</span></h2>
            <div className="group">
              {deals.data?.items.map((d) => (
                <Link key={d.id} className="row" to={`/deals/${d.id}`} style={{ alignItems: "flex-start" }}>
                  <div className="row-main">
                    <div className="row-title">{d.title}<span className="t-cap">　{d.stage_label}</span></div>
                    <div className="row-sub">{d.customer?.name ?? "还没有客户"}</div>
                    <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginTop: 6 }}>{d.participants.map(dot)}</div>
                  </div>
                  <ChevronRight size={16} className="chev" aria-hidden="true" />
                </Link>
              ))}
              {deals.data && deals.data.items.length === 0 && <div className="row"><div className="row-main t-sub">没有需要你跟进的案子。从客户会话里「新建案子」，就能在这里看到每笔生意卡在谁手上。</div></div>}
              {deals.isLoading && <div className="row"><div className="row-main t-sub">读取中…</div></div>}
            </div>
          </div>

          <div className="block">
            <h2 className="group-title">等你回复的买家<span className="count num">{i?.waiting_total ?? ""}</span></h2>
            {waiting.isLoading ? <Skeleton lines={4} /> : (
              <div className="group">
                {waiting.data?.items.map((conv) => (
                  <Link key={conv.key} className="row" to={`/inbox?conversation=${encodeURIComponent(conv.key)}&c=${encodeURIComponent(conv.key)}`}>
                    <MessageSquareReply size={20} aria-hidden="true" style={{ color: "var(--tint)" }} />
                    <div className="row-main">
                      <div className="row-title">{conv.buyer_name || "未命名买家"}{conv.channel_label && <span className="t-cap">　{conv.channel_label}</span>}{conv.country && <span className="t-cap">　{conv.country}</span>}</div>
                      <div className="row-sub" style={{ whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{conv.last.text}</div>
                    </div>
                    <div style={{ display: "grid", gap: 4, justifyItems: "end" }}>
                      <Chip tone={(conv.waiting_hours ?? 0) > 24 ? "red" : "orange"}>{waitText(conv.waiting_hours)}</Chip>
                      {conv.has_draft && <Chip tone="blue">已有 AI 草稿</Chip>}
                    </div>
                    <ChevronRight size={16} className="chev" aria-hidden="true" />
                  </Link>
                ))}
                {waiting.isError && <div className="row"><div className="row-main t-sub">暂时读不到买家会话。</div></div>}
                {waiting.data && waiting.data.items.length === 0 && <div className="row"><div className="row-main t-sub">没有等你回复的买家。</div></div>}
              </div>
            )}
          </div>

          <div className="block">
            <h2 className="group-title">供应商资料待补<span className="count num">{p?.next_actions.length ?? ""}</span></h2>
            <div className="group">
              {archive.data && archive.data.products.candidate > 0 && (
                <Link className="row" to="/products?status=candidate">
                  <PackageSearch size={20} aria-hidden="true" style={{ color: "var(--tint)" }} />
                  <div className="row-main">
                    <div className="row-title">{archive.data.products.candidate} 个产品候选等你确认{archive.data.offers.suggested ? `，${archive.data.offers.suggested} 条报价等你核对` : ""}</div>
                    <div className="row-sub">核对外箱尺寸、毛重和报价有效期后点「确认入库」，之后才能用来算价格。</div>
                  </div>
                  <ChevronRight size={16} className="chev" aria-hidden="true" />
                </Link>
              )}
              {archive.data && (archive.data.offers.expired + archive.data.freight.expired + archive.data.offers.expiring + archive.data.freight.expiring) > 0 && (
                <Link className="row" to="/freight?state=expiring">
                  <TriangleAlert size={20} aria-hidden="true" style={{ color: "var(--orange)" }} />
                  <div className="row-main">
                    <div className="row-title">{archive.data.offers.expiring + archive.data.freight.expiring} 条报价快到期，{archive.data.offers.expired + archive.data.freight.expired} 条已过期</div>
                    <div className="row-sub">过期的价格不能用来报价，请向供应商或货代要新价。</div>
                  </div>
                  <ChevronRight size={16} className="chev" aria-hidden="true" />
                </Link>
              )}
              {p?.next_actions.map((a) => (
                <Link key={a.supplier} className="row" to={`/packing?supplier=${encodeURIComponent(a.supplier)}`}>
                  <PackageSearch size={20} aria-hidden="true" style={{ color: "var(--tint)" }} />
                  <div className="row-main">
                    <div className="row-title">{a.supplier}</div>
                    <div className="row-sub">{a.reason}。话术已写好，点开复制。</div>
                  </div>
                  <ChevronRight size={16} className="chev" aria-hidden="true" />
                </Link>
              ))}
              {p && p.totals.conflict > 0 && (
                <Link className="row" to="/packing?status=conflict">
                  <TriangleAlert size={20} aria-hidden="true" style={{ color: "var(--orange)" }} />
                  <div className="row-main">
                    <div className="row-title">{p.totals.conflict} 条资料有冲突</div>
                    <div className="row-sub">其中 {p.totals.multi_model} 条疑似一条记录混进了多个型号，要拆开核对，否则运费会算错。</div>
                  </div>
                  <ChevronRight size={16} className="chev" aria-hidden="true" />
                </Link>
              )}
              {packing.isLoading && <div className="row"><div className="row-main t-sub">读取中…</div></div>}
            </div>
          </div>
        </div>

        {!embedded && <div>
          {warn.length > 0 && (
            <div className="block">
              <h2 className="group-title">需要注意</h2>
              <div className="group">
                {warn.map((text) => (
                  <Link key={text} className="row" to="/system/channels">
                    <TriangleAlert size={20} aria-hidden="true" style={{ color: "var(--orange)" }} />
                    <div className="row-main"><div className="row-sub" style={{ color: "var(--label)" }}>{text}</div></div>
                  </Link>
                ))}
              </div>
            </div>
          )}

          <div className="block">
            <h2 className="group-title">渠道</h2>
            <div className="group">
              {i?.channels.map((ch) => (
                <Link key={`${ch.channel}-${ch.account}`} className="row" to="/system/channels">
                  <div className="row-main">
                    <div className="row-title">{CHANNEL_NAME[ch.channel] ?? ch.channel}{ch.account && ch.account !== CHANNEL_NAME[ch.channel] ? `　${ch.account}` : ""}</div>
                    <div className="row-sub num">{ch.conversations} 个会话 · 24 小时 {ch.messages_24h} 条 · 最近 {ago(ch.last_message_at)}</div>
                  </div>
                </Link>
              ))}
              {inbox.isLoading && <div className="row"><div className="row-main t-sub">读取中…</div></div>}
              {inbox.isError && <div className="row"><div className="row-main t-sub">暂时读不到渠道数据。</div></div>}
            </div>
          </div>

          <div className="block">
            <h2 className="group-title">AI 在帮你做的事</h2>
            <div className="group">
              <Link className="row" to="/inspiration?status=new">
                <Lightbulb size={20} aria-hidden="true" style={{ color: "var(--purple)" }} />
                <div className="row-main">
                  <div className="row-title">{c ? `${c.status.new} 张视频号卡片等你评估` : "整理视频号卡片"}</div>
                  <div className="row-sub">{c ? `已自动分好类：${c.relevance.ai_dev} 张 AI 开发，${c.relevance.industry} 张同行与产品。` : "读取中…"}</div>
                </div>
                <ChevronRight size={16} className="chev" aria-hidden="true" />
              </Link>
              <div className="row">
                <div className="row-main">
                  <div className="row-title">{p ? `抽取并核对了 ${p.totals.entries} 条供应商规格` : "抽取供应商规格"}</div>
                  <div className="row-sub num">{p ? `${p.totals.ready} 条资料齐全，${p.totals.estimable} 条可估算。更新于 ${ago(p.generated_at)}。` : "读取中…"}</div>
                </div>
              </div>
            </div>
          </div>
        </div>}
      </div>
    </>
  );
}
