import { useMutation, useQuery } from "@tanstack/react-query";
import { Sparkles } from "lucide-react";
import { useState } from "react";
import { useSearchParams } from "react-router";
import { get, postForm, qs } from "../api";
import { Chip, Empty, Skeleton } from "../ui/bits";
import { Segmented } from "../ui/Segmented";
import { useToast } from "../ui/Toast";

interface Hit {
  rank: number; family: string; family_label: string; title: string; source_type_label?: string; date?: string | null;
  snippet: string; confidential?: boolean; is_cost_price?: boolean; source_id: string;
}
interface SearchData { items?: Hit[]; results?: Hit[]; mode?: string; ready?: boolean }
interface AskData { answer: string; sources: { n: number; title: string; type?: string; date?: string | null }[]; mode?: string }

const EXAMPLES = ["200W 频闪灯 外箱尺寸", "LED 面光灯 毛重", "哪家供应商做防水光束灯", "CE 认证资料"];
const dateText = (v?: string | null) => (v ? v.slice(0, 10) : "");

export function Search() {
  const [params, setParams] = useSearchParams();
  const toast = useToast();
  const q = params.get("q") ?? "";
  const mode = (params.get("mode") as "find" | "ask" | null) ?? "find";
  const purpose = params.get("purpose") ?? "owner";
  const [draft, setDraft] = useState(q);
  const set = (changes: Record<string, string>) => {
    const next = new URLSearchParams(params);
    for (const [key, value] of Object.entries(changes)) (value ? next.set(key, value) : next.delete(key));
    setParams(next, { replace: true });
  };

  const stats = useQuery({ queryKey: ["kb-stats"], queryFn: () => get<{ chunks: number; mode: string; families: { family: string; label: string; chunks: number }[] }>("/api/v1/kb/stats"), retry: false });
  const found = useQuery({
    queryKey: ["kb-search", q, purpose],
    queryFn: () => get<SearchData>(`/api/v1/kb/search${qs({ q, purpose, top_k: 12 })}`),
    enabled: q.length > 0, retry: false,
  });
  const ask = useMutation({
    mutationFn: () => postForm<AskData>("/api/v1/kb/ask", { q, purpose }),
    onError: (error: Error) => toast(error.message, "error"),
  });
  const hits = found.data?.items ?? found.data?.results ?? [];

  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    const text = draft.trim();
    if (!text) return;
    set({ q: text });
    if (mode === "ask") { ask.reset(); window.setTimeout(() => ask.mutate(), 0); }
  };

  return (
    <>
      <div className="page-head">
        <div>
          <h1 className="t-large">知识检索</h1>
          <p>用一句话问产品、供应商、历史聊天，答案带来源和时间。{stats.data ? `知识库现有 ${stats.data.chunks.toLocaleString()} 条资料。` : ""}</p>
        </div>
      </div>

      <form onSubmit={submit} className="ask-box" role="search">
        <label className="field" style={{ flex: 1 }}>
          <span className="sr-only">要查什么</span>
          <input type="search" name="q" autoComplete="off" spellCheck={false} placeholder="例如：200W 频闪灯 外箱尺寸…" value={draft} onChange={(e) => setDraft(e.target.value)} />
        </label>
        <button type="submit" className="btn primary">{mode === "ask" ? "问知识库" : "搜索"}</button>
      </form>
      <div className="toolbar" style={{ marginTop: 12 }}>
        <Segmented label="方式" value={mode} onChange={(value) => set({ mode: value === "find" ? "" : value })}
          options={[{ value: "find", label: "找资料" }, { value: "ask", label: "直接问（AI 回答）" }]} />
        <Segmented label="范围" value={purpose} onChange={(value) => set({ purpose: value === "owner" ? "" : value })}
          options={[{ value: "owner", label: "全部资料" }, { value: "product_facts", label: "仅产品与供应商" }]} />
      </div>

      {!q && (
        <div className="group">
          <Empty title="想查什么？">试试这些：
            <span style={{ display: "flex", gap: 8, flexWrap: "wrap", justifyContent: "center", marginTop: 12 }}>
              {EXAMPLES.map((text) => <button key={text} type="button" className="btn sm" onClick={() => { setDraft(text); set({ q: text }); }}>{text}</button>)}
            </span>
          </Empty>
        </div>
      )}

      {q && mode === "ask" && (
        <div className="block">
          <div className="group" style={{ padding: 18 }}>
            <h2 className="t-headline" style={{ display: "flex", gap: 8, alignItems: "center" }}><Sparkles size={18} aria-hidden="true" style={{ color: "var(--tint)" }} />AI 回答</h2>
            {ask.isPending && <p className="t-sub" style={{ marginTop: 8 }}>正在查资料并回答，约需十几秒…</p>}
            {!ask.isPending && !ask.data && !ask.isError && <p className="t-sub" style={{ marginTop: 8 }}>点右上角「问知识库」获取回答。</p>}
            {ask.data && (
              <>
                <p style={{ marginTop: 10, whiteSpace: "pre-wrap", maxWidth: "70ch" }}>{ask.data.answer}</p>
                {ask.data.sources.length > 0 && (
                  <ol className="t-foot" style={{ margin: "12px 0 0", paddingLeft: 20, display: "grid", gap: 2 }}>
                    {ask.data.sources.map((s) => <li key={s.n} value={s.n}>{s.title}{s.type ? ` · ${s.type}` : ""}{s.date ? ` · ${dateText(s.date)}` : ""}</li>)}
                  </ol>
                )}
                <p className="t-cap" style={{ marginTop: 10 }}>AI 只依据上面的资料回答；涉及价格、交期，请以原始资料为准再对外使用。</p>
              </>
            )}
          </div>
        </div>
      )}

      {q && (
        <div className="block">
          <h2 className="group-title">相关资料{hits.length > 0 && <span className="count num">{hits.length}</span>}{found.data?.mode === "lexical" && <span className="count">（向量服务不可用，仅按关键词）</span>}</h2>
          {found.isLoading && <Skeleton lines={4} />}
          {found.isError && <div className="group"><Empty title="没能查到">{(found.error as Error).message}</Empty></div>}
          {found.data && hits.length === 0 && <div className="group"><Empty title="知识库里没有找到相关资料">换个说法，或把型号、供应商名写上再试。</Empty></div>}
          {hits.length > 0 && (
            <div className="group">
              {hits.map((hit, i) => (
                <div className="row" key={hit.source_id + i} style={{ alignItems: "flex-start" }}>
                  <span className="t-cap num" style={{ width: 20, marginTop: 3 }}>{i + 1}</span>
                  <div className="row-main">
                    <div className="row-title">{hit.title}</div>
                    <div className="row-sub" style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center", margin: "2px 0 4px" }}>
                      <Chip tone="blue" dot={false}>{hit.family_label}</Chip>
                      {hit.source_type_label && <span>{hit.source_type_label}</span>}
                      {hit.date && <span className="num">{dateText(hit.date)}</span>}
                      {hit.is_cost_price && <Chip tone="orange">成本价，不要对外</Chip>}
                      {hit.confidential && !hit.is_cost_price && <Chip tone="orange">内部资料</Chip>}
                    </div>
                    <div className="t-sub" style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{hit.snippet}</div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </>
  );
}
