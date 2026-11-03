import type { Tone } from "./labels";

export interface Offer {
  id: number; product_id: number; currency: string; unit: string; price: string; price_basis?: string | null; tax_basis?: string | null;
  includes_flightcase?: number | null; moq_units?: number | null; lead_days_min?: number | null; lead_days_max?: number | null;
  quoted_on: string; valid_until?: string | null; status: "suggested" | "confirmed" | "rejected"; is_current: boolean;
  validity: string; usable_for_quote: boolean; validity_notice?: string;
}
export interface Product {
  id: number; supplier_id: number | null; name: string; model?: string | null; category?: string | null; wattage?: number | null; description?: string | null;
  status: "candidate" | "confirmed" | "hidden";
  product_l_cm?: number | null; product_w_cm?: number | null; product_h_cm?: number | null;
  carton_l_cm?: number | null; carton_w_cm?: number | null; carton_h_cm?: number | null;
  gross_kg?: number | null; net_kg?: number | null; units_per_carton?: number | null;
  field_state: Record<string, { state: string; source?: string }>; packing_ready: boolean;
  current_offer?: Offer | null; validity?: string | null;
  offers?: Offer[]; sources?: { id: number; kind: string; title?: string | null; note?: string | null }[];
}
export interface Page<T> { items: T[]; total: number; page: number; page_size: number }
export interface Party {
  id: number; kind: "supplier" | "forwarder"; name: string; short_name?: string | null; conversation_ref?: string | null; notes?: string | null; status: string;
  aliases: string[]; products: { candidate: number; confirmed: number; hidden: number };
  offers: { valid: number; expiring: number; expired: number; no_expiry: number; suggested: number }; packing_coverage: number;
  terms?: Record<string, { id: number; value: string; status: string; source_ref?: string | null }[]>;
  product_items?: Product[];
}
export interface Freight {
  id: number; forwarder_id: number; origin: string; destination_country: string; destination_detail?: string | null; mode: string; service: string; unit: string;
  price: string; currency: string; min_charge?: string | null; transit_days_min?: number | null; transit_days_max?: number | null;
  quoted_on: string; valid_until?: string | null; status: "suggested" | "confirmed" | "rejected"; validity: string; usable_for_quote?: boolean; validity_notice?: string;
  forwarder_name?: string; channel?: "platform_alibaba" | "forwarder" | "other";
}

export const VALIDITY: Record<string, { text: string; tone: Tone }> = {
  valid: { text: "有效", tone: "green" },
  expiring: { text: "快到期", tone: "orange" },
  expired: { text: "已过期", tone: "red" },
  no_expiry: { text: "没写有效期", tone: "orange" },
};
export const MODE: Record<string, string> = { air: "空运", sea: "海运", express: "快递", rail: "铁路", truck: "卡车" };
export const UNIT: Record<string, string> = { kg: "每公斤", cbm: "每立方", ticket: "每票", container: "每柜" };
export const FIELD_STATE: Record<string, { text: string; tone: Tone }> = {
  confirmed: { text: "已确认", tone: "green" },
  manual: { text: "手工填写", tone: "blue" },
  extracted: { text: "系统抽取，待核对", tone: "orange" },
};

export const dims = (l?: number | null, w?: number | null, h?: number | null) =>
  l != null && w != null && h != null ? `${+l.toFixed(1)}×${+w.toFixed(1)}×${+h.toFixed(1)} cm` : null;
export const money = (price: string, currency: string) => `${currency === "CNY" ? "¥" : currency + " "}${price}`;
export const todayIso = () => new Date().toISOString().slice(0, 10);
