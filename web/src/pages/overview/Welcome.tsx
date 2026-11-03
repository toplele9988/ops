import { useState } from "react";
import { Link } from "react-router";
import { readRemembered, rememberClosed, WELCOME_KEY } from "../../ops/queue";
import "./queue.css";

export function Welcome() {
  const [closed, setClosed] = useState(() => readRemembered(WELCOME_KEY));
  const [notice, setNotice] = useState("");
  if (closed) return null;
  return <aside className="queue-welcome" aria-label="新手提示">
    <div><h2 className="t-title">第一次用，先从今天的待办开始</h2><p>点每条右边的按钮去处理，回来刷新看进展。不熟悉时，照着八步指引走。</p><p className="t-foot">发送给买家、供应商和确认收款，都由你亲自确认。</p></div>
    <div className="queue-actions"><Link className="btn" to="/guide">查看怎么用</Link><button type="button" className="btn plain" onClick={() => {
      if (rememberClosed(WELCOME_KEY)) setClosed(true);
      else setNotice("当前浏览器无法记住关闭提示；本次可先收起。");
    }}>知道了，不再提示</button>{notice && <><p role="status">{notice}</p><button className="btn" onClick={() => setClosed(true)}>本次收起</button></>}</div>
  </aside>;
}
