// Fictional workflow counts. Interception never forwards requests to a backend.
import assert from "node:assert/strict";
import { installFixtures } from "./fixtures.mjs";

const profileStages = { potential: 5, quoted: 4, customer: 2, after_sales: 1, dormant: 3 };
const counts = { inquiry: 2, sourcing: 1, quoting: 4, negotiating: 2, awaiting_payment: 3, paid: 2, inspection: 1, shipped: 1, delivered: 1, aftersales: 2, reorder: 1 };
const navigation = [
  ["messages", "待回复会话", "1", "/inbox?state=waiting&role=customer", "/api/v1/inbox/conversations", { state: "waiting", role: "customer" }],
  ["identity", "待确认归属", "2", "/triage?status=pending", "/api/v1/triage", { status: "pending" }],
  ["prospects", "问过价的客户", "5", "/customers?stage=potential", "/api/v1/profiles", { kind: "customer" }],
  ["prospects", "询盘案子", "2", "/deals?status=open&stage=inquiry", "/api/v1/deals", { status: "open", stage: "inquiry" }],
  ["quoted", "报过价的客户", "4", "/customers?stage=quoted", "/api/v1/profiles", { kind: "customer" }],
  ["payment", "等客户付款", "3", "/deals?status=open&stage=awaiting_payment", "/api/v1/deals", { status: "open", stage: "awaiting_payment" }],
  ["shipping", "备货案子", "2", "/deals?status=all&stage=paid", "/api/v1/deals", { status: "all", stage: "paid" }],
  ["shipping", "验货案子", "1", "/deals?status=all&stage=inspection", "/api/v1/deals", { status: "all", stage: "inspection" }],
  ["support", "售后案子", "2", "/deals?status=all&stage=aftersales", "/api/v1/deals", { status: "all", stage: "aftersales" }],
];

export async function installFlowFixtures(page, problems, scenario) {
  const state = await installFixtures(page, problems);
  const prototype = structuredClone(state.deals.deals[0]);
  let sequence = 1000;
  const seeded = scenario === "空记录" ? [] : Object.entries(counts).flatMap(([stage, size]) => Array.from({ length: size }, () => ({
    ...structuredClone(prototype), id: ++sequence, title: `虚构流程案子 ${sequence}`, stage,
    status: ["paid", "inspection", "shipped", "delivered", "aftersales", "reorder"].includes(stage) ? "won" : "open",
  })));
  state.deals.deals.splice(0, state.deals.deals.length, ...seeded);
  const reads = new Map();
  let release;
  const gate = new Promise(resolve => { release = resolve; });
  const requests = [];
  page.on("request", request => { if (new URL(request.url()).pathname.startsWith("/api/")) requests.push(new URL(request.url())); });
  await page.route("**/api/v1/**", async route => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    // The message list asks for already-cached translations; that is a read, not a change.
    if (route.request().method() === "POST" && path === "/api/v1/lang/translate" && /"mode":"(cached|preview)"|mode=(cached|preview)/.test(route.request().postData() || "")) return route.fallback();
    if (route.request().method() !== "GET") { problems.push("Flow acceptance must be read-only: " + path); return route.abort(); }
    const isAftersalesCount = path === "/api/v1/deals" && url.searchParams.get("page_size") === "1" && url.searchParams.get("stage") === "aftersales";
    const source = ["/api/v1/inbox/summary", "/api/v1/triage", "/api/v1/deals/summary", "/api/v1/fulfilment/board", "/api/v1/profiles"].includes(path) || isAftersalesCount;
    if (!source) return route.fallback();
    reads.set(path, (reads.get(path) ?? 0) + 1);
    if (scenario === "加载") await gate;
    if (["接口失败", "404"].includes(scenario) || scenario === "刷新失败" && reads.get(path) > 1 || scenario === "局部失败" && path === "/api/v1/fulfilment/board") {
      return route.fulfill({ status: scenario === "404" ? 404 : 500, json: { error: "虚构统计读取失败" } });
    }
    if (scenario === "空值") {
      const data = path.endsWith("/summary") ? path.includes("inbox") ? { ready: true, waiting_total: null } : { by_stage: Object.keys(counts).map(stage => ({ stage, count: null })) }
        : path.endsWith("/triage") ? { items: null } : path.endsWith("/board") ? { items: [], total: null }
        : path.endsWith("/profiles") ? { items: [], counts: { kind: {}, stage: { potential: null, quoted: null } }, updated_at: null } : { total: null };
      return route.fulfill({ json: data });
    }
    if (path === "/api/v1/inbox/summary" && ["未就绪", "空记录"].includes(scenario)) return route.fulfill({ json: { ready: scenario !== "未就绪", waiting_total: 0 } });
    if (path === "/api/v1/deals/summary" && scenario === "缺阶段") return route.fulfill({ json: { by_stage: [] } });
    if (path === "/api/v1/fulfilment/board" && scenario === "缺记录") return route.fulfill({ json: { items: [], total: 5 } });
    if (path === "/api/v1/profiles") return route.fulfill({ json: { items: [], updated_at: 1893456000,
      counts: { kind: { customer: scenario === "空记录" ? 0 : 15 }, stage: Object.fromEntries(Object.entries(profileStages).map(([k, v]) => [k, scenario === "空记录" ? 0 : v])) } } });
    if (path === "/api/v1/triage") return route.fulfill({ json: { items: scenario === "空记录" ? [] : [1, 2].map(id => ({
      id, action: "identity", confidence: 0.8, status: "pending", reason: "虚构归属建议，待人工核对", conversation_key: `fictional-flow-${id}`,
      suggested_party_kind: "supplier", suggested_party_name: `虚构流程供应商 ${id}`, conversation: { counterpart_name: `虚构流程会话 ${id}`, channel: "wechat", preview: "虚构流程资料", last_at: 1893456000 },
    })) } });
    return route.fallback();
  });
  return { requests, release, state };
}

