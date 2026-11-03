// Workspace-only acceptance. Build first, then run node e2e/smoke.mjs from web/.
// Uses fictional data, a loopback static server and strict CSP; never calls a backend.
import { chromium } from "playwright-core";
import { checkOverview } from "./overview.mjs";

import assert from "node:assert/strict";
import { mkdir, readdir, writeFile } from "node:fs/promises";
import { installFixtures } from "./fixtures.mjs";
import { startLocalServer } from "./local-server.mjs";

// Always serve this workspace's build and intercept every API with fictional fixtures.
// BASE is deliberately not accepted: this acceptance run cannot contact production.
const server = await startLocalServer();
const BASE = server.base;
const CHROME = process.env.CHROME;
const OUT = process.env.OUT ?? new URL("../../artifacts/ux-smoke", import.meta.url).pathname;
await mkdir(OUT, { recursive: true });
const WAIT = Number(process.env.WAIT ?? 250);
const OVERVIEW_ONLY = process.env.ONLY === "overview";
const ERROR_TEXT = /没能读到|没能打开|没能查到|暂时无法|请求没有成功|Something went wrong/;

const FRONT = ["总览", "商机沟通", "案子", "客户", "供应商", "产品", "备货与发货", "知识检索", "AI 员工"];
const FRONT_PATHS = ["/", "/inbox", "/deals", "/customers", "/suppliers", "/products", "/fulfilment", "/search", "/roles"];
const BACK = ["渠道接入", "数据管道健康", "阿里订单接入", "知识中枢", "语言设置", "账号与权限旧后台", "待整理会话", "待分类", "微信通讯录", "数据体检", "货代运价", "价格建议", "供应商包装核对", "需求与验收"];
const BACK_PATHS = ["/system/channels", "/system/pipeline", "/system/alibaba-open", "/system/knowledge", "/system/language", "/v3/admin", "/triage", "/classify", "/directory", "/audit", "/freight", "/prices", "/packing", "/requirements"];
const BACK_TOOLS = new Set(["/triage", "/classify", "/directory", "/audit", "/freight", "/prices", "/packing", "/requirements"]);

async function checkNavigation(page, path, viewport) {
  const pathname = path.split("?")[0];
  const back = pathname.startsWith("/system") || BACK_TOOLS.has(pathname);
  const modal = await page.locator("dialog[open]").count() > 0;
  if (viewport.width <= 900 && !modal) {
    await page.getByRole("button", { name: "打开导航", exact: true }).click();
    await page.waitForFunction(() => document.querySelector(".sider").getBoundingClientRect().left === 0);
    const box = await page.locator(".sider").boundingBox();
    assert.ok(box.x >= 0 && box.x + box.width <= viewport.width, "Mobile navigation fits the viewport");
  }
  const nav = page.locator(`nav[aria-label="${back ? "后台设置入口" : "运营台入口"}"]`);
  assert.equal(await nav.isVisible(), true, "Navigation must be reachable");
  const labels = await nav.locator("a .grow").allTextContents();
  assert.deepEqual(labels, back ? BACK : FRONT, "Navigation count and fixed order");
  const hrefs = await nav.locator("a").evaluateAll((links) => links.map((a) => a.getAttribute("href")));
  assert.deepEqual(hrefs, (back ? BACK_PATHS : FRONT_PATHS).map((path) => path.startsWith("/v3/") ? path : path === "/" ? "/app" : "/app" + path));
  assert.equal(hrefs.filter((href) => href.startsWith("/v3/")).length, back ? 1 : 0, "Only account admin may link to v3");
  assert.doesNotMatch(await nav.innerText(), /旧版工具|规划中|怎么用|AI 开发灵感库|运行与数据状态/);
  if (back) assert.deepEqual(await nav.locator(".nav-group").allTextContents(), ["渠道与数据", "设置", "整理工具"]);
  if (pathname === "/" || pathname === "/overview") assert.equal(await nav.locator('a[aria-current="page"]').getAttribute("href"), "/app");
  if (viewport.width <= 900 && !modal) {
    if (pathname === "/" || pathname === "/system") await page.screenshot({ path: `${OUT}/手机-${back ? "后台设置导航" : "运营台导航"}.png` });
    await page.locator(".scrim").click({ position: { x: viewport.width - 10, y: 100 } });
    await page.waitForFunction(() => document.querySelector(".sider").getBoundingClientRect().right <= 0);
  }
}

