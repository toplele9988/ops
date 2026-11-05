// Workspace-only acceptance. Build first, then run node e2e/smoke.mjs from web/.
// Uses fictional data, a loopback static server and strict CSP; never calls a backend.
import { chromium } from "playwright-core";
import { checkOverview } from "./overview.mjs";
import { familyKey } from "./fixtures.dossiers.mjs";

import assert from "node:assert/strict";
import { mkdir, readdir, writeFile } from "node:fs/promises";
import { installFixtures, conversationKey, supplierKey } from "./fixtures.mjs";
import { startLocalServer } from "./local-server.mjs";

// Always serve this workspace's build and intercept every API with fictional fixtures.
// BASE is deliberately not accepted: this acceptance run cannot contact production.
if (process.env.IMGW_REHEARSAL === '1' && process.env.BASE) {
  const { runRehearsal } = await import('./rehearsal.mjs');
  process.exit(await runRehearsal());
}
const server = await startLocalServer();
const BASE = server.base;
const CHROME = process.env.CHROME;
const OUT = process.env.OUT ?? new URL("../../artifacts/ux-smoke", import.meta.url).pathname;
await mkdir(OUT, { recursive: true });
const WAIT = Number(process.env.WAIT ?? 250);
const OVERVIEW_ONLY = process.env.ONLY === "overview";
const ERROR_TEXT = /没能读到|没能打开|没能查到|暂时无法|请求没有成功|Something went wrong/;

const FRONT = ["首页", "待我处理", "消息", "待确认归属", "草稿未发送", "客户", "案子", "备货与发货", "供应商", "产品", "缺资料", "AI 运营员", "知识检索"];
const FRONT_PATHS = ["/", "/workbench", "/inbox", "/triage", "/outbox", "/customers", "/deals", "/fulfilment", "/supplier-dossiers", "/product-dossiers", "/dossier-gaps", "/operator", "/search"];
const BACK = ["渠道接入", "数据管道健康", "阿里订单接入", "知识中枢", "语言设置", "账号与权限旧后台", "待分类", "微信通讯录", "数据体检", "货代运价", "价格建议", "供应商包装核对", "需求与验收", "供应商旧列表", "产品旧列表", "AI 员工", "灵感库", "怎么用"];
const BACK_PATHS = ["/system/channels", "/system/pipeline", "/system/alibaba-open", "/system/knowledge", "/system/language", "/v3/admin", "/classify", "/directory", "/audit", "/freight", "/prices", "/packing", "/requirements", "/suppliers", "/products", "/roles", "/inspiration", "/guide"];
const BACK_TOOLS = new Set([...BACK_PATHS, "/quotes", "/listing"]);

async function checkNavigation(page, path, viewport) {
  const pathname = path.split("?")[0];
  const back = pathname.startsWith("/system") || BACK_TOOLS.has(pathname) || pathname.startsWith("/suppliers/");
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
  assert.doesNotMatch(await nav.innerText(), /旧版工具|规划中|AI 开发灵感库|运行与数据状态/);
  assert.deepEqual(await nav.locator(".nav-group").allTextContents(), back ? ["渠道与数据", "设置", "整理工具", "资料维护", "使用与改进"] : ["今天", "沟通", "客户与生意", "供应与产品", "AI 助手"]);
  if (pathname === "/" || pathname === "/overview") assert.equal(await nav.locator('a[aria-current="page"]').getAttribute("href"), "/app");
  if (!modal) assert.equal(await page.getByRole("button", { name: "关闭导航", exact: true }).isVisible(), viewport.width <= 900);
  if (viewport.width <= 900 && !modal) {
    if (pathname === "/" || pathname === "/system") await page.screenshot({ path: `${OUT}/手机-${back ? "后台设置导航" : "运营台导航"}.png` });
    await page.locator(".scrim").click({ position: { x: viewport.width - 10, y: 100 } });
    await page.waitForFunction(() => document.querySelector(".sider").getBoundingClientRect().right <= 0);
    if (pathname === "/") {
      assert.equal(await page.locator(".sider").evaluate(node => node.inert), true, "Closed drawer is not in the Tab order");
      await page.getByRole("button", { name: "打开导航", exact: true }).click();
      await nav.locator("a").last().focus();
      await page.keyboard.press("Tab");
      assert.equal(await page.getByRole("button", { name: "关闭导航", exact: true }).evaluate(node => node === document.activeElement), true, "Tab wraps within mobile navigation");
      await page.keyboard.press("Shift+Tab");
      assert.equal(await nav.locator("a").last().evaluate(node => node === document.activeElement), true);
      await page.keyboard.press("Escape");
      await page.waitForFunction(() => document.querySelector(".sider").getBoundingClientRect().right <= 0);
      assert.equal(await page.getByRole("button", { name: "打开导航", exact: true }).evaluate(node => node === document.activeElement), true, "Escape returns focus to opener");
    }
  }
}

