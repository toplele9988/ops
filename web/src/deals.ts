import type { Tone } from "./labels";

export const STAGES = [
  { key: "inquiry", label: "询盘" }, { key: "sourcing", label: "询价核实" }, { key: "quoting", label: "报价" },
  { key: "negotiating", label: "谈判" }, { key: "awaiting_payment", label: "待付款" }, { key: "paid", label: "已成交·备货" },
  { key: "inspection", label: "验货" }, { key: "shipped", label: "已发货" }, { key: "delivered", label: "已签收" },
  { key: "aftersales", label: "售后" }, { key: "reorder", label: "返单" },
] as const;

export const ROLE: Record<string, string> = { customer: "客户", supplier: "产品供应商", forwarder: "货代", vendor: "配套供应商" };
export const ROLE_SHORT: Record<string, string> = { customer: "客户", supplier: "供应商", forwarder: "货代", vendor: "配套" };
export const STATUS_TEXT: Record<string, { text: string; tone: Tone }> = {
  open: { text: "进行中", tone: "blue" }, won: { text: "已成交", tone: "green" }, lost: { text: "已丢单", tone: "plain" }, paused: { text: "暂停", tone: "orange" },
};

export interface Participant { id?: number; party_id: number; name: string; role: string; state: "us" | "them" | "none"; last_at: number | null; note?: string | null; conversations?: { key: string; channel_label: string; title: string; last_role: string; last_at: number }[] }
export interface DealRow {
  id: number; title: string; customer: { id: number; name: string; country?: string; timezone?: string } | null; stage: string; stage_label: string; status: string;
  value: string | null; currency: string; next_action?: string | null; next_action_due?: string | null; last_activity_at: number | null;
  participants: Participant[]; waiting_on_us: boolean; waiting_on: string[]; overdue: string[]; needs_attention: boolean; open_tasks: number;
}
export interface DealTask { id: number; stage: string; title: string; due?: string | null; status: "open" | "done" | "skipped"; auto: number }
export interface DealDetail extends DealRow {
  destination_country?: string | null; drafts?: { id: number; kind: string; text: string; mode: string; state: string; created_at: string }[];
  notes?: string | null; lost_reason?: string | null; unlinked_participants: number;
  conversations: { id: number; conversation_key?: string; key?: string; party_id: number | null; channel_label?: string; title?: string }[];
  items: { id: number; name: string; qty?: number | null; target_price?: string | null; currency?: string; note?: string | null }[];
  tasks: Record<string, DealTask[]>;
  events: { id: number; at: string; kind: string; summary: string; who?: string | null }[];
  stages: { key: string; label: string; index: number; current: boolean; done: boolean }[];
}
