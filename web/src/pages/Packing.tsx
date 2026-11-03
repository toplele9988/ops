import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Check, ChevronRight, Copy } from "lucide-react";
import { useSearchParams } from "react-router";
import { copyText, get, postForm, qs } from "../api";
import { ago, percent } from "../format";
import { PACK_STATUS } from "../labels";
import { Chip, Empty, Meter, SearchField, Skeleton } from "../ui/bits";
import { Segmented } from "../ui/Segmented";
import { useToast } from "../ui/Toast";

interface Supplier {
  supplier: string; total: number; with_packing: number; without_packing: number; coverage_rate: number;
  estimable: number; ready: number; last_contacted: number | null; contacted_note: string | null; priority: number;
}
interface Summary {
  ready: boolean; generated_at: string; notice: string;
  totals: { entries: number; ready: number; estimable: number; partial: number; missing: number; conflict: number; multi_model: number; value_mismatch: number };
  suppliers: Supplier[]; next_actions: { supplier: string; reason: string; count: number }[];
}
interface Item {
  id: string; label: string; supplier: string; source_file: string | null; status: string; status_text: string;
  conflict_reason: string | null; conflict_text: string | null; assumption: string | null; missing: string[];
  facts: { carton_cm: number[] | null; gross_kg: number | null; net_kg: number | null; units_per_carton: number | null; product_cm: number[] | null };
}
interface Items { items: Item[]; total: number; page: number; page_size: number }
interface Followup { supplier: string; lines: string[]; text: string; last_contacted: number | null; contacted_note: string | null }

const PAGE_SIZE = 30;
const size = (v: number[] | null) => (v ? `${v.map((n) => +n.toFixed(1)).join("×")} cm` : null);

function Facts({ item }: { item: Item }) {
  const f = item.facts;
  const parts: { k: string; v: string | null }[] = [
    { k: "外箱", v: size(f.carton_cm) },
    { k: "毛重", v: f.gross_kg != null ? `${+f.gross_kg.toFixed(2)} kg` : null },
    { k: "每箱", v: f.units_per_carton != null ? `${f.units_per_carton} 台` : null },
  ];
  return (
    <div className="facts">
      {parts.filter((p) => p.v).map((p) => <span className="fact num" key={p.k}><b>{p.k}</b>{p.v}</span>)}
      {item.missing.length > 0 && <span className="fact miss">缺：{item.missing.join("、")}</span>}
    </div>
  );
}

