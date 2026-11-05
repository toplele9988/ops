import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Sparkles } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router";
import { get, postForm } from "../api";
import { ago } from "../format";
import { CHANNEL_NAME } from "../notes";
import { ACTION_TEXT, PARTY_KIND, Triage as T } from "../roles";
import { Chip, Empty, Skeleton } from "../ui/bits";
import { useToast } from "../ui/Toast";

interface ScanResult { items: T[]; scanned: number; remaining: number; message?: string }
interface AdoptionResult { id: number; label: string; state: "waiting" | "running" | "success" | "failed"; error?: string }
const conf = (c: number) => (c >= 0.7 ? { text: "把握较大", tone: "green" as const } : c >= 0.5 ? { text: "有些线索", tone: "orange" as const } : { text: "只能猜", tone: "plain" as const });

function Row({ s, busy, onAccept, onDismiss }: { s: T; busy: boolean; onAccept: () => void; onDismiss: () => void }) {
  const c = conf(s.confidence);
  const name = s.suggested_party_name || s.conversation.counterpart_name;
  return (
    <div id={`triage-${s.id}`} className="row" tabIndex={-1} style={{ alignItems: "flex-start" }}>
      <div className="row-main" style={{ whiteSpace: "normal" }}>
        <div className="row-title" style={{ whiteSpace: "normal" }}>{s.conversation.counterpart_name} <Chip tone="plain">{CHANNEL_NAME[s.conversation.channel] ?? s.conversation.channel}</Chip> <Chip tone={c.tone}>{c.text}</Chip></div>
        <div className="row-sub" style={{ whiteSpace: "normal" }}>{s.conversation.preview}</div>
        <div className="row-sub" style={{ whiteSpace: "normal" }}>系统建议：<b>{ACTION_TEXT[s.action]}</b>{s.suggested_party_kind ? `，${s.action === "identity" ? `记为${PARTY_KIND[s.suggested_party_kind] ?? ""}「${name}」` : `对方是${PARTY_KIND[s.suggested_party_kind] ?? ""}`}` : ""}。{s.reason}　<span className="t-cap">{ago(s.conversation.last_at)}</span></div>
      </div>
      <div style={{ display: "flex", gap: 6, flex: "none", flexWrap: "wrap", justifyContent: "flex-end" }}>
        <button type="button" className="btn sm primary" disabled={busy} onClick={onAccept}>{s.action === "identity" ? "确认" : s.action === "link" ? "加入案子" : "新建案子"}</button>
        <button type="button" className="btn sm" disabled={busy} onClick={onDismiss}>不用管</button>
      </div>
    </div>
  );
}

