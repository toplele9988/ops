import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link } from "react-router";
import { postForm } from "../../api";
import { contactLanguage, detectLanguage, langError, langSettings, languageName, translateMessages, TranslationError, type ComposeResult, type TranslationItem } from "../../lang";

interface Message { text: string; role: string }
const preferenceKey = "imgw-lang-chinese-only";
const storedChoice = () => { try { return localStorage.getItem(preferenceKey) === "1"; } catch { return false; } };

export function useConversationTranslation(convKey: string, messages: Message[]) {
  const client = useQueryClient();
  const settings = useQuery({ queryKey: ["lang-settings"], queryFn: langSettings, retry: false });
  const [onlyChinese, setOnlyChinese] = useState(storedChoice);
  const [manual, setManual] = useState<Record<number, TranslationItem>>({});
  const [retryAt, setRetryAt] = useState(0);
  const [now, setNow] = useState(Date.now());
  const incoming = settings.data?.settings.auto_translate_incoming !== false;
  const automatic = useQuery({
    queryKey: ["lang-thread", convKey, messages.map(m => [m.role, m.text]), incoming],
    queryFn: () => translateMessages(convKey, "zh-CN", [], incoming ? "auto" : "cached"),
    enabled: Boolean(settings.data && messages.length), retry: false, staleTime: 0,
    refetchOnWindowFocus: false, refetchOnReconnect: false,
  });
  const { dataUpdatedAt, data } = automatic;
  useEffect(() => {
    if (dataUpdatedAt) {
      setRetryAt(data?.quota_exhausted ? dataUpdatedAt + (data.retry_after ?? 60) * 1000 : 0);
      void client.invalidateQueries({ queryKey: ["lang-previews"] });
    }
  }, [dataUpdatedAt, data, client]);
  useEffect(() => {
    if (!retryAt || !incoming) return;
    const timer = window.setInterval(() => {
      const tick = Date.now(); setNow(tick);
      if (tick >= retryAt) { window.clearInterval(timer); void automatic.refetch(); }
    }, 1000);
    return () => window.clearInterval(timer);
  }, [retryAt, incoming, automatic.refetch]);
  const retry = useMutation({
    mutationFn: (index: number) => translateMessages(convKey, "zh-CN", [index]),
    onSuccess: result => { setManual(old => ({ ...old, ...Object.fromEntries(result.items.map(item => [item.index, item])) })); void client.invalidateQueries({ queryKey: ["lang-previews"] }); },
  });
  const changeChoice = () => setOnlyChinese(old => {
    try { localStorage.setItem(preferenceKey, old ? "0" : "1"); } catch { /* Browser storage may be unavailable. */ }
    return !old;
  });
  const quotaHint = retryAt > now ? `本小时翻译额度已用完，${Math.max(1, Math.ceil((retryAt - now) / 60000))} 分钟后继续` : "";
  const message = (index: number) => {
    const item = manual[index]?.source_text === messages[index]?.text ? manual[index] : data?.items.find(i => i.index === index && i.source_text === messages[index]?.text);
    const relevant = messages[index]?.role === "buyer" && Boolean(item);
    return { item, relevant, onlyChinese, hideOriginal: Boolean(onlyChinese && item?.complete && item.text),
      pending: automatic.isFetching || (retry.isPending && retry.variables === index),
      retry: () => retry.mutate(index), quotaHint };
  };
  return { settings, message, onlyChinese, changeChoice, quotaHint,
    busy: automatic.isFetching || retry.isPending, error: automatic.error || retry.error,
    retryAll: () => automatic.refetch() };
}

export function TranslationToolbar({ translation: t }: { translation: ReturnType<typeof useConversationTranslation> }) {
  return <div className="translation-toolbar">
    <span className="t-foot">收到的消息{t.settings.data?.settings.auto_translate_incoming === false ? "可手动翻成中文" : "自动翻成中文"}</span>
    <button type="button" className="btn sm plain" onClick={t.changeChoice}>{t.onlyChinese ? "看原文" : "只看中文"}</button>
    {t.busy && <span className="t-cap" role="status">正在翻译…</span>}
    {t.quotaHint && <p className="t-foot" role="status">{t.quotaHint}</p>}
    {t.error && <p className="t-foot" role={t.error instanceof TranslationError && t.error.status === 429 ? "status" : "alert"}>{langError(t.error)} <button type="button" className="btn sm plain" onClick={t.retryAll}>重新读取译文</button></p>}
    {t.settings.error && <p role="alert">{langError(t.settings.error)}</p>}
  </div>;
}

export function MessageTranslationControl({ state, busy }: { state: ReturnType<ReturnType<typeof useConversationTranslation>["message"]>; busy: boolean }) {
  if (!state.relevant) return null;
  const item = state.item;
  return <div className="message-translation" data-testid="message-translation">
    {item?.text && <div dir="auto">{item.text}</div>}
    {item?.source_truncated && <p>原文只预览前 4000 字，译文按完整消息翻译，请到原渠道核对。</p>}
    {!item?.complete && !state.quotaHint && <>
      {item?.chunks.filter(c => c.text).map(c => <div key={c.index} dir="auto">{c.text}</div>)}
      <span>{state.pending ? "正在翻译…" : "这条消息还没翻译完成。"}</span>
      <button type="button" className="btn sm plain" disabled={busy} onClick={state.retry}>重试翻译</button>
    </>}
  </div>;
}