function Detail({ supplier, summaryEntry }: { supplier: string; summaryEntry?: Supplier }) {
  const [params, setParams] = useSearchParams();
  const toast = useToast();
  const client = useQueryClient();
  const status = params.get("status") ?? "";
  const q = params.get("q") ?? "";
  const page = Math.max(1, Number(params.get("page")) || 1);
  const setFilter = (changes: Record<string, string>, keepPage = false) => {
    const next = new URLSearchParams(params);
    for (const [key, value] of Object.entries(changes)) (value ? next.set(key, value) : next.delete(key));
    if (!keepPage) next.delete("page");
    setParams(next, { replace: true });
  };

  const items = useQuery({
    queryKey: ["packing-items", supplier, status, q, page],
    queryFn: () => get<Items>(`/api/v1/packing/items${qs({ supplier, status, q, page, page_size: PAGE_SIZE })}`),
    placeholderData: keepPreviousData,
  });
  const followup = useQuery({
    queryKey: ["packing-followup", supplier],
    queryFn: () => get<Followup>(`/api/v1/packing/followup${qs({ supplier })}`),
    retry: false,
  });
  const contacted = useMutation({
    mutationFn: () => postForm("/api/v1/packing/contacted", { supplier }),
    onSuccess: () => {
      toast("已记录：你已向这家供应商追问过。系统不会替你发消息。");
      client.invalidateQueries({ queryKey: ["packing-summary"] });
      client.invalidateQueries({ queryKey: ["packing-followup", supplier] });
    },
    onError: (error: Error) => toast(`没记录成功：${error.message}`, "error"),
  });

  const copy = async () => {
    if (!followup.data) return;
    toast((await copyText(followup.data.text)) ? "追问话术已复制，可以直接粘贴到微信" : "复制没成功，请在下方文字框里手动复制", "ok");
  };

  const total = items.data?.total ?? 0;
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const e = summaryEntry;
  const options = [
    { value: "", label: "有资料的", count: e?.with_packing },
    { value: "conflict", label: "有冲突" },
    { value: "estimable", label: "可估算", count: e?.estimable },
    { value: "partial", label: "资料不全" },
    { value: "missing", label: "暂无资料", count: e?.without_packing },
    { value: "ready", label: "资料齐全", count: e?.ready },
  ];

  return (
    <div>
      <div className="block">
        <div className="group" style={{ padding: 16 }}>
          <div style={{ display: "flex", justifyContent: "space-between", gap: 12, flexWrap: "wrap", alignItems: "flex-start" }}>
            <div>
              <h2 className="t-title">{supplier}</h2>
              {e && (
                <p className="t-sub num" style={{ marginTop: 2 }}>
                  {e.total} 条资料，其中 {e.with_packing} 条有包装信息（{percent(e.coverage_rate)}）
                  {e.last_contacted && <> · {ago(e.last_contacted)}已催过</>}
                </p>
              )}
            </div>
            <div style={{ display: "flex", gap: 8 }}>
              <button type="button" className="btn primary" disabled={!followup.data} onClick={copy}><Copy size={16} aria-hidden="true" />复制追问话术</button>
              <button type="button" className="btn" disabled={contacted.isPending} onClick={() => contacted.mutate()}><Check size={16} aria-hidden="true" />我已催过</button>
            </div>
          </div>
          {followup.isError && <p className="t-foot" style={{ marginTop: 12 }}>这家供应商没有需要追问的条目。</p>}
          {followup.data && (
            <label style={{ display: "block", marginTop: 14 }}>
              <span className="t-foot">发给供应商的话（可以先改再复制）</span>
              <textarea className="textarea" name="followup" rows={Math.min(8, followup.data.lines.length + 1)} defaultValue={followup.data.text} key={followup.data.supplier} spellCheck={false} />
            </label>
          )}
        </div>
      </div>

      <div className="toolbar">
        <Segmented label="按资料状态筛选" value={status} options={options} onChange={(value) => setFilter({ status: value })} />
      </div>
      <div className="toolbar">
        <div className="grow"><SearchField label="搜索产品" placeholder="搜产品名、来源文件…" value={q} onSearch={(value) => setFilter({ q: value })} /></div>
        {items.data && <span className="t-foot num">共 {total} 条</span>}
      </div>

      {items.isLoading && <Skeleton lines={4} />}
      {items.isError && <Empty title="没能读到明细">{(items.error as Error).message}</Empty>}
      {items.data && items.data.items.length === 0 && (
        <div className="group"><Empty title="这一类下没有条目">换一个筛选看看。</Empty></div>
      )}
      {items.data && items.data.items.length > 0 && (
        <div className="group" style={{ opacity: items.isPlaceholderData ? 0.6 : 1, transition: "opacity var(--dur-1)" }}>
          {items.data.items.map((item) => (
            <div className="row" key={item.id} style={{ alignItems: "flex-start" }}>
              <div className="row-main">
                <div className="row-title">{item.label}</div>
                {item.source_file && <div className="row-sub">来自 {item.source_file}</div>}
                <Facts item={item} />
                {item.conflict_text && <div className="row-sub" style={{ color: "var(--red)" }}>{item.conflict_text}</div>}
                {item.assumption && <div className="row-sub">{item.assumption}</div>}
              </div>
              <Chip tone={PACK_STATUS[item.status]?.tone}>{PACK_STATUS[item.status]?.text ?? item.status_text}</Chip>
            </div>
          ))}
        </div>
      )}
      {items.data && pages > 1 && (
        <div className="pager">
          <button type="button" className="btn sm" disabled={page <= 1} onClick={() => setFilter({ page: String(page - 1) }, true)}>上一页</button>
          <span className="num">第 {page} / {pages} 页</span>
          <button type="button" className="btn sm" disabled={page >= pages} onClick={() => setFilter({ page: String(page + 1) }, true)}>下一页</button>
        </div>
      )}
    </div>
  );
}

