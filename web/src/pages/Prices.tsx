import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Sparkles } from "lucide-react";
import { useState } from "react";
import { get, postForm, qs } from "../api";
import { MODE, Page, Party, Product, UNIT } from "../archive";
import { ago } from "../format";
import { Chip, Empty, SearchField, Sheet, Skeleton } from "../ui/bits";
import { Segmented } from "../ui/Segmented";
import { useToast } from "../ui/Toast";

interface Sug {
  id: number; kind: "offer" | "freight"; conversation_key: string; party_id: number | null; party_hint: string | null; product_text: string | null; matched_product_id: number | null;
  price_fen: number | null; currency: string | null; unit: string | null; moq_units: number | null; lead_time_text: string | null; tax_text: string | null;
  route: string | null; mode: string | null; origin: string | null; destination_country: string | null; min_charge_fen: number | null;
  transit_days_min?: number | null; transit_days_max?: number | null;
  validity_text: string | null; suggested_valid_until: string | null; quoted_on: string | null; excerpt: string | null; has_attachment: boolean; confidence: number; source: "rules" | "ai";
}
interface Summary { pending: { offer: number; freight: number }; total: number; last_scan_at: string | null }
const yuan = (fen: number | null) => (fen == null ? "" : (fen / 100).toFixed(2));
const OFFER_UNITS = ["pcs", "set", "unit", "piece", "台", "件", "套"];
const today = () => new Date().toISOString().slice(0, 10);

