import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Paperclip } from "lucide-react";
import { useRef, useState } from "react";
import { postForm } from "../api";
import { uuid } from "../lib_uuid";
import { Sheet } from "./bits";
import { useToast } from "./Toast";

interface Candidate { id: number; filename: string; mimetype: string; size: number }
interface SendResult {
  message: { state: "sending" | "sent" | "failed" | "unknown"; state_text: string; error_text?: string | null };
  error_text?: string;
  needs_new_recipient_confirmation?: boolean;
}

const MAX_SIZE = 20 * 1024 * 1024;
const fileSize = (bytes: number) => bytes < 1024 ? `${bytes} 字节` : bytes < 1024 * 1024
  ? `${(bytes / 1024).toFixed(1)} KB` : `${(bytes / (1024 * 1024)).toFixed(1)} MB`;

function base64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error("文件读取失败，请重新选择文件"));
    reader.onload = () => {
      const result = reader.result;
      if (typeof result !== "string" || !result.includes(",")) reject(new Error("文件读取失败，请重新选择文件"));
      else resolve(result.slice(result.indexOf(",") + 1));
    };
    reader.readAsDataURL(file);
  });
}

export function WaAttachSend({ convKey, recipient, enabled }: { convKey: string; recipient: string; enabled: boolean }) {
  const toast = useToast();
  const client = useQueryClient();
  const [open, setOpen] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [candidate, setCandidate] = useState<Candidate | null>(null);
  const [needsRecipient, setNeedsRecipient] = useState(false);
  const [newRecipient, setNewRecipient] = useState(false);
  const [uncertain, setUncertain] = useState(false);
  const candidateRequest = useRef(uuid());
  const sendRequest = useRef(uuid());

  const reset = () => {
    setOpen(false); setFile(null); setCandidate(null); setNeedsRecipient(false); setNewRecipient(false); setUncertain(false);
    candidateRequest.current = uuid(); sendRequest.current = uuid();
  };
  const make = useMutation({
    mutationFn: async () => {
      if (!file || file.size === 0 || file.size > MAX_SIZE) throw new Error("请选择不超过 20 MB 的非空文件");
      return postForm<{ candidate: Candidate }>(`/api/v1/inbox/conversations/${convKey}/attachments`, {
        filename: file.name, mimetype: file.type, data: await base64(file), request_id: candidateRequest.current,
      });
    },
    onSuccess: (result) => setCandidate(result.candidate),
    onError: (error: Error) => toast(error.message, "error"),
  });
  const send = useMutation({
    retry: false,
    mutationFn: () => postForm<SendResult>(`/api/v1/inbox/conversations/${convKey}/attachments/send`, {
      candidate_id: String(candidate!.id), request_id: sendRequest.current, confirm: "1",
      confirm_new_recipient: newRecipient ? "1" : "0",
    }),
    onSuccess: (result) => {
      client.invalidateQueries({ queryKey: ["outbox", convKey] });
      client.invalidateQueries({ queryKey: ["inbox-detail", convKey] });
      if (result.needs_new_recipient_confirmation) {
        sendRequest.current = uuid();
        setNeedsRecipient(true);
        toast("这是第一次给这位联系人发消息，需要你多确认一次", "error");
        return;
      }
      if (result.message.state === "sent") { reset(); toast("附件已发出"); }
      else {
        setUncertain(true);
        toast(result.error_text ?? result.message.error_text ?? result.message.state_text, "error");
      }
    },
    onError: () => {
      setUncertain(true);
      client.invalidateQueries({ queryKey: ["outbox", convKey] });
      toast("发送结果未能确认，请先到 WhatsApp 核对，不要重复发送", "error");
    },
  });

  return (
    <>
      <button type="button" className="btn" disabled={!enabled} onClick={() => setOpen(true)}><Paperclip size={16} aria-hidden="true" />附件…</button>
      <Sheet open={open} title={candidate ? "确认发送附件" : "选择附件"} onClose={() => { if (!make.isPending && !send.isPending) reset(); }}>
        {!candidate ? (
          <>
            <label className="field"><span>选择要发送的文件</span><input type="file" accept="image/jpeg,image/png,image/webp,video/mp4,application/pdf,text/plain,application/zip,application/vnd.openxmlformats-officedocument.wordprocessingml.document,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,application/vnd.openxmlformats-officedocument.presentationml.presentation" onChange={(event) => {
              setFile(event.target.files?.[0] ?? null); candidateRequest.current = uuid();
            }} /></label>
            <p className="t-foot">支持 JPEG、PNG、WebP 图片、MP4 视频、PDF、文本、ZIP 和新版 Office 文件，不超过 20 MB。选择后先生成候选，你确认后才会发送。</p>
            {file && <p className="t-foot">{file.name} · {fileSize(file.size)}</p>}
            <div className="sheet-actions"><button type="button" className="btn" disabled={make.isPending} onClick={reset}>取消</button>
              <button type="button" className="btn primary" disabled={!file || make.isPending} onClick={() => make.mutate()}>{make.isPending ? "正在生成…" : "生成发送候选"}</button></div>
          </>
        ) : (
          <>
            <dl className="kv" style={{ gridTemplateColumns: "64px 1fr", marginTop: 0 }}>
              <dt>发给</dt><dd>{recipient || "对方"}</dd><dt>渠道</dt><dd>WhatsApp</dd>
              <dt>文件</dt><dd>{candidate.filename}</dd><dt>大小</dt><dd>{fileSize(candidate.size)}</dd>
            </dl>
            {needsRecipient && <label style={{ display: "flex", gap: 8, alignItems: "flex-start", marginTop: 12 }}>
              <input type="checkbox" checked={newRecipient} onChange={(event) => setNewRecipient(event.target.checked)} style={{ marginTop: 4 }} />
              <span>我确认：这是第一次通过后台给这位联系人发消息。</span>
            </label>}
            <p className="t-foot" style={{ marginTop: 12 }}>{uncertain ? "请先到 WhatsApp 核对发送记录，不要重复发送。" : "点「确认发送」后会立刻发给对方。结果不确定时系统不会自动重发。"}</p>
            <div className="sheet-actions"><button type="button" className="btn" disabled={send.isPending} onClick={reset}>关闭</button>
              <button type="button" className="btn primary" disabled={!enabled || uncertain || send.isPending || (needsRecipient && !newRecipient)} onClick={() => send.mutate()}>{send.isPending ? "发送中…" : "确认发送"}</button></div>
          </>
        )}
      </Sheet>
    </>
  );
}
