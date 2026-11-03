import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router";
import { readOpsDrafts } from "../api";
import { formatTime } from "../format";
import { Empty, Skeleton } from "../ui/bits";
import { PageIntro } from "./overview/PageIntro";

export function Outbox() {
  const q = useQuery({ queryKey: ["ops-drafts"], queryFn: readOpsDrafts, retry: false, refetchOnMount: "always" });
  return <><div className="page-head"><h1 className="t-large">草稿未发送</h1><button className="btn" disabled={q.isFetching} onClick={() => q.refetch()}>刷新草稿</button></div>
    <PageIntro text="这里只读查看商机沟通和往来单位的演练草稿；所有记录均未发送。" />
    <p>草稿未发送 · dry-run。人工确认过的草稿仍未发送。</p>
    {q.isLoading && <Skeleton />}
    {q.isError && <Empty title="没能读到草稿" action={<button className="btn" onClick={() => q.refetch()}>重新读取</button>}>{q.error.message}</Empty>}
    {q.data?.items.length === 0 && <Empty title="还没有演练草稿" action={<Link className="btn" to="/inbox">去起草回复</Link>} />}
    <div className="group">{q.data?.items.map(d => <article className="row" key={`${d.source}-${d.id}`}><div className="row-main"><b>{d.source === "inbox" ? "商机沟通" : "往来单位"} · 草稿未发送</b><p style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{d.text}</p><p className="t-foot">{formatTime(d.created_at)} · {d.reviewed_at || d.state === "queued" ? "已人工核对，未发送" : "待人工核对"}</p><Link className="btn sm" to={`/inbox?conversation=${encodeURIComponent(d.conversation_key)}&role=all`}>查看会话</Link></div></article>)}</div>
  </>;
}
