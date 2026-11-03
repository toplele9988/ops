import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { QrCode, RefreshCw } from "lucide-react";
import { useEffect, useState } from "react";
import { get, postForm } from "../api";
import { ago } from "../format";
import { Chip, Sheet } from "./bits";
import { useToast } from "./Toast";

interface WaStatus { session: string; status: string; status_label: string; needs_qr: boolean; phone_masked: string | null; engine: string | null; last_message_at: number | null; next_step: string }
const TONE: Record<string, "green" | "orange" | "red" | "plain"> = { WORKING: "green", SCAN_QR_CODE: "orange", STARTING: "orange", FAILED: "red", STOPPED: "red", UNKNOWN: "plain" };

/** WhatsApp session login: shows the live QR code the phone must scan. */
export function WhatsAppLogin() {
  const toast = useToast();
  const client = useQueryClient();
  const [confirm, setConfirm] = useState(false);
  const [qr, setQr] = useState<string | null>(null);
  const [qrNote, setQrNote] = useState("");
  const q = useQuery({
    queryKey: ["wa-status"], queryFn: () => get<WaStatus>("/api/v1/system/whatsapp/status"), retry: false,
    // Poll quickly while the person is mid-login; slowly otherwise.
    refetchInterval: (query) => { const s = query.state.data?.status; return s === "SCAN_QR_CODE" || s === "STARTING" ? 3000 : 20000; },
  });
  const d = q.data;
  const showQr = d?.status === "SCAN_QR_CODE";
  // The QR image is fetched as bytes and shown as a data: URL (the page's CSP allows data: images, and the
  // endpoint rejects query strings, so it cannot be cache-busted with ?t=). WAHA rotates the code, so refetch often.
  useEffect(() => {
    if (!showQr) { setQr(null); setQrNote(""); return; }
    let stopped = false;
    const load = async () => {
      try {
        const r = await fetch("/api/v1/system/whatsapp/qr", { credentials: "same-origin", cache: "no-store" });
        if (!r.ok) { const j = await r.json().catch(() => ({})); throw new Error((j as { error?: string }).error ?? `HTTP ${r.status}`); }
        const bytes = new Uint8Array(await r.arrayBuffer());
        let bin = "";
        for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
        if (!stopped) { setQr(`data:image/png;base64,${btoa(bin)}`); setQrNote(""); }
      } catch (e) { if (!stopped) setQrNote((e as Error).message); }
    };
    load();
    const id = window.setInterval(load, 5000);
    return () => { stopped = true; window.clearInterval(id); };
  }, [showQr]);
  const start = useMutation({
    mutationFn: () => postForm<{ accepted?: boolean }>("/api/v1/system/whatsapp/start", { confirm: "1" }),
    onSuccess: () => { setConfirm(false); toast("已发出登录请求，稍等几十秒，二维码会出现在下面"); client.invalidateQueries({ queryKey: ["wa-status"] }); },
    onError: (e: Error) => { setConfirm(false); toast(e.message, "error"); client.invalidateQueries({ queryKey: ["wa-status"] }); },
  });
  const canStart = d && (d.status === "FAILED" || d.status === "STOPPED");
  return (
    <div className="block">
      <h2 className="group-title"><QrCode size={15} aria-hidden="true" style={{ verticalAlign: -2, marginRight: 4 }} />WhatsApp 登录{d && <Chip tone={TONE[d.status] ?? "plain"}>{d.status_label}</Chip>}</h2>
      <div className="group">
        {q.isLoading && <div className="row"><div className="row-main t-sub">正在读取登录状态…</div></div>}
        {q.isError && <div className="row"><div className="row-main"><div className="row-title" style={{ color: "var(--orange)" }}>没能读到 WhatsApp 的登录状态</div><div className="row-sub" style={{ whiteSpace: "normal" }}>{(q.error as Error).message}</div></div></div>}
        {d && (
          <div className="row" style={{ alignItems: "flex-start" }}>
            <div className="row-main" style={{ whiteSpace: "normal" }}>
              <div className="row-title" style={{ whiteSpace: "normal" }}>{d.next_step}</div>
              <div className="row-sub num">{d.phone_masked ? `号码 ${d.phone_masked} · ` : ""}{d.last_message_at ? `最近收到消息 ${ago(d.last_message_at)}` : "还没有收到过消息"}</div>
              {showQr && (
                <div className="qr-box">
                  {qr
                    ? <img src={qr} width={264} height={264} alt="WhatsApp 登录二维码" />
                    : <div className="t-sub" style={{ padding: 24, width: 264 }}>{qrNote ? `二维码还没取到：${qrNote}` : "正在取二维码…"}</div>}
                  <ol className="plain-list" style={{ marginTop: 0 }}>
                    <li>手机打开 WhatsApp（要用登录这个业务号的那台手机）。</li>
                    <li>安卓：右上角 ⋮ →「已关联的设备」→「关联设备」；iPhone：设置 →「已关联的设备」→「关联设备」。</li>
                    <li>对准左边的二维码扫描。二维码每隔几秒会自动换新，扫不上就等它刷新一次。</li>
                    <li>扫成功后，这里会变成“已登录”。</li>
                  </ol>
                </div>
              )}
            </div>
            <div style={{ display: "flex", gap: 6, flex: "none" }}>
              <button type="button" className="btn sm" onClick={() => client.invalidateQueries({ queryKey: ["wa-status"] })}><RefreshCw size={14} aria-hidden="true" />刷新状态</button>
              {canStart && <button type="button" className="btn sm primary" disabled={start.isPending} onClick={() => setConfirm(true)}>登录</button>}
            </div>
          </div>
        )}
      </div>
      <Sheet open={confirm} title="重新登录 WhatsApp？" onClose={() => setConfirm(false)}>
        <p>系统会重启 WhatsApp 的网页会话，然后出现二维码。这一步不会给任何人发消息，也不会改动聊天记录。</p>
        <p className="t-foot">重启需要几十秒，请保持这个页面打开，二维码出现后用手机扫描。</p>
        <div className="sheet-actions"><button type="button" className="btn" onClick={() => setConfirm(false)}>先不要</button><button type="button" className="btn primary" disabled={start.isPending} onClick={() => start.mutate()}>{start.isPending ? "正在发送…" : "确认登录"}</button></div>
      </Sheet>
    </div>
  );
}
