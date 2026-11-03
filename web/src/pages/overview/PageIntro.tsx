import { Link } from "react-router";
import "./queue.css";

export const PAGE_PURPOSES = {
  overview: "先处理今天最要紧的生意，再检查消息和资料是否齐全。",
  workbench: "在这里跟进待回复的买家、案子进展和供应商资料。",
  guide: "照着八步做，从开工、询盘一路跟到收款、发货和复购。",
  inbox: "集中看买家与供应商的往来，起草回复并把会话归到案子。",
  deals: "看每笔生意卡在谁手上，打开案子推进下一步。",
  customers: "整理客户资料和往来，安排下一次跟进。",
  suppliers: "核对供应商价格、规格和交期，缺什么就去问。",
  products: "把产品规格和有效成本核准好，再用来报价。",
  fulfilment: "跟进备货、验货、发货和签收，及时处理超期节点。",
  search: "查有出处的资料，核对后用到回复或案子备注。",
  roles: "查看 AI 员工做了什么，逐条决定采用、修改或拒绝。",
};
export function PageIntro({ page, text }: { page?: keyof typeof PAGE_PURPOSES; text?: string }) {
  return <aside className="queue-intro" aria-label="这页怎么用"><p>{text ?? (page ? PAGE_PURPOSES[page] : "先查看待办，再选择下一步。")}</p><Link to="/guide">查看八步指引</Link></aside>;
}