export function NativeComposePanel({ convKey, reply, onAdopt }: { convKey: string; reply: string; onAdopt: (text: string) => void }) {
  const client = useQueryClient();
  const settings = useQuery({ queryKey: ["lang-settings"], queryFn: langSettings, retry: false });
  const contact = useQuery({ queryKey: ["lang-contact", convKey], queryFn: () => contactLanguage(convKey), retry: false });
  const [manual, setManual] = useState("");
  const [draft, setDraft] = useState<{ source: string; result: ComposeResult } | null>(null);
  const [adopted, setAdopted] = useState("");
  const updateContact = () => { void client.invalidateQueries({ queryKey: ["lang-contact", convKey] }); setDraft(null); setAdopted(""); };
  const detect = useMutation({ mutationFn: () => detectLanguage(convKey), onSuccess: updateContact });
  const lock = useMutation({ mutationFn: () => postForm("/api/v1/lang/contact", { conversation_key: convKey, native_language: manual, locked: "1" }), onSuccess: updateContact });
  const compose = useMutation({
    mutationFn: (text: string) => postForm<ComposeResult>("/api/v1/lang/compose", { conversation_key: convKey, text }),
    onSuccess: (result, source) => { setDraft({ source, result }); setAdopted(""); void client.invalidateQueries({ queryKey: ["lang-settings"] }); },
  });
  const known = contact.data?.contact;
  const languages = settings.data?.languages ?? [];
  const result = draft?.result;
  const fresh = Boolean(draft && (draft.source === reply || (adopted && adopted === reply)));
  const expectedNative = known?.source === "manual" || (known?.confidence ?? 0) >= .8 ? known?.native_language ?? null : null;
  const sameSettings = Boolean(result && settings.data && result.native_language === expectedNative && result.my_language === settings.data.settings.my_language && result.include_local === settings.data.settings.auto_native_send);
  const safe = Boolean(result?.validation.ok && result.back_validation?.ok !== false && result.versions?.length && result.versions.every(v => v.validation.ok && v.back_validation.ok && v.back_translation.trim()) && !result.warnings.some(w => w.kind === "error"));
  return <details className="reply-translation" open aria-label="英文和当地语言回复">
    <summary>英文和当地语言回复</summary>
    <p className="t-foot">客户母语：{languageName(known?.native_language, languages)}。{!expectedNative ? "尚未确认，先生成英文。" : settings.data?.settings.auto_native_send === false || expectedNative === "en" ? "本次只生成英文。" : "英文在上，当地语言在下。"} <Link to="/system/language">语言设置</Link></p>
    <div className="inbox-actions"><button type="button" className="btn sm" disabled={detect.isPending || compose.isPending || known?.locked} onClick={() => detect.mutate()}>识别母语</button>
      <label className="t-foot">确认母语<select aria-label="确认客户母语" value={manual} onChange={e => setManual(e.target.value)}><option value="">请选择语言</option>{languages.map(l => <option key={l.code} value={l.code}>{l.label}</option>)}</select></label>
      <button type="button" className="btn sm" disabled={!manual || lock.isPending || compose.isPending} onClick={() => lock.mutate()}>锁定母语</button></div>
    <button type="button" className="btn sm primary" disabled={!reply.trim() || reply.includes("【待确认") || compose.isPending || !settings.data} onClick={() => compose.mutate(reply)}>{compose.isPending ? "正在生成回复和核对稿…" : "生成英文及当地语言稿"}</button>
    {[contact.error, settings.error, detect.error, lock.error, compose.error].filter(Boolean).map((error, i) => <p key={i} role="alert" className="t-foot" style={{ color: "var(--red)" }}>{langError(error)}</p>)}
    {result && <div className="compose-versions">
      {result.versions.map((version, i) => <section key={version.language} aria-label={i === 0 ? "英文版" : "当地语言版"}>
        <b className="t-foot">{i === 0 ? "英文版" : "当地语言版"} · {languageName(version.language, languages)}</b>
        <div className="quote" dir="auto">{version.text}</div><span className="t-cap">回译核对稿</span><div className="quote" dir="auto">{version.back_translation}</div>
      </section>)}
      {result.warnings.map((warning, i) => <p key={i} className="t-foot" role={warning.kind === "error" ? "alert" : "note"} style={{ color: warning.kind === "error" ? "var(--red)" : "var(--label-2)" }}>{warning.message}{warning.fragments.length ? `：${warning.fragments.join("、")}` : ""}</p>)}
      {(!fresh || !sameSettings) && <p className="t-foot">原稿或语言设置已改动，请重新生成。</p>}
      <button type="button" className="btn sm primary" disabled={!fresh || !sameSettings || !safe || compose.isPending} onClick={() => { setAdopted(result.text); onAdopt(result.text); }}>采用回复稿，填入回复框</button>
    </div>}
    <p className="t-cap">核对两段回复和回译稿后再采用。采用只填入回复框，发送仍须你确认。</p>
  </details>;
}
