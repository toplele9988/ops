export interface Payment { id: number; kind: "deposit" | "balance" | "full" | "refund"; amount_fen: number; currency: string; received_on: string; method: string; status: "pending" | "confirmed" | "void"; note: string | null; signed_amount_fen: number; confirmed_at: string | null; confirmed_by: string | null; quote_round_id: number | null; currency_note: string | null; qualifies: number }
export interface PaySummary { currency: string; received_fen: number; receivable_fen: number | null; unreceived_fen: number | null }
export interface Node {
  id: number | null; node: string; label: string; status: "todo" | "doing" | "done" | "blocked"; responsible: string; party_id: number | null; planned_on: string | null; completed_on: string | null; note: string | null;
  promised_on: string | null; inspection_result: string | null; inspector: string | null; carrier: string | null; tracking_number: string | null; etd: string | null; eta: string | null; departed_on: string | null; signed_on: string | null;
  due_on: string | null; days_remaining: number | null; overdue: boolean;
}
export interface Fulfilment { eligible: boolean; items: Node[]; done: number; total: number; next_due: string | null; overdue: boolean; suggestions: { reason: string; stage: string }[] }
export interface Issue { id: number; kind: string; responsible: string; severity: string; status: "open" | "waiting_party" | "resolved" | "closed"; description: string; resolution: string | null; created_at: string; notes: { id: number; note: string; at: string; who: string }[] }
export const PAY_KIND: Record<string, string> = { deposit: "定金", balance: "尾款", full: "全款", refund: "退款" };
export const PAY_METHOD: Record<string, string> = { wire: "电汇", paypal: "PayPal", lc: "信用证", other: "其他" };
export const NODE_STATUS: Record<string, { text: string; tone: "green" | "orange" | "red" | "plain" }> = { todo: { text: "还没开始", tone: "plain" }, doing: { text: "进行中", tone: "orange" }, done: { text: "已完成", tone: "green" }, blocked: { text: "卡住了", tone: "red" } };
export const RESP: Record<string, string> = { us: "我方", supplier: "供应商", forwarder: "货代", vendor: "配套", customer: "客户", unknown: "待判断" };
export const ISSUE_KIND: Record<string, string> = { quality: "质量", shortage: "少发错发", damage: "破损", delay: "延误", documents: "资料不符", feedback: "客户意见建议", other: "其他" };
export const ISSUE_STATUS: Record<string, { text: string; tone: "green" | "orange" | "red" | "plain" }> = { open: { text: "待处理", tone: "red" }, waiting_party: { text: "等对方回复", tone: "orange" }, resolved: { text: "已解决", tone: "green" }, closed: { text: "已关闭", tone: "plain" } };
export const SEVERITY_TEXT: Record<string, string> = { low: "轻微", medium: "一般", high: "严重", critical: "非常严重" };
/** "1234.5" -> "123450"; null when the text is not a plain amount with at most 2 decimals. */
export const toFen = (text: string): string | null => {
  const m = /^(\d{1,10})(?:\.(\d{1,2}))?$/.exec(text.trim());
  if (!m) return null;
  const fen = Number(m[1]) * 100 + Number((m[2] ?? "").padEnd(2, "0"));
  return fen > 0 ? String(fen) : null;
};
export const fromFen = (fen: number | null | undefined) => (fen == null ? "—" : (fen / 100).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 }));

export interface ReorderLead {
  deal_id: number; customer: { id: number; name: string }; title: string; days_since_signed: number; suggestion: string;
  products: { product_id: number | null; name: string; qty: number | null; sell_unit_minor: number | null; currency: string | null }[];
  suppliers: { id: number; name: string; source_label: string }[];
  feedback: { id: number; kind: string; kind_label: string; status: string; description: string; resolution: string | null; product_id: number | null; party_id: number | null }[];
}
