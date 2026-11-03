import type { SupplierGapSummary } from "../pages/overview/SupplierGapCard";
import { useEffect, useState } from "react";

export interface QueueItem {
  id: string; kind: string; label: string; title: string; detail: string;
  to: string; action: string; priority: number; revision: string; due_on: string | null;
}
export interface QueueResponse {
  supplier_dossiers?: SupplierGapSummary;
  generated_at: number; items: QueueItem[]; total: number; complete: boolean;
  unavailable: string[]; counts: Record<string, number>;
}
interface Decision { revision: string; action: "done" | "later"; until: number }
type Decisions = Record<string, Decision>;
const KEY = "ops.queue.feedback.v1";
export const WELCOME_KEY = "ops.queue.welcome.v1";

export function readRemembered(key: string) {
  try { return window.localStorage.getItem(key) === "closed"; } catch { return false; }
}
export function rememberClosed(key: string) {
  try { window.localStorage.setItem(key, "closed"); return true; } catch { return false; }
}
function read(): Decisions {
  try {
    const value: unknown = JSON.parse(localStorage.getItem(KEY) || "{}");
    if (!value || typeof value !== "object" || Array.isArray(value)) return {};
    return Object.fromEntries(Object.entries(value).filter(([, d]) => d && typeof d.revision === "string"
      && ["done", "later"].includes(d.action) && Number.isFinite(d.until) && d.until > Date.now()));
  } catch { return {}; }
}
function nextMorning() {
  const beijing = new Date(Date.now() + 8 * 3600_000);
  return Date.UTC(beijing.getUTCFullYear(), beijing.getUTCMonth(), beijing.getUTCDate() + 1) - 8 * 3600_000;
}
export function useQueueFeedback() {
  const [decisions, setDecisions] = useState(read);
  const [now, setNow] = useState(Date.now);
  const [notice, setNotice] = useState("");
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 15_000);
    const sync = () => { setDecisions(read()); setNow(Date.now()); };
    window.addEventListener("storage", sync);
    return () => { window.clearInterval(timer); window.removeEventListener("storage", sync); };
  }, []);
  const save = (next: Decisions, message: string) => {
    let remembered = true;
    try { localStorage.setItem(KEY, JSON.stringify(next)); } catch { remembered = false; }
    setDecisions(next); setNow(Date.now());
    setNotice(message + (remembered ? "" : " 当前浏览器无法记住，刷新后会重新提醒。"));
  };
  return {
    notice,
    hidden: (item: QueueItem) => decisions[item.id]?.revision === item.revision && decisions[item.id].until > now,
    decide: (item: QueueItem, action: "done" | "later") => save({ ...read(), [item.id]: {
      revision: item.revision, action, until: action === "later" ? Date.now() + 2 * 3600_000 : nextMorning(),
    } }, action === "later" ? "已安排 2 小时后再提醒，可随时恢复。" : "已从本机今日待办移出；新进展或明天会重新提醒。"),
    restore: () => save({}, "已恢复本机暂存的待办，请继续处理。"),
  };
}
