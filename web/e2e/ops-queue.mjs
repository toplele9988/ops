// Real clicks at desktop/mobile widths. All state lives inside browser fixtures.
import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";
import { chromium } from "playwright-core";
import { startLocalServer } from "./local-server.mjs";
import { installQueueFixtures, queueResponse } from "./fixtures.queue.mjs";
import { checkOverview } from "./overview.mjs";

const out = new URL("../../artifacts/queue/", import.meta.url).pathname;
await mkdir(out, { recursive: true });
const server = await startLocalServer();
const browser = await chromium.launch({ ...(process.env.CHROME ? { executablePath: process.env.CHROME } : {}), args: ["--no-sandbox"] });
const viewports = [["桌面", { width: 1440, height: 900 }], ["手机", { width: 400, height: 860 }]];
const results = [];
try {
  for (const [name, viewport] of viewports) {
    const context = await browser.newContext({ viewport });
    const page = await context.newPage();
    const problems = [];
    const state = await installQueueFixtures(page, problems);
    page.on("pageerror", (error) => problems.push(error.message));
    page.on("console", (message) => { if (message.type() === "error" && !(state.failQueue && message.text().includes("503"))) problems.push(message.text()); });
    await context.route("**/*", async (route) => {
      if (new URL(route.request().url()).origin !== server.base) { problems.push("请求离开演练服务"); return route.abort(); }
      return route.fallback();
    });
    const queue = () => page.getByTestId("today-queue");
    const count = async (number) => { await page.waitForFunction((n) => document.querySelectorAll(".queue-item").length === n, number); assert.equal(await page.locator(".queue-item").count(), number); };
    const home = async () => { await page.goto(server.base + "/app/"); await queue().getByRole("heading", { name: "今天要做的事" }).waitFor(); await count(state.items.length); };
    await page.clock.install({ time: new Date("2030-01-01T08:00:00+08:00") });
    await home();
    assert.equal(await page.locator(".queue-item").first().getAttribute("data-kind"), "pipeline");
    await page.getByRole("button", { name: "知道了，不再提示" }).click();
    await page.reload();
    await count(7);
    assert.equal(await page.getByRole("complementary", { name: "新手提示" }).count(), 0);
    await page.screenshot({ path: `${out}/${name}-今日待办.png`, fullPage: true });
    await page.locator('[data-kind="pipeline"]').getByRole("button", { name: "稍后 · 2 小时" }).click();
    await count(6);
    assert.match(await queue().getByRole("status").innerText(), /2 小时后/);
    await page.locator('[data-kind="payment"]').getByRole("button", { name: "已处理", exact: true }).click();
    await count(5);
    assert.equal(state.calls.filter((c) => c.method === "POST").length, 0, "Feedback must never confirm money or mutate business records");
    await page.reload();
    await count(5);
    await queue().getByRole("button", { name: /恢复本机暂存的/ }).click();
    await count(7);

    await page.locator('[data-kind="buyer_reply"]').getByRole("link", { name: "去回复买家" }).click();
    assert.equal(new URL(page.url()).searchParams.get("conversation"), "demo-alibaba");
    await page.locator(".thread-body .bubble").first().waitFor();
    if (viewport.width < 900) await page.getByRole("button", { name: "客户资料与 AI 建议 · 展开操作" }).click();
    await page.getByRole("button", { name: "AI 起草回复" }).click();
    await page.waitForFunction(() => document.querySelector(".composer textarea")?.value.includes("演示规格正在核对"));
    assert.equal(state.outbox.length, 1);
    assert.equal(state.outbox[0].dry_run, true);
    assert.equal(state.outbox[0].status, "draft");
    assert.match(await page.getByRole("textbox", { name: "回复内容（可编辑）", exact: true }).inputValue(), /演示规格正在核对/);

    await page.goBack();
    await count(7); // Drafting alone is not a sent reply.
    await page.locator('[data-kind="buyer_reply"]').getByRole("button", { name: "已处理", exact: true }).click();
    await count(6);
    state.items.find((i) => i.kind === "buyer_reply").revision = "fictional-new-message";
    await queue().getByRole("button", { name: "更新待办", exact: true }).click();
    await count(7);

    await page.locator('[data-kind="logistics"]').getByRole("link", { name: "去跟进物流" }).click();
    assert.equal(new URL(page.url()).pathname, "/app/deals/1");
    await page.getByRole("button", { name: "记录生产中", exact: true }).click();
    const edit = page.locator("dialog[open]");
    await edit.getByLabel("进展", { exact: true }).selectOption("done");
    await edit.getByRole("button", { name: "保存节点", exact: true }).click();
    await page.waitForFunction(() => !document.querySelector("dialog[open]"));
    assert.equal(state.nodeDone, true);
    assert.ok(state.calls.some((c) => c.method === "POST" && c.path === "/api/v1/deals/1/fulfilment/production/update" && c.body.status === "done"));
    await page.goBack();
    await count(6);
    assert.equal(await page.locator('[data-kind="logistics"]').count(), 0);
    while (await page.locator(".queue-item").count()) await page.locator(".queue-item").first().getByRole("button", { name: "已处理", exact: true }).click();
    await queue().getByRole("heading", { name: "今天没有待办", exact: true }).waitFor();
    await page.screenshot({ path: `${out}/${name}-全部处理完.png`, fullPage: true });
    await queue().getByRole("button", { name: /恢复本机暂存的/ }).click();
    await count(6);
    await page.locator('[data-kind="pipeline"]').getByRole("button", { name: "稍后 · 2 小时" }).click();
    await count(5);
    await page.clock.setSystemTime(new Date("2030-01-01T10:01:00+08:00"));
    await page.clock.runFor(16_000);
    await count(6);
    state.failQueue = true;
    await queue().getByRole("button", { name: "更新待办", exact: true }).click();
    await queue().getByRole("alert").waitFor();
    assert.equal(await queue().getByRole("heading", { name: "今天没有待办", exact: true }).count(), 0);
    assert.equal(await page.locator(".queue-item").first().getByRole("button", { name: "已处理", exact: true }).isDisabled(), true);
    state.failQueue = false;
    state.complete = false;
    state.unavailable = ["虚构会话来源未核对齐"];
    state.items = [];
    await queue().getByRole("button", { name: "更新待办", exact: true }).click();
    await queue().getByRole("heading", { name: "已读到的待办已处理，请核对其余来源" }).waitFor();
    assert.equal(await queue().getByRole("heading", { name: "今天没有待办", exact: true }).count(), 0);
    state.complete = true;
    state.unavailable = [];
    await page.goto(server.base + "/app/workbench");
    await page.getByRole("complementary", { name: "这页怎么用" }).waitFor();
    await page.goto(server.base + "/app/guide");
    assert.equal(await page.locator(".queue-guide > li").count(), 8);
    const destinations = ["/app", "/app/inbox", "/app/deals", "/app/customers", "/app/suppliers", "/app/deals", "/app/fulfilment", "/app/search"];
    for (let step = 0; step < 8; step++) {
      const link = page.locator(".queue-guide > li").nth(step).getByRole("link", { name: /^去做/ }).first();
      const bounds = await link.boundingBox();
      assert.ok(bounds.width <= viewport.width && bounds.height >= 44, "Action fits and is tappable");
      await link.click();
      assert.equal(new URL(page.url()).pathname, destinations[step]);
      await page.goBack();
      await page.locator(".queue-guide").waitFor();
    }
    await page.getByRole("link", { name: "去做：审核 AI 员工建议" }).click();
    assert.equal(new URL(page.url()).pathname, "/app/roles");
    await page.goBack();
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 2));
    await page.screenshot({ path: `${out}/${name}-八步指引.png`, fullPage: true });
    assert.equal(state.calls.filter((c) => c.method === "POST").length, 2, "Only draft and fulfilment node completion are allowed");
    assert.deepEqual(problems, []);
    results.push({ name, status: "PASS", posts: state.calls.filter((c) => c.method === "POST"), dryRunDrafts: state.outbox.length });
    console.log(`PASS ${name}: 7 类待办、深链接、dry-run 草稿、履约节点副作用、反馈与恢复、稍后到期、失败与不完整来源、8 步跳转`);
    await context.close();
  }
  // Add only the new endpoint; execute the unmodified existing overview assertions.
  const regressionBrowser = { newContext: async (options) => {
    const context = await browser.newContext(options);
    const newPage = context.newPage.bind(context);
    context.newPage = async () => {
      const page = await newPage();
      const register = page.route.bind(page);
      page.route = async (pattern, handler, options) => {
        await register(pattern, handler, options);
        if (pattern === "**/api/**") await register("**/api/v1/ops/queue", (route) => route.fulfill({ json: queueResponse([]) }));
      };
      return page;
    };
    return context;
  } };
  const regression = await checkOverview({ browser: regressionBrowser, base: server.base, out, viewports });
  assert.deepEqual(regression.failures, []);
  console.log(`PASS 原总览 ${regression.checked} 个桌面/手机边界场景`);
  results.push({ name: "既有总览回归", checked: regression.checked, failures: regression.failures });
  await writeFile(out + "result.json", JSON.stringify({ mode: "loopback static build + fictional in-memory browser fixtures", results }, null, 2));
} catch (error) {
  await writeFile(out + "failure.txt", error.stack ?? String(error));
  throw error;
} finally {
  await browser.close();
  await server.close();
}
