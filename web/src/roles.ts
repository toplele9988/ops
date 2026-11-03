export interface Run<T = unknown> {
  id: number; role: string; subject_key: string; status: "ok" | "fallback" | "failed"; source: "ai" | "rules"; model: string | null;
  output_json: T; output_text: string; error: string | null; created_at: string; reused?: boolean;
}
export interface SecretaryOut {
  headline: string; suggested_stage?: string; suggested_stage_label?: string;
  waiting_on: { party: string; who: string; what: string; elapsed: string; state: string }[];
  next_steps: { who: string; what: string; why: string }[];
  risks: (string | { message?: string; text?: string })[];
}
export interface BriefOut {
  narrative: string; pending_triage: number; negotiating: number;
  results: Record<"today" | "week" | "month", { count: number; amount_pending: number; amounts: Record<string, string> }>;
  quotes_this_week: { sent: number; accepted: number };
  lost_this_week: { count: number };
  priorities: { kind: string; message: string; severity: string; deal_id: number | null }[];
}
export interface Triage {
  id: number; conversation_key: string; action: "link" | "create" | "identity" | "ignore"; suggested_deal_id: number | null; suggested_party_id: number | null;
  suggested_party_kind: string | null; suggested_party_name?: string | null; reason: string; confidence: number; source: "rules" | "ai"; status: string;
  conversation: { channel: string; counterpart_name: string; last_at: number; preview: string };
}
export interface Alert { id: number; kind: string; deal_id: number | null; message: string; severity: "high" | "medium" | "low"; first_seen_at: string; last_seen_at: string }
export interface RoleInfo { key: string; name: string; responsibility: string; level: string; last_run_at: string | null; last_status: string | null; last_source: string | null; runs_24h: number; failures_24h: number; model: string; last_model: string | null }
export const SEVERITY = { high: { text: "紧急", tone: "red" }, medium: { text: "留意", tone: "orange" }, low: { text: "提示", tone: "plain" } } as const;
export const PARTY_KIND: Record<string, string> = { customer: "客户", supplier: "产品供应商", forwarder: "货代", vendor: "配套供应商" };
export const ACTION_TEXT: Record<string, string> = { link: "加入已有案子", create: "新建案子", identity: "确认这是谁", ignore: "忽略" };
export const riskText = (r: SecretaryOut["risks"][number]) => (typeof r === "string" ? r : r.message ?? r.text ?? "");
