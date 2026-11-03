import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Sparkles } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router";
import { get, postForm } from "../api";
import { ago } from "../format";
import { CHANNEL_NAME } from "../notes";
import { ACTION_TEXT, PARTY_KIND, Triage as T } from "../roles";
import { Chip, Empty, Skeleton } from "../ui/bits";
import { useToast } from "../ui/Toast";

interface ScanResult { items: T[]; scanned: number; remaining: number; message?: string }
const conf = (c: number) => (c >= 0.7 ? { text: "把握较大", tone: "green" as const } : c >= 0.5 ? { text: "有些线索", tone: "orange" as const } : { text: "只能猜", tone: "plain" as const });

function Row({ s, busy, onAccept, onDismiss }: { s: T; busy: boolean; onAccept: () => void; onDismiss: () => void }) {
  const c = conf(s.confidence);
  const name = s.suggested_party_name || s.conversation.counterpart_name;
  return (
    <div className="row" style={{ alignItems: "flex-start" }}>
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
  const items = q.data?.items ?? [];
  const idents = items.filter((s) => s.action === "identity");
  const others = items.filter((s) => s.action !== "identity");
  const acceptAll = async () => {
    setBulk(true);
    let ok = 0;
    for (const s of idents) { try { await postForm(`/api/v1/triage/${s.id}/accept`, {}); ok += 1; } catch { /* keep going, the rest stay in the list */ } }
    setBulk(false); refresh(); toast(`已确认 ${ok} 个${ok < idents.length ? `，${idents.length - ok} 个没成功，请单独处理` : ""}`);
  };
  return (
    <>
      <div className="page-head">
        <div><h1 className="t-large">待整理的会话</h1><p>还没归到任何案子或往来单位的新会话。分拣员先替你看了一遍并给出建议，你点“确认”才会生效；它不会替你回复任何人。</p></div>
        <button type="button" className="btn primary" disabled={scan.isPending} onClick={() => scan.mutate()}><Sparkles size={16} aria-hidden="true" />{scan.isPending ? "分拣员在看…" : "让分拣员扫描一遍"}</button>
      </div>
      {q.isLoading && <Skeleton lines={5} />}
      {q.isError && <div className="group"><Empty title="没能读到待整理的会话">{(q.error as Error).message}</Empty></div>}
      {q.data && items.length === 0 && <div className="group"><Empty title="没有待整理的会话" action={<button type="button" className="btn" disabled={scan.isPending} onClick={() => scan.mutate()}>扫描一遍</button>}>最近 30 天里，对方说了话、又还没归到案子的会话，会出现在这里。</Empty></div>}
      {idents.length > 0 && (
        <div className="block">
          <h2 className="group-title">这些像是供应商、货代群，确认一下是谁<span className="count num">{idents.length}</span></h2>
          <div className="group">
            {idents.map((s) => <Row key={s.id} s={s} busy={busyId === s.id || bulk} onAccept={() => decide.mutate({ id: s.id, path: "accept" })} onDismiss={() => decide.mutate({ id: s.id, path: "dismiss" })} />)}
            {idents.length > 1 && <div className="row"><button type="button" className="btn" disabled={bulk} onClick={acceptAll}>{bulk ? "正在确认…" : `全部按建议确认（${idents.length} 个）`}</button></div>}
          </div>
        </div>
      )}
      {others.length > 0 && (
        <div className="block">
          <h2 className="group-title">可能是新的生意<span className="count num">{others.length}</span></h2>
          <div className="group">{others.map((s) => <Row key={s.id} s={s} busy={busyId === s.id} onAccept={() => decide.mutate({ id: s.id, path: "accept" })} onDismiss={() => decide.mutate({ id: s.id, path: "dismiss" })} />)}</div>
        </div>
      )}
      <p className="t-foot">想自己挑，也可以到 <Link to="/inbox">商机沟通</Link> 打开会话，在右边“这笔生意”里处理。</p>
    </>
  );
}
