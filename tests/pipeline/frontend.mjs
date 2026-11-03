// Browser integration over intercepted fictional responses; no HTTP server or real channel.
import assert from "node:assert/strict";
import { readFile, mkdir } from "node:fs/promises";
import { resolve } from "node:path";
import { test, after } from "node:test";
import { chromium } from "../../web/node_modules/playwright-core/index.mjs";

const now = Date.parse("2026-10-31T04:30:00Z") / 1000;
const keys = ["alibaba_inbound", "alibaba_probe", "wechat_messages", "wechat_callbacks", "whatsapp_events", "whatsapp_session", "whatsapp_messages"];
const labels = ["阿里入站", "阿里心跳", "微信消息", "微信回调", "WhatsApp 桥接服务（辅助）", "WhatsApp 会话状态", "WhatsApp 消息"];
const data = {
  generated_at: now, summary: "三个渠道：2 正常，1 断流", notes: ["虚构数据，仅用于页面验证。"],
  scorecard: ["alibaba", "wechat", "whatsapp"].map((channel, index) => ({ channel, label: ["阿里国际站", "微信", "WhatsApp"][index],
    result: "unknown", result_label: "未知", metrics: [{ key: "fictional-unknown", name: index ? "消息最后到达" : "A1 心跳连续性", target: index ? "能够核对消息接收时间" : "24 小时到达率至少 99%",
      actual: "未知", result: "unknown", result_label: "未知", source: "虚构计数来源", method: "缺少逐次流水，不能当作达标。", required: true }] })),
  failed_jobs: { status: "not_connected", count: null, reason: "后台作业失败清单未接入，请管理员接入只读的服务状态读取。", items: [] },
  channels: ["alibaba", "wechat", "whatsapp"].map((channel, i) => ({ channel, label: ["阿里国际站", "微信", "WhatsApp"][i], status: i ? "ok" : "interrupted", status_label: i ? "正常" : "断流",
    coverage: ["attachments", "translations"].map((key, j) => ({ key, label: j ? "已翻译会话占比" : "带附件消息占比", status: "not_connected", numerator: null, denominator: null, ratio: null, reason: "未接入，等待统计接口。" })) })),
  sources: keys.map((key, i) => ({ key, channel: key.split("_")[0], label: labels[i], status: i < 2 ? "interrupted" : "ok", status_label: i < 2 ? "断流" : "正常",
    last_seen_at: now - (i === 1 ? 3 * 86400 : 10), time_basis: i === 2 ? "message_time" : i === 4 ? "service_event" : i === 5 ? "session_status" : "arrival", count_1h: i === 1 || i === 5 ? null : 4, count_24h: i === 1 || i === 5 ? null : 48,
    hourly: i === 1 || i === 5 ? null : Array.from({ length: 48 }, (_, index) => ({ start_at: now - 1800 - (47 - index) * 3600, count: index % 9 })),
    ...(i === 5 ? { session_status: "WORKING", session_status_label: "已登录可用", last_working_at: now - 10, failed_count_1h: 0, failed_count_24h: 0, has_me: true, restart_storm: false } : {}),
    reason: i === 0 ? "近 24 小时只有历史在进，没有实时消息；实时消息长时间没有更新，已超过断流阈值。" : i === 1 ? "心跳长时间没有更新，已超过断流阈值。" : "最近有更新，未超过变慢阈值。",
    next_step: i < 2 ? "到 Win10 检查：浏览器是否开着并登录阿里消息页，脚本是否暂停，电脑是否休眠；核对心跳上报的脚本版本。" : "继续观察，有消息后核对到达时间。", notes: [],
    mix: i ? null : { live: { count_24h: 0, ratio_24h: 0, last_arrival_at: now - 3 * 86400 }, history: { count_24h: 48, ratio_24h: 1, last_arrival_at: now - 10 }, unknown: { count_24h: 0, ratio_24h: 0, last_arrival_at: null } },
    script_version: i === 1 ? "0.3.5.4" : null, thresholds: { slow_seconds: 7200, interrupted_seconds: 21600, ...(i < 2 ? { day: { start: "08:00", end: "24:00", night_multiplier: 4 } } : {}) } })),
};
const browser = await chromium.launch({ args: ["--no-sandbox", "--disable-dev-shm-usage"] });
after(() => browser.close());

async function fixture(width = 1440) {
  const context = await browser.newContext({ viewport: { width, height: 1000 } });
  const page = await context.newPage();
  await page.clock.install({ time: new Date(now * 1000) });
  const state = { count: 0, mode: "normal", errors: [], requests: [] };
  page.on("pageerror", (error) => state.errors.push(error.message));
  await page.route("**/*", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    state.requests.push([request.method(), url.pathname]);
    assert.equal(url.origin, "http://pipeline.test");
    assert.equal(request.method(), "GET");
    if (url.pathname === "/api/v1/pipeline/health") {
      state.count++;
      return route.fulfill({ status: state.mode === "error" ? 503 : 200, contentType: "application/json", body: JSON.stringify(state.mode === "error" ? { error: "虚构服务暂时不可用" } : state.mode === "empty" ? { ...data, sources: [], summary: "暂无统计" } : data) });
    }
    if (url.pathname === "/api/v1/inbox/summary") return route.fulfill({ contentType: "application/json", body: JSON.stringify({ ready: true, waiting_total: 0, draft_new: 0 }) });
    const file = url.pathname.startsWith("/app/assets/") ? resolve("web/dist", url.pathname.slice(5)) : resolve("web/dist/index.html");
    assert.ok(file.startsWith(resolve("web/dist") + "/"));
    const contentType = file.endsWith(".js") ? "text/javascript" : file.endsWith(".css") ? "text/css" : "text/html";
    return route.fulfill({ contentType, body: await readFile(file), headers: { "Content-Security-Policy": "default-src 'self'; img-src 'self' data:; script-src 'self'; style-src 'self'; frame-ancestors 'none'" } });
  });
  await page.goto("http://pipeline.test/app/system/pipeline", { waitUntil: "domcontentloaded" });
  await page.getByText("三个渠道：2 正常，1 断流", { exact: true }).waitFor();
  return { page, state, context };
}

