import { Briefcase, Bot, Factory, Home, MessageSquareReply, Receipt, Truck, Users } from "lucide-react";
import { Link } from "react-router";
import { PageIntro } from "./overview/PageIntro";
import "./overview/queue.css";

const STEPS = [
  { title: "开工：先看今天要做的事", icon: Home, flow: "打开首页 → 看流程数字 → 打开待我处理", how: "首页看流程和渠道状态；待回复、催供应商、核对收款和超期事项到“待我处理”查看。点按钮带着会话或案子过去，处理后回来刷新。暂时顾不上可选“稍后”。", result: "办完的条目减少；本机可暂存和恢复提醒。", to: "/workbench", action: "打开待我处理" },
  { title: "收到询盘：回复并归到案子", icon: MessageSquareReply, flow: "打开会话 → AI 起草 → 核对并确认发送", how: "到“消息”看完整往来，编辑草稿，核对后再确认发送。点“新建案子”或关联已有案子；需要厂家答复时起草询问。", result: "回复和这笔生意有了去处。演练只保存草稿，不会发出。", to: "/inbox", action: "去做：打开消息" },
  { title: "管案子：把下一步安排清楚", icon: Briefcase, flow: "打开案子 → 勾选待办 → 推进阶段", how: "先看卡在谁手上，再看参与方、往来和待办。补产品和数量，办完一项就勾选；丢单时核对后果并写原因。", result: "下一步和负责人明确，进展记入案子。", to: "/deals", action: "去做：跟进案子" },
  { title: "管客户：记住谁要跟进", icon: Users, flow: "查找客户 → 核对资料与往来 → 安排跟进", how: "把同一个客户的多渠道身份归到一起，补国家、备注和下次跟进日期。合并重复客户前先核对；跟进消息先起草再确认。", result: "客户资料、案子和跟进安排能一起查看。", to: "/customers", action: "去做：整理客户" },
  { title: "管供应商：核准价格和资料", icon: Factory, flow: "打开供应商 → 查有效报价 → 询价或催资料", how: "核对产品型号、外箱尺寸、毛重、价格有效期和交期。缺资料就起草询问，核实后再用于客户报价。", result: "可用成本有出处、有日期，缺什么知道去问谁。", to: "/supplier-dossiers", action: "去做：核对供应商" },
  { title: "报价与成交：核价后报价，到账后确认", icon: Receipt, flow: "案子内生成报价 → 人确认发送 → 人核对到账", how: "在案子里选产品和数量，核对成本、运费、利润和有效期，再生成报价或形式发票草稿。收到付款后查到账凭证，亲自确认收款。", result: "确认收款后案子进入已成交，继续安排备货。", to: "/deals", action: "去做：打开案子报价与收款" },
  { title: "履约：盯住备货到签收", icon: Truck, flow: "备货 → 验货 → 订舱发货 → 签收", how: "在备货与发货打开已成交案子，逐项记录负责人、约定日期和完成情况。交期或物流超期后从今日待办继续跟进。", result: "每个节点都有日期与进展，超期及时提醒。", to: "/fulfilment", action: "去做：跟进备货与发货" },
  { title: "知识与 AI 员工：有出处，再采用", icon: Bot, flow: "查资料 → 核对出处 → 引用或保存 → 审核建议", how: "在知识检索核对命中的资料、来源和时间，再引用到回复或保存为案子备注。在 AI 员工查看最近做了什么，逐条采用、修改或拒绝。", result: "资料用于当前生意，建议由你核对后采用。", to: "/search", action: "去做：查找可引用资料", extra: { to: "/roles", action: "去做：审核 AI 员工建议" } },
];

export function Guide() {
  return <>
    <div className="page-head"><div><h1 className="t-large">怎么用</h1><p>每天照这八步，把一笔生意从询盘跟到签收。</p></div><Link className="btn" to="/">回首页看流程</Link></div>
    <PageIntro page="guide" />
    <p className="callout">一笔生意就是一个案子。先确认消息是谁的，再跟进报价、收款、发货和售后。演练草稿不会发出；确认收款须由你核对到账后操作。</p>
    <ol className="queue-guide" aria-label="八步运营流程">{STEPS.map((step, index) => <li key={step.to + index}>
      <header className="queue-guide-head"><step.icon size={28} aria-hidden="true" /><h2>{index + 1}. {step.title}</h2></header>
      <p className="queue-guide-flow" aria-label={`第 ${index + 1} 步流程`}>{step.flow}</p><p>{step.how}</p><p className="queue-guide-result t-foot">做完看到：{step.result}</p>
      <div className="queue-empty-actions"><Link className="btn primary" to={step.to}>{step.action}</Link>{step.extra && <Link className="btn" to={step.extra.to}>{step.extra.action}</Link>}</div>
    </li>)}</ol>
    <p className="t-foot">如果页面提示未接入或读不到，请先按页面提示核对。这里的指引不代表对应渠道已经连通。</p>
  </>;
}
