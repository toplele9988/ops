export interface QuoteLine {
  id: number; product_id: number | null; name: string; qty: number; unit: string | null; offer_id: number | null; cost_unit: string | null;
  cost_validity: string | null; cost_valid_until: string | null; sell_unit_price: string | null; units_per_carton: number | null; gross_kg: number | null;
}
export interface QuoteCost {
  id: number; kind: string; description: string | null; manual: number; valid_until: string | null; validity: string | null; source_note: string | null; amount: string | null;
  party_id?: number | null;
  calc?: { unit?: string; mode?: string; unit_price?: string; needs_review?: boolean; party?: { name: string }; source_offer?: { name: string; source_ref: string; conversation_key?: string | null }; quantity?: number; blocking?: { code: string; message: string }[]; [k: string]: unknown } | null;
}
export interface Issue { code: string; message: string; line_no?: number }
export interface QuoteDetail {
  id: number; deal_id: number; round_no: number; status: "draft" | "sent" | "accepted" | "rejected" | "superseded"; currency: string; incoterm: string;
  destination_country: string | null; valid_until: string | null; notes: string | null; fx_rate: string | null; fx_source: string | null; target_margin: string;
  totals: { cost_cny: string | null; revenue_cny: string | null; profit_cny: string | null; margin: string | null; revenue: string | null; cost_complete: boolean };
  per_line: { line_id: number; line_no: number; landed_cost_cny: string | null; min_price_for_target: string | null }[];
  blocking: Issue[]; warnings: Issue[]; inverted: boolean; below_target: boolean;
  fx?: { usd_cny: string; source: string; as_of: number | null } | null;
  lines: QuoteLine[]; costs: QuoteCost[];
  terms: { payment?: string; delivery_scope?: string; seller_name?: string; seller_address?: string; confirmed?: boolean; confirmed_by?: string; confirmed_at?: string };
  review?: { reviewed_by: string; reviewed_at: string; source_hash: string; lang: string } | null;
  parties: { id: number; name: string; kind: string }[];
  cost_sources: { id: number; product_id: number; name: string; party_id: number; party_name: string; unit: string; unit_price: string; currency: string; valid_until: string; source_ref: string | null }[];
  customer_conversations: { conversation_key: string; party_id: number }[];
  negotiations: { id: number; side: string; party_id?: number | null; note: string; price?: string | null; currency?: string | null; at: string; who?: string | null }[];
}
export interface QuoteText { text: string; lang: string; source_hash: string; ready: boolean; back_translation?: string; native_language?: string; contact?: { source: string; locked: boolean }; blocking?: Issue[]; missing?: string[]; notice?: string }
export interface QuoteSummary { id: number; round_no: number; status: string; currency: string; incoterm: string; sent_at: string | null; totals: QuoteDetail["totals"]; blocking: number; warnings: number }
export const QSTATUS: Record<string, { text: string; tone: "green" | "orange" | "red" | "blue" | "plain" }> = {
  draft: { text: "草稿", tone: "orange" }, sent: { text: "已发出", tone: "blue" }, accepted: { text: "客户接受", tone: "green" }, rejected: { text: "客户拒绝", tone: "plain" }, superseded: { text: "已被新一轮取代", tone: "plain" },
};
export const INCOTERMS = ["DDP", "DAP", "CIF", "FOB", "EXW"];
export const SIDE: Record<string, string> = { customer: "客户", supplier: "供应商", forwarder: "货代", vendor: "配套供应商", us: "我方" };
export const COST_KIND: Record<string, string> = { freight: "国际运费", domestic: "国内费用", vendor: "配套费用", other: "其他" };

export interface FreightOption {
  id: number; freight_quote_id: number; forwarder_name: string; origin: string; destination_country: string; mode: string; service: string; unit: string; currency: string;
  unit_price: string; min_charge: string | null; transit_days_min: number | null; transit_days_max: number | null; valid_until: string | null; validity: string; remaining_validity_days: number | null;
  channel: "platform_alibaba" | "forwarder" | "other"; calculable: boolean; calculation_reasons: string[]; amount_cny: string | null; chargeable_kg?: string | null; recommendations: ("cheapest" | "fastest" | "balanced")[];
  amount_in_quote_currency?: string | null;
}
export interface FreightOptions { destination_country: string | null; destination_unspecified: boolean; destination_notice: string | null; items: FreightOption[] }
export const CHANNEL_TEXT: Record<string, string> = { platform_alibaba: "阿里国际站物流", forwarder: "外部货代", other: "其他" };
export const REC_TEXT: Record<string, { text: string; tone: "green" | "blue" | "orange" }> = { cheapest: { text: "最便宜", tone: "green" }, fastest: { text: "最快", tone: "blue" }, balanced: { text: "价格和速度均衡", tone: "orange" } };
