// Entirely fictional acceptance data. Unknown APIs fail closed instead of reaching a service.
import { readFile } from "node:fs/promises";
import ts from "typescript";

const typed = await readFile(new URL("../src/fixtures/overview.demo.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(typed, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
const { overviewDemo } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`);
const at = "2026-11-02T08:00:00+08:00";
const seconds = Date.parse(at) / 1000;
const page = (items = []) => ({ items, total: items.length, page: 1, page_size: 40 });
const offer = { id: 1, product_id: 1, currency: "CNY", unit: "件", price: "100.00", quoted_on: "2026-11-01", valid_until: "2026-12-01", status: "confirmed", is_current: true, validity: "valid", usable_for_quote: true };
const product = { id: 1, supplier_id: 1, name: "演示灯具 A", model: "DEMO-A", status: "candidate", field_state: {}, packing_ready: true, carton_l_cm: 40, carton_w_cm: 30, carton_h_cm: 20, gross_kg: 5, net_kg: 4, units_per_carton: 1, offers: [offer], current_offer: offer, sources: [] };
const supplier = { id: 1, kind: "supplier", name: "演示供应商", short_name: "演示供应商", aliases: [], status: "active", conversation_ref: "demo-wechat", notes: "虚构验收资料", products: { candidate: 1, confirmed: 0, hidden: 0 }, offers: { valid: 1, expiring: 0, expired: 0, no_expiry: 0, suggested: 0 }, packing_coverage: 1, terms: {}, product_items: [product] };
const conversations = ["alibaba", "wechat"].map((channel) => ({ key: `demo-${channel}`, channel, channel_label: channel === "wechat" ? "微信" : "阿里国际站", account: "演示账号", buyer_name: channel === "wechat" ? "演示供应商群" : "演示客户", country: "演示地区", n_messages: 2, counterpart_role: channel === "wechat" ? "supplier" : "customer", dormant: false, last: { role: "buyer", text: "请核对演示产品的外箱尺寸。", sent_at: at }, waiting_reply: true, waiting_hours: 2, has_draft: false }));
const customer = { key: "demo-customer", buyer_name: "演示客户", channel: "alibaba", channel_label: "阿里国际站", country: "演示地区", account: "演示账号", n_conversations: 1, n_messages: 2, first_seen: at, last_seen: at, waiting_reply: true, conversations: [conversations[0]] };
const stages = ["inquiry", "sourcing", "quoting", "negotiating", "awaiting_payment", "paid", "inspection", "shipped", "delivered", "aftersales", "reorder"];
const deal = { id: 1, title: "演示灯具询盘", customer: { id: 2, name: "演示客户" }, stage: "inquiry", stage_label: "询盘", status: "open", value: "200.00", currency: "USD", last_activity_at: seconds, participants: [{ party_id: 2, name: "演示客户", role: "customer", state: "us", last_at: seconds, conversations: [] }], waiting_on_us: true, waiting_on: ["customer"], overdue: [], needs_attention: true, open_tasks: 1, next_action: "核对包装并回复", next_action_due: "2026-11-03", unlinked_participants: 0, conversations: [], items: [], tasks: {}, events: [{ id: 1, at, kind: "note", summary: "虚构案子已建立" }], stages: stages.map((key, index) => ({ key, label: key === "inquiry" ? "询盘" : key, index, current: index === 0, done: false })) };
const secondDeal = { ...deal, id: 2, title: "演示待报价案子", stage: "quoting", stage_label: "报价" };
const totals = { cost_cny: null, revenue_cny: null, profit_cny: null, margin: null, revenue: null, cost_complete: false };
const quote = { id: 1, deal_id: 1, round_no: 1, status: "draft", currency: "USD", incoterm: "FOB", destination_country: "演示地区", target_margin: "0.20", totals, per_line: [], blocking: [], warnings: [], inverted: false, below_target: false, lines: [], costs: [], terms: {}, parties: [], cost_sources: [], customer_conversations: [], negotiations: [] };
const brief = { id: 1, role: "brief", source: "rules", status: "ok", created_at: at, output_json: { narrative: "演示简报：先回复客户，再核对包装。", results: Object.fromEntries(["today", "week", "month"].map((key) => [key, { count: 0, amounts: {}, amount_pending: 0 }])), negotiating: 2, quotes_this_week: { sent: 0, accepted: 0 }, priorities: [] } };
const counts = { products: { candidate: 1, confirmed: 0, hidden: 0 }, offers: { valid: 1, expiring: 0, expired: 0, no_expiry: 0, suggested: 0 }, freight: { valid: 0, expiring: 0, expired: 0, no_expiry: 0, suggested: 0 } };

function fixture(url, method) {
  const path = url.pathname.replace(/^\/api\/v1/, "");
  if (method === "POST" && path === "/classify/scan") return { message: "虚构扫描完成", notes: [] };
  if (method !== "GET") throw new Error(`Unexpected mutation: ${method} ${path}`);
  const exact = {
    "/overview": overviewDemo,
    "/ops/queue": { generated_at: seconds, items: [], total: 0, complete: true, unavailable: [], counts: {} },
    "/ops/drafts": { items: [], dry_run: true },
    "/inbox/summary": { ready: true, waiting_total: 2, draft_new: 0, new_24h: 2, by_channel: conversations.map((c) => ({ channel: c.channel, label: c.channel_label, ready: true, waiting: 1, conversations: 1 })), channels: conversations.map((c) => ({ channel: c.channel, account: c.account, conversations: 1, waiting_reply: 1, messages_24h: 2, last_message_at: seconds })) },
    "/outbox": page(), "/outbox/settings": { channels: [] },
    "/customers": page([customer]), "/customers/demo-customer": customer,
    "/deals/summary": { open: 2, won: 0, lost: 0, paused: 0, by_stage: [], waiting_on_us: 2, overdue: 0, due_tasks: 1 },
    "/deals/by-conversation": { deals: [{ id: 1, title: deal.title, stage_label: "询盘", status: "open" }], party: { id: 2, name: "演示客户", kind: "customer" } },
    "/deals/1": deal, "/deals/1/quotes": page([{ ...quote, blocking: 0, warnings: 0 }]),
    "/deals/1/payments": { items: [], summary: [] }, "/deals/1/fulfilment": { eligible: false }, "/deals/1/issues": page(),
    "/deals/1/demands": { ...page(), conversations: [] }, "/deals/1/offers": { customer: null, participants: [], items: [], draft_rounds: [] },
    "/deals/1/waiting": { participants: [], items: [], products: [] },
    "/deals/1/secretary": { id: 1, source: "rules", status: "ok", created_at: at, output_json: { headline: "先核对尺寸再回复", waiting_on: [], next_steps: [], risks: [] } },
    "/quotes/1": quote,
    "/archive/summary": counts, "/archive/parties/1": supplier, "/archive/products/1": product,
    "/packing/summary": { ready: true, generated_at: at, notice: "虚构验收数据", totals: { entries: 0, ready: 0, estimable: 0, partial: 0, missing: 0, conflict: 0, multi_model: 0, value_mismatch: 0 }, suppliers: [], next_actions: [] },
    "/audit": { summary: "虚构检查正常", attention_count: 0, counts: {}, generated_at: at, items: [], failed_count: 0 },
    "/triage": page(), "/patrol/alerts": page(), "/brief/today": brief,
    "/roles": page([{ key: "patrol", name: "巡检员", responsibility: "提醒需要跟进的案子", level: "L0", last_run_at: at, last_status: "ok", runs_24h: 1, failures_24h: 0, model: "演示规则" }]), "/roles/runs": page(),
    "/fulfilment/board": page(), "/reorder/leads": page(),
    "/system/channels": { generated_at: seconds, channels: conversations.map((c) => ({ channel: c.channel, label: c.channel_label, ready: true, state: "ok", accounts: [], messages_24h: 2, last_message_at: seconds })), notes: [] },
    "/system/whatsapp/status": { session: "demo", status: "WORKING", status_label: "已连接", needs_qr: false, phone_masked: null, next_step: "虚构连接" },
    "/pipeline/health": { generated_at: seconds, summary: "虚构数据管道检查", sources: [], channels: [], notes: [], scorecard: [], failed_jobs: { status: "not_connected", count: null, items: [], reason: "演示未接入" } },
    "/alibaba-open/status": { configured: false, authorized: false, token_expires_in: null, last_poll_at: null, last_poll_result: null, next_step: "演示未授权", paused: true, daily_limit: 10, calls_today: 0, next_poll_at: null },
    "/alibaba-open/candidates": page(),
    "/kb/stats": { ready: true, chunks: 1, families: [], loaded_at: seconds, mode: "lexical" }, "/kb/brief": { text: "虚构公司背景", version: 1, versions: [] }, "/kb/search": { items: [], mode: "lexical", ready: true },
    "/directory/wechat/summary": { ready: true, conclusion: "虚构名录检查", total: 0, conflicts: 0, not_enrolled_suspected: { supplier: 0, customer: 0 }, notes: [], groups: { total: 0, by_state: {} }, name_health: { total: 6, named: 4, missing: 2, reasons: { "接口没返回群名": 2 } } }, "/directory/wechat": page(),
    "/extract/summary": { pending: { offer: 0, freight: 0 }, total: 0, last_scan_at: null }, "/extract/suggestions": page(),
    "/classify": { ...page(), day: "2026-11-02" }, "/classify/summary": { total: 0, pending: 0, confirmed: 0, ignored: 0, unknown_first_seen: 0 },
    "/lang/settings": { settings: { my_language: "zh-CN", auto_native_send: false }, languages: [{ code: "zh-CN", label: "中文" }, { code: "en", label: "英语" }], quota: { hourly_limit: 10, used: 0, remaining: 10 } },
    "/lang/glossary": page(), "/lang/contacts": page(), "/lang/contact": { contact: { native_language: "en", source: "manual", confidence: 1, locked: true } },
    "/inspiration": { ...page(), ready: true, counts: { total: 0, relevance: {}, status: {} } },
  };
  if (path === "/inbox/conversations") return page(conversations.filter((c) => !url.searchParams.get("channel") || c.channel === url.searchParams.get("channel")));
  if (/^\/inbox\/conversations\/demo-[^/]+\/ops\/context$/.test(path)) return { workflow_state: "waiting", remind_at: null, reminder_due: false, customer_key: customer.key, customer_name: customer.buyer_name, customer, party: null, deals: [], suppliers: [], outbox: [], dry_run: true };
  if (path.startsWith("/inbox/conversations/demo-")) {
    const conv = conversations.find((c) => path.endsWith(c.key));
    if (!conv) throw new Error(`Unknown conversation ${path}`);
    return { conversation: { ...conv, first_seen: at, last_seen: at, source_url: null, can_send: false, copy_hint: "演示会话，请到原渠道回复" }, messages: [{ role: "buyer", text: "请核对外箱尺寸。", sent_at: at }, { role: "seller", text: "正在核对演示规格。", sent_at: at }], draft: null };
  }
  if (path === "/deals") return page(url.searchParams.get("status") === "won" ? [] : [deal, secondDeal]);
  if (path === "/archive/parties") return page(url.searchParams.get("kind") === "forwarder" ? [] : [supplier]);
  if (path === "/archive/products") return page([product]);
  if (path === "/archive/freight" || path === "/packing/items") return page();
  if (Object.hasOwn(exact, path)) return exact[path];
  throw new Error(`Missing fictional fixture: ${method} ${path}`);
}

const installed = new WeakSet();
export async function installBaseFixtures(browserPage, problems = []) {
  if (installed.has(browserPage)) return;
  installed.add(browserPage);
  await browserPage.context().route("**/*", async route => {
    const url = new URL(route.request().url());
    if (url.hostname !== "127.0.0.1") { problems.push("External request blocked: " + url.origin); return route.abort(); }
    return route.fallback();
  });
  await browserPage.route("**/api/**", async (route) => {
    try { await route.fulfill({ json: structuredClone(fixture(new URL(route.request().url()), route.request().method())) }); }
    catch (error) { problems.push(error.message); await route.fulfill({ status: 501, json: { error: error.message } }); }
  });
}

export { deal as demoDeal, quote as demoQuote };
