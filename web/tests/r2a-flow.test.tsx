import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter, Route, Routes } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, get, postForm } from "../src/api";
import { STAGES, type DealDetail } from "../src/deals";
import { FulfilBoardPage } from "../src/pages/FulfilBoard";
import { DealPage } from "../src/pages/DealPage";
import { isUnknownWrite, readAfterWriteFailure } from "../src/readback";
import { FulfilmentPanel, IssuesPanel, PaymentsPanel } from "../src/ui/DealFulfil";
import { ReadFailure, WriteRecovery, type useWriteRecovery } from "../src/ui/ReadState";

vi.mock("../src/ui/RolePanels", () => ({ SecretaryPanel: () => null, AlertList: () => null }));

const clients: QueryClient[] = [];
const makeClient = () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, retryOnMount: false, gcTime: Infinity, staleTime: Infinity, refetchOnMount: false } } });
  clients.push(client);
  return client;
};
const failed = (client: QueryClient, key: readonly unknown[], error: Error, data?: unknown) => {
  if (data !== undefined) client.setQueryData(key, data);
  client.getQueryCache().build(client, { queryKey: key }).setState({ status: "error", fetchStatus: "idle", error, errorUpdatedAt: Date.now() });
};
const render = (client: QueryClient, node: ReactNode, path = "/") => renderToStaticMarkup(
  <QueryClientProvider client={client}>
    <MemoryRouter initialEntries={[path]}><Routes><Route path={path.startsWith("/deals/") ? "/deals/:id" : "*"} element={node} /></Routes></MemoryRouter>
  </QueryClientProvider>,
);
const plain = (html: string) => html.replace(/<[^>]*>/g, "");
const buttons = (html: string) => [...html.matchAll(/<button\b([^>]*)>([\s\S]*?)<\/button>/g)]
  .map((match) => ({ text: plain(match[2]), disabled: /\bdisabled(?:=|\s|$)/.test(match[1]) }));
const noEnabledButton = (html: string, text: string) => expect(buttons(html).filter((button) => button.text.includes(text)).every((button) => button.disabled)).toBe(true);
const errors = [
  ["权限拒绝", new ApiError(403, "虚构权限错误")],
  ["服务不可用", new ApiError(503, "虚构服务错误")],
  ["读取超时", new DOMException("虚构超时", "AbortError")],
] as const;

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn(() => { throw new Error("测试只能使用假服务"); }));
});
afterEach(() => {
  clients.splice(0).forEach((client) => client.clear());
  vi.unstubAllGlobals();
});

