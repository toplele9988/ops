import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router";
import { get, postForm } from "../api";
import { formatTime } from "../format";
import { Chip, Skeleton } from "./bits";
import { useToast } from "./Toast";
import { WorkflowError } from "./WorkflowError";

interface Party {
  id: number; name: string; kind: string; codes: string[]; warnings: string[];
  party_api: string; party_href: string | null; conversations: { key: string; title: string; href: string }[];
}
interface Option {
  offer_id: number; product_id: number; supplier: Party; model: string | null; name: string;
  price: string; currency: string; unit: string | null; moq_units: number | null; lead_days_min: number | null; lead_days_max: number | null;
  specification: { wattage?: number; product_l_cm?: number; product_w_cm?: number; product_h_cm?: number; description?: string };
  packing: { units_per_carton: number | null; carton_l_cm: number | null; carton_w_cm: number | null; carton_h_cm: number | null; gross_kg: number | null; net_kg: number | null; includes_flightcase: number | null };
  requires_spec_confirmation: boolean; comparison_notice: string; validity_notice: string | null; usable_for_quote: boolean; validity: string;
  source_ref: string | null; sources: { kind: string; ref: string; title: string | null; note: string | null }[];
  quoted_on: string | null; valid_until: string | null; tax_basis: string | null; price_basis: string | null;
}
interface Selection { offer_id: number; reason: string; selected_at: string; round_id: number | null }
interface OfferItem { deal_item_id: number; name: string; qty: number | null; note: string | null; options: Option[]; selection: Selection | null; history: Selection[] }
interface Offers { customer: Party | null; participants: Party[]; items: OfferItem[]; draft_rounds: { id: number; round_no: number }[] }

function PartyReference({ party }: { party: Party }) {
  const [details, setDetails] = useState(false);
  const q = useQuery({ queryKey: ["offer-party", party.id], queryFn: () => get<{ name: string; short_name?: string; country?: string }>(party.party_api), enabled: details, retry: false });
  return <div><strong>{party.name}</strong> <span className="num">{party.codes.length ? party.codes.join(" / ") : "编号待核对"}</span>
    <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginTop: 6 }}><button type="button" className="btn sm" onClick={() => setDetails(!details)}>单位资料</button>{party.party_href && <Link className="btn sm" to={party.party_href}>打开供应商</Link>}{party.conversations.map((c) => <Link className="btn sm" to={c.href} key={c.key}>{c.title}</Link>)}</div>
    {details && <div className="t-foot">{q.isLoading ? "正在读取单位资料…" : q.isError ? `单位资料没读到：${q.error.message}，请重新打开。` : `${q.data?.name ?? party.name}${q.data?.country ? ` · ${q.data.country}` : ""}`}</div>}
    {party.warnings.map((warning) => <p key={warning} className="t-foot" style={{ color: "var(--orange)" }}>{warning}</p>)}
  </div>;
}

function OfferChoice({ option, item, dealId, rounds }: { option: Option; item: OfferItem; dealId: number; rounds: Offers["draft_rounds"] }) {
  const toast = useToast();
  const client = useQueryClient();
  const [reason, setReason] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [round, setRound] = useState("");
  const choose = useMutation({ mutationFn: () => postForm(`/api/v1/deals/${dealId}/items/${item.deal_item_id}/offer`, {
    offer_id: String(option.offer_id), reason, confirm_specs: confirmed ? "1" : "0", ...(round ? { round_id: round } : {}),
  }), retry: false, onSuccess: () => { client.invalidateQueries({ queryKey: ["deal-offers", dealId] }); client.invalidateQueries({ queryKey: ["quotes"] }); if (round) client.invalidateQueries({ queryKey: ["quote", round] }); toast("已记录方案和选择理由"); }, onError: (e: Error) => toast(e.message, "error") });
  return <div style={{ display: "grid", gap: 8 }}>
    {item.selection?.offer_id === option.offer_id && <Chip tone="green">本项已选方案</Chip>}
    <p className="t-foot">{option.comparison_notice}</p>
    {option.requires_spec_confirmation && <label className="t-foot"><input type="checkbox" checked={confirmed} onChange={(e) => setConfirmed(e.target.checked)} />我已核对规格，确认可用于这项需求</label>}
    <label className="t-foot">选择理由<textarea className="textarea" rows={2} maxLength={500} value={reason} onChange={(e) => setReason(e.target.value)} placeholder="例如：规格已核对，交期符合本案要求" /></label>
    <label className="t-foot">引用到报价轮<select aria-label="引用到报价轮" value={round} onChange={(e) => setRound(e.target.value)}><option value="">先只记录方案引用</option>{rounds.map((r) => <option key={r.id} value={r.id}>第 {r.round_no} 轮草稿</option>)}</select></label>
    <button type="button" className="btn" disabled={choose.isPending || !reason.trim() || !option.usable_for_quote || (option.requires_spec_confirmation && !confirmed)} onClick={() => choose.mutate()}>选用这个报价</button>
    <span className="t-cap">{option.validity_notice ?? (option.validity === "expired" ? "报价已过期，请向供应商确认新价" : option.validity === "expiring" ? "报价即将到期，请核对客户报价期限" : !option.usable_for_quote ? "报价尚未生效，请核对报价日" : "有效期内，选用前仍须核对条件")}</span>
  </div>;
}

