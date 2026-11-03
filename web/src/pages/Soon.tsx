import { useLocation } from "react-router";
import { findNav } from "../nav";
import { Empty } from "../ui/bits";

// Honest placeholder: says what the module will do and what is missing, never fakes data.
export function Soon() {
  const item = findNav(useLocation().pathname);
  if (!item) return <Empty title="没有找到这个页面">请从左侧导航进入。</Empty>;
  return (
    <>
      <div className="page-head">
        <div>
          <h1 className="t-large">{item.label}</h1>
          <p>还没接入。下面说明它做好后能帮你做什么，以及现在缺什么。</p>
        </div>
      </div>
      <div className="group">
        <div className="row"><div className="row-main"><div className="row-title">做好后能做什么</div><div className="row-sub">{item.purpose}</div></div></div>
        <div className="row"><div className="row-main"><div className="row-title">现在的情况</div><div className="row-sub">{item.needs}</div></div></div>
        <div className="row"><div className="row-main"><div className="row-title">排在哪个阶段</div><div className="row-sub">{item.step}</div></div></div>
      </div>
    </>
  );
}