export function PricesPage() {
  const toast = useToast();
  const client = useQueryClient();
  const [kind, setKind] = useState<"offer" | "freight">("offer");
  const [open, setOpen] = useState<Sug | null>(null);
  const [f, setF] = useState<Record<string, string>>({});
  const [pq, setPq] = useState("");
  const sum = useQuery({ queryKey: ["extract-summary"], queryFn: () => get<Summary>("/api/v1/extract/summary"), retry: false });
  const list = useQuery({ queryKey: ["extract", kind], queryFn: () => get<Page<Sug>>(`/api/v1/extract/suggestions${qs({ status: "pending", kind, page_size: 50 })}`), retry: false });
  const products = useQuery({ queryKey: ["products-pick", pq], queryFn: () => get<Page<Product>>(`/api/v1/archive/products${qs({ q: pq, page_size: 12 })}`), enabled: open?.kind === "offer", retry: false });
  const forwarders = useQuery({ queryKey: ["parties", "forwarder"], queryFn: () => get<Page<Party>>("/api/v1/archive/parties?kind=forwarder&page_size=100"), enabled: open?.kind === "freight", retry: false });
  const refresh = () => { client.invalidateQueries({ queryKey: ["extract"] }); client.invalidateQueries({ queryKey: ["extract-summary"] }); client.invalidateQueries({ queryKey: ["audit"] }); client.invalidateQueries({ queryKey: ["archive-summary"] }); };
  const scan = useMutation({
    mutationFn: () => postForm<{ message?: string; scanned?: number; created?: number; remaining?: number }>("/api/v1/extract/scan", {}),
    onSuccess: (r) => { refresh(); toast(r.message ?? `扫了 ${r.scanned ?? 0} 个会话，新增 ${r.created ?? 0} 条建议`); },
    onError: (e: Error) => toast(e.message, "error"),
  });
  const decide = useMutation({
    mutationFn: ({ id, path, fields }: { id: number; path: "accept" | "dismiss"; fields?: Record<string, string> }) => postForm(`/api/v1/extract/suggestions/${id}/${path}`, fields ?? {}),
    onSuccess: (_r, v) => { refresh(); toast(v.path === "accept" ? "已确认入档，现在可以用来报价了" : "已忽略"); setOpen(null); },
    onError: (e: Error) => toast(e.message, "error"),
  });
  const begin = (s: Sug) => {
    setPq(""); setOpen(s);
    setF({
      valid_until: s.suggested_valid_until ?? "", price: yuan(s.price_fen), currency: s.currency ?? "CNY", unit: s.unit ?? "", quoted_on: s.quoted_on ?? today(),
      product_id: s.matched_product_id ? String(s.matched_product_id) : "", product_name: s.product_text ?? "", forwarder_id: s.party_id ? String(s.party_id) : "",
      mode: s.mode ?? "air", origin: s.origin ?? "", service: "", destination_country: s.destination_country ?? "", min_charge: yuan(s.min_charge_fen),
      channel: "forwarder", transit_days_min: s.transit_days_min != null ? String(s.transit_days_min) : "", transit_days_max: s.transit_days_max != null ? String(s.transit_days_max) : "",
    });
  };
  const set = (k: string) => (e: { target: { value: string } }) => setF({ ...f, [k]: e.target.value });
  const submit = () => {
    if (!open) return;
    const base: Record<string, string> = { price: f.price, currency: f.currency, unit: f.unit, quoted_on: f.quoted_on, valid_until: f.valid_until };
    const fields = open.kind === "offer"
      ? { ...base, ...(f.product_id ? { product_id: f.product_id } : { create_product: "1", product_name: f.product_name }) }
      : { ...base, forwarder_id: f.forwarder_id, mode: f.mode, origin: f.origin, service: f.service, destination_country: f.destination_country, channel: f.channel, ...(f.min_charge ? { min_charge: f.min_charge } : {}), ...(f.transit_days_min ? { transit_days_min: f.transit_days_min } : {}), ...(f.transit_days_max ? { transit_days_max: f.transit_days_max } : {}) };
    decide.mutate({ id: open.id, path: "accept", fields });
  };
  const ready = open && f.valid_until && f.price && f.unit && (open.kind === "offer" ? (f.product_id || f.product_name.trim()) : (f.forwarder_id && f.origin.trim() && f.service.trim() && f.destination_country.trim()));
  const s = sum.data; const items = list.data?.items ?? [];
  return (
    <>
      <div className="page-head">
        <div><h1 className="t-large">价格建议</h1><p>AI 从供应商群、货代群的聊天里找出的价格，等你核对。你确认并写上有效期，它才会进档案、才能用来报价；AI 不会替你确认。</p></div>
        <button type="button" className="btn primary" disabled={scan.isPending} onClick={() => scan.mutate()}><Sparkles size={16} aria-hidden="true" />{scan.isPending ? "抽取员在看…" : "让抽取员扫一遍"}</button>
      </div>
      <div style={{ margin: "0 0 12px" }}><Segmented value={kind} onChange={setKind} label="类型" options={[{ value: "offer", label: `产品报价${s ? ` ${s.pending.offer}` : ""}` }, { value: "freight", label: `货代运价${s ? ` ${s.pending.freight}` : ""}` }]} /></div>
      {list.isLoading && <Skeleton lines={5} />}
      {list.isError && <div className="group"><Empty title="没能读到价格建议">{(list.error as Error).message}</Empty></div>}
      {list.data && items.length === 0 && <div className="group"><Empty title="没有待确认的价格" action={<button type="button" className="btn" disabled={scan.isPending} onClick={() => scan.mutate()}>扫一遍</button>}>{s?.last_scan_at ? `上次扫描 ${ago(s.last_scan_at)}。` : "还没扫描过。"}供应商和货代在群里报的价，会出现在这里。</Empty></div>}
      {items.length > 0 && (
        <div className="group">
          {items.map((i) => (
            <div className="row" key={i.id} style={{ alignItems: "flex-start" }}>
              <div className="row-main" style={{ whiteSpace: "normal" }}>
                <div className="row-title" style={{ whiteSpace: "normal" }}>
                  {i.kind === "offer" ? (i.product_text || "（没认出产品）") : `${MODE[i.mode ?? ""] ?? "运费"} · ${i.route || i.destination_country || ""}`}
                  {" "}<span className="num">{i.price_fen != null ? `${i.currency ?? ""} ${yuan(i.price_fen)}${i.unit ? ` / ${UNIT[i.unit] ?? i.unit}` : ""}` : "价格没认出"}</span>
                  {" "}<Chip tone={i.confidence >= 0.7 ? "green" : i.confidence >= 0.5 ? "orange" : "plain"}>{i.confidence >= 0.7 ? "把握较大" : i.confidence >= 0.5 ? "有些线索" : "只能猜"}</Chip>
                  {i.has_attachment && <Chip tone="orange">有附件，要人工看</Chip>}
                </div>
                <div className="row-sub" style={{ whiteSpace: "normal" }}>{[i.party_hint && `来自：${i.party_hint}`, i.moq_units && `起订 ${i.moq_units}`, i.lead_time_text && `交期：${i.lead_time_text}`, i.tax_text, i.validity_text && `对方说有效期：${i.validity_text}`, i.suggested_valid_until && `可换算到 ${i.suggested_valid_until}`].filter(Boolean).join(" · ")}</div>
                {i.excerpt && <div className="row-sub" style={{ whiteSpace: "normal" }}>原话：“{i.excerpt}”</div>}
              </div>
              <div style={{ display: "flex", gap: 6, flex: "none" }}>
                <button type="button" className="btn sm primary" onClick={() => begin(i)}>核对并确认</button>
                <button type="button" className="btn sm" disabled={decide.isPending} onClick={() => decide.mutate({ id: i.id, path: "dismiss" })}>不是价格</button>
              </div>
            </div>
          ))}
        </div>
      )}
      <Sheet open={open != null} wide title={open?.kind === "freight" ? "核对货代运价" : "核对产品报价"} onClose={() => setOpen(null)}>
        {open?.excerpt && <p className="callout" role="note">原话：“{open.excerpt}”</p>}
        <div className="form-grid" style={{ gridTemplateColumns: "1fr 1fr" }}>
          {open?.kind === "offer" && (<>
            <label className="span2"><span>对应哪个产品</span>
              <select name="product_id" value={f.product_id} onChange={set("product_id")}><option value="">新建一个产品候选（用下面的名字）</option>{f.product_id && !(products.data?.items ?? []).some((p) => String(p.id) === f.product_id) && <option value={f.product_id}>系统匹配的产品 #{f.product_id}</option>}{(products.data?.items ?? []).map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</select></label>
            <label className="span2"><span>找产品</span><SearchField label="搜产品" placeholder="搜名称、型号…" value={pq} onSearch={setPq} /></label>
            {!f.product_id && <label className="span2"><span>新产品名称</span><input name="product_name" autoComplete="off" value={f.product_name} onChange={set("product_name")} /></label>}
          </>)}
          {open?.kind === "freight" && (<>
            <label className="span2"><span>哪家货代</span><select name="forwarder_id" value={f.forwarder_id} onChange={set("forwarder_id")}><option value="">请选择…</option>{(forwarders.data?.items ?? []).map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</select></label>
            <label><span>运价来源（请核对）</span><select name="channel" value={f.channel} onChange={set("channel")}><option value="forwarder">外部货代</option><option value="platform_alibaba">阿里国际站平台物流</option><option value="other">其他来源</option></select></label>
            <label><span>时效最短（天）</span><input inputMode="numeric" value={f.transit_days_min} onChange={set("transit_days_min")} /></label>
            <label><span>时效最长（天）</span><input inputMode="numeric" value={f.transit_days_max} onChange={set("transit_days_max")} /></label>
            <label><span>运输方式</span><select name="mode" value={f.mode} onChange={set("mode")}>{Object.entries(MODE).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></label>
            <label><span>从哪里发</span><input name="origin" autoComplete="off" placeholder="例如：广州…" value={f.origin} onChange={set("origin")} /></label>
            <label><span>服务名称</span><input name="service" autoComplete="off" placeholder="例如：空运专线…" value={f.service} onChange={set("service")} /></label>
            <label><span>目的国</span><input name="destination_country" autoComplete="off" placeholder="例如：美国…" value={f.destination_country} onChange={set("destination_country")} /></label>
            <label><span>最低收费（可不填）</span><input name="min_charge" inputMode="decimal" autoComplete="off" value={f.min_charge} onChange={set("min_charge")} /></label>
          </>)}
          <label><span>价格</span><input name="price" inputMode="decimal" autoComplete="off" value={f.price} onChange={set("price")} /></label>
          <label><span>币种</span><input name="currency" autoComplete="off" value={f.currency} onChange={(e) => setF({ ...f, currency: e.target.value.toUpperCase() })} /></label>
          <label><span>按什么计价</span><select name="unit" value={f.unit} onChange={set("unit")}><option value="">请选择…</option>{(open?.kind === "freight" ? Object.entries(UNIT) : OFFER_UNITS.map((u) => [u, u])).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></label>
          <label><span>报价日期</span><input name="quoted_on" type="date" value={f.quoted_on} onChange={set("quoted_on")} /></label>
          <label className="span2"><span>这个价格有效到哪天（必填）</span><input name="valid_until" type="date" value={f.valid_until} onChange={set("valid_until")} />{open?.validity_text && <span className="t-cap">对方原话：{open.validity_text}。{open.suggested_valid_until ? "已按消息日期换算，请核对。" : "没能确定换算成哪一天，请你定。"}</span>}</label>
        </div>
        <div className="sheet-actions"><button type="button" className="btn" onClick={() => setOpen(null)}>取消</button><button type="button" className="btn primary" disabled={!ready || decide.isPending} onClick={submit}>{decide.isPending ? "正在写入…" : "确认入档"}</button></div>
      </Sheet>
    </>
  );
}