export function Packing() {
  const [params, setParams] = useSearchParams();
  const summary = useQuery({ queryKey: ["packing-summary"], queryFn: () => get<Summary>("/api/v1/packing/summary") });
  const chosen = params.get("supplier") ?? "";
  const data = summary.data;
  const suppliers = data?.suppliers ?? [];
  const effective = chosen || suppliers[0]?.supplier || "";
  const t = data?.totals;

  const choose = (name: string) => {
    const next = new URLSearchParams();
    next.set("supplier", name);
    setParams(next);
  };

  return (
    <>
      <div className="page-head">
        <div>
          <h1 className="t-large">供应商包装核对</h1>
          <p>报价和运费算得准不准，取决于每个产品的外箱尺寸、毛重和每箱数量。这里看哪家供应商缺什么，一键复制追问话术。{data?.notice && `${data.notice}。`}</p>
        </div>
      </div>

      {summary.isLoading && <Skeleton lines={4} />}
      {summary.isError && <Empty title="没能读到包装资料">{(summary.error as Error).message}</Empty>}
      {data && data.ready === false && (
        <div className="group"><Empty title="还没有生成报告">后台需要先跑一次包装资料核对。</Empty></div>
      )}

      {data && data.ready && t && (
        <>
          <div className="block">
            <h2 className="group-title">全部资料条目<span className="count num">{t.entries}</span></h2>
            <div className="group stats">
              {[
                { l: "资料齐全", v: t.ready, c: "var(--green)", h: "可直接报价" },
                { l: "可估算", v: t.estimable, c: "var(--tint)", h: "须确认每箱数量" },
                { l: "资料不全", v: t.partial, c: "var(--orange)", h: "已有一部分" },
                { l: "有冲突", v: t.conflict, c: "var(--red)", h: `其中 ${t.multi_model} 条疑似混入多个型号` },
                { l: "暂无资料", v: t.missing, c: "var(--label-2)", h: "没有任何包装信息" },
              ].map((s) => (
                <div className="stat" key={s.l}>
                  <div className="t-foot">{s.l}</div>
                  <div className="stat-n num" style={{ color: s.c }}>{s.v}</div>
                  <div className="t-cap">{s.h}</div>
                </div>
              ))}
            </div>
          </div>

          <div className="split" data-detail={Boolean(chosen)}>
            <div className="split-list">
              <h2 className="group-title">供应商<span className="count">先处理排在前面的</span></h2>
              <div className="group">
                {suppliers.map((s) => (
                  <button key={s.supplier} type="button" className="row sup" aria-current={s.supplier === effective ? "true" : undefined} onClick={() => choose(s.supplier)}>
                    <div className="row-main">
                      <div className="row-title">{s.supplier}</div>
                      <div className="row-sub num">{s.with_packing} / {s.total} 条有包装信息{s.last_contacted ? ` · ${ago(s.last_contacted)}已催` : ""}</div>
                      <div style={{ marginTop: 6 }}><Meter rate={s.coverage_rate} /></div>
                    </div>
                    <ChevronRight size={16} className="chev" aria-hidden="true" />
                  </button>
                ))}
              </div>
            </div>
            <div className="split-detail">
              {chosen && (
                <button type="button" className="btn plain back" onClick={() => setParams({})}><ArrowLeft size={16} aria-hidden="true" />全部供应商</button>
              )}
              {effective && <Detail supplier={effective} summaryEntry={suppliers.find((s) => s.supplier === effective)} />}
            </div>
          </div>
        </>
      )}
    </>
  );
}
