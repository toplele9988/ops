import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { RefreshCw } from "lucide-react";
import { useState } from "react";
import { get, postForm } from "../api";
import { Chip, Disclosure, Skeleton } from "../ui/bits";
import { Segmented } from "../ui/Segmented";
import { useToast } from "../ui/Toast";

interface Check { key: string; title: string; level: "high" | "medium" | "low" | "ok"; count: number; meaning: string; remedy: string; samples?: Record<string, unknown>[]; channel: string; error?: string }
interface Audit { summary: string; attention_count: number; counts: Record<string, number>; generated_at: string; items: Check[]; failed_count: number }

const LEVEL = { high: { text: "紧急", tone: "red" }, medium: { text: "要处理", tone: "orange" }, low: { text: "留意", tone: "plain" }, ok: { text: "正常", tone: "green" } } as const;
const CHANNELS = [{ value: "", label: "全部" }, { value: "wechat", label: "微信" }, { value: "alibaba", label: "阿里国际站" }, { value: "whatsapp", label: "WhatsApp" }, { value: "archive", label: "产品与报价" }];
const NAME: Record<string, string> = { wechat: "微信", alibaba: "阿里国际站", whatsapp: "WhatsApp", archive: "产品与报价" };
const KEYS: Record<string, string> = { account: "账号", last_message_at: "最近消息", last_heartbeat_at: "最近心跳", age_seconds: "已过秒数", script_version: "脚本版本", name: "名称" };
const show = (k: string, v: unknown) => {
  if (typeof v === "number" && /_at$/.test(k)) return new Date(v * 1000).toLocaleString("zh-CN", { hour12: false });
  if (typeof v === "number" && k === "age_seconds") return `${Math.round(v / 3600)} 小时`;
  return String(v);
};

export function AuditPage() {
  const toast = useToast();
  const client = useQueryClient();
  const [channel, setChannel] = useState("");
  const [showOk, setShowOk] = useState(false);
  const q = useQuery({ queryKey: ["audit"], queryFn: () => get<Audit>("/api/v1/audit"), retry: false });
  const run = useMutation({
    mutationFn: () => postForm("/api/v1/audit/run", {}),
    onSuccess: () => { toast("已重新体检"); client.invalidateQueries({ queryKey: ["audit"] }); },
    onError: (e: Error) => toast(e.message, "error"),
  });
  const items = (q.data?.items ?? []).filter((c) => (!channel || c.channel === channel) && (showOk || c.level !== "ok"));
  const okCount = (q.data?.items ?? []).filter((c) => c.level === "ok" && (!channel || c.channel === channel)).length;
  return (
    <>
      <div className="page-head">
        <div><h1 className="t-large">数据体检</h1><p>系统的数据哪里有缺口、为什么、怎么补。看到“没消息”，先看这里，往往是采集断了，不是没人联系你。</p></div>
        <button type="button" className="btn" disabled={run.isPending} onClick={() => run.mutate()}><RefreshCw size={16} aria-hidden="true" />{run.isPending ? "正在体检…" : "重新体检"}</button>
      </div>
      {q.isLoading && <Skeleton lines={5} />}
      {q.isError && <div className="callout" role="note"><b>没能读到体检结果。</b>{(q.error as Error).message}</div>}
      {q.data && (
        <>
          <div className="kpis" style={{ marginBottom: 16 }}>
            {(["high", "medium", "low"] as const).map((k) => <div className="kpi" key={k}><span className="t-foot">{LEVEL[k].text}</span><span className="kpi-n num" style={{ color: k === "high" ? "var(--red)" : k === "medium" ? "var(--orange)" : undefined }}>{q.data.counts[k] ?? 0}</span><span className="t-cap">项</span></div>)}
            <div className="kpi"><span className="t-foot">正常</span><span className="kpi-n num" style={{ color: "var(--green)" }}>{q.data.counts.ok ?? 0}</span><span className="t-cap">项</span></div>
          </div>
          {q.data.failed_count > 0 && <p className="t-foot">有 {q.data.failed_count} 项没能读取，已在下面单独标出，不影响其他项。</p>}
          <div style={{ margin: "0 0 12px" }}><Segmented value={channel} onChange={setChannel} options={CHANNELS} label="渠道" /></div>
          <div className="group">
            {items.length === 0 && <div className="row"><div className="row-main t-sub">这个渠道没有需要处理的问题。</div></div>}
            {items.map((c) => (
              <div className="row" key={c.key} style={{ alignItems: "flex-start" }}>
                <div className="row-main" style={{ whiteSpace: "normal" }}>
                  <div className="row-title" style={{ whiteSpace: "normal" }}>{c.title} <Chip tone={LEVEL[c.level].tone}>{LEVEL[c.level].text}</Chip> <Chip tone="plain">{NAME[c.channel] ?? c.channel}</Chip>{c.level !== "ok" && <span className="num">　{c.count}</span>}</div>
                  <div className="row-sub" style={{ whiteSpace: "normal" }}>{c.error ? `读取失败：${c.error}` : c.meaning}</div>
                  {c.level !== "ok" && c.remedy && <div className="row-sub" style={{ whiteSpace: "normal", color: "var(--label)" }}><b>怎么补：</b>{c.remedy}</div>}
                  {c.samples && c.samples.length > 0 && c.level !== "ok" && (
                    <Disclosure head={<span className="t-cap">看几个例子</span>}>
                      <ul className="plain-list">{c.samples.slice(0, 5).map((s, i) => <li key={i} className="t-cap">{Object.entries(s).map(([k, v]) => `${KEYS[k] ?? k}：${show(k, v)}`).join("　")}</li>)}</ul>
                    </Disclosure>
                  )}
                </div>
              </div>
            ))}
          </div>
          {okCount > 0 && <p style={{ marginTop: 12 }}><button type="button" className="btn sm plain" onClick={() => setShowOk(!showOk)}>{showOk ? "收起正常的项" : `看 ${okCount} 项正常的`}</button></p>}
        </>
      )}
    </>
  );
}
