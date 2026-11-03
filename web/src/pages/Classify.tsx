import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { get, postForm, qs } from "../api";
import { Chip, Empty, Skeleton } from "../ui/bits";
import { useToast } from "../ui/Toast";

interface Item {
  key: string; name: string; channel: string; suggested_role: string; label: string; confidence: number;
  conflict: boolean; reasons: string[]; revision: number; status: string; decision_role: string | null; first_seen: number | null;
}
interface Page { items: Item[]; total: number; day: string }
interface Summary { total: number; pending: number; confirmed: number; ignored: number; unknown_first_seen: number }
const ROLES: Record<string, string> = { supplier: "供应商", customer: "客户", forwarder: "物流", vendor: "配套", internal: "内部", test: "测试", spam: "垃圾" };
const CHANNELS: Record<string, string> = { wechat: "微信", whatsapp: "WhatsApp", alibaba: "阿里国际站", website: "独立站" };
const today = () => new Intl.DateTimeFormat("sv-SE", { timeZone: "Asia/Shanghai" }).format(new Date());
const currentRole = (row: Item) => row.decision_role && ROLES[row.decision_role] ? row.decision_role : row.suggested_role;

export function ClassifyPage() {
  const toast = useToast();
  const client = useQueryClient();
  const [day, setDay] = useState(today);
  const [status, setStatus] = useState("pending");
  const [selected, setSelected] = useState<string[]>([]);
  const [edits, setEdits] = useState<Record<string, { role: string; name: string }>>({});
  const [notes, setNotes] = useState<string[]>([]);
  const started = useRef(false);
  const refresh = () => {
    client.invalidateQueries({ queryKey: ["classify"] });
    for (const key of ["inbox-summary", "inbox-list", "inbox-detail"]) client.invalidateQueries({ queryKey: [key] });
    setSelected([]);
  };
  const list = useQuery({ queryKey: ["classify", "list", day, status], queryFn: () => get<Page>(`/api/v1/classify${qs({ day, status })}`), retry: false });
  const sum = useQuery({ queryKey: ["classify", "summary", day], queryFn: () => get<Summary>(`/api/v1/classify/summary${qs({ day })}`), retry: false });
  const scan = useMutation({
    mutationFn: () => postForm<{ message: string; notes: string[] }>("/api/v1/classify/scan", { confirm: "1" }),
    onSuccess: (r) => { setNotes(r.notes); refresh(); toast(r.message); },
    onError: (e: Error) => toast(e.message, "error"), retry: false,
  });
  useEffect(() => {
    if (!started.current) { started.current = true; scan.mutate(); }
  }, [scan]);
  const decide = useMutation({
    mutationFn: ({ action, rows }: { action: string; rows: Item[] }) => postForm<{ message: string }>("/api/v1/classify/decide", {
      action, items: JSON.stringify(rows.map((row) => ({ key: row.key, revision: row.revision,
        ...(action === "confirm" ? { role: edits[row.key]?.role ?? currentRole(row),
          ...(edits[row.key]?.name !== undefined && edits[row.key].name !== row.name ? { name: edits[row.key].name } : {}) } : {}) }))),
    }),
    onSuccess: (r) => { refresh(); setEdits({}); toast(r.message); },
    onError: (e: Error) => toast(e.message, "error"), retry: false,
  });
  const rows = list.data?.items ?? [];
  const chosen = rows.filter((r) => selected.includes(r.key));
  const busy = scan.isPending || decide.isPending;
  return <>
    <div className="page-head"><div><h1 className="t-large">待分类</h1><p>新群和联系人先给建议，你核对后才写入名录。分类不会开启微信采集，也不会发消息。</p></div>
      <button className="btn" type="button" disabled={busy} onClick={() => scan.mutate()}>{scan.isPending ? "正在扫描本地记录…" : "扫描本地新记录"}</button></div>
    <div className="callout">按北京时间统计当天首次出现的记录，建议按置信度从高到低排列。线索有冲突时请逐条核对。内部、测试、垃圾和忽略会话不计待回复，也不能起草回复。</div>
    {notes.map((note) => <p className="t-foot" key={note}>{note}</p>)}
    {sum.data && <p>{day === "all" ? "已发现" : "当天新增"} {sum.data.total} 条，待确认 {sum.data.pending} 条，已确认 {sum.data.confirmed} 条，已忽略或内部 {sum.data.ignored} 条。{sum.data.unknown_first_seen > 0 && `其中 ${sum.data.unknown_first_seen} 条缺少发现时间，按首次扫描日期计入。`}</p>}
    {sum.isError && <p role="alert">当天汇总读取失败：{sum.error.message}</p>}
    <div style={{ display: "flex", flexWrap: "wrap", gap: 12, alignItems: "center", margin: "16px 0" }}>
      <label>发现日期 <input type="date" value={day === "all" ? "" : day} onChange={(e) => { setDay(e.target.value || today()); setSelected([]); }} /></label>
      <button className="btn sm" type="button" onClick={() => { setDay(day === "all" ? today() : "all"); setSelected([]); }}>{day === "all" ? "回到今天" : "查看所有日期"}</button>
      <label>显示 <select value={status} onChange={(e) => { setStatus(e.target.value); setSelected([]); }}><option value="pending">待确认</option><option value="confirmed">已确认</option><option value="ignored">忽略或内部</option><option value="all">全部</option></select></label>
      <button className="btn sm" type="button" disabled={busy || !rows.length} onClick={() => setSelected(rows.slice(0, 20).map((r) => r.key))}>选前 20 条</button>
      <button className="btn sm" type="button" onClick={() => setSelected([])}>取消选择</button>
      <button className="btn sm" type="button" disabled={busy || !chosen.length || chosen.some((r) => !ROLES[edits[r.key]?.role ?? currentRole(r)])} onClick={() => decide.mutate({ action: "confirm", rows: chosen })}>确认所选（{chosen.length}）</button>
      <button className="btn sm" type="button" disabled={busy || !chosen.length} onClick={() => decide.mutate({ action: "ignore", rows: chosen })}>忽略所选</button>
    </div>
    {list.isLoading && <Skeleton lines={6} />}
    {list.isError && <div className="group"><Empty title="待分类记录读取失败">{list.error.message}；请检查分类库后刷新。</Empty></div>}
    {list.data && !rows.length && <div className="group"><Empty title="这一天没有符合条件的记录">可切换日期或状态，或扫描已经收到的本地记录。</Empty></div>}
    <div className="group">{rows.map((row) => {
      const edit = edits[row.key] ?? { role: currentRole(row), name: row.name };
      return <div className="row" key={row.key} style={{ display: "block", padding: 16 }}>
        <div style={{ display: "flex", gap: 10, alignItems: "flex-start", overflowWrap: "anywhere" }}>
          <input type="checkbox" aria-label={`选择${row.name}`} checked={selected.includes(row.key)} disabled={busy || (!selected.includes(row.key) && selected.length >= 20)} onChange={(e) => setSelected(e.target.checked ? [...selected, row.key] : selected.filter((key) => key !== row.key))} />
          <div style={{ minWidth: 0 }}><b>{row.name}</b> <Chip tone={row.conflict ? "orange" : "plain"}>{row.label} · {Math.round(row.confidence * 100)}%</Chip>
            <p className="t-foot">{CHANNELS[row.channel] ?? "其他渠道"} · {row.status === "pending" ? "待确认" : `已标记：${ROLES[row.decision_role ?? ""] ?? "忽略"}`}</p>
            {row.reasons.map((reason) => <div className="row-sub" key={reason} style={{ whiteSpace: "normal" }}>{reason}</div>)}</div>
        </div>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 10, alignItems: "center", marginTop: 12 }}>
          <label>分类 <select aria-label={`${row.name}的分类`} value={edit.role} disabled={busy} onChange={(e) => setEdits({ ...edits, [row.key]: { ...edit, role: e.target.value } })}><option value="unclassified">请核对后选择</option>{Object.entries(ROLES).map(([key, label]) => <option value={key} key={key}>{label}</option>)}</select></label>
          <label style={{ minWidth: 0 }}>名录名称 <input style={{ maxWidth: "100%", boxSizing: "border-box" }} aria-label={`${row.name}的名录名称`} value={edit.name} maxLength={500} disabled={busy} onChange={(e) => setEdits({ ...edits, [row.key]: { ...edit, name: e.target.value } })} /></label>
          <button className="btn sm" type="button" disabled={busy || !ROLES[edit.role] || !edit.name.trim()} onClick={() => decide.mutate({ action: "confirm", rows: [row] })}>{row.status === "pending" ? "确认分类" : "改正分类"}</button>
          <button className="btn sm" type="button" disabled={busy} onClick={() => decide.mutate({ action: "ignore", rows: [row] })}>忽略</button>
          {row.status !== "pending" && <button className="btn sm" type="button" disabled={busy} onClick={() => decide.mutate({ action: "undo", rows: [row] })}>撤销标记</button>}
        </div>
      </div>;
    })}</div>
    <p className="t-foot" style={{ marginTop: 16 }}>页面打开时自动扫描一次已有本地记录，无定时任务。没有接入的群可能只有名称建议；完整成员、个人微信消息和真实数据仍需核验。撤销标记恢复待确认并解除忽略，已经建立的名录档案会保留；改正分类也会保留先前档案，请在名录核对。</p>
  </>;
}
