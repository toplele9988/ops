import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus } from "lucide-react";
import { useEffect, useState } from "react";
import { useSearchParams } from "react-router";
import { get, postForm, qs } from "../api";
import { dims, FIELD_STATE, money, Offer, Page, Party, Product, todayIso, VALIDITY } from "../archive";
import { Chip, Empty, Pick, SearchField, Sheet, Skeleton } from "../ui/bits";
import { Segmented } from "../ui/Segmented";
import { useToast } from "../ui/Toast";

const STATUS_TEXT = { candidate: "待确认", confirmed: "已确认", hidden: "已隐藏" } as const;
const TAX: Record<string, string> = { unknown: "含税情况未知" };
const FIELDS: { key: keyof Product; label: string; step?: string }[] = [
  { key: "name", label: "名称" }, { key: "model", label: "型号" }, { key: "category", label: "分类" }, { key: "wattage", label: "功率 W", step: "any" },
  { key: "product_l_cm", label: "产品 长 cm", step: "any" }, { key: "product_w_cm", label: "产品 宽 cm", step: "any" }, { key: "product_h_cm", label: "产品 高 cm", step: "any" },
  { key: "carton_l_cm", label: "外箱 长 cm", step: "any" }, { key: "carton_w_cm", label: "外箱 宽 cm", step: "any" }, { key: "carton_h_cm", label: "外箱 高 cm", step: "any" },
  { key: "gross_kg", label: "毛重 kg", step: "any" }, { key: "net_kg", label: "净重 kg", step: "any" }, { key: "units_per_carton", label: "每箱数量", step: "1" },
];

function OfferRow({ offer, productId }: { offer: Offer; productId: number }) {
  const toast = useToast();
  const client = useQueryClient();
  const act = useMutation({
    mutationFn: (action: "confirm" | "reject") => postForm(`/api/v1/archive/offers/${offer.id}/status`, { action }),
    onSuccess: () => { toast("已保存"); client.invalidateQueries({ queryKey: ["product", productId] }); client.invalidateQueries({ queryKey: ["products"] }); client.invalidateQueries({ queryKey: ["archive-summary"] }); },
    onError: (e: Error) => toast(e.message, "error"),
  });
  const v = VALIDITY[offer.validity];
  return (
    <div className="row" style={{ alignItems: "flex-start", padding: "10px 12px" }}>
      <div className="row-main">
        <div className="row-title num">{money(offer.price, offer.currency)}{offer.unit && offer.unit !== "未注明" ? ` / ${offer.unit}` : "（单位未注明）"} <Chip tone="orange" dot={false}>成本价，不对外</Chip></div>
        <div className="row-sub num">报价日 {offer.quoted_on}{offer.valid_until ? ` · 有效至 ${offer.valid_until}` : ""}{offer.moq_units ? ` · 起订 ${offer.moq_units}` : ""}{offer.lead_days_min != null ? ` · 交期 ${offer.lead_days_min}${offer.lead_days_max && offer.lead_days_max !== offer.lead_days_min ? `–${offer.lead_days_max}` : ""} 天` : ""}{offer.tax_basis ? ` · ${TAX[offer.tax_basis] ?? offer.tax_basis}` : ""}</div>
        {offer.validity_notice && offer.status === "confirmed" && <div className="row-sub" style={{ color: "var(--orange)" }}>{offer.validity_notice}</div>}
      </div>
      {offer.status === "suggested" ? (
        <span style={{ display: "flex", gap: 6 }}>
          <button type="button" className="btn sm primary" disabled={act.isPending} onClick={() => act.mutate("confirm")}>确认</button>
          <button type="button" className="btn sm" disabled={act.isPending} onClick={() => act.mutate("reject")}>不对</button>
        </span>
      ) : offer.status === "rejected" ? <Chip tone="plain">已否决</Chip> : v ? <Chip tone={v.tone}>{v.text}</Chip> : null}
    </div>
  );
}