export async function checkBusinessFlow({ browser, base, out, viewports }) {
  const failures = [];
  let checked = 0;
  for (const [vname, viewport] of viewports) for (const scenario of ["成功", "接口失败", "404", "空值", "缺阶段", "缺记录", "未就绪", "空记录", "局部失败", "刷新失败", "加载"]) {
    const context = await browser.newContext({ viewport });
    const page = await context.newPage();
    const problems = [];
    page.on("pageerror", error => problems.push(error.message));
    page.on("console", message => {
      if (message.type() !== "error") return;
      if (/Failed to load resource:.*status of (?:404|500)/.test(message.text()) && ["接口失败", "404", "局部失败", "刷新失败"].includes(scenario)) return;
      problems.push(message.text());
    });
    const fixture = await installFlowFixtures(page, problems, scenario);
    try {
      await page.goto(base + "/app/", { waitUntil: "load" });
      const line = page.locator(".business-flow-line");
      assert.deepEqual(await line.locator("h3").allTextContents(), ["新消息", "待认人", "潜在客户", "已报价", "待收款", "待发货", "售后"]);
      if (scenario === "加载") {
        await page.getByText("正在读取数量。", { exact: true }).first().waitFor();
        assert.ok((await line.locator(".business-flow-number").allTextContents()).every(value => value === "—"));
        fixture.release();
      }
      // Every endpoint has completed once. Wait for either a resolved number or its reason.
      await page.waitForFunction(() => !document.querySelector(".business-flow-line")?.textContent.includes("正在读取数量"));
      if (scenario === "刷新失败") {
        assert.equal(await line.locator(".business-flow-number").first().innerText(), "1");
        await page.getByRole("button", { name: "刷新", exact: true }).click();
        await page.getByText("数量读取失败，请刷新或打开列表核对。", { exact: true }).first().waitFor();
        await page.waitForFunction(() => [...document.querySelectorAll(".business-flow-number")].every(node => node.textContent === "—"));
      }
      if (["接口失败", "404", "空值", "刷新失败"].includes(scenario)) {
        assert.ok((await line.locator(".business-flow-number").allTextContents()).every(value => value === "—"));
        for (const metric of await line.locator(".business-flow-metric").all()) assert.ok((await metric.locator(".business-flow-reason").innerText()).length > 6, "Unknown counts explain why");
        assert.match(await page.locator(".top-alert").getAttribute("aria-label"), /数量未知/);
      } else if (scenario === "缺阶段") {
        // Deal stages are missing; the AI-profile customer counts still show.
        assert.deepEqual(await line.locator('[data-flow-step="prospects"] .business-flow-number').allTextContents(), ["5", "—"]);
        assert.equal(await line.locator('[data-flow-step="payment"] .business-flow-number').innerText(), "—");
      } else if (["局部失败", "缺记录"].includes(scenario)) {
        assert.deepEqual(await line.locator('[data-flow-step="shipping"] .business-flow-number').allTextContents(), ["—", "—"]);
        assert.equal(await line.locator('[data-flow-step="payment"] .business-flow-number').innerText(), "3");
      } else if (scenario === "未就绪") {
        assert.equal(await line.locator('[data-flow-step="messages"] .business-flow-number').innerText(), "—");
        assert.match(await line.locator('[data-flow-step="messages"]').innerText(), /尚未就绪/);
      } else if (scenario === "空记录") {
        assert.deepEqual(await line.locator(".business-flow-number").allTextContents(), ["0", "0", "0", "0", "0", "0", "0", "0", "0"]);
      } else {
        for (const [step, label, value] of navigation) assert.equal(await line.locator(`[data-flow-step="${step}"]`).getByRole("link", { name: new RegExp(`^${label}：`) }).innerText(), value);
      }
      assert.match(await line.locator('[data-flow-step="quoted"]').innerText(), /报过价的客户/);
      assert.equal(await page.locator("[data-page-intro]").count(), 1);
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 2), "No whole-page overflow");
      if (viewport.width < 760) {
        const scroll = page.locator(".business-flow-scroll");
        await scroll.focus();
        await page.keyboard.press("ArrowRight");
        await page.waitForTimeout(100);
        assert.ok(await scroll.evaluate(node => node.scrollLeft > 0), "Keyboard can scroll the workflow");
        await scroll.evaluate(node => { node.scrollLeft = 0; });
      } else {
        assert.ok(await line.evaluate(node => node.scrollWidth <= node.clientWidth + 2), "All seven steps fit the desktop");
        if (["成功", "加载", "空记录"].includes(scenario)) {
          const box = await page.locator(".overview-compact-channels").boundingBox();
          assert.ok(box && box.y + box.height <= viewport.height, `Workflow and compact channels fit the first desktop screen (${box?.y} + ${box?.height})`);
        }
      }
      if (out && ["成功", "接口失败", "空记录", "局部失败"].includes(scenario)) await page.screenshot({ path: `${out}/${vname}-流程首页-${scenario}.png`, fullPage: true });
      if (scenario === "成功") {
        for (const [step, label, , path, apiPath, filters] of navigation) {
          const start = fixture.requests.length;
          // Triage reuses the exact pending query already loaded by the homepage.
          const reusesPending = apiPath === "/api/v1/triage";
          const destinationRequest = reusesPending ? Promise.resolve() : page.waitForRequest(request => {
            const url = new URL(request.url());
            return url.pathname === apiPath && Object.entries(filters).every(([key, value]) => url.searchParams.get(key) === value);
          });
          const link = page.locator(`[data-flow-step="${step}"]`).getByRole("link", { name: new RegExp(`^${label}：`) });
          // Keyboard activation also checks the reachable focus target on narrow screens.
          await link.focus();
          await page.keyboard.press("Enter");
          await page.waitForURL(base + "/app" + path);
          await destinationRequest;
          await page.waitForFunction(() => !document.querySelector('[aria-busy="true"]'));
          assert.ok((reusesPending ? fixture.requests : fixture.requests.slice(start)).some(url => url.pathname === apiPath && Object.entries(filters).every(([key, value]) => url.searchParams.get(key) === value)), `Destination API receives filters: ${path}`);
          if (reusesPending) { await page.locator('[id^="triage-"]').first().waitFor({ timeout: 5000 }).catch(() => {}); const n = await page.locator('[id^="triage-"]').count(); assert.equal(n, 2, "Cached pending suggestions match the displayed count: " + n + " " + (await page.locator("main").innerText()).slice(0, 300).replace(/\n/g, " ")); }
          if (apiPath === "/api/v1/deals") {
            await page.locator(".deal-board").waitFor();
            const stage = filters.stage;
            const columns = await page.locator(".deal-column").all();
            for (const column of columns) if (await column.locator('h2[id^="stage-"]').getAttribute("id") !== `stage-${stage}`) assert.equal(await column.locator(".deal-card").count(), 0, "Other stages are filtered out");
          }
          await page.goto(base + "/app/", { waitUntil: "load" });
          await page.waitForFunction(() => !document.querySelector(".business-flow-line")?.textContent.includes("正在读取数量"));
        }
      }
    } catch (error) { problems.push(String(error)); }
    finally { fixture.release(); await context.close(); }
    checked += 1;
    if (problems.length) failures.push({ vname, name: `业务流程-${scenario}`, path: "/", problems });
    console.log(`${problems.length ? "FAIL" : "PASS"} [${vname}] 业务流程-${scenario}${problems.length ? ": " + problems.join("; ").slice(0, 200) : ""}`);
  }
  return { checked, failures };
}
