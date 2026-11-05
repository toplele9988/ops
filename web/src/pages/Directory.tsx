import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { RefreshCw } from "lucide-react";
import { useState } from "react";
import { get, postForm, qs } from "../api";
import { ago } from "../format";
import { Chip, Empty, SearchField, Skeleton } from "../ui/bits";
import { Segmented } from "../ui/Segmented";
import { useToast } from "../ui/Toast";

interface Item {
  id: string; kind: "group" | "private" | "official"; name: string; collect_state: string; collect_state_reason: string; role: string | null; named_role_label: string | null;
  role_conflict: boolean; role_conflict_reason: string | null; n_messages: number | null; last_message_at: number | null; inbox_visible: boolean; inbox_visibility_reason: string | null; registered: boolean;
}
interface Summary { ready: boolean; conclusion: string; total: number; conflicts: number; not_enrolled_suspected: { supplier: number; customer: number }; notes: string[]; groups: { total: number; by_state: Record<string, number> }; name_health?: { total: number; missing: number; reasons: Record<string, number> } }
interface Page { items: Item[]; total: number; page: number; page_size: number }

const STATE: Record<string, { text: string; tone: "green" | "orange" | "red" | "plain" }> = {
  collecting: { text: "在收消息", tone: "green" }, enrolled_silent: { text: "已接入，最近没消息", tone: "orange" }, enrolled_no_messages: { text: "已接入，从没收到过消息", tone: "orange" },
  not_enrolled: { text: "没接入采集", tone: "red" }, unknown: { text: "未核验", tone: "plain" },
};
const ROLE: Record<string, string> = { supplier: "供应商", customer: "客户", learning: "学习", internal: "内部", unclassified: "未分类", unknown: "未知" };
const KIND = [{ value: "group", label: "群" }, { value: "private", label: "私聊" }, { value: "official", label: "公众号" }];
const STATES = [{ value: "", label: "全部状态" }, { value: "not_enrolled", label: "没接入" }, { value: "collecting", label: "在收消息" }, { value: "enrolled_silent", label: "最近没消息" }, { value: "enrolled_no_messages", label: "从没消息" }];

