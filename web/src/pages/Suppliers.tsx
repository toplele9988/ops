import { PartiesWorkbench } from "../ui/PartiesWorkbench";
import { Link } from "react-router";

export function Suppliers() {
  return <><p className="callout">这里维护往来单位与联系关系。<Link to="/supplier-dossiers">查看带来源的供应商档案、配置与报价</Link></p><PartiesWorkbench initialKind="supplier" /></>;
}