export function TriagePage() {
  const [params] = useSearchParams();
  const toast = useToast();
  const client = useQueryClient();
  const [busyId, setBusyId] = useState<number | null>(null);
  const q = useQuery({ queryKey: ["triage"], queryFn: () => get<{ items: T[] }>("/api/v1/triage?status=pending"), retry: false });
  const refresh = () => { client.invalidateQueries({ queryKey: ["triage"] }); client.invalidateQueries({ queryKey: ["deals"] }); client.invalidateQueries({ queryKey: ["deals-summary"] }); client.invalidateQueries({ queryKey: ["brief"] }); };
  const scan = useMutation({
    mutationFn: () => postForm<ScanResult>("/api/v1/triage/scan", {}),
    onSuccess: (r) => { refresh(); toast(r.message ?? `扫描了 ${r.scanned} 个会话${r.remaining ? `，还有 ${r.remaining} 个下次再看` : ""}`); },
    onError: (e: Error) => toast(e.message, "error"),
  });
  const decide = useMutation({
    mutationFn: ({ id, path }: { id: number; path: "accept" | "dismiss" }) => postForm<{ deal_id?: number | null }>(`/api/v1/triage/${id}/${path}`, {}),
    onMutate: ({ id }) => setBusyId(id),
    onSettled: () => setBusyId(null),
    onSuccess: (r, v) => { refresh(); toast(v.path === "dismiss" ? "已忽略，不会再提醒这个会话" : r.deal_id ? "已建好案子，在「案子」里能看到" : "已记下"); },
    onError: (e: Error) => toast(e.message, "error"),
  });
  const [bulk, setBulk] = useState(false);
  const [adoptions, setAdoptions] = useState<AdoptionResult[]>([]);
  const items = q.data?.items ?? [];
  const target = items.find(s => (!params.get("suggestion") || String(s.id) === params.get("suggestion")) && (!params.get("conversation") || s.conversation_key === params.get("conversation")));
  useEffect(() => { if (!(params.has("suggestion") || params.has("conversation")) || !target) return; const el = document.getElementById(`triage-${target.id}`); el?.scrollIntoView({ block: "center" }); el?.focus({ preventScroll: true }); el?.setAttribute("data-queue-target", "true"); return () => el?.removeAttribute("data-queue-target"); }, [target?.id, params]);
  const idents = items.filter((s) => s.action === "identity");
  const others = items.filter((s) => s.action !== "identity");
  const dossiers = items.filter((s) => s.status === "pending" && s.action === "identity" && s.confidence >= 0.9 && s.reason.startsWith("档案："));
  const busy = bulk || busyId !== null || scan.isPending;
  const acceptDossiers = async () => {
    if (busy || !dossiers.length) return;
    const targets = dossiers.map((s) => ({ id: s.id, label: `${s.conversation.counterpart_name} → ${s.suggested_party_name || s.conversation.counterpart_name}` }));
    if (!window.confirm(`将采用以下 ${targets.length} 条档案匹配，绑定群与往来单位；单位不存在时会新建：\n\n${targets.map((s) => s.label).join("\n")}\n\n确认采用？`)) return;
    setBulk(true);
    setAdoptions(targets.map((s) => ({ ...s, state: "waiting" })));
    let ok = 0;
    const report = (id: number, state: AdoptionResult["state"], error?: string) => setAdoptions((rows) => rows.map((row) => row.id === id ? { ...row, state, error } : row));
    for (const s of targets) {
      report(s.id, "running");
      try {
        await postForm(`/api/v1/triage/${s.id}/accept`, {});
        ok += 1;
        report(s.id, "success");
        client.setQueryData<{ items: T[] }>(["triage"], (data) => data ? { ...data, items: data.items.filter((item) => item.id !== s.id) } : data);
      } catch (error) {
        report(s.id, "failed", error instanceof Error ? error.message : "请求没有成功，请稍后再试");
      }
    }
    setBulk(false); refresh(); toast(`已采用 ${ok} 条档案匹配${ok < targets.length ? `，${targets.length - ok} 条失败，请查看原因` : ""}`);
  };
  return (
    <>
      <div className="page-head">
        <div><h1 className="t-large">待整理的会话</h1><p>还没归到任何案子或往来单位的新会话。分拣员先替你看了一遍并给出建议，你点“确认”才会生效；它不会替你回复任何人。</p><p>档案匹配 {dossiers.length} 条</p></div>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <button type="button" className="btn" disabled={busy || !dossiers.length} onClick={acceptDossiers}>{bulk ? "正在采用档案匹配…" : "全部采用档案匹配"}</button>
          <button type="button" className="btn primary" disabled={busy} onClick={() => scan.mutate()}><Sparkles size={16} aria-hidden="true" />{scan.isPending ? "分拣员在看…" : "让分拣员扫描一遍"}</button>
        </div>
      </div>
      {adoptions.length > 0 && <div className="block" aria-live="polite" aria-label="档案匹配采用结果">
        <h2 className="group-title">档案匹配采用结果</h2>
        <ul>{adoptions.map((s) => <li key={s.id}>{s.label}：{s.state === "success" ? "已采用" : s.state === "failed" ? `失败：${s.error}` : s.state === "running" ? "正在采用…" : "等待处理"}</li>)}</ul>
      </div>}
      {(params.has("suggestion") || params.has("conversation")) && q.data && !target && <p role="status">这条待办已处理或暂不在当前列表，请核对会话后刷新。</p>}
      {q.isLoading && <Skeleton lines={5} />}
      {q.isError && <div className="group"><Empty title="没能读到待整理的会话">{(q.error as Error).message}</Empty></div>}
      {q.data && items.length === 0 && <div className="group"><Empty title="没有待整理的会话" action={<button type="button" className="btn" disabled={busy} onClick={() => scan.mutate()}>扫描一遍</button>}>最近 30 天对方说过话的会话，以及最近 180 天有消息的档案工作群，会在扫描后出现在这里。</Empty></div>}
      {idents.length > 0 && (
        <div className="block">
          <h2 className="group-title">这些像是供应商、货代群，确认一下是谁<span className="count num">{idents.length}</span></h2>
          <div className="group">
            {idents.map((s) => <Row key={s.id} s={s} busy={busy} onAccept={() => decide.mutate({ id: s.id, path: "accept" })} onDismiss={() => decide.mutate({ id: s.id, path: "dismiss" })} />)}
          </div>
        </div>
      )}
      {others.length > 0 && (
        <div className="block">
          <h2 className="group-title">可能是新的生意<span className="count num">{others.length}</span></h2>
          <div className="group">{others.map((s) => <Row key={s.id} s={s} busy={busy} onAccept={() => decide.mutate({ id: s.id, path: "accept" })} onDismiss={() => decide.mutate({ id: s.id, path: "dismiss" })} />)}</div>
        </div>
      )}
      <p className="t-foot">想自己挑，也可以到 <Link to="/inbox">商机沟通</Link> 打开会话，在右边“这笔生意”里处理。</p>
    </>
  );
}
