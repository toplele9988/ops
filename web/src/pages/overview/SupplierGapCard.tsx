import { Link } from "react-router";
import "../Dossiers.css";

export interface SupplierGapSummary { ready: boolean; total: number | null; items: {gap_id:string;supplier_name:string;ask_text:string;impact:string;priority:number;to:string}[] }
export function SupplierGapCard({value}: {value?:SupplierGapSummary}) {
  return <section className="ds-queue-card" aria-label="供应商缺资料"><h3>供应商缺资料</h3>{!value?.ready?<p>缺资料清单暂时读不到，请打开档案核对。</p>:<><p>还有 {value.total} 项缺口，先看优先级最高的 {value.items.length} 项。</p><ol>{value.items.map(g=><li key={g.gap_id}><Link to={g.to}><b>{g.supplier_name}</b> · {g.ask_text}</Link></li>)}</ol></>}<Link className="btn sm" to="/dossier-gaps">查看缺资料 / 生成问话草稿</Link></section>;
}