export function DealOffers({ dealId }: { dealId: number }) {
  const q = useQuery({ queryKey: ["deal-offers", dealId], queryFn: () => get<Offers>(`/api/v1/deals/${dealId}/offers`), retry: false });
  const packing = (o: Option) => { const p = o.packing; return `${p.units_per_carton ?? "待确认"} 件/箱；${[p.carton_l_cm, p.carton_w_cm, p.carton_h_cm].map((v) => v ?? "?").join(" × ")} cm；毛重 ${p.gross_kg ?? "待确认"} kg；航空箱${p.includes_flightcase === 1 ? "已含" : p.includes_flightcase === 0 ? "未含" : "待确认"}`; };
  const rows: [string, (o: Option) => string][] = [
    ["产品 / 型号", (o) => `${o.name} / ${o.model || "型号待确认"}`],
    ["规格", (o) => [o.specification.wattage ? `${o.specification.wattage} W` : "", [o.specification.product_l_cm, o.specification.product_w_cm, o.specification.product_h_cm].some((v) => v !== null && v !== undefined) ? `${[o.specification.product_l_cm, o.specification.product_w_cm, o.specification.product_h_cm].map((v) => v ?? "?").join(" × ")} cm` : "", o.specification.description].filter(Boolean).join("；") || "待确认"],
    ["单价", (o) => `${o.price} ${o.currency} / ${o.unit || "单位待确认"}`], ["最少起订", (o) => o.moq_units === null ? "待确认" : `${o.moq_units} ${o.unit || "单位待确认"}`],
    ["交期", (o) => o.lead_days_min === null && o.lead_days_max === null ? "待确认" : `${o.lead_days_min ?? "?"}—${o.lead_days_max ?? "?"} 天`],
    ["包装", packing], ["报价日 / 有效期", (o) => `${o.quoted_on ?? "未填写"} / ${o.valid_until ?? "未填写"}`],
    ["价格依据", (o) => [o.price_basis, o.tax_basis].filter(Boolean).join("；") || "待确认"],
    ["来源", (o) => [o.source_ref, ...o.sources.map((s) => [s.title, s.ref].filter(Boolean).join("："))].filter(Boolean).join("；") || "来源待补充"],
  ];
  return <div className="block"><h2 className="group-title">同一需求的供应商方案</h2>
    <p className="t-foot">逐项核对型号、规格和条件；币种、单位、包装不同，不能直接按数字挑最低价。编号只供核对来源，不会自动合并单位。</p>
    {q.isLoading ? <Skeleton /> : q.isError ? <WorkflowError message={`供应商方案没读到：${q.error.message}`} refresh={() => q.refetch()} /> : <>
      {q.data?.customer && <div className="group" style={{ padding: 16, marginBottom: 12 }}>客户 <PartyReference party={q.data.customer} /></div>}
      {q.data?.items.length === 0 && <div className="group" style={{ padding: 16 }}>先确认询盘需求，或在产品清单里添加需求，再关联本案的产品供应商。</div>}
      {q.data?.items.map((item) => <div className="group" key={item.deal_item_id} style={{ marginBottom: 12, padding: 16 }}>
        <h3>{item.name} · {item.qty ? `${item.qty} 件` : "数量待确认"}</h3>{item.note && <p className="t-foot">{item.note}</p>}
        {!item.options.length ? <p className="t-sub">还没有可对照的报价。先给本案添加产品供应商，再把供应商的产品和报价入档。</p> : <div style={{ overflowX: "auto" }}><table><thead><tr><th style={{ minWidth: 100, verticalAlign: "top", textAlign: "left" }}>核对项目<div className="t-foot">客户 {q.data?.customer?.codes.join(" / ") || "编号待核对"}</div></th>{item.options.map((o) => <th key={o.offer_id} style={{ minWidth: 260, verticalAlign: "top", textAlign: "left" }}><PartyReference party={o.supplier} /></th>)}</tr></thead><tbody>
          {rows.map(([label, render]) => <tr key={label}><th style={{ textAlign: "left", verticalAlign: "top" }}>{label}</th>{item.options.map((o) => <td key={o.offer_id} style={{ padding: 10, verticalAlign: "top", whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{render(o)}</td>)}</tr>)}
          <tr><th style={{ textAlign: "left", verticalAlign: "top" }}>核对与选择</th>{item.options.map((o) => <td key={o.offer_id} style={{ padding: 10, verticalAlign: "top" }}><OfferChoice option={o} item={item} dealId={dealId} rounds={q.data!.draft_rounds} /></td>)}</tr>
        </tbody></table></div>}
        {item.selection && <p className="t-foot">已选报价 #{item.selection.offer_id} · {item.selection.reason} · {formatTime(item.selection.selected_at)}{item.selection.round_id ? <Link to={`/quotes/${item.selection.round_id}`}> · 查看引用报价轮</Link> : " · 仅记录方案，创建报价后可选草稿轮引用"}</p>}
        {item.history.length > 1 && <details><summary className="t-foot">以前的选择（{item.history.length - 1} 次）</summary>{item.history.slice(0, -1).map((s, i) => <p className="t-foot" key={i}>报价 #{s.offer_id} · {s.reason} · {formatTime(s.selected_at)}</p>)}</details>}
      </div>)}
    </>}
  </div>;
}