function ProductSheet({ id, onClose }: { id: number | null; onClose: () => void }) {
  const toast = useToast();
  const client = useQueryClient();
  const detail = useQuery({ queryKey: ["product", id], queryFn: () => get<Product>(`/api/v1/archive/products/${id}`), enabled: id != null, retry: false });
  const [form, setForm] = useState<Record<string, string>>({});
  const [offer, setOffer] = useState({ price: "", unit: "台", quoted_on: todayIso(), valid_until: "", moq_units: "", lead_days_min: "", lead_days_max: "", tax_basis: "" });
  useEffect(() => {
    if (!detail.data) return;
    const next: Record<string, string> = {};
    for (const f of FIELDS) next[f.key] = detail.data[f.key] == null ? "" : String(detail.data[f.key]);
    setForm(next);
  }, [detail.data]);
  const refresh = () => { client.invalidateQueries({ queryKey: ["product", id] }); client.invalidateQueries({ queryKey: ["products"] }); client.invalidateQueries({ queryKey: ["archive-summary"] }); };
  const act = useMutation({
    mutationFn: ({ path, fields }: { path: string; fields?: Record<string, string> }) => postForm(`/api/v1/archive/products/${id}/${path}`, fields ?? {}),
    onSuccess: () => { toast("已保存"); refresh(); },
    onError: (e: Error) => toast(e.message, "error"),
  });
  const addOffer = useMutation({
    mutationFn: () => postForm(`/api/v1/archive/products/${id}/offers`, Object.fromEntries(Object.entries(offer).filter(([, v]) => v !== ""))),
    onSuccess: () => { toast("报价已记录"); setOffer({ ...offer, price: "", valid_until: "" }); refresh(); },
    onError: (e: Error) => toast(e.message, "error"),
  });
  const p = detail.data;
  const changed = p ? Object.fromEntries(FIELDS.filter((f) => (p[f.key] == null ? "" : String(p[f.key])) !== form[f.key]).map((f) => [f.key, form[f.key] ?? ""])) : {};
  const hasChange = Object.keys(changed).length > 0;

  return (
    <Sheet open={id != null} wide title={p?.name ?? "产品"} onClose={onClose}>
      {detail.isLoading && <Skeleton lines={4} />}
      {p && (
        <>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
            <Chip tone={p.status === "confirmed" ? "green" : p.status === "hidden" ? "plain" : "orange"}>{STATUS_TEXT[p.status]}</Chip>
            {p.packing_ready && <Chip tone="green">包装资料齐全</Chip>}
            {p.current_offer ? <Chip tone={VALIDITY[p.current_offer.validity]?.tone}>报价{VALIDITY[p.current_offer.validity]?.text}</Chip> : <Chip tone="plain">没有可用报价</Chip>}
          </div>
          <p className="t-foot" style={{ marginTop: 8 }}>系统抽取的内容只是候选，请核对后再「确认入库」。你改过的字段，以后同步不会覆盖。</p>

          <div className="section-title">规格与包装</div>
          <div className="form-grid">
            {FIELDS.map((f) => (
              <label key={f.key} className={f.key === "name" ? "span2" : undefined}>
                <span>{f.label}{p.field_state[f.key] && <> · <span style={{ color: FIELD_STATE[p.field_state[f.key].state]?.tone === "orange" ? "var(--orange)" : "var(--label-3)" }}>{FIELD_STATE[p.field_state[f.key].state]?.text}</span></>}</span>
                <input name={f.key} type={f.step ? "number" : "text"} step={f.step} inputMode={f.step ? "decimal" : undefined} autoComplete="off" value={form[f.key] ?? ""} onChange={(e) => setForm({ ...form, [f.key]: e.target.value })} />
              </label>
            ))}
          </div>
          <div className="sheet-actions" style={{ justifyContent: "space-between" }}>
            <span className="t-cap num">{dims(p.carton_l_cm, p.carton_w_cm, p.carton_h_cm) ? `外箱 ${dims(p.carton_l_cm, p.carton_w_cm, p.carton_h_cm)}` : "外箱尺寸还没有"}{p.gross_kg != null ? ` · 毛重 ${p.gross_kg} kg` : ""}</span>
            <button type="button" className="btn primary" disabled={!hasChange || act.isPending} onClick={() => act.mutate({ path: "update", fields: changed as Record<string, string> })}>保存修改</button>
          </div>

          <div className="section-title">供应商报价（成本价）</div>
          <div className="group">
            {(p.offers ?? []).length === 0 && <div className="row"><div className="row-main t-sub">还没有报价。</div></div>}
            {(p.offers ?? []).map((o) => <OfferRow key={o.id} offer={o} productId={p.id} />)}
          </div>
          <div className="section-title">新增一条报价</div>
          <div className="form-grid">
            <label><span>价格（元）</span><input name="price" inputMode="decimal" autoComplete="off" placeholder="例如 128.50…" value={offer.price} onChange={(e) => setOffer({ ...offer, price: e.target.value })} /></label>
            <label><span>单位</span><input name="unit" autoComplete="off" value={offer.unit} onChange={(e) => setOffer({ ...offer, unit: e.target.value })} /></label>
            <label><span>报价日期</span><input name="quoted_on" type="date" value={offer.quoted_on} onChange={(e) => setOffer({ ...offer, quoted_on: e.target.value })} /></label>
            <label><span>有效期至（必填才能用于报价）</span><input name="valid_until" type="date" value={offer.valid_until} onChange={(e) => setOffer({ ...offer, valid_until: e.target.value })} /></label>
            <label><span>起订量</span><input name="moq_units" inputMode="numeric" autoComplete="off" value={offer.moq_units} onChange={(e) => setOffer({ ...offer, moq_units: e.target.value })} /></label>
            <label><span>交期最短（天）</span><input name="lead_days_min" inputMode="numeric" autoComplete="off" value={offer.lead_days_min} onChange={(e) => setOffer({ ...offer, lead_days_min: e.target.value })} /></label>
            <label><span>交期最长（天）</span><input name="lead_days_max" inputMode="numeric" autoComplete="off" value={offer.lead_days_max} onChange={(e) => setOffer({ ...offer, lead_days_max: e.target.value })} /></label>
            <label><span>含税说明</span><input name="tax_basis" autoComplete="off" placeholder="如 含13%税…" value={offer.tax_basis} onChange={(e) => setOffer({ ...offer, tax_basis: e.target.value })} /></label>
          </div>
          <div className="sheet-actions"><button type="button" className="btn" disabled={!offer.price || addOffer.isPending} onClick={() => addOffer.mutate()}><Plus size={16} aria-hidden="true" />记录报价</button></div>

          {(p.sources ?? []).length > 0 && (<><div className="section-title">资料出处</div><ul className="t-foot" style={{ margin: 0, paddingLeft: 18 }}>{p.sources!.map((s) => <li key={s.id}>{s.title ?? s.kind}{s.note ? `（${s.note}）` : ""}</li>)}</ul></>)}

          <div className="sheet-actions" style={{ marginTop: 22 }}>
            {p.status !== "hidden" ? <button type="button" className="btn" onClick={() => act.mutate({ path: "hide" })}>隐藏</button> : <button type="button" className="btn" onClick={() => act.mutate({ path: "restore" })}>恢复</button>}
            {p.status === "candidate" && <button type="button" className="btn primary" disabled={act.isPending} onClick={() => act.mutate({ path: "confirm" })}>确认入库</button>}
            <button type="button" className="btn plain" onClick={onClose}>关闭</button>
          </div>
        </>
      )}
    </Sheet>
  );
}