const PAGES = [
  ["全局总览", "/"], ["总览旧书签", "/overview"],
  ["工作台保留页", "/workbench"], ["案子列表", "/deals"], ["案子列表-要我处理", "/deals?waiting=us"],
  ["商机沟通", "/inbox"], ["商机沟通-微信含供应商", "/inbox?channel=wechat&role=all"], ["客户", "/customers"],
  ["知识检索", "/search"], ["知识检索-搜索", "/search?q=" + encodeURIComponent("外箱尺寸")],
  ["供应商", "/suppliers"], ["产品", "/products"], ["货代运价", "/freight"], ["包装核对", "/packing"],
  ["灵感库", "/inspiration"], ["怎么用", "/guide"], ["待整理会话", "/triage"], ["AI 员工", "/roles"], ["微信通讯录", "/directory"], ["数据体检", "/audit"], ["价格建议", "/prices"], ["备货与发货", "/fulfilment"], ["微信通讯录-没接入", "/directory?state=not_enrolled"], ["需求与验收", "/requirements"],
  ["系统总览", "/system"], ["渠道接入", "/system/channels"], ["数据管道健康", "/system/pipeline"], ["阿里订单接入", "/system/alibaba-open"], ["待分类", "/classify"], ["语言设置", "/system/language"], ["知识中枢", "/system/knowledge"], ["旧版工具", "/system/legacy"],
  ["规划报价保留页", "/quotes"], ["规划发品保留页", "/listing"], ["规划分类保留页", "/system/classify"], ["规划模型保留页", "/system/models"],
  ["客户资料", "/customers?c=demo-customer"], ["供应商资料", "/suppliers?party=1"],
  ["会话-客户", "/inbox?c=demo-alibaba"], ["会话-微信", "/inbox?channel=wechat&role=all&c=demo-wechat"],
  ["案子页", "/deals/1"], ["报价页", "/quotes/1"], ["产品详情", "/products?status=candidate&open=1"],
];
const VIEWPORTS = [["桌面", { width: 1440, height: 900 }], ["手机", { width: 400, height: 860 }]];

