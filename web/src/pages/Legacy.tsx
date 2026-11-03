import { ExternalLink } from "lucide-react";
import { Link } from "react-router";
import { Chip } from "../ui/bits";

type State = "replaced" | "partial" | "keep" | "pending";
interface Tool { name: string; does: string; path: string; now: string; to?: string; state: State }

const STATE: Record<State, { text: string; tone: "green" | "orange" | "plain" | "red" }> = {
  replaced: { text: "已被新版取代", tone: "green" }, partial: { text: "新版已有大部分", tone: "orange" }, pending: { text: "新版还没有，先用旧版", tone: "red" }, keep: { text: "继续保留", tone: "plain" },
};

const TOOLS: Tool[] = [
  { name: "经营总览（旧首页）", does: "旧版的首页和数据概览", path: "/v3/", now: "打开网址会自动跳到新版工作台", to: "/", state: "replaced" },
  { name: "客户运营工作台", does: "旧版的客户会话、备注、跟进", path: "/v3/workbench", now: "新版“商机沟通”“案子”已经能做；旧版的备注历史还在这里", to: "/inbox", state: "partial" },
  { name: "阿里英文草稿与运营", does: "审核 AI 写的阿里英文回复，并交给 Win10 执行发送", path: "/v3/icbu-drafts", now: "新版“商机沟通”有 AI 起草和确认发送，但“交给 Win10 执行”这条链还只在旧版", state: "pending" },
  { name: "客户跟进", does: "到期要跟进的客户和备注", path: "/v3/followups", now: "新版用“案子”“巡检提醒”替代，旧数据还在这里", to: "/deals", state: "partial" },
  { name: "询价雷达", does: "新询价的利润与毛利预估", path: "/v3/inquiries", now: "新版“报价”会算利润，但这个雷达页还没迁过来", state: "pending" },
  { name: "待审批批次", does: "批量导入的产品资料，逐批审核后应用", path: "/v3/batches", now: "新版“价格建议”“产品”能确认单条，批量审批还没迁过来", state: "pending" },
  { name: "产品库 / 资料库 / 供应商画像", does: "旧版从资料里整理出的产品、供应商和资料目录", path: "/v3/products", now: "新版“产品”“供应商”是你确认过的正式档案，数据量比旧版少；两边还没合并", to: "/products", state: "partial" },
  { name: "微信 / WhatsApp / 阿里 知识搜索", does: "按业务词搜聊天里的知识", path: "/v3/wechat-knowledge", now: "新版“知识检索”覆盖了文字资料", to: "/search", state: "replaced" },
  { name: "图片资料检索", does: "用文字找微信里收到的图片", path: "/v3/wechat-image-library", now: "新版还没有图片检索", state: "pending" },
  { name: "微信客户登记", does: "把微信客户会话登记成客户", path: "/v3/wechat-knowledge", now: "新版“待整理会话”“微信通讯录”替代；写配置的登记仍在旧版", to: "/triage", state: "partial" },
  { name: "运行与数据状态", does: "服务是否正常、模型额度", path: "/v3/operations/status", now: "后台工具，继续保留", state: "keep" },
  { name: "账号与权限", does: "登录账号、子账号管理", path: "/v3/admin", now: "继续保留", state: "keep" },
  { name: "灯具库与 CPQ 选型报价", does: "频闪灯选型、生成给客户的报价页和文件", path: "/strobes", now: "独立的报价系统，客户打开的报价链接也在这里，不动。它和新版“报价轮”的关系还要你定", state: "keep" },
];

export function LegacyPage() {
  const open = TOOLS.filter((t) => t.state === "pending").length;
  return (
    <>
      <div className="page-head"><div><h1 className="t-large">旧版工具</h1><p>以前几个版本留下的页面都在这里列清楚：哪些已经被新版取代、哪些还没搬过来。你只需要用新版；这里只是让你知道还有什么没搬完。</p></div></div>
      <div className="callout" role="note"><b>新版是唯一的入口。</b>旧首页已经自动跳到新版。还有 {open} 个旧工具的功能新版没有，我会按顺序搬过来，搬完一个就把旧入口收掉。</div>
      <div className="group" style={{ marginTop: 16 }}>
        {TOOLS.map((t) => (
          <div className="row" key={t.name} style={{ alignItems: "flex-start" }}>
            <div className="row-main" style={{ whiteSpace: "normal" }}>
              <div className="row-title" style={{ whiteSpace: "normal" }}>{t.name} <Chip tone={STATE[t.state].tone}>{STATE[t.state].text}</Chip></div>
              <div className="row-sub" style={{ whiteSpace: "normal" }}>{t.does}</div>
              <div className="row-sub" style={{ whiteSpace: "normal", color: "var(--label)" }}>{t.now}</div>
            </div>
            <div style={{ display: "flex", gap: 6, flex: "none", flexWrap: "wrap", justifyContent: "flex-end" }}>
              {t.to && <Link className="btn sm primary" to={t.to}>去新版</Link>}
              {t.state !== "replaced" && <a className="btn sm" href={t.path}><ExternalLink size={14} aria-hidden="true" />打开旧版</a>}
            </div>
          </div>
        ))}
      </div>
    </>
  );
}
