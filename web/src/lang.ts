import { ApiError, get, postForm, qs } from "./api";

export interface Language { code: string; label: string }
export interface LangSettings { my_language: string; auto_native_send: boolean; auto_translate_incoming: boolean }
export interface SettingsResult { settings: LangSettings; languages: Language[]; quota: { hourly_limit: number; used: number; remaining: number } }
export interface Term { id: number; term: string; mode: "keep" | "translate"; translation: string }
export interface ContactLanguage {
  channel: string; conversation_key: string; display_name?: string; native_language: string | null;
  source: "auto" | "manual" | null; confidence: number; updated_at: number | null; locked: boolean; country_hint: string | null;
}
export interface TranslationItem {
  index: number; source_hash: string; source_text: string; source_characters?: number; source_truncated?: boolean; text: string | null; error: string | null; cached: boolean; complete: boolean;
  chunks: { index: number; text: string | null; error: string | null }[];
}
export interface TranslationResult { target_language: string; items: TranslationItem[]; quota_exhausted?: boolean; retry_after?: number; hint?: string }
export interface Validation { ok: boolean; missing: string[]; added?: string[] }
export interface ComposeVersion { language: string; text: string; back_translation: string; validation: Validation; back_validation: Validation }
export interface ComposeResult {
  native_language: string | null; my_language: string; text: string; back_translation: string;
  english_text: string; local_text: string | null; include_local: boolean; versions: ComposeVersion[];
  warnings: { kind: string; message: string; fragments: string[] }[];
  validation: { ok: boolean; missing: string[] }; back_validation?: { ok: boolean; missing: string[] }; contact: ContactLanguage;
}
export const langError = (error: unknown) => error instanceof ApiError ? error.message : "语言功能暂时连接不上，请检查网络后重试";
export const languageName = (code: string | null | undefined, languages: Language[] = []) => !code ? "尚未确认" : languages.find((l) => l.code === code)?.label ?? code;
export const langSettings = () => get<SettingsResult>("/api/v1/lang/settings");
export const contactLanguage = (key: string) => get<{ contact: ContactLanguage }>(`/api/v1/lang/contact${qs({ conversation_key: key })}`);
export const detectLanguage = (key: string) => postForm<{ contact: ContactLanguage }>("/api/v1/lang/detect", { conversation_key: key });

export class TranslationError extends ApiError {
  result?: TranslationResult;
  constructor(status: number, message: string, result?: TranslationResult) {
    super(status, message);
    this.result = result;
  }
}

export async function translateMessages(key: string, target: string, indices: number[], mode = "manual"): Promise<TranslationResult> {
  const value = document.querySelector('meta[name="csrf-token"]')?.getAttribute("content") ?? "";
  let response: Response;
  try { response = await fetch("/api/v1/lang/translate", {
    method: "POST", credentials: "same-origin",
    headers: { "Content-Type": "application/x-www-form-urlencoded", "X-CSRF-Token": value === "__CSRF__" ? "" : value },
    body: new URLSearchParams({ conversation_key: key, target_language: target, message_indices: indices.join(","), mode }),
  }); } catch { throw new TranslationError(0, "语言服务暂时连接不上，请检查网络后重试"); }
  if (response.redirected && response.url.includes("/login")) {
    window.location.assign("/login");
    throw new TranslationError(401, "登录已过期，请重新登录");
  }
  let data: TranslationResult & { error?: string; result?: TranslationResult };
  try { data = await response.json(); } catch { throw new TranslationError(response.status, "翻译结果暂时无法读取，请稍后再试"); }
  if (!response.ok) throw new TranslationError(response.status, data.error ?? "翻译没有完成，请稍后再试", data.result);
  return data;
}

export interface TranslationPreview { conversation_key: string; source_text: string; text: string }
export async function translationPreviews(keys: string[]): Promise<TranslationPreview[]> {
  const previews: TranslationPreview[] = [];
  let batch: string[] = [];
  const flush = async () => {
    if (!batch.length) return;
    const result = await postForm<{ previews: TranslationPreview[] }>("/api/v1/lang/translate", { mode: "preview", conversation_keys: JSON.stringify(batch) });
    previews.push(...result.previews);
    batch = [];
  };
  for (const key of keys) {
    if (batch.length >= 40 || new URLSearchParams({ mode: "preview", conversation_keys: JSON.stringify([...batch, key]) }).toString().length > 7000) await flush();
    batch.push(key);
  }
  await flush();
  return previews;
}
