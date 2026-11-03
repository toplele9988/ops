import { useEffect, useState } from "react";
import { BlockView, type Block } from "../../ui/Blocks";
import { Sheet } from "../../ui/bits";

// Fetch archived files only after a click. Text is rendered as text, never HTML.
export function InboxBlock({ block }: { block: Block }) {
  const [open, setOpen] = useState(false);
  const [file, setFile] = useState<{ url: string; text: string } | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    if (!open || !block.media) return;
    const controller = new AbortController();
    let objectUrl = "";
    setFile(null); setError("");
    void fetch(`/api/v1/media/${encodeURIComponent(block.media)}`, { credentials: "same-origin", signal: controller.signal }).then(async (response) => {
      if (!response.ok) throw new Error("文件暂时打不开，请稍后重试或到原渠道核对。");
      const blob = await response.blob();
      if (blob.size > 30 * 1024 * 1024) throw new Error("文件较大，请到原渠道下载查看。");
      const text = blob.type.startsWith("text/plain") && blob.size <= 100000 ? await blob.text() : "";
      if (controller.signal.aborted) return;
      objectUrl = URL.createObjectURL(blob); setFile({ url: objectUrl, text });
    }).catch((e: Error) => { if (!controller.signal.aborted) setError(e.message); });
    return () => { controller.abort(); if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [open, block.media]);
  if (block.type !== "file") return <BlockView block={block} />;
  return <div className="media-card">
    <div><b>{block.name || "文件"}</b>{block.media ? <button className="btn sm" onClick={() => setOpen(true)}>打开文件</button> : <p className="t-foot">文件尚未收录，请到原渠道查看。</p>}</div>
    <Sheet open={open} title={block.name || "文件"} onClose={() => setOpen(false)}>
      {!file && !error && <p role="status">正在打开文件…</p>}
      {error && <p role="alert">{error}</p>}
      {file && <>{file.text ? <pre className="inbox-file-text">{file.text}</pre> : <p>文件已备好，下载后用本机打开。</p>}<a className="btn primary" href={file.url} download={block.name || "附件"}>下载文件</a></>}
      <div className="sheet-actions"><button className="btn" onClick={() => setOpen(false)}>关闭文件</button></div>
    </Sheet>
  </div>;
}
