import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { get, postForm } from "../api";
import { ago, formatTime } from "../format";
import { Empty, Skeleton } from "../ui/bits";
import { useToast } from "../ui/Toast";

interface Stats { ready?: boolean; chunks: number; with_vectors?: number; loaded_at?: number | null; mode?: string; families: { family: string; label: string; chunks: number }[]; assets_by_type?: { label: string; count: number }[] }
interface Brief { text: string; version?: number; versions?: { version: number; saved_at: number; saved_by?: string }[] }

export function KnowledgeHub() {
  const toast = useToast();
  const client = useQueryClient();
  const stats = useQuery({ queryKey: ["kb-stats"], queryFn: () => get<Stats>("/api/v1/kb/stats"), retry: false });
  const brief = useQuery({ queryKey: ["kb-brief"], queryFn: () => get<Brief>("/api/v1/kb/brief"), retry: false });
  const [text, setText] = useState("");
  useEffect(() => { if (brief.data) setText(brief.data.text); }, [brief.data?.text, brief.data?.version]);
  const save = useMutation({
    mutationFn: () => postForm("/api/v1/kb/brief", { text }),
    onSuccess: () => { toast("已保存，所有 AI 下次调用就会读到新版"); client.invalidateQueries({ queryKey: ["kb-brief"] }); },
    onError: (error: Error) => toast(error.message, "error"),
  });
  const total = stats.data?.chunks ?? 0;

  return (
    <>
      <div className="page-head">
        <div>
          <h1 className="t-large">知识中枢</h1>
          <p>所有 AI（起草回复、问答，以及 Claude、Codex 的会话）都从这里取资料，并且先读下面这份「公司背景」。改一次，所有 AI 同时生效，不用再对每个 AI 交代一遍。</p>
        </div>
      </div>

      <div className="cols">
        <div>
          <div className="block">
            <h2 className="group-title">公司背景与规则<span className="count">AI 每次都会先读它</span></h2>
            {brief.isLoading && <Skeleton lines={4} />}
            {brief.isError && <div className="group"><Empty title="没能读到公司背景">{(brief.error as Error).message}</Empty></div>}
            {brief.data && (
              <div className="group" style={{ padding: 16 }}>
                <label>
                  <span className="sr-only">公司背景与规则</span>
                  <textarea className="textarea" name="brief" rows={16} maxLength={8000} value={text} onChange={(e) => setText(e.target.value)} spellCheck={false} style={{ minHeight: 280 }} />
                </label>
                <div className="composer-row" style={{ marginTop: 10 }}>
                  <span className="t-cap num">{text.length} / 8000 字{brief.data.version ? ` · 当前第 ${brief.data.version} 版` : ""}</span>
                  <button type="button" className="btn primary" disabled={save.isPending || text === brief.data.text || !text.trim()} onClick={() => save.mutate()}>{save.isPending ? "保存中…" : "保存"}</button>
                </div>
                {brief.data.versions && brief.data.versions.length > 0 && (
                  <p className="t-cap" style={{ marginTop: 10 }}>最近保存：{brief.data.versions.map((v) => `第 ${v.version} 版 ${formatTime(v.saved_at)}`).join("；")}</p>
                )}
              </div>
            )}
          </div>
        </div>
        <div>
          <div className="block">
            <h2 className="group-title">知识库现状</h2>
            {stats.isLoading && <Skeleton lines={3} />}
            {stats.isError && <div className="group"><Empty title="没能读到状态">{(stats.error as Error).message}</Empty></div>}
            {stats.data && (
              <div className="group">
                <div className="row"><div className="row-main"><div className="row-title num">{total.toLocaleString()} 条资料</div>
                  <div className="row-sub">{!stats.data.loaded_at ? "索引正在加载，第一次搜索会稍慢" : stats.data.mode === "lexical" ? "向量服务不可用，目前只按关键词查" : "关键词 + 语义向量混合检索"}{stats.data.loaded_at ? ` · 索引更新于 ${ago(stats.data.loaded_at)}` : ""}</div></div></div>
                {stats.data.families.map((f) => (
                  <div className="row" key={f.family}><div className="row-main"><div className="row-title">{f.label}</div></div><span className="num t-sub">{f.chunks.toLocaleString()}</span></div>
                ))}
              </div>
            )}
          </div>
          <div className="callout" role="note">
            <b>保密规则（自动执行）</b>
            <ul>
              <li>给客户起草回复时，AI 看不到成本价、保密资料，也看不到别的客户的对话。</li>
              <li>只有你自己查询时才能看到全部，并且成本价会标出来。</li>
            </ul>
          </div>
        </div>
      </div>
    </>
  );
}
