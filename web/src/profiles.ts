import { ApiError, get, qs } from "./api";

export type CustomerStage = "potential" | "quoted" | "customer" | "after_sales" | "dormant";
export type ProfileChannel = "wechat" | "alibaba" | "whatsapp";
export type ProfileTime = number | string | null;
export interface ProfileEvidence { claim: string; quote: string; message_index: number; sent_at: ProfileTime }
export interface ConversationProfile {
  conversation_key: string;
  channel: ProfileChannel;
  title: string;
  kind: string;
  customer_stage: CustomerStage | null;
  org_name: string | null;
  people: { name: string; role: string }[];
  country: string | null;
  country_name: string | null;
  language: string | null;
  products: string[];
  summary: string;
  evidence: ProfileEvidence[];
  confidence: number | null;
  messages_read: number;
  messages_total: number;
  first_at: ProfileTime;
  last_at: ProfileTime;
  last_inbound_at: ProfileTime;
  state: "ready" | "partial" | "failed" | "skipped";
  updated_at: ProfileTime;
}
export interface ProfileListing {
  items: ConversationProfile[];
  counts: { kind: Record<string, number>; stage: Record<CustomerStage, number> };
  updated_at: ProfileTime;
}

// The backend is delivered separately. An absent route is an expected waiting state.
async function optionalProfile<T>(url: string): Promise<T | null> {
  try { return await get<T>(url); }
  catch (error) {
    if (error instanceof ApiError && [404, 405, 501].includes(error.status)) return null;
    throw error;
  }
}
export async function readProfiles(filters: { stage?: CustomerStage; channel?: ProfileChannel; q?: string } = {}) {
  const result = await optionalProfile<ProfileListing>("/api/v1/profiles" + qs({ kind: "customer", ...filters }));
  if (result && !Array.isArray(result.items)) throw new Error("客户分析暂时没读完整，请稍后重试");
  return result;
}
export const readProfile = (key: string) => optionalProfile<ConversationProfile>(`/api/v1/profiles/${encodeURIComponent(key)}`);