describe("读取失败与真实空列表", () => {
  it.each(errors)("收款%s不会变成没有记录或允许再次登记", (_, error) => {
    const client = makeClient();
    failed(client, ["payments", 41], error);
    const html = render(client, <PaymentsPanel dealId={41} />);
    expect(plain(html)).toContain("没能读到");
    expect(plain(html)).toContain("重新读取");
    expect(plain(html)).not.toContain("还没有收款记录");
    expect(plain(html)).not.toContain("客户付了款，点下面");
    noEnabledButton(html, "记一笔收款");
  });

  it("成功读到空收款列表才显示登记入口", () => {
    const client = makeClient();
    client.setQueryData(["payments", 41], { items: [], summary: [] });
    const html = render(client, <PaymentsPanel dealId={41} />);
    expect(plain(html)).toContain("还没有收款记录");
    expect(buttons(html).some((button) => button.text.includes("记一笔收款") && !button.disabled)).toBe(true);
    expect(plain(html)).not.toContain("没能读到");
  });

  it("有缓存时重新读取失败仍阻止重复登记和确认", () => {
    const client = makeClient();
    failed(client, ["payments", 41], new ApiError(503, "虚构服务错误"), {
      items: [{ id: 7, kind: "deposit", amount_fen: 1200, currency: "USD", received_on: "2030-01-01", method: "wire", status: "pending", note: null, confirmed_at: null }], summary: [],
    });
    const html = render(client, <PaymentsPanel dealId={41} />);
    expect(plain(html)).toContain("没能读到");
    noEnabledButton(html, "记一笔收款");
    noEnabledButton(html, "确认收款");
    noEnabledButton(html, "作废");
  });

  it("历史成交但缺收款记录时先提示核对原始到账依据", () => {
    const client = makeClient();
    client.setQueryData(["payments", 41], { items: [], summary: [] });
    const html = render(client, <PaymentsPanel dealId={41} dealStatus="won" />);
    expect(plain(html)).toContain("核对原始到账依据");
    expect(plain(html)).not.toContain("客户付了款，点下面的按钮记一笔");
  });

  it.each(errors)("履约%s会明确显示错误和重读入口", (_, error) => {
    const client = makeClient();
    failed(client, ["fulfilment", 41], error);
    const html = render(client, <FulfilmentPanel dealId={41} />);
    expect(plain(html)).toContain("没能读到");
    expect(plain(html)).toContain("重新读取");
  });

  it.each(errors)("问题%s不会当成没有问题", (_, error) => {
    const client = makeClient();
    failed(client, ["issues", 41], error);
    const html = render(client, <IssuesPanel dealId={41} />);
    expect(plain(html)).toContain("没能读到");
    expect(plain(html)).toContain("重新读取");
    expect(plain(html)).not.toContain("没有记录。客户反映");
    noEnabledButton(html, "记一个问题或意见");
  });

  it("成功读到空问题列表才显示没有记录和新增入口", () => {
    const client = makeClient();
    client.setQueryData(["issues", 41], { items: [] });
    const html = render(client, <IssuesPanel dealId={41} />);
    expect(plain(html)).toContain("没有记录");
    expect(buttons(html).some((button) => button.text.includes("记一个问题或意见") && !button.disabled)).toBe(true);
    expect(plain(html)).not.toContain("没能读到");
  });

  it.each(errors)("履约看板%s不会当成没有成交", (_, error) => {
    const client = makeClient();
    failed(client, ["fulfil-board"], error);
    client.setQueryData(["reorder-leads"], { items: [] });
    const html = render(client, <FulfilBoardPage />);
    expect(plain(html)).toContain("没能读到");
    expect(plain(html)).toContain("重新读取");
    expect(plain(html)).not.toContain("还没有已成交的案子");
  });

  it.each(errors)("返单线索%s不会静默隐藏", (_, error) => {
    const client = makeClient();
    client.setQueryData(["fulfil-board"], { items: [], total: 0 });
    failed(client, ["reorder-leads"], error);
    const html = render(client, <FulfilBoardPage />);
    expect(plain(html)).toContain("没能读到返单");
    expect(plain(html)).toContain("重新读取");
    noEnabledButton(html, "创建返单草稿");
  });

  it("空履约看板是独立的成功状态", () => {
    const client = makeClient();
    client.setQueryData(["fulfil-board"], { items: [], total: 0 });
    client.setQueryData(["reorder-leads"], { items: [] });
    const html = render(client, <FulfilBoardPage />);
    expect(plain(html)).toContain("还没有已成交的案子");
    expect(plain(html)).not.toContain("没能读到");
  });

  it.each(errors)("案子详情%s显示恢复读取入口", (_, error) => {
    const client = makeClient();
    failed(client, ["deal", "41"], error);
    const html = render(client, <DealPage />, "/deals/41");
    expect(plain(html)).toContain("没能读到");
    expect(plain(html)).toContain("重新读取");
  });
});

const detail = (stage: string, status: string): DealDetail => ({
  id: 41, title: "虚构灯具案子", customer: { id: 3, name: "虚构客户" }, stage, stage_label: STAGES.find((item) => item.key === stage)!.label, status,
  currency: "USD", value: null, last_activity_at: null, participants: [], waiting_on_us: false, waiting_on: [], overdue: [], needs_attention: false, open_tasks: 0,
  unlinked_participants: 0, conversations: [], items: [], tasks: {}, events: [],
  stages: STAGES.map((item, index) => ({ ...item, index, current: item.key === stage, done: index < STAGES.findIndex((value) => value.key === stage) })),
});
const seedDeal = (client: QueryClient, stage: string, status: string) => {
  client.setQueryData(["deal", "41"], detail(stage, status));
  client.setQueryData(["quotes", "41"], { items: [] });
  client.setQueryData(["payments", 41], { items: [], summary: [] });
  client.setQueryData(["fulfilment", 41], { eligible: false, items: [], suggestions: [] });
  client.setQueryData(["issues", 41], { items: [] });
};

