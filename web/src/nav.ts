import {
  Archive, Languages, BookUser, Tags, Truck, Stethoscope, Bot, Sparkles, BookOpenText, Briefcase, Boxes, ClipboardCheck, Cpu, Factory, Database, FolderTree, Home, Inbox, KeyRound, Lightbulb, MonitorCog,
  LayoutDashboard, PackageSearch, Radio, Receipt, Search, Ship, Store, Users, type LucideIcon,
} from "lucide-react";

// Page metadata retains hidden URLs; NAV alone controls visible entrypoints.
// front = 运营台 (daily work), back = 后台设置 (channels, data and maintenance).
export type Area = "front" | "back";
export type NavKind = "live" | "plan" | "external";

export interface NavItem {
  key: string;
  label: string;
  path: string;
  kind: NavKind;
  area: Area;
  group: string;
  icon: LucideIcon;
  note?: string;
  /** plan items: what it will do for the owner, in business words */
  purpose?: string;
  /** plan items: what is missing today */
  needs?: string;
  /** plan items: roadmap step */
  step?: string;
}

const PAGES: NavItem[] = [
  { key: "overview", label: "总览", path: "/", kind: "live", area: "front", group: "日常工作", icon: LayoutDashboard },
  { key: "guide", label: "怎么用", path: "/guide", kind: "live", area: "front", group: "日常工作", icon: BookOpenText },
  { key: "workbench", label: "工作台", path: "/workbench", kind: "live", area: "front", group: "日常工作", icon: Home },
  { key: "triage", label: "待整理会话", path: "/triage", kind: "live", area: "back", group: "整理工具", icon: Sparkles },
  { key: "deals", label: "案子", path: "/deals", kind: "live", area: "front", group: "日常工作", icon: Briefcase },
  { key: "fulfil", label: "备货与发货", path: "/fulfilment", kind: "live", area: "front", group: "日常工作", icon: Truck },
  { key: "inbox", label: "商机沟通", path: "/inbox", kind: "live", area: "front", group: "日常工作", icon: Inbox },
  { key: "outbox", label: "草稿未发送", path: "/outbox", kind: "live", area: "front", group: "日常工作", icon: Inbox },
  { key: "customers", label: "客户", path: "/customers", kind: "live", area: "front", group: "日常工作", icon: Users },
  { key: "directory", label: "微信通讯录", path: "/directory", kind: "live", area: "back", group: "整理工具", icon: BookUser },
  { key: "classification-review", label: "待分类", path: "/classify", kind: "live", area: "back", group: "整理工具", icon: Tags },
  { key: "audit", label: "数据体检", path: "/audit", kind: "live", area: "back", group: "整理工具", icon: Stethoscope },
  { key: "search", label: "知识检索", path: "/search", kind: "live", area: "front", group: "日常工作", icon: Search },
  { key: "suppliers", label: "供应商", path: "/suppliers", kind: "live", area: "front", group: "产品与供应商", icon: Factory },
  { key: "products", label: "产品", path: "/products", kind: "live", area: "front", group: "产品与供应商", icon: Boxes },
  { key: "freight", label: "货代运价", path: "/freight", kind: "live", area: "back", group: "整理工具", icon: Ship },
  { key: "prices", label: "价格建议", path: "/prices", kind: "live", area: "back", group: "整理工具", icon: Tags },
  { key: "packing", label: "供应商包装核对", path: "/packing", kind: "live", area: "back", group: "整理工具", icon: PackageSearch },
  { key: "roles", label: "AI 员工", path: "/roles", kind: "live", area: "front", group: "学习与改进", icon: Bot },
  { key: "inspiration", label: "AI 开发灵感库", path: "/inspiration", kind: "live", area: "front", group: "学习与改进", icon: Lightbulb },
  { key: "requirements", label: "需求与验收", path: "/requirements", kind: "live", area: "back", group: "整理工具", icon: ClipboardCheck },
  {
    key: "quotes", label: "报价与订单", path: "/quotes", kind: "plan", area: "front", group: "规划中", icon: Receipt,
    purpose: "客户问价时，自动算出 FOB、DDP 到门价，并提醒产品加运费是否倒挂；报价后跟进订单、物流和收款。",
    needs: "供应商装箱尺寸和毛重要先核准，再加上物流线路和运价。",
    step: "第 2 阶段",
  },
  {
    key: "listing", label: "发品", path: "/listing", kind: "plan", area: "front", group: "规划中", icon: Store,
    purpose: "从核准过的产品资料自动生成阿里国际站和独立站的商品草稿，你审核后发布。",
    needs: "产品资料先核准完整，再接发品规则和图片。",
    step: "第 5 阶段",
  },

  { key: "system-home", label: "系统总览", path: "/system", kind: "live", area: "back", group: "系统", icon: MonitorCog },
  { key: "channels", label: "渠道接入", path: "/system/channels", kind: "live", area: "back", group: "渠道与数据", icon: Radio },
  { key: "alibaba-open", label: "阿里订单接入", path: "/system/alibaba-open", kind: "live", area: "back", group: "渠道与数据", icon: Receipt },
  { key: "pipeline", label: "数据管道健康", path: "/system/pipeline", kind: "live", area: "back", group: "渠道与数据", icon: Database },
  { key: "language", label: "语言设置", path: "/system/language", kind: "live", area: "back", group: "设置", icon: Languages },
  { key: "knowledge", label: "知识中枢", path: "/system/knowledge", kind: "live", area: "back", group: "渠道与数据", icon: BookOpenText },
  { key: "legacy", label: "旧版工具", path: "/system/legacy", kind: "live", area: "back", group: "系统", icon: Archive },
  { key: "legacy-status", label: "运行与数据状态", path: "/v3/operations/status", kind: "external", area: "back", group: "系统", icon: Database },
  { key: "admin", label: "账号与权限", path: "/v3/admin", kind: "external", area: "back", group: "设置", note: "旧后台", icon: KeyRound },
  {
    key: "classify", label: "新群与联系人分类", path: "/system/classify", kind: "plan", area: "back", group: "规划中", icon: FolderTree,
    purpose: "每天新出现的群和联系人，自动判断是供应商、客户还是物流，你只确认不确定的。",
    needs: "群和联系人的历史消息已在库里，需要分类规则和确认界面。",
    step: "第 3 阶段",
  },
  {
    key: "models", label: "模型与任务", path: "/system/models", kind: "plan", area: "back", group: "规划中", icon: Cpu,
    purpose: "看各国产模型的剩余额度、到期时间，以及最近跑了哪些整理任务。",
    needs: "额度状态命令已可用，需要接成页面。",
    step: "第 1.5 阶段",
  },
];

// Fixed business order; hidden pages never become navigation by registering a route.
const ORDER = [
  "overview", "inbox", "deals", "customers", "suppliers", "products", "fulfil", "search", "roles",
  "channels", "pipeline", "alibaba-open", "knowledge", "language", "admin",
  "triage", "classification-review", "directory", "audit", "freight", "prices", "packing", "requirements",
];
export const NAV: NavItem[] = ORDER.map((key) => {
  const page = PAGES.find((item) => item.key === key);
  if (!page) throw new Error(`Missing navigation page: ${key}`);
  return { ...page, group: page.area === "front" ? "运营台" : page.group };
});
export const PLANNED_PAGES = PAGES.filter((item) => item.kind === "plan");
export const findNav = (pathname: string) => {
  const path = pathname === "/overview" ? "/" : pathname;
  return PAGES.find((item) => item.path === path && item.kind !== "external")
    ?? PAGES.find((item) => item.path !== "/" && item.kind === "live" && path.startsWith(item.path + "/"));
};
export const areaOf = (pathname: string): Area => findNav(pathname)?.area ?? (pathname.startsWith("/system") ? "back" : "front");