export function Products() {
  const [params, setParams] = useSearchParams();
  const [page, setPage] = useState(1);
  const status = params.get("status") ?? "candidate";
  const supplier = params.get("supplier") ?? "";
  const packing = params.get("packing") ?? "";
  const price = params.get("price") ?? "";
  const q = params.get("q") ?? "";
  const open = params.get("open") ? Number(params.get("open")) : null;
  const set = (changes: Record<string, string>) => {
    const next = new URLSearchParams(params);
    for (const [key, value] of Object.entries(changes)) (value ? next.set(key, value) : next.delete(key));
    setParams(next, { replace: true });
  };
  useEffect(() => setPage(1), [status, supplier, packing, price, q]);

  const parties = useQuery({ queryKey: ["parties", "supplier"], queryFn: () => get<Page<Party>>("/api/v1/archive/parties?kind=supplier&page_size=100"), retry: false });
  const summary = useQuery({ queryKey: ["archive-summary"], queryFn: () => get<{ products: { candidate: number; confirmed: number; hidden: number } }>("/api/v1/archive/summary"), retry: false });
  const list = useQuery({
    queryKey: ["products", status, supplier, packing, price, q, page],
    queryFn: () => get<Page<Product>>(`/api/v1/archive/products${qs({ status, supplier, packing, price, q, page, page_size: 30 })}`),
    placeholderData: keepPreviousData, retry: false,
  });
  const names = new Map((parties.data?.items ?? []).map((x) => [x.id, x.short_name || x.name]));
  const pages = list.data ? Math.max(1, Math.ceil(list.data.total / 30)) : 1;
  const c = summary.data?.products;

  return (
    <>
      <div className="page-head">
        <div>
          <h1 className="t-large">产品</h1>
          <p>每个产品的规格、外箱尺寸、毛重和供应商报价都在这里。系统从资料里抽出的内容先放在「待确认」，你核对后点「确认入库」才算数。没写有效期的报价不会用来报价。</p>
        </div>
      </div>
      <div className="toolbar">
        <Segmented label="产品状态" value={status} onChange={(value) => set({ status: value })}
          options={[{ value: "candidate", label: "待确认", count: c?.candidate }, { value: "confirmed", label: "已确认", count: c?.confirmed }, { value: "hidden", label: "已隐藏", count: c?.hidden }]} />
      </div>
      <div className="toolbar">
        <div className="grow"><SearchField label="搜索产品" placeholder="搜名称、型号…" value={q} onSearch={(value) => set({ q: value })} /></div>
        <Pick label="供应商" value={supplier} onChange={(value) => set({ supplier: value })} options={[{ value: "", label: "全部供应商" }, ...(parties.data?.items ?? []).map((x) => ({ value: String(x.id), label: x.short_name || x.name }))]} />
        <Pick label="包装资料" value={packing} onChange={(value) => set({ packing: value })} options={[{ value: "", label: "包装不限" }, { value: "ready", label: "包装齐全" }, { value: "missing", label: "缺包装资料" }]} />
        <Pick label="报价" value={price} onChange={(value) => set({ price: value })} options={[{ value: "", label: "报价不限" }, { value: "valid", label: "有效" }, { value: "expiring", label: "快到期" }, { value: "expired", label: "已过期" }, { value: "none", label: "没有可用报价" }]} />
        {list.data && <span className="t-foot num">共 {list.data.total} 个</span>}
      </div>

      {list.isLoading && <Skeleton lines={5} />}
      {list.isError && <div className="group"><Empty title="没能读到产品">{(list.error as Error).message}</Empty></div>}
      {list.data && list.data.items.length === 0 && <div className="group"><Empty title="没有符合条件的产品">{status === "candidate" ? "候选产品都处理完了。" : "换个筛选看看。"}</Empty></div>}
      {list.data && list.data.items.length > 0 && (
        <div className="group" style={{ opacity: list.isPlaceholderData ? 0.6 : 1 }}>
          {list.data.items.map((p) => (
            <button key={p.id} type="button" className="row" onClick={() => set({ open: String(p.id) })} style={{ alignItems: "flex-start" }}>
              <div className="row-main">
                <div className="row-title">{p.name}{p.model && p.model !== p.name ? <span className="t-cap">　{p.model}</span> : null}</div>
                <div className="row-sub num">
                  {p.supplier_id ? names.get(p.supplier_id) ?? "供应商" : "未注明供应商"}
                  {p.wattage ? ` · ${p.wattage}W` : ""}
                  {dims(p.carton_l_cm, p.carton_w_cm, p.carton_h_cm) ? ` · 外箱 ${dims(p.carton_l_cm, p.carton_w_cm, p.carton_h_cm)}` : ""}
                  {p.gross_kg != null ? ` · 毛重 ${p.gross_kg}kg` : ""}
                </div>
              </div>
              <span style={{ display: "flex", gap: 6, flexWrap: "wrap", justifyContent: "flex-end" }}>
                {p.packing_ready ? <Chip tone="green">包装齐</Chip> : <Chip tone="plain">缺包装</Chip>}
                {p.current_offer ? <Chip tone={VALIDITY[p.current_offer.validity]?.tone}>报价{VALIDITY[p.current_offer.validity]?.text}</Chip> : <Chip tone="plain">无可用报价</Chip>}
              </span>
            </button>
          ))}
        </div>
      )}
      {list.data && pages > 1 && (
        <div className="pager">
          <button type="button" className="btn sm" disabled={page <= 1} onClick={() => setPage(page - 1)}>上一页</button>
          <span className="num">{page} / {pages}</span>
          <button type="button" className="btn sm" disabled={page >= pages} onClick={() => setPage(page + 1)}>下一页</button>
        </div>
      )}
      <ProductSheet id={open} onClose={() => set({ open: "" })} />
    </>
  );
}
