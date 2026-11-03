import { WhatsAppLogin } from "../ui/WhatsAppLogin";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { TriangleAlert } from "lucide-react";
import { get, postForm } from "../api";
import { ago } from "../format";
import { dedupe } from "../notes";
import type { Tone } from "../labels";
import { Chip, Empty, Skeleton, Switch } from "../ui/bits";
import { useToast } from "../ui/Toast";

interface Account { name: string; conversations?: number; messages?: number; messages_24h?: number; last_message_at?: number | null; state?: "ok" | "quiet" | "stale" }
interface Channel {
  channel: string; label: string; ready: boolean; state?: "ok" | "quiet" | "stale"; summary?: string; accounts: Account[];
  messages_24h: number; last_message_at: number | null; diagnosis?: string | null; checklist?: string[];
  probe?: { last_heartbeat_at?: number | null; version?: string | null; state?: string | null } | null;
}
interface Data { generated_at: number; channels: Channel[]; notes: string[] }
interface Settings { channels: { channel: string; label: string; enabled: boolean; available: boolean; reason?: string }[] }

const STATE: Record<string, { text: string; tone: Tone }> = {
  ok: { text: "正常", tone: "green" },
  quiet: { text: "较安静", tone: "orange" },
  stale: { text: "很久没有消息", tone: "red" },
};

export function Channels() {
  const toast = useToast();
  const client = useQueryClient();
  const q = useQuery({ queryKey: ["system-channels"], queryFn: () => get<Data>("/api/v1/system/channels"), retry: false });
  const settings = useQuery({ queryKey: ["outbox-settings"], queryFn: () => get<Settings>("/api/v1/outbox/settings"), retry: false });
  const toggle = useMutation({
    mutationFn: ({ channel, enabled }: { channel: string; enabled: boolean }) => postForm("/api/v1/outbox/settings", { channel, enabled: enabled ? "1" : "0" }),
    onSuccess: () => { toast("已保存"); client.invalidateQueries({ queryKey: ["outbox-settings"] }); },
    onError: (error: Error) => toast(error.message, "error"),
  });

  return (
    <>
      <div className="page-head">
        <div>
          <h1 className="t-large">渠道接入</h1>
          <p>买家和供应商的消息从哪些渠道进来，哪些渠道可以直接在后台回复。看到「很久没有消息」，通常是采集断了，而不是真的没人联系。</p>
        </div>
      </div>
      {q.isLoading && <Skeleton lines={4} />}
      {q.isError && <Empty title="没能读到渠道状态">{(q.error as Error).message}</Empty>}
      {dedupe(q.data?.notes ?? []).map((note) => (
        <div key={note} className="callout" style={{ marginBottom: 12, display: "flex", gap: 10, alignItems: "flex-start" }} role="note">
          <TriangleAlert size={18} aria-hidden="true" style={{ color: "var(--orange)", flex: "none", marginTop: 2 }} />
          <span>{note}</span>
        </div>
      ))}
      <div style={{ marginTop: 16 }}><WhatsAppLogin /></div>
      <div className="cols even" style={{ marginTop: 16 }}>
        {q.data?.channels.map((ch) => {
          const send = settings.data?.channels.find((s) => s.channel === ch.channel);
          return (
            <div className="block" key={ch.channel}>
              <h2 className="group-title">
                {ch.label}
                {ch.channel === "website" && !ch.ready ? <Chip tone="plain">未启用</Chip> : ch.state && <Chip tone={STATE[ch.state]?.tone}>{STATE[ch.state]?.text}</Chip>}
                <span className="count num">24 小时 {ch.messages_24h} 条 · 最近 {ago(ch.last_message_at)}</span>
              </h2>
              <div className="group">
                {ch.summary && <div className="row"><div className="row-main t-sub">{ch.summary}</div></div>}
                {!ch.ready && ch.channel !== "website" && <div className="row"><div className="row-main t-sub">还没有读到这个渠道的数据。</div></div>}
                {ch.accounts.map((a) => (
                  <div className="row" key={a.name}>
                    <div className="row-main">
                      <div className="row-title">{a.name}</div>
                      <div className="row-sub num">
                        {a.conversations != null && `${a.conversations} 个会话 · `}{a.messages != null && `共 ${a.messages} 条 · `}24 小时 {a.messages_24h ?? 0} 条 · 最近 {ago(a.last_message_at)}
                      </div>
                    </div>
                    {a.state && <Chip tone={STATE[a.state]?.tone}>{STATE[a.state]?.text}</Chip>}
                  </div>
                ))}
                {ch.diagnosis && (
                  <div className="row" style={{ alignItems: "flex-start" }}>
                    <div className="row-main">
                      <div className="row-title" style={{ color: "var(--orange)" }}>{ch.diagnosis}</div>
                      {ch.checklist && (
                        <ol className="t-foot" style={{ margin: "6px 0 0", paddingLeft: 18, display: "grid", gap: 2 }}>
                          {ch.checklist.map((item) => <li key={item}>{item}</li>)}
                        </ol>
                      )}
                      {ch.probe && <div className="t-cap" style={{ marginTop: 6 }}>探针最后心跳 {ago(ch.probe.last_heartbeat_at)} · 版本 {ch.probe.version ?? "未知"} · {ch.probe.state ?? "未知"}</div>}
                      {ch.channel === "alibaba" && ch.last_message_at != null && Date.now() / 1000 - ch.last_message_at > 6 * 3600 && (
                        <div className="callout" role="note" style={{ marginTop: 10 }}>
                          <b>阿里的消息已经 {ago(ch.last_message_at).replace("前", "")}没有进来了。</b>221 这边的接收服务是正常在线的，问题在 Win10 那台电脑上的采集脚本没有在往 221 发数据。请在 Win10 上这样查：
                          <ol className="plain-list" style={{ marginTop: 6 }}>
                            <li>用装了“油猴（Tampermonkey）”的 Chrome，确认已经登录阿里国际站<b>主账号</b>。</li>
                            <li>打开阿里的消息页面（网址以 message.alibaba.com/message/maDetail.htm 或 onetalk.alibaba.com/message/messageManager.htm 开头），<b>保持这个标签页开着</b>，电脑不要休眠、不要锁成断网。</li>
                            <li>点浏览器右上角的油猴图标：确认脚本“Amy当前询盘详情 → 221”是<b>开启</b>状态，并看一下版本号。221 上放的最新版是 0.3.5.4，上次心跳报的是 {ch.probe?.version ?? "未知"}。</li>
                            <li>刷新阿里消息页，等 1～2 分钟，再点本页的“刷新”，看上面的“最近”时间有没有变新。</li>
                            <li>还是不动：告诉我油猴里脚本的版本和页面上看到的提示，我来判断要不要换新版脚本。</li>
                          </ol>
                          <div className="t-foot" style={{ marginTop: 6 }}>在采集恢复之前，阿里已经谈成的生意，可以先在“案子 → 录入一笔生意”里直接录进来，不会丢。</div>
                        </div>
                      )}
                    </div>
                  </div>
                )}
                {send && (
                  <div className="row">
                    <div className="row-main">
                      <div className="row-title">允许从后台直接回复</div>
                      <div className="row-sub">{send.available ? "打开后，可在收件箱里直接发送。每次发送前都会让你确认，所有发送都有记录。" : send.reason}</div>
                    </div>
                    <Switch label={`${ch.label} 后台发送`} checked={send.enabled} disabled={!send.available || toggle.isPending}
                      onChange={(value) => toggle.mutate({ channel: ch.channel, enabled: value })} />
                  </div>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </>
  );
}