const PAGES = [
  ["全局总览", "/"], ["总览旧书签", "/overview"],
  ["工作台保留页", "/workbench"], ["案子列表", "/deals"], ["案子列表-要我处理", "/deals?waiting=us"],
  ["商机沟通", "/inbox"], ["商机沟通-微信含供应商", "/inbox?channel=wechat&role=all"], ["客户", "/customers"],
  ["知识检索", "/search"], ["知识检索-搜索", "/search?q=" + encodeURIComponent("外箱尺寸")],
  ["供应商档案", "/supplier-dossiers"], ["产品档案", "/product-dossiers"], ["缺资料", "/dossier-gaps"], ["AI 运营员", "/operator"],
  ["供应商", "/suppliers"], ["产品", "/products"], ["货代运价", "/freight"], ["包装核对", "/packing"],
  ["灵感库", "/inspiration"], ["怎么用", "/guide"], ["待整理会话", "/triage"], ["AI 员工", "/roles"], ["微信通讯录", "/directory"], ["数据体检", "/audit"], ["价格建议", "/prices"], ["备货与发货", "/fulfilment"], ["微信通讯录-没接入", "/directory?state=not_enrolled"], ["需求与验收", "/requirements"],
  ["系统总览", "/system"], ["渠道接入", "/system/channels"], ["数据管道健康", "/system/pipeline"], ["阿里订单接入", "/system/alibaba-open"], ["待分类", "/classify"], ["语言设置", "/system/language"], ["知识中枢", "/system/knowledge"], ["旧版工具", "/system/legacy"],
  ["规划报价保留页", "/quotes"], ["规划发品保留页", "/listing"], ["规划分类保留页", "/system/classify"], ["规划模型保留页", "/system/models"],
  ["客户资料", "/customers/1"], ["供应商资料", "/suppliers/3"],
  ["供应商档案详情", "/supplier-dossiers/S1"], ["产品档案详情", `/product-dossiers/${familyKey}`],
  ["会话-客户", `/inbox?conversation=${conversationKey}`], ["会话-微信", `/inbox?channel=wechat&role=all&conversation=${supplierKey}`],
  ["草稿未发送", "/outbox"], ["案子页", "/deals/1"], ["报价页", "/quotes/1"], ["产品详情", "/products?status=candidate&open=1"],
];
// Optional device split for execution environments with short process lifetimes.
const VIEWPORT_PRESETS = { desktop: ["桌面", { width: 1440, height: 900 }], mobile: ["手机", { width: 400, height: 860 }] };
const VIEWPORTS = Object.entries(VIEWPORT_PRESETS).filter(([key]) => !process.env.VIEWPORT || process.env.VIEWPORT === key).map(([, value]) => value);
assert.ok(VIEWPORTS.length, "VIEWPORT must be desktop or mobile when set");

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
      if (path.startsWith("/inbox?") && (path.includes("c=") || path.includes("conversation="))) {
        await page.locator(".thread-body .bubble").first().waitFor();
        if (viewport.width <= 900) {
          const toggle = page.getByRole("button", { name: "客户资料与 AI 建议 · 展开操作" });
          await toggle.click();
          assert.equal(await page.locator("#conversation-profile").isVisible(), true);
          await page.getByRole("button", { name: "收起客户资料和操作" }).click();
          assert.equal(await page.locator("#conversation-profile").isVisible(), false);
        } else {
          const boxes = await Promise.all([".listing", ".thread", ".side"].map((selector) => page.locator(selector).boundingBox()));
          assert.ok(boxes[0].x < boxes[1].x && boxes[1].x < boxes[2].x, "Desktop inbox uses three columns");
        }
      }
      if (path === "/deals") assert.equal(await page.getByRole("region", { name: "销售管道看板" }).isVisible(), true);
      if (path === "/customers/1") assert.match(await page.locator(".contact-timeline").innerText(), /虚构产品报价/);
      if (path === "/suppliers/3") assert.match(await page.locator(".contact-timeline").innerText(), /虚构产品报价/);
      if (path === "/") {
        assert.equal(await page.getByRole("heading", { name: "流程总览", exact: true }).isVisible(), true);
        assert.equal(await page.locator("[data-flow-step]").count(), 7);
        assert.equal(await page.getByRole("heading", { name: "渠道有没有进消息", exact: true }).isVisible(), true);
      }
      // Layout-level coverage keeps Customers and Inbox untouched and prevents duplicate intros.
      if (FRONT_PATHS.includes(path.split("?")[0]) || /^\/(customers|deals|supplier-dossiers|product-dossiers|quotes)\//.test(path)) {
        assert.equal(await page.locator("[data-page-intro]").count(), 1, "Exactly one page purpose and next step");
        assert.match(await page.locator("[data-page-intro] p").innerText(), /；/);
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
      if (OUT) await page.screenshot({ path: `${OUT}/${vname}-${name.replace(/[^\w一-龥-]/g, "_")}.png`, fullPage: ["/", "/deals", "/customers/1", "/suppliers/3"].includes(path) });
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