export function DirectoryPage() {
  const toast = useToast();
  const client = useQueryClient();
  const [kind, setKind] = useState("group");
  const [state, setState] = useState(() => new URLSearchParams(window.location.search).get("state") ?? "");
  const [q, setQ] = useState("");
  const [page, setPage] = useState(1);
  const sum = useQuery({ queryKey: ["dir-summary"], queryFn: () => get<Summary>("/api/v1/directory/wechat/summary"), retry: false });
  const list = useQuery({ queryKey: ["dir", kind, state, q, page], queryFn: () => get<Page>(`/api/v1/directory/wechat${qs({ kind, state, q, page, page_size: 30 })}`), retry: false, placeholderData: (p) => p });
  const refresh = useMutation({
    mutationFn: () => postForm<{ message?: string }>("/api/v1/directory/wechat/refresh", { confirm: "1" }),
    onSuccess: (r) => { toast(r.message ?? "通讯录已刷新"); client.invalidateQueries({ queryKey: ["dir"] }); client.invalidateQueries({ queryKey: ["dir-summary"] }); },
    onError: (e: Error) => toast(e.message, "error"),
  });
  const s = sum.data;
  return (
    <>
      <div className="page-head">
        <div><h1 className="t-large">微信通讯录</h1><p>通讯录里所有的群和联系人，哪些已经接入采集、哪些系统根本收不到消息，一眼看清。</p></div>
        <button type="button" className="btn" disabled={refresh.isPending} onClick={() => refresh.mutate()}><RefreshCw size={16} aria-hidden="true" />{refresh.isPending ? "正在刷新…" : "刷新通讯录"}</button>
      </div>
      {sum.isLoading && <Skeleton lines={2} />}
      {s && <div className="callout" role="note"><b>{s.conclusion}</b>{(s.not_enrolled_suspected.supplier + s.not_enrolled_suspected.customer) > 0 && <> 其中疑似供应商群 {s.not_enrolled_suspected.supplier} 个、客户群 {s.not_enrolled_suspected.customer} 个还没接入。</>}{s.conflicts > 0 && <> 有 {s.conflicts} 个群的角色和群名对不上，请核对。</>}</div>}
      {s?.notes.map((n) => <p className="t-foot" key={n}>{n}</p>)}
      {s?.name_health && <p className="t-foot" data-testid="missing-names">有消息的微信会话共 {s.name_health.total} 个，还有 {s.name_health.missing} 个没名字。{Object.entries(s.name_health.reasons).map(([reason, count]) => `${reason}：${count} 个。`).join(" ")}</p>}
      <div className="toolbar" style={{ display: "flex", gap: 10, flexWrap: "wrap", margin: "16px 0 12px", alignItems: "center" }}>
        <Segmented value={kind} onChange={(v) => { setKind(v); setPage(1); }} options={KIND} label="类型" />
        <Segmented value={state} onChange={(v) => { setState(v); setPage(1); }} options={STATES} label="接入状态" />
        <SearchField label="搜名称" placeholder="搜群名、联系人…" value={q} onSearch={(v) => { setQ(v); setPage(1); }} />
      </div>
      {list.isLoading && <Skeleton lines={6} />}
      {list.isError && <div className="group"><Empty title="没能读到通讯录">{(list.error as Error).message}</Empty></div>}
      {list.data && list.data.items.length === 0 && <div className="group"><Empty title="没有符合条件的">换个筛选条件看看。</Empty></div>}
      {list.data && list.data.items.length > 0 && (
        <div className="group">
          {list.data.items.map((i) => {
            const st = STATE[i.collect_state] ?? STATE.unknown;
            return (
              <div className="row" key={i.id} style={{ alignItems: "flex-start" }}>
                <div className="row-main" style={{ whiteSpace: "normal" }}>
                  <div className="row-title" style={{ whiteSpace: "normal" }}>{i.name} <Chip tone={st.tone}>{st.text}</Chip> {i.role && i.role !== "unclassified" && <Chip tone="plain">{ROLE[i.role] ?? i.role}</Chip>}{i.named_role_label && !i.role_conflict && i.role === "unclassified" && <Chip tone="orange">群名像{i.named_role_label}</Chip>}{i.role_conflict && <Chip tone="red">角色和群名对不上</Chip>}</div>
                  <div className="row-sub" style={{ whiteSpace: "normal" }}>{i.collect_state_reason}{i.kind === "group" && i.inbox_visibility_reason ? `　收件箱：${i.inbox_visibility_reason}` : ""}</div>
                  {i.role_conflict_reason && <div className="row-sub" style={{ color: "var(--red)", whiteSpace: "normal" }}>{i.role_conflict_reason}</div>}
                </div>
                <div className="t-cap num" style={{ textAlign: "right", flex: "none" }}>{i.n_messages != null ? `${i.n_messages} 条` : ""}<br />{i.last_message_at ? `最近 ${ago(i.last_message_at)}` : ""}</div>
              </div>
            );
          })}
        </div>
      )}
      {list.data && list.data.total > list.data.page_size && (
        <div style={{ display: "flex", gap: 8, justifyContent: "center", marginTop: 12, alignItems: "center" }}>
          <button type="button" className="btn sm" disabled={page <= 1} onClick={() => setPage(page - 1)}>上一页</button>
          <span className="t-cap num">第 {page} 页 / 共 {Math.ceil(list.data.total / list.data.page_size)} 页</span>
          <button type="button" className="btn sm" disabled={page * list.data.page_size >= list.data.total} onClick={() => setPage(page + 1)}>下一页</button>
        </div>
      )}
      <p className="t-foot" style={{ marginTop: 16 }}>想让没接入的群开始收消息，要改采集配置，需要你确认后由系统按现有的接入流程来做；这个页面本身只读，不会改任何配置。</p>
    </>
  );
}