describe("付款口径与人工履约入口", () => {
  it("待付款案子没有普通更新到已成交的按钮", () => {
    const client = makeClient();
    seedDeal(client, "awaiting_payment", "open");
    const html = render(client, <DealPage />, "/deals/41");
    expect(buttons(html).some((button) => button.text.includes("进入「已成交"))).toBe(false);
  });

  it.each([["paid", "验货"], ["inspection", "已发货"], ["shipped", "已签收"], ["delivered", "售后"]])("已成交%s案子保留人工进入%s入口", (stage, label) => {
    const client = makeClient();
    seedDeal(client, stage, "won");
    const html = render(client, <DealPage />, "/deals/41");
    expect(buttons(html).some((button) => button.text.includes(label) && !button.disabled)).toBe(true);
    expect(buttons(html).some((button) => button.text.includes("重新打开"))).toBe(false);
  });

  it.each(errors)("报价%s不会出现空报价提示或允许盲目新建", (_, error) => {
    const client = makeClient();
    seedDeal(client, "awaiting_payment", "open");
    failed(client, ["quotes", "41"], error);
    const html = render(client, <DealPage />, "/deals/41");
    expect(plain(html)).toContain("没能读到报价");
    expect(plain(html)).not.toContain("还没有报价。");
    noEnabledButton(html, "新建报价");
  });

  it("履约建议展示真实reason并保留人工确认", () => {
    const client = makeClient();
    client.setQueryData(["fulfilment", 41], { eligible: true, items: [], done: 0, total: 8, overdue: false, next_due: null, suggestions: [{ stage: "inspection", reason: "虚构生产已完成，请安排验货" }] });
    const html = render(client, <FulfilmentPanel dealId={41} />);
    expect(plain(html)).toContain("虚构生产已完成，请安排验货");
    expect(buttons(html).some((button) => button.text.includes("验货") && !button.disabled)).toBe(true);
  });

  it("返单线索先预览接受售价、反馈和供应商，再由人创建草稿", () => {
    const client = makeClient();
    client.setQueryData(["fulfil-board"], { items: [], total: 0 });
    client.setQueryData(["reorder-leads"], { items: [{
      deal_id: 41, customer: { id: 3, name: "虚构客户海星" }, title: "虚构上次灯具案子", days_since_signed: 40, signed_on: "2030-01-01", suggestion: "虚构返单建议",
      products: [{ product_id: 6, name: "虚构灯具", qty: 2, sell_unit_minor: 12300, currency: "USD" }],
      suppliers: [{ id: 9, name: "虚构供应商星河", source_label: "本案供应商参与方，需核对真实供货方" }],
      feedback: [{ id: 8, kind: "feedback", kind_label: "客户意见建议", status: "resolved", description: "虚构客户建议改进外箱", resolution: "虚构厂家已改善", product_id: 6, party_id: 9 }],
    }] });
    const html = render(client, <FulfilBoardPage />);
    expect(plain(html)).toContain("虚构灯具");
    expect(plain(html)).toContain("123.00");
    expect(plain(html)).toContain("USD");
    expect(plain(html)).toContain("虚构供应商星河");
    expect(plain(html)).toContain("虚构客户建议改进外箱");
    expect(buttons(html).some((button) => button.text.includes("创建返单草稿") && !button.disabled)).toBe(true);
  });
});

