import { useQuery } from "@tanstack/react-query";
import { useEffect } from "react";
import { Link } from "react-router";
import { get } from "../../api";
import { useQueueFeedback, type QueueResponse } from "../../ops/queue";
import { Chip, Empty, Skeleton } from "../../ui/bits";
import "./queue.css";

export function TodayQueue() {
  const q = useQuery({ queryKey: ["ops-queue"], queryFn: () => get<QueueResponse>("/api/v1/ops/queue"),
    retry: false, refetchInterval: 60_000, refetchOnMount: "always" });
  const feedback = useQueueFeedback();
  useEffect(() => {
    const refresh = () => { if (document.visibilityState === "visible") void q.refetch(); };
    window.addEventListener("focus", refresh);
    return () => window.removeEventListener("focus", refresh);
  }, [q.refetch]);
  const rows = [...(q.data?.items ?? [])].sort((a, b) => a.priority - b.priority || (a.due_on ?? "9999").localeCompare(b.due_on ?? "9999") || a.id.localeCompare(b.id));
  const visible = rows.filter((item) => !feedback.hidden(item));
  const hidden = rows.length - visible.length;
  return <section className="queue-panel" aria-labelledby="today-queue-title" data-testid="today-queue">
    <header className="queue-heading"><div><h2 id="today-queue-title" className="t-headline">今天要做的事</h2><p>先看最紧急的，点右边按钮去处理，回来刷新看剩余待办。</p></div>
      <button type="button" className="btn" disabled={q.isFetching} onClick={() => q.refetch()}>{q.isFetching ? "正在更新待办…" : "更新待办"}</button></header>
    <p className="t-foot">已处理和稍后仅记在这台设备；收款仍须到案子核对到账。新进展会重新提醒。</p>
    {q.isLoading && <Skeleton lines={3} />}
    {q.isError && <div role="alert" className="callout"><b>待办暂时读不到</b><p>请点“更新待办”重试，也可以直接去<Link to="/inbox">商机沟通</Link>或<Link to="/deals">案子</Link>核对。{q.data && " 下方保留上次结果，请核对后再处理。"}</p></div>}
    {q.data && !q.data.complete && <div className="callout" role="note"><b>还有待办未核对齐</b>{q.data.unavailable.map((text) => <p key={text}>{text}</p>)}<Link to="/system/pipeline">查看消息与资料检查</Link></div>}
    <div className="queue-feedback" role="status" aria-live="polite">{feedback.notice}</div>
    {q.data && <><p className="t-foot" data-testid="queue-count">还需处理 {visible.length} 条{hidden > 0 && ` · 本机暂存 ${hidden} 条`}</p>
      {visible.length ? <ol className="queue-list">{visible.map((item) => <li key={item.id} className="queue-item" data-kind={item.kind} data-queue-id={item.id}>
        <div className="queue-copy"><Chip tone={item.priority <= 1 ? "red" : item.priority <= 3 ? "orange" : "plain"}>{item.label}</Chip><h3>{item.title}</h3><p>{item.detail}</p>{item.due_on && <p className="t-foot">约定日期：{item.due_on}</p>}</div>
        <div className="queue-actions"><Link className="btn primary" to={item.to}>{item.action}</Link><div className="queue-secondary"><button type="button" className="btn plain" disabled={q.isError} onClick={() => feedback.decide(item, "done")}>已处理</button><button type="button" className="btn plain" disabled={q.isError} onClick={() => feedback.decide(item, "later")}>稍后 · 2 小时</button></div></div>
      </li>)}</ol> : !q.isError && <Empty title={q.data.complete ? "今天没有待办" : "已读到的待办已处理，请核对其余来源"} action={<div className="queue-empty-actions"><Link className="btn" to="/inbox">去看商机沟通</Link><Link className="btn" to="/guide">查看八步指引</Link></div>}>可以安排下一次客户跟进；有新消息和进展时会重新提醒。</Empty>}
      {hidden > 0 && <button type="button" className="btn" onClick={feedback.restore}>恢复本机暂存的 {hidden} 条</button>}
    </>}
  </section>;
}
