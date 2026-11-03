import { get, postForm } from "./api";

export interface AlibabaOpenStatus {
  configured: boolean;
  authorized: boolean;
  token_expires_in: number | null;
  last_poll_at: number | null;
  last_poll_result: string | null;
  next_step: string;
  paused: boolean;
  daily_limit: number;
  calls_today: number;
  next_poll_at: number | null;
  network_enabled?: boolean;
}

export interface AlibabaOrderCandidate {
  order_id: string;
  amount: string;
  currency: string;
  buyer_id: string;
  payment_status: string;
  paid_at_ms: number | null;
  modified_at_ms: number;
  deal_id: number;
  claimed: boolean;
}

const prefix = "/api/v1/alibaba-open";
export const alibabaOpenStatus = () => get<AlibabaOpenStatus>(`${prefix}/status`);
export const alibabaOpenCandidates = () => get<{ items: AlibabaOrderCandidate[] }>(`${prefix}/candidates`);
export const alibabaOpenAuthorization = () => get<{ url: string }>(`${prefix}/authorization`);
export const authorizeAlibabaOpen = (code: string) => postForm(`${prefix}/authorize`, { code, confirm: "1" });
export const pollAlibabaOpen = () => postForm(`${prefix}/poll`, { confirm: "1" });
export const saveAlibabaOpenSettings = (paused: boolean, dailyLimit: string) =>
  postForm(`${prefix}/settings`, { paused: paused ? "1" : "0", daily_limit: dailyLimit, confirm: "1" });
export const claimAlibabaOpenCandidate = (orderId: string, dealId: string) =>
  postForm(`${prefix}/candidates/${encodeURIComponent(orderId)}/claim`, { deal_id: dealId, confirm: "1" });
