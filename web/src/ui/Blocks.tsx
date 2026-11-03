import { ExternalLink, FileText, Mic, Play, StickyNote } from "lucide-react";
import { useState, type ReactNode } from "react";
import { Disclosure, Sheet } from "./bits";

export interface Block {
  type: string;
  text?: string; title?: string; desc?: string; url?: string | null; source?: string; sender?: string;
  media?: string; name?: string; size_bytes?: number | null; duration_s?: number | null; transcript?: string; playable?: boolean;
  count?: number; items?: Block[]; kind?: string; subtitle?: string; time?: string; thumbnail?: string;
}

const MEDIA = (token: string) => `/api/v1/media/${token}`;
const size = (n?: number | null) => (n == null ? "" : n > 1e6 ? `${(n / 1e6).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1e3))} KB`);
const secs = (n?: number | null) => (n == null ? "" : n >= 60 ? `${Math.floor(n / 60)}分${Math.round(n % 60)}秒` : `${Math.round(n)}秒`);

function Image({ block }: { block: Block }) {
  const [open, setOpen] = useState(false);
  const [failed, setFailed] = useState(false);
  if (!block.media || failed) return <span className="media-miss">[图片] 文件不在本地，暂时无法显示</span>;
  return (
    <>
      <button type="button" className="img-btn" aria-label="放大图片" onClick={() => setOpen(true)}>
        <img src={MEDIA(block.media)} alt="聊天图片" loading="lazy" onError={() => setFailed(true)} />
      </button>
      <Sheet open={open} title="图片" onClose={() => setOpen(false)}>
        <img src={MEDIA(block.media)} alt="聊天图片（大图）" style={{ maxWidth: "100%", borderRadius: 10 }} />
        <div className="sheet-actions"><a className="btn" href={MEDIA(block.media)} download>下载</a><button type="button" className="btn primary" onClick={() => setOpen(false)}>关闭</button></div>
      </Sheet>
    </>
  );
}

function Plain({ icon, children }: { icon: ReactNode; children: ReactNode }) {
  return <div className="media-card">{icon}<div style={{ minWidth: 0 }}>{children}</div></div>;
}

function Video({ block }: { block: Block }) {
  const [playing, setPlaying] = useState(false);
  const [failed, setFailed] = useState(false);
  const [duration, setDuration] = useState(block.duration_s);
  if (!block.media) return <span className="media-miss">[视频] 文件不在本地，暂时无法播放</span>;
  return (
    <div>
      {playing && !failed ? <video className="video" controls autoPlay preload="metadata" src={MEDIA(block.media)} aria-label="聊天视频"
        onLoadedMetadata={(event) => { if (Number.isFinite(event.currentTarget.duration)) setDuration(event.currentTarget.duration); }}
        onError={() => setFailed(true)} /> : <Plain icon={<Play size={18} aria-hidden="true" />}>
        <div>视频</div>
        {failed ? <div className="t-cap">视频暂时无法播放，请下载后用播放器打开</div> : <button type="button" className="btn sm" onClick={() => setPlaying(true)}>播放视频</button>}
      </Plain>}
      <div className="t-cap num">{secs(duration)} {size(block.size_bytes)} · <a href={MEDIA(block.media)} download>下载视频</a></div>
    </div>
  );
}

export function BlockView({ block }: { block: Block }) {
  switch (block.type) {
    case "text":
      return <div className="bubble-text-inner">{block.text}</div>;
    case "image":
      return <Image block={block} />;
    case "video":
      return <Video block={block} />;
    case "voice":
      return (
        <Plain icon={<Mic size={18} aria-hidden="true" />}>
          <div>语音 {secs(block.duration_s)}</div>
          {block.media && block.playable ? <audio controls preload="none" src={MEDIA(block.media)} /> : <div className="t-cap">暂不支持在线播放</div>}
          {block.transcript && <div className="t-sub" style={{ marginTop: 4 }}>转写：{block.transcript}</div>}
        </Plain>
      );
    case "file":
      return (
        <Plain icon={<FileText size={18} aria-hidden="true" />}>
          <div style={{ overflowWrap: "anywhere" }}>{block.name || "文件"}</div>
          <div className="t-cap num">{size(block.size_bytes)}{block.media && <> · <a href={MEDIA(block.media)} download>下载</a></>}</div>
        </Plain>
      );
    case "link":
      return (
        <Plain icon={<ExternalLink size={18} aria-hidden="true" />}>
          {block.thumbnail && /^data:image\/jpeg;base64,[A-Za-z0-9+/=]+$/.test(block.thumbnail) && block.thumbnail.length <= 87407 && <img src={block.thumbnail} alt="链接缩略图" loading="lazy" style={{ maxWidth: 160, maxHeight: 100, objectFit: "contain", borderRadius: 6 }} />}
          <div style={{ fontWeight: 500, overflowWrap: "anywhere" }}>{block.title || block.url}</div>
          {block.desc && <div className="t-sub">{block.desc}</div>}
          <div className="t-cap">{block.source}{block.url && <> · <a href={block.url} target="_blank" rel="noreferrer noopener">打开链接</a></>}</div>
        </Plain>
      );
    case "quote":
      return <div className="quote-block"><div className="t-cap">{block.sender}</div><div>{block.text}</div></div>;
    case "note":
      return (
        <Plain icon={<StickyNote size={18} aria-hidden="true" />}>
          <div style={{ fontWeight: 500 }}>笔记 · {block.title}</div>
          <div className="t-sub" style={{ whiteSpace: "pre-wrap" }}>{block.text}</div>
        </Plain>
      );
    case "card":
      return (
        <Plain icon={<Play size={18} aria-hidden="true" />}>
          <div style={{ fontWeight: 500 }}>{block.title}</div>
          {block.subtitle && <div className="t-sub">{block.subtitle}</div>}
        </Plain>
      );
    case "chat_record":
      return (
        <div className="record">
          <Disclosure head={<div><div style={{ fontWeight: 500 }}>聊天记录 · {block.title}</div><div className="t-cap">共 {block.count ?? block.items?.length ?? 0} 条，点开查看</div></div>}>
            <div style={{ display: "grid", gap: 8 }}>
              {block.items?.map((item, i) => (
                <div key={i} className="record-item">
                  <div className="t-cap">{item.sender}{item.time ? `　${item.time}` : ""}</div>
                  {item.type === "image" ? <Image block={item} /> : <div style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{item.text}</div>}
                </div>
              ))}
            </div>
          </Disclosure>
        </div>
      );
    default:
      return <div className="bubble-text-inner">{block.text ?? "[暂不支持的消息]"}</div>;
  }
}
