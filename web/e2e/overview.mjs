import assert from "node:assert/strict";
import { readFile, mkdir } from "node:fs/promises";
import ts from "typescript";
import { installFixtures } from "./fixtures.mjs";
import { checkBusinessFlow } from "./fixtures.flow.mjs";

// Compile the same typed, fictional fixture used during development; no live data is saved.
const source = await readFile(new URL("../src/fixtures/overview.demo.ts", import.meta.url), "utf8");
const fixtureModule = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
const { overviewDemo, overviewDemoNow } = await import(`data:text/javascript;base64,${Buffer.from(fixtureModule).toString("base64")}`);
const clone = () => structuredClone(overviewDemo);

export async function checkOverview({ browser, base, out, viewports }) {
  const failures = [];
  let checked = 0;
  if (out) await mkdir(out, { recursive: true });
  const cases = ["演示", "404", "500", "断网", "空值", "空记录", "未接入", "缺失环节", "余额精度", "刷新失败", "加载"];
  for (const [vname, viewport] of viewports) {
    for (const name of cases) {
      const context = await browser.newContext({ viewport, timezoneId: "America/Los_Angeles" });
      const page = await context.newPage();
      const problems = [];
      page.on("pageerror", (error) => problems.push(`脚本异常：${error.message}`));
      page.on("console", (message) => {
        if (message.type() !== "error") return;
        if (/Failed to load resource:.*(?:status of (?:404|500)|net::ERR_FAILED)/.test(message.text()) && ["404", "500", "断网", "刷新失败"].includes(name)) return;
        problems.push(`浏览器报错：${message.text().slice(0, 180)}`);
      });
      await installFixtures(page, problems);
      await page.clock.install({ time: new Date(overviewDemoNow) });
      await page.route("**/api/v1/inbox/summary", (route) => route.fulfill({ json: { ready: false, waiting_total: 0, draft_new: 0 } }));
      let requests = 0;
      let release;
      const loading = new Promise((resolve) => { release = resolve; });
      await page.route("**/api/v1/overview", async (route) => {
        requests += 1;
        if (name === "加载") await loading;
        if (name === "断网") return route.abort("failed");
        if (name === "404" || name === "500" || (name === "刷新失败" && requests > 1)) return route.fulfill({ status: name === "404" ? 404 : 500, json: { error: "synthetic failure" } });
        const data = clone();
        if (name === "空值") {
          data.model_budget.deepseek_official = null;
          for (const pool of data.model_budget.free_pools) { pool.remaining_tokens = null; pool.observed_at = null; pool.expires_on = null; }
          const history = data.history.alibaba;
          Object.assign(history, { messages: null, conversations: null, earliest_sent_at: null, latest_sent_at: null, completeness: null, by_month: [{ month: "2026-10", count: null }] });
          data.generated_at = null;
          for (const channel of data.channels) for (const stage of channel.stages) for (const key of ["last_arrival_at", "age_seconds", "count_24h", "total", "processed", "pending", "last_processed_at"]) stage[key] = null;
        }
        if (name === "空记录") { data.channels = []; data.summary.problems = []; data.model_budget.free_pools = []; data.history.alibaba.by_month = []; }
        if (name === "未接入") {
          data.channels[0].state = "not_connected";
          data.channels[0].stages[0].state = "not_connected";
          data.channels[0].stages[1].state = "never";
          data.channels[0].stages.pop();
        }
        if (name === "缺失环节") data.channels[0].stages.pop();
        if (name === "余额精度") data.model_budget.deepseek_official.balance_micros = 1;
        return route.fulfill({ json: data });
      });
      try {
        await page.goto(`${base}/app/`, { waitUntil: "load" });
        await page.getByRole("heading", { name: "流程总览", exact: true }).waitFor();
        if (name === "加载") {
          await page.locator('.overview-page > [aria-busy="true"]').waitFor();
          release();
        }
        if (!["404", "500", "断网"].includes(name)) {
          await page.locator(".overview-channel, .overview-error, .overview-channels").first().waitFor();
          await page.locator(".overview-more, .overview-channel-details").evaluateAll(nodes => nodes.forEach(node => { node.open = true; }));
        }
        if (["404", "500", "断网"].includes(name)) {
          const state = name === "404" ? "not-deployed" : "read-failed";
          await page.locator(`[data-overview-error="${state}"]`).waitFor();
          assert.match(await page.locator("[role=alert]").innerText(), name === "404" ? /接口尚未部署/ : /读取失败/);
          assert.equal(await page.locator(".overview-channel").count(), 0, "Failure must not show demo channels");
        } else if (name === "空记录") {
          await page.getByText("未知 · 暂无渠道状态", { exact: true }).waitFor();
          assert.match(await page.locator(".overview-budget").innerText(), /暂无免费额度记录/);
          assert.match(await page.locator(".overview-history").innerText(), /暂无按月统计/);
        } else {
          await page.locator(".overview-channel").first().waitFor();
          assert.equal(await page.locator(".overview-channel").first().getAttribute("data-channel"), "alibaba", "Red channel must be first");
          assert.match(await page.locator(".overview-problems li").first().innerText(), /阿里国际站[\s\S]*下一步[\s\S]*Win10/);
          assert.equal(await page.locator(".overview-stage").count(), 12);
          if (name === "空值") {
            for (const value of await page.locator(".overview-metrics dd").allInnerTexts()) assert.equal(value, "未知");
            assert.match(await page.locator(".overview-balance").innerText(), /未知/);
            assert.match(await page.locator(".overview-pools").innerText(), /未知/);
          } else if (name === "未接入") {
            const channel = page.locator('[data-channel="wechat"]');
            assert.match(await channel.innerText(), /未接入[\s\S]*尚无记录/);
            assert.match(await channel.locator('[data-stage="knowledge"]').innerText(), /未知/);
          } else if (name === "缺失环节") {
            const channel = page.locator('[data-channel="wechat"]');
            assert.equal(await channel.getAttribute("data-state"), "unavailable");
            assert.match(await channel.locator('[data-stage="knowledge"]').innerText(), /未知/);
          } else if (name === "余额精度") {
            assert.match(await page.locator(".overview-balance").innerText(), /0.000001 CNY/);
          } else {
            const alibaba = page.locator('[data-channel="alibaba"] [data-stage="collecting"]');
            assert.match(await alibaba.innerText(), /已中断[\s\S]*47 分钟/);
            assert.match(await alibaba.innerText(), /13:13 北京时间[\s\S]*47 分钟前/);
            assert.match(await page.locator('[data-channel="whatsapp"] [data-stage="stored"]').innerText(), /未知/);
            assert.equal(await page.locator('[data-channel="wechat"] [data-stage="knowledge"] dd').last().innerText(), "0", "Known zero must stay zero");
            assert.match(await page.locator(".overview-balance").innerText(), /86.52 CNY/);
          }
          if (name === "刷新失败") {
            await page.getByRole("button", { name: "刷新", exact: true }).click();
            await page.locator('[data-overview-error="read-failed"]').waitFor();
            assert.match(await page.locator("[role=alert]").innerText(), /上次读取结果/);
            assert.equal(await page.locator(".overview-channel").count(), 3);
          }
          const flow = page.locator(".overview-flow-scroll").first();
          if (viewport.width < 760) {
            await flow.evaluate((element) => { element.scrollLeft = element.scrollWidth; });
            assert.ok(await flow.evaluate((element) => element.scrollLeft > 0), "Mobile pipeline should scroll");
            await flow.evaluate((element) => { element.scrollLeft = 0; });
          }
        }
        assert.ok(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth <= 2), "No document overflow");
        const firstNav = page.locator(".nav-item").first();
        assert.equal(await firstNav.innerText(), "首页");
        assert.equal(await firstNav.getAttribute("aria-current"), "page");
        if (out && ["演示", "404", "500"].includes(name)) await page.screenshot({ path: `${out}/${vname}-全局总览-${name}.png`, fullPage: true });
      } catch (error) {
        problems.push(String(error));
      } finally {
        release();
        await context.close();
      }
      checked += 1;
      if (problems.length) failures.push({ vname, name: `全局总览-${name}`, path: "/overview", problems });
    }
  }
  const flow = await checkBusinessFlow({ browser, base, out, viewports });
  return { checked: checked + flow.checked, failures: [...failures, ...flow.failures] };
}
