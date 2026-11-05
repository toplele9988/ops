// Fictional browser-memory state only. No real service, sender or payment transport.
import { installBaseFixtures as installFixtures } from "./fixtures.base.mjs";

const item = (kind, title, priority, to, action) => ({ id: `queue-${kind}`, kind, title, label: ({ pipeline: "管道异常", buyer_reply: "待回复买家", supplier_chase: "要催的供应商", payment: "待确认收款", quote_expiry: "报价到期", logistics: "物流节点超期", ownership: "待你确认归属" })[kind], priority, to, action, detail: "纯虚构验收待办，请核对后处理。", revision: "fictional-v1", due_on: null });
export const queueItems = [
  item("pipeline", "演示渠道需要检查", 0, "/system/pipeline?channel=alibaba", "查看恢复步骤"),
  item("logistics", "演示物流节点超期", 1, "/deals/1", "去跟进物流"),
  item("payment", "演示收款待核对", 2, "/deals/1", "去核对收款"),
  item("buyer_reply", "演示客户等回复", 2, "/inbox?conversation=demo-alibaba&c=demo-alibaba&role=all&channel=alibaba", "去回复买家"),
  item("quote_expiry", "演示报价快到期", 3, "/deals/1", "去跟进报价"),
  item("supplier_chase", "演示供应商等跟进", 4, "/inbox?conversation=demo-wechat&c=demo-wechat&role=all&channel=wechat", "去催供应商"),
  item("ownership", "演示会话待确认归属", 5, "/triage?conversation=demo-wechat&suggestion=1", "去确认归属"),
];
export function queueResponse(items = queueItems) {
  return { generated_at: Date.now() / 1000, items, total: items.length, complete: true, unavailable: [], counts: Object.fromEntries(items.map((i) => [i.kind, 1])) };
}
export async function installQueueFixtures(page, problems = [], { queueOnly = false } = {}) {
  await installFixtures(page, problems);
  const state = { items: structuredClone(queueItems), calls: [], outbox: [], nodeDone: false, failQueue: false, complete: true, unavailable: [] };
  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const method = request.method();
    const body = Object.fromEntries(new URLSearchParams(request.postData() ?? ""));
    state.calls.push({ method, path, body });
    const respond = (json, status = 200) => route.fulfill({ status, json: structuredClone(json) });
    if (path === "/api/v1/ops/queue" && method === "GET") {
      if (state.failQueue) return respond({ error: "虚构暂时读不到" }, 503);
      return respond({ ...queueResponse(state.items), complete: state.complete, unavailable: state.unavailable });
    }
    if (queueOnly) return route.fallback();
    if (path === "/api/v1/inbox/conversations/demo-alibaba/draft" && method === "POST") {
      const draft = { id: 101, status: "new", text: "演示规格正在核对，请确认数量。", language: "zh-CN", needs_confirmation: [], model: "fictional", created_at: Date.now() / 1000 };
      state.outbox.push({ id: 101, status: "draft", dry_run: true, conversation_key: "demo-alibaba", text: draft.text });
      return respond({ draft });
    }
    if (path === "/api/v1/inbox/conversations/demo-alibaba" && method === "GET") {
      return respond({ conversation: { key: "demo-alibaba", channel: "alibaba", channel_label: "阿里国际站", account: "演示账号", buyer_name: "演示客户", country: "演示地区", counterpart_role: "customer", first_seen: "2026-11-02T08:00:00+08:00", last_seen: "2026-11-02T08:00:00+08:00", n_messages: 1, source_url: null, can_send: false, copy_hint: "演练只保存草稿" },
        messages: [{ role: "buyer", text: "请核对演示外箱尺寸。", sent_at: "2026-11-02T08:00:00+08:00" }],
        draft: state.outbox.length ? { id: 101, text: state.outbox.at(-1).text, language: "zh-CN", needs_confirmation: [], model: "fictional", created_at: Date.now() / 1000 } : null });
    }
    if (path === "/api/v1/deals/1/fulfilment/production/update" && method === "POST") {
      if (body.status !== "done") return respond({ error: "虚构请求参数不符" }, 400);
      state.nodeDone = true;
      state.items = state.items.filter((i) => i.kind !== "logistics");
      return respond({ ok: true, id: 91 });
    }
    if (path === "/api/v1/deals/1/fulfilment" && method === "GET") return respond({
      eligible: true, done: state.nodeDone ? 1 : 0, total: 1, next_due: state.nodeDone ? null : "2029-12-31", overdue: !state.nodeDone, suggestions: [],
      items: [{ id: 91, node: "production", label: "生产中", status: state.nodeDone ? "done" : "doing", responsible: "supplier", party_id: null, planned_on: "2029-12-31", completed_on: state.nodeDone ? "2030-01-01" : null, due_on: "2029-12-31", overdue: !state.nodeDone, note: "虚构进度" }],
    });
    // Opening a conversation asks for automatic Chinese translation; the list asks for cached ones.
    if (path === "/api/v1/lang/translate" && method === "POST" && ["preview", "cached", "auto"].includes(body.mode))
      return respond(body.mode === "preview" ? { previews: [] } : { target_language: "zh-CN", items: [] });
    if (method !== "GET") { problems.push(`未登记演练动作 ${method} ${path}`); return respond({ error: "未登记演练动作" }, 501); }
    return route.fallback();
  });
  // This overriding detail fixture starts from a fictional schema, not a network response.
  if (!queueOnly) await page.route("**/api/v1/deals/1", async (route) => route.fulfill({ json: {
    id: 1, title: "演示灯具询盘", customer: { id: 2, name: "演示客户" }, stage: "paid", stage_label: "已成交·备货", status: "won", value: "200.00", currency: "USD", last_activity_at: 1793577600,
    participants: [], waiting_on_us: true, waiting_on: [], overdue: [], needs_attention: true, open_tasks: 0, next_action: "核对物流", unlinked_participants: 0, conversations: [], items: [],
    tasks: {},
    events: [{ id: 1, at: "2026-11-02T08:00:00+08:00", kind: "note", summary: "虚构案子已建立" }],
    stages: ["inquiry", "sourcing", "quoting", "negotiating", "awaiting_payment", "paid", "inspection", "shipped", "delivered", "aftersales", "reorder"].map((key, index) => ({ key, label: key === "paid" ? "已成交·备货" : key, index, current: index === 5, done: index < 5 })),
  } }));
  return state;
}
