import { ChevronRight } from "lucide-react";
import { Link } from "react-router";
import { NAV } from "../nav";

export function SystemHome() {
  return <>
    <div className="page-head"><div><h1 className="t-large">后台设置</h1><p>接入渠道、维护数据和账号，集中在这里处理。</p></div></div>
    {["渠道与数据", "设置", "整理工具"].map((group) => <section className="block" key={group}>
      <h2 className="group-title">{group}</h2>
      <div className="group">{NAV.filter((item) => item.area === "back" && item.group === group).map((item) => {
        const body = <><item.icon size={20} aria-hidden="true" /><div className="row-main"><div className="row-title">{item.label}{item.note && <span className="t-cap"> · {item.note}</span>}</div></div><ChevronRight size={16} aria-hidden="true" /></>;
        return item.kind === "external" ? <a key={item.key} className="row" href={item.path}>{body}</a> : <Link key={item.key} className="row" to={item.path}>{body}</Link>;
      })}</div>
    </section>)}
  </>;
}