for (const width of [1440, 390, 320]) test(`虚构页面 ${width} 像素布局与小时图`, async () => {
  const { page, state, context } = await fixture(width);
  try {
    assert.equal(await page.locator(".pipeline-card[aria-labelledby]").count(), 7);
    assert.equal(await page.locator(".pipeline-interrupted").count(), 2);
    assert.equal(await page.locator(".pipeline-chart rect").count(), 5 * 48);
    assert.equal(await page.getByRole("heading", { name: "验收达标", exact: true }).count(), 1);
    assert.equal(await page.locator(".pipeline-score-channel").count(), 3);
    assert.equal(await page.locator(".pipeline-scorecard .chip").filter({ hasText: "达标" }).count(), 0);
    assert.ok((await page.locator(".pipeline-jobs").innerText()).includes("未接入"));
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    const coverageColumns = await page.locator(".pipeline-coverage-row").first().evaluate((element) => getComputedStyle(element).gridTemplateColumns.split(" ").length);
    assert.equal(coverageColumns, width <= 760 ? 1 : 3);
    await page.locator(".pipeline-chart summary").first().click();
    assert.equal(await page.locator(".pipeline-chart table").first().locator("tbody tr").count(), 48);
    await mkdir("/tmp/pipeline-preview", { recursive: true });
    await page.screenshot({ path: `/tmp/pipeline-preview/${width}.png`, fullPage: true });
    assert.deepEqual(state.errors, []);
  } finally { await context.close(); }
});

test("手动刷新、60 秒自动刷新、失败保留旧结果和恢复", async () => {
  const { page, state, context } = await fixture();
  try {
    const start = state.count;
    await page.getByRole("button", { name: "刷新", exact: true }).click();
    await page.getByRole("button", { name: "刷新", exact: true }).waitFor();
    assert.equal(state.count, start + 1);
    await page.clock.fastForward(60_001);
    await page.getByRole("button", { name: "刷新", exact: true }).waitFor();
    assert.equal(state.count, start + 2);
    state.mode = "error";
    await page.getByRole("button", { name: "刷新", exact: true }).click();
    await page.getByRole("alert").waitFor();
    assert.ok((await page.getByRole("alert").innerText()).includes("上次结果"));
    assert.equal(await page.locator(".pipeline-card[aria-labelledby]").count(), 7);
    state.mode = "normal";
    await page.getByRole("button", { name: "刷新", exact: true }).click();
    await page.getByRole("alert").waitFor({ state: "hidden" });
    assert.deepEqual(state.errors, []);
  } finally { await context.close(); }
});

test("空态说明与未接入覆盖率", async () => {
  const { page, state, context } = await fixture();
  try {
    assert.equal(await page.locator(".pipeline-coverage b").filter({ hasText: "未接入" }).count(), 6);
    state.mode = "empty";
    await page.getByRole("button", { name: "刷新", exact: true }).click();
    await page.getByText("暂时没有管道统计", { exact: true }).waitFor();
    assert.equal(await page.locator(".pipeline-card[aria-labelledby]").count(), 0);
    assert.deepEqual(state.errors, []);
  } finally { await context.close(); }
});

test("失败记分卡、登录风暴和后台作业时间可见", async () => {
  const { page, state, context } = await fixture();
  const originalCard = data.scorecard[2];
  const originalSession = data.sources[5];
  const originalJobs = data.failed_jobs;
  try {
    data.scorecard[2] = { ...originalCard, result: "fail", result_label: "不达标", metrics: [{ ...originalCard.metrics[0], name: "会话是否已登录可用", result: "fail", result_label: "不达标", actual: "一直在失败重试" }] };
    data.sources[5] = { ...originalSession, status: "interrupted", status_label: "断流", session_status: "STARTING", session_status_label: "正在启动", restart_storm: true, has_me: false, failed_count_1h: 25, failed_count_24h: 30,
      reason: "WhatsApp 会话没有登录成功，一直在失败重试。", next_step: "到渠道接入页查看是否需要重新扫码；请管理员查看 WAHA 容器。" };
    data.failed_jobs = { status: "ready", count: 1, reason: "以下后台作业失败，请管理员核对服务状态。", items: [{ unit: "fictional-worker.service", failed_at: now - 60 }] };
    await page.getByRole("button", { name: "刷新", exact: true }).click();
    await page.getByText("一直在失败重试", { exact: false }).first().waitFor();
    assert.ok((await page.locator(".pipeline-score-channel").last().innerText()).includes("不达标"));
    const sessionCard = page.locator("section[aria-labelledby='pipeline-whatsapp_session']");
    assert.ok((await sessionCard.innerText()).includes("重启风暴：是"));
    assert.ok((await sessionCard.innerText()).includes("WAHA 容器"));
    assert.ok((await page.locator(".pipeline-jobs").innerText()).includes("fictional-worker.service"));
    assert.ok((await page.locator(".pipeline-jobs").innerText()).includes("失败时间：10/31"));
    assert.deepEqual(state.errors, []);
  } finally {
    data.scorecard[2] = originalCard;
    data.sources[5] = originalSession;
    data.failed_jobs = originalJobs;
    await context.close();
  }
});
