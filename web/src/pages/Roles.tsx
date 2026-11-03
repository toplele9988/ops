import { PageIntro } from "./overview/PageIntro";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { RefreshCw } from "lucide-react";
import { Link } from "react-router";
import { get, postForm } from "../api";
import { ago } from "../format";
import { Run, RoleInfo } from "../roles";
import { Chip, Skeleton } from "../ui/bits";
import { useToast } from "../ui/Toast";
import { AlertList } from "../ui/RolePanels";

const NAME: Record<string, string> = { secretary: "案子秘书", triage: "分拣员", patrol: "巡检员", brief: "每日成交简报" };
const WHERE: Record<string, { to: string; text: string }> = {
  secretary: { to: "/deals", text: "打开任意案子就能看到" },
  triage: { to: "/triage", text: "去看待整理的会话" },
  patrol: { to: "/", text: "提醒显示在总览" },
  brief: { to: "/", text: "显示在总览的今日运营" },
};
const STATE = (r: RoleInfo) => !r.last_run_at ? { text: "还没干过活", tone: "plain" as const } : r.last_status === "ok" ? { text: "正常", tone: "green" as const } : { text: "上次用规则兜底", tone: "orange" as const };

export function RolesPage() {
  const toast = useToast();
  const client = useQueryClient();
  const roles = useQuery({ queryKey: ["roles"], queryFn: () => get<{ items: RoleInfo[] }>("/api/v1/roles"), retry: false });
  const runs = useQuery({ queryKey: ["role-runs"], queryFn: () => get<{ items: Run[] }>("/api/v1/roles/runs?limit=20"), retry: false });
  const run = useMutation({
    mutationFn: (path: string) => postForm(`/api/v1/${path}/run`, {}),
    onSuccess: () => { client.invalidateQueries({ queryKey: ["roles"] }); client.invalidateQueries({ queryKey: ["role-runs"] }); client.invalidateQueries({ queryKey: ["alerts"] }); client.invalidateQueries({ queryKey: ["brief"] }); toast("已运行"); },
    onError: (e: Error) => toast(e.message, "error"),
  });
  return (
    <>
      <div className="page-head"><div><h1 className="t-large">AI 员工</h1><p>每个岗位负责什么、最近干了什么活、出了什么问题，都在这里。它们只做整理、提醒和起草，不会替你对外发消息、改价格或推进成交。</p></div></div>
      <PageIntro page="roles" />
      {roles.isLoading && <Skeleton lines={4} />}
      <div className="group">
        {(roles.data?.items ?? []).map((r) => {
          const st = STATE(r); const w = WHERE[r.key];
          return (
            <div className="row" key={r.key} style={{ alignItems: "flex-start" }}>
              <div className="row-main" style={{ whiteSpace: "normal" }}>
                <div className="row-title">{NAME[r.key] ?? r.name} <Chip tone={st.tone}>{st.text}</Chip> <Chip tone="plain">{r.level === "L1" ? "只写草稿等你确认" : "只给建议"}</Chip></div>
                <div className="row-sub" style={{ whiteSpace: "normal" }}>{r.responsibility}。{w && <><Link to={w.to}>{w.text}</Link></>}</div>
                <div className="row-sub num">{r.last_run_at ? `上次 ${ago(r.last_run_at)}` : "还没运行过"} · 24 小时内 {r.runs_24h} 次{r.failures_24h ? `，其中 ${r.failures_24h} 次 AI 没成功` : ""} · 用 {r.last_model ?? r.model}</div>
              </div>
              {(r.key === "patrol" || r.key === "brief") && <button type="button" className="btn sm" disabled={run.isPending} onClick={() => run.mutate(r.key)}><RefreshCw size={14} aria-hidden="true" />现在运行</button>}
              {r.key === "triage" && <Link to="/triage" className="btn sm">去整理</Link>}
            </div>
          );
        })}
      </div>
      <div className="block">
        <h2 className="group-title">当前提醒</h2>
        <div className="group"><AlertList limit={20} empty="没有需要提醒的事。" /></div>
      </div>
      <div className="block">
        <h2 className="group-title">最近的工作记录</h2>
        <div className="group">
          {(runs.data?.items ?? []).length === 0 && <div className="row"><div className="row-main t-sub">还没有记录。</div></div>}
          {(runs.data?.items ?? []).map((r) => (
            <div className="row" key={r.id} style={{ alignItems: "flex-start" }}>
              <div className="row-main" style={{ whiteSpace: "normal" }}>
                <div className="row-title">{NAME[r.role] ?? r.role} <Chip tone={r.status === "ok" ? "green" : "orange"}>{r.source === "ai" ? "AI" : "规则"}</Chip></div>
                <div className="row-sub" style={{ whiteSpace: "normal" }}>{(r.output_text || "").split("\n")[0].slice(0, 120)}{r.error ? `　（${r.error}）` : ""}</div>
              </div>
              <span className="t-cap num">{ago(r.created_at)}</span>
            </div>
          ))}
        </div>
      </div>
    </>
  );
}
