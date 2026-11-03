import type { OverviewResponse, OverviewStage } from "../types/overview";

// Entirely fictional. Used only by isolated browser checks, never as an API fallback.
export const overviewDemoNow = Date.parse("2026-11-02T06:00:00Z");
const now = overviewDemoNow / 1000;
const stage = (value: Partial<OverviewStage> & Pick<OverviewStage, "key" | "label" | "state" | "detail">): OverviewStage => ({
  last_arrival_at: null, age_seconds: null, count_24h: null, total: null,
  processed: null, pending: null, last_processed_at: null, ...value,
});

export const overviewDemo: OverviewResponse = {
  generated_at: now,
  summary: {
    state: "interrupted", headline: "阿里采集已暂停，优先恢复探针；微信正常，WhatsApp 知识整理偏慢。",
    problems: [
      { channel: "whatsapp", text: "知识整理仍有 36 条待处理。", next_step: "查看知识中枢的处理队列，确认整理任务持续运行。" },
      { channel: "alibaba", text: "阿里探针已暂停 47 分钟，新消息可能无法到达。", next_step: "检查 Win10 是否休眠，打开阿里工作台并恢复探针；刷新确认有新上报。" },
    ],
  },
  // Deliberately unsorted, to exercise red-first presentation.
  channels: [
    { key: "wechat", label: "微信", state: "ok", headline: "四个环节正常，消息持续进入知识库。", notes: ["以下均为虚构演示数据，不代表线上运行状态。"], stages: [
      stage({ key: "connected", label: "接入", state: "ok", detail: "接收服务在线。", last_arrival_at: now - 60, age_seconds: 60 }),
      stage({ key: "collecting", label: "实时采集", state: "ok", detail: "新消息持续到达。", count_24h: 1286, last_arrival_at: now - 120, age_seconds: 120 }),
      stage({ key: "stored", label: "保存", state: "ok", detail: "原始消息已保存。", count_24h: 1286, total: 42860, last_arrival_at: now - 120 }),
      stage({ key: "knowledge", label: "进知识库", state: "ok", detail: "已完成当前队列。", processed: 42500, pending: 0, last_processed_at: now - 180 }),
    ] },
    { key: "whatsapp", label: "WhatsApp", state: "slow", headline: "消息可接收，知识整理有积压；保存总量暂时未知。", notes: [], stages: [
      stage({ key: "connected", label: "接入", state: "ok", detail: "会话已登录。", last_arrival_at: now - 180, age_seconds: 180 }),
      stage({ key: "collecting", label: "实时采集", state: "ok", detail: "接收正常。", count_24h: 92, last_arrival_at: now - 240, age_seconds: 240 }),
      stage({ key: "stored", label: "保存", state: "unavailable", detail: "暂时读不到保存统计，请检查统计服务。" }),
      stage({ key: "knowledge", label: "进知识库", state: "slow", detail: "整理任务速度偏慢。", processed: 816, pending: 36, last_processed_at: now - 1200 }),
    ] },
    { key: "alibaba", label: "阿里国际站", state: "interrupted", headline: "探针超过 30 分钟未上报，实时采集已中断。", notes: ["历史保存量不能证明实时采集正常。"], stages: [
      stage({ key: "connected", label: "接入", state: "ok", detail: "账号已接入。", last_arrival_at: now - 2820, age_seconds: 2820 }),
      stage({ key: "collecting", label: "实时采集", state: "interrupted", detail: "探针暂停 47 分钟，超过 30 分钟阈值。", count_24h: 168, last_arrival_at: now - 2820, age_seconds: 2820 }),
      stage({ key: "stored", label: "保存", state: "ok", detail: "已有消息已保存。", count_24h: 168, total: 18320, last_arrival_at: now - 2820 }),
      stage({ key: "knowledge", label: "进知识库", state: "ok", detail: "已有消息已完成整理。", processed: 18000, pending: 0, last_processed_at: now - 2700 }),
    ] },
  ],
  model_budget: {
    deepseek_official: { currency: "CNY", balance_micros: 86520000, observed_at: now - 600 },
    free_pools: [
      { model: "qwen-plus（演示）", remaining_tokens: 2400000, observed_at: now - 600, expires_on: "2026-12-31" },
      { model: "qwen-turbo（演示）", remaining_tokens: null, observed_at: null, expires_on: null },
    ],
    note: "官方余额与百炼免费额度分别统计，不能合并；未知额度请在百炼控制台核对。",
  },
  history: { alibaba: {
    messages: 18320, conversations: 246,
    earliest_sent_at: "2026-06-01T01:20:00Z", latest_sent_at: now - 2820,
    by_month: [{ month: "2026-06", count: 2100 }, { month: "2026-07", count: 3980 }, { month: "2026-08", count: 4200 }, { month: "2026-09", count: 3540 }, { month: "2026-10", count: 4200 }, { month: "2026-11", count: 300 }],
    completeness: "partial", detail: "仅覆盖已加载的会话与历史文字；更早消息和未打开的会话仍需补采，不能认定全量完整。",
  } },
};