const browser = await chromium.launch({ ...(CHROME ? { executablePath: CHROME } : {}), args: ["--no-sandbox"] });
const failures = [];
let checked = 0;
for (const [vname, viewport] of VIEWPORTS) {
  for (const [name, path] of OVERVIEW_ONLY ? [] : PAGES) {
    const context = await browser.newContext({ viewport });
    const page = await context.newPage();
    const problems = [];
    await installFixtures(page, problems);
    let missingMedia = 0;   // an image that was never archived answers 404; the page shows "file not here", which is expected
    let consoleNotFound = 0;
    let overviewNotDeployed = false;
    page.on("console", (m) => {
      if (m.type() !== "error") return;
      if (/status of 404/.test(m.text())) { consoleNotFound += 1; return; }
      problems.push("浏览器报错：" + m.text().slice(0, 140));
    });
    page.on("pageerror", (e) => problems.push("页面脚本异常：" + String(e).slice(0, 140)));
    page.on("response", (r) => {
      const url = new URL(r.url());
      if (url.origin !== new URL(BASE).origin || r.status() < 400) return;
      if ((path === "/overview" || path === "/") && url.pathname === "/api/v1/overview" && r.status() === 404) { overviewNotDeployed = true; missingMedia += 1; return; }
      if ((url.pathname.startsWith("/api/v1/media/") || /^\/api\/v1\/(brief\/today|deals\/\d+\/secretary)$/.test(url.pathname)) && r.status() === 404) { missingMedia += 1; return; }   // "not generated yet" is a normal answer for brief/secretary
      problems.push(`接口失败 ${r.status()} ${url.pathname}`);
    });
    try {
      await page.goto(BASE + "/app" + path, { waitUntil: "load" });
      await page.waitForTimeout(WAIT);
      await checkNavigation(page, path, viewport);
      if (path.startsWith("/inbox?") && path.includes("c=")) {
        await page.locator(".thread-body .bubble").first().waitFor();
        if (viewport.width <= 900) {
          const toggle = page.getByRole("button", { name: "查看资料与 AI 建议" });
          await toggle.click();
          assert.equal(await page.locator("#conversation-profile").isVisible(), true);
          await page.getByRole("button", { name: "收起资料与 AI 建议" }).click();
          assert.equal(await page.locator("#conversation-profile").isVisible(), false);
        } else {
          const boxes = await Promise.all([".listing", ".thread", ".side"].map((selector) => page.locator(selector).boundingBox()));
          assert.ok(boxes[0].x < boxes[1].x && boxes[1].x < boxes[2].x, "Desktop inbox uses three columns");
        }
      }
      if (path === "/deals") assert.equal(await page.getByRole("region", { name: "销售管道看板" }).isVisible(), true);
      if (path === "/customers?c=demo-customer") assert.match(await page.locator(".contact-timeline").innerText(), /外箱尺寸/);
      if (path === "/suppliers?party=1") assert.match(await page.locator(".contact-timeline").innerText(), /2026-11-01/);
      if (path === "/") {
        assert.equal(await page.getByRole("heading", { name: "全局总览", exact: true }).isVisible(), true);
        for (const text of ["今日运营", "今日成交简报", "等你回复的买家", "供应商资料待补", "成交结果"]) {
          // The empty fixture has no won deals, so its conditional result block is absent.
          if (text !== "成交结果") assert.equal(await page.getByRole("heading", { name: new RegExp(text) }).count() > 0, true, text);
        }
      }
      if ((await page.locator("h1, .pane-head").count()) === 0) problems.push("页面没有标题（可能白屏）");
      const body = await page.locator("body").innerText();
      const hit = body.match(ERROR_TEXT);
      if (hit) problems.push(`页面显示错误状态：「${hit[0]}」`);
      if ((path === "/overview" || path === "/")) {
        if (overviewNotDeployed && !(await page.locator('[data-overview-error="not-deployed"]').isVisible())) problems.push("总览接口 404 未显示“接口尚未部署”失败态");
        if (!overviewNotDeployed && await page.locator("[data-overview-error]").count()) problems.push("总览读取失败");
      }
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
      if (overflow > 2) problems.push(`页面横向溢出 ${overflow}px`);
      if (consoleNotFound > missingMedia) problems.push(`有 ${consoleNotFound - missingMedia} 个资源 404（不是未归档的图片）`);
      if (OUT) await page.screenshot({ path: `${OUT}/${vname}-${name.replace(/[^\w一-龥-]/g, "_")}.png`, fullPage: ["/", "/deals", "/customers?c=demo-customer", "/suppliers?party=1"].includes(path) });
    } catch (error) {
      problems.push("打开失败：" + String(error).slice(0, 140));
    }
    checked += 1;
    if (problems.length) failures.push({ vname, name, path, problems: [...new Set(problems)] });
    console.log(`${problems.length ? "FAIL" : "PASS"} [${vname}] ${name}${problems.length ? ": " + problems.join("; ").slice(0, 240) : ""}`);
    await context.close();
  }
}
const overview = await checkOverview({ browser, base: BASE, out: OUT, viewports: VIEWPORTS });
failures.push(...overview.failures);
checked += overview.checked;
await browser.close();
await server.close();
await writeFile(`${OUT}/result.json`, JSON.stringify({ checked, failures, mode: "workspace build + fictional API fixtures", viewports: VIEWPORTS }, null, 2));
const screenshots = (await readdir(OUT)).filter((name) => name.endsWith(".png")).sort();
await writeFile(`${OUT}/screenshots.md`, "# 本地虚构数据截图\n\n" + screenshots.map((name) => `- [${name}](${encodeURI(name)})`).join("\n") + "\n");
console.log(`检查了 ${checked} 个页面视图（含总览成功、失败、空值及刷新场景，桌面与手机）`);
if (failures.length === 0) {
  console.log("全部通过：没有浏览器报错、接口失败、错误状态或横向溢出。");
} else {
  console.log(`有 ${failures.length} 处不通过：`);
  for (const f of failures) console.log(`- [${f.vname}] ${f.name}  ${f.path}\n    ` + f.problems.join("\n    "));
  process.exitCode = 1;
}
