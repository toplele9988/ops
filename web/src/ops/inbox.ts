import { get, postForm } from "../api";
import type { Block } from "../ui/Blocks";
export interface Conv {
  key: string; channel: string; channel_label: string; account: string; buyer_name: string; country: string; n_messages: number;
  counterpart_role: string; last: { role: string; text: string; sent_at: string }; waiting_reply: boolean; waiting_hours: number | null;
  workflow_state?: string; customer_name?: string; remind_at?: number | null; reminder_due?: boolean;
}
export interface Draft { id: number; text: string; language: string; needs_confirmation: string[]; model: string; sources?: { title: string; date?: string }[] }
export interface Detail { conversation: Conv & { first_seen: string; last_seen: string }; messages: { role: string; text: string; sent_at: string; sender_label?: string; blocks?: Block[] }[]; draft: Draft | null }
export interface Customer { key: string; buyer_name: string; country: string; channel: string; account: string; n_conversations: number; n_messages: number; last_seen: string }
export interface QueueRow { id: number; text: string; state: string; dry_run: number; conversation_key: string; source_key: string; created_at: number }
export interface Context { workflow_state: string; remind_at: number | null; reminder_due: boolean; customer_key: string | null; customer_name: string | null;
  customer: Customer | null; deals: { id: number; title: string; stage_label: string }[]; party: { name: string } | null;
  suppliers: { key: string; buyer_name: string; channel_label: string }[]; outbox: QueueRow[]; dry_run: boolean }
export interface Result { notice: string; id?: number; message?: QueueRow; recipient?: string }
export const inboxContext = (key: string) => get<Context>(`/api/v1/inbox/conversations/${key}/ops/context`);
export const inboxAction = (key: string, fields: Record<string, string>) => postForm<Result>(`/api/v1/inbox/conversations/${key}/ops/action`, fields);
export const supplierDraft = (key: string, fields: Record<string, string>) => postForm<Result>(`/api/v1/inbox/conversations/${key}/ops/supplier-draft`, fields);