describe("POST结果未知只读取核对", () => {
  it.each([400, 401, 403, 404, 409, 422])("HTTP %i明确拒绝不触发核对或重放", async (status) => {
    const read = vi.fn();
    const error = new ApiError(status, "虚构明确拒绝");
    expect(isUnknownWrite(error)).toBe(false);
    await expect(readAfterWriteFailure(error, read)).resolves.toMatchObject({ unknown: false, checked: false });
    expect(read).not.toHaveBeenCalled();
  });

  it.each([new ApiError(408, "虚构超时"), new ApiError(503, "虚构服务故障"), new TypeError("虚构断线"), new SyntaxError("虚构响应丢失")])("%s必须先读取核对", async (error) => {
    const read = vi.fn().mockResolvedValue({ items: [{ id: 7 }] });
    expect(isUnknownWrite(error)).toBe(true);
    await expect(readAfterWriteFailure(error, read)).resolves.toMatchObject({ unknown: true, checked: true });
    expect(read).toHaveBeenCalledTimes(1);
  });

  it.each(["断线", "503", "响应解析失败"])("登记收款%s后只GET一次，不重放POST", async (mode) => {
    vi.stubGlobal("document", { querySelector: () => null });
    const calls: { path: string; method: string }[] = [];
    const fakeFetch = vi.fn(async (path: string, options: RequestInit = {}) => {
      calls.push({ path, method: options.method ?? "GET" });
      if (options.method === "POST") {
        if (mode === "断线") throw new TypeError("虚构断线");
        if (mode === "503") return new Response(JSON.stringify({ error: "虚构服务故障" }), { status: 503 });
        return new Response("虚构损坏响应", { status: 200 });
      }
      return new Response(JSON.stringify({ items: [{ id: 7, status: "pending" }], summary: [] }), { status: 200 });
    });
    vi.stubGlobal("fetch", fakeFetch);
    let result;
    try { await postForm("/api/v1/deals/41/payments", { amount_fen: "1200" }); }
    catch (error) { result = await readAfterWriteFailure(error, () => get("/api/v1/deals/41/payments")); }
    expect(result).toMatchObject({ unknown: true, checked: true });
    expect(calls).toEqual([{ path: "/api/v1/deals/41/payments", method: "POST" }, { path: "/api/v1/deals/41/payments", method: "GET" }]);
  });

  it("核对也失败时保持未知，不尝试重新写入", async () => {
    const readError = new ApiError(503, "虚构读失败");
    const read = vi.fn().mockRejectedValue(readError);
    await expect(readAfterWriteFailure(new TypeError("虚构断线"), read)).resolves.toEqual({ unknown: true, checked: false, error: readError });
    expect(read).toHaveBeenCalledTimes(1);
  });

  it("读回暂时为空也不能认定上次未保存", async () => {
    const read = vi.fn().mockResolvedValue({ items: [] });
    await expect(readAfterWriteFailure(new TypeError("虚构断线"), read)).resolves.toEqual({ unknown: true, checked: true, error: null });
    expect(read).toHaveBeenCalledTimes(1);
  });

  it.each([
    ["手工建案", "/api/v1/deals", "/api/v1/deals?status=all&party=3"],
    ["复制返单", "/api/v1/deals/41/reorder", "/api/v1/deals?status=all&party=3"],
  ])("%s响应丢失时查回候选案，不自动再创建", async (_, writePath, readPath) => {
    vi.stubGlobal("document", { querySelector: () => null });
    const calls: { path: string; method: string }[] = [];
    vi.stubGlobal("fetch", vi.fn(async (path: string, options: RequestInit = {}) => {
      calls.push({ path, method: options.method ?? "GET" });
      if (options.method === "POST") throw new TypeError("虚构响应丢失");
      return new Response(JSON.stringify({ items: [{ id: 52, title: "虚构新案草稿" }] }), { status: 200 });
    }));
    let result;
    try { await postForm(writePath, {}); }
    catch (error) { result = await readAfterWriteFailure(error, () => get(readPath)); }
    expect(result).toMatchObject({ unknown: true, checked: true });
    expect(calls).toEqual([{ path: writePath, method: "POST" }, { path: readPath, method: "GET" }]);
  });

  it.each([false, true])("核对checked=%s仍需人工决定何时继续", (checked) => {
    const recovery = { state: { checking: false, checked, error: checked ? null : new ApiError(503, "虚构读失败") }, blocked: true, recheck: vi.fn(), clear: vi.fn(), fail: vi.fn() } as ReturnType<typeof useWriteRecovery>;
    const html = render(makeClient(), <WriteRecovery recovery={recovery} subject="收款登记" />);
    expect(plain(html)).toContain("结果还需核对");
    expect(plain(html)).toContain("系统没有重新提交");
    expect(buttons(html).some((button) => button.text.includes("我已核对"))).toBe(checked);
  });

  it("读错误说明不透传内部路径或异常正文", () => {
    const html = render(makeClient(), <ReadFailure subject="收款记录" error={new Error("虚构内部路径 /private/fictional.sqlite3")} retry={vi.fn()} />);
    expect(plain(html)).toContain("没能读到收款记录");
    expect(plain(html)).not.toContain("/private/");
    expect(plain(html)).toContain("重新读取");
  });
});
