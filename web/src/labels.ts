// Business wording for the values the API stores as codes.
export type Tone = "green" | "orange" | "red" | "blue" | "purple" | "plain";

export const RELEVANCE: Record<string, { text: string; tone: Tone }> = {
  ai_dev: { text: "AI 开发", tone: "blue" },
  industry: { text: "同行与产品", tone: "purple" },
  business: { text: "外贸运营", tone: "green" },
  other: { text: "与业务无关", tone: "plain" },
  unknown: { text: "待整理", tone: "orange" },
};

export const STATUS: Record<string, string> = {
  new: "待评估",
  evaluating: "评估中",
  trying: "试用中",
  adopted: "已采用",
  ignored: "已忽略",
};

export const ITEM_KIND: Record<string, string> = { skill: "skill", project: "开源项目", tool: "工具", plugin: "插件", method: "方法" };

export const CONFIDENCE: Record<string, { text: string; tone: Tone }> = {
  high: { text: "依据充分", tone: "green" },
  medium: { text: "依据一般", tone: "orange" },
  low: { text: "需看原视频", tone: "orange" },
};

export const PACK_STATUS: Record<string, { text: string; tone: Tone }> = {
  ready: { text: "资料齐全", tone: "green" },
  estimable: { text: "可估算", tone: "blue" },
  partial: { text: "资料不全", tone: "orange" },
  missing: { text: "暂无资料", tone: "plain" },
  conflict: { text: "资料有冲突", tone: "red" },
};
