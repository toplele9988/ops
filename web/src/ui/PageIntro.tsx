import { createContext, useContext } from "react";
import { Link } from "react-router";
import "./page-intro.css";

// The shell supplies one introduction, including while a page is loading or failed.
// Existing inline introductions remain usable outside the shell without duplicating it.
export const PageIntroProvided = createContext(false);
export const PAGE_PURPOSES = {
  overview: "从左到右看消息、客户和案子走到哪一步；点数字处理，拿不准就看“怎么用”。",
  workbench: "今天等你回复、核对收款和催交期的事都在这里；先处理最紧急的，办完回来更新待办。",
  inbox: "三个渠道的客户消息都在这里；先处理“要我回复”的，拿不准就打开会话点“AI 起草回复”。",
  triage: "还没认清是谁、属于哪笔生意的会话在这里；核对建议后确认归属，再去消息里回复。",
  outbox: "这里的草稿还没发送；先核对内容，再回到对应会话继续处理。",
  customers: "客户资料和往来都在这里；先认准同一个客户，再打开会话跟进需求。",
  deals: "一笔生意就是一个案子，从询盘跟到收款和售后；先看卡在谁手上，再处理下一步。",
  deal: "这笔生意的往来、报价和待办都在这里；先核对进展，再推进阶段，到账后亲自确认收款。",
  fulfil: "已收款后的备货、验货、发货和签收都在这里；先处理延期的，再记录每一步的进展。",
  fulfilment: "已收款后的备货、验货、发货和签收都在这里；先处理延期的，再记录每一步的进展。",
  "supplier-dossiers": "供应商发来的资料和报价在这里；先看出处和日期，缺什么就去消息里问。",
  "product-dossiers": "产品规格、图片和报价出处在这里；先核对资料，再用来回复客户或报价。",
  "dossier-gaps": "供应商和产品缺什么资料都列在这里；先找到缺项和出处，再向供应商补问。",
  operator: "AI 运营员的开关和处理记录在这里；先看演练结果，再决定哪些事交给它做。",
  search: "有出处的资料在这里查；先核对来源和日期，再用到回复或案子备注里。",
  quote: "这轮报价的产品、成本和运费在这里；先核对利润和有效期，再生成报价或形式发票草稿。",
  guide: "照着这八步，从消息一路做到收款和发货；看完一步就点按钮去处理。",
  suppliers: "这里保留原来的供应商价格列表；先核对有效期，完整资料请看前台的“供应商”。",
  products: "这里保留原来的产品成本列表；先核对可用成本，完整资料请看前台的“产品”。",
  roles: "AI 员工最近做了什么在这里查；先核对记录，再决定是否采用建议。",
} as const;

export function PageIntro({ page, text }: { page?: keyof typeof PAGE_PURPOSES; text?: string }) {
  const provided = useContext(PageIntroProvided);
  if (provided) return null;
  return <aside className="page-intro" aria-label="这页怎么用" data-page-intro={page}>
    <p>{text ?? (page ? PAGE_PURPOSES[page] : "先查看待办，再选择下一步。")}</p>
    {page !== "guide" && <Link to="/guide">怎么用</Link>}
  </aside>;
}
