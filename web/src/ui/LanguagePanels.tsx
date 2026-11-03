import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Languages } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router";
import { postForm } from "../api";
import { contactLanguage, detectLanguage, langError, langSettings, languageName, translateMessages, TranslationError, type ComposeResult, type TranslationItem } from "../lang";
import { Chip, Switch } from "./bits";
import { useToast } from "./Toast";

interface Message { text: string }
interface StoredTranslation { original: string; target: string; item: TranslationItem }

export function useConversationTranslation(convKey: string, messages: Message[]) {
  const settings = useQuery({ queryKey: ["lang-settings"], queryFn: langSettings, retry: false });
  const [targetOverride, setTargetOverride] = useState("");
  const target = targetOverride || settings.data?.settings.my_language || "zh-CN";
  const [showAll, setShowAll] = useState(false);
  const [visible, setVisible] = useState<Record<number, boolean>>({});
  const [stored, setStored] = useState<Record<number, StoredTranslation>>({});
  const [pending, setPending] = useState<Set<number>>(new Set());
  const [error, setError] = useState("");
  const inFlight = useRef(false);
  const current = (index: number) => {
    const value = stored[index];
    return value?.original === messages[index]?.text && value.target === target ? value.item : undefined;
  };
  const run = async (indices: number[]) => {
    if (inFlight.current || !indices.length) return;
    inFlight.current = true;
    setError("");
    const originals = messages.map((m) => m.text);
    const requestedTarget = target;
    const merge = (items: TranslationItem[]) => {
      if (items.some((item) => originals[item.index] !== item.source_text)) setError("会话消息已更新，部分译文未显示。请重新读取会话后翻译");
      setStored((old) => {
        const next = { ...old };
        for (const item of items) {
          if (originals[item.index] !== item.source_text) continue;
          next[item.index] = { original: originals[item.index], target: requestedTarget, item };
        }
        return next;
      });
    };
    try {
      for (let offset = 0; offset < indices.length; offset += 20) {
        const batch = indices.slice(offset, offset + 20);
        setPending(new Set(batch));
        try {
          const result = await translateMessages(convKey, requestedTarget, batch);
          merge(result.items);
        } catch (e) {
          if (e instanceof TranslationError && e.result?.items) merge(e.result.items);
          throw e;
        }
      }
    } catch (e) { setError(langError(e)); }
    finally { inFlight.current = false; setPending(new Set()); }
  };
  const toggleAll = (value: boolean) => {
    setShowAll(value);
    setVisible({});
    if (value) void run(messages.flatMap((m, i) => m.text.trim() && !current(i)?.complete ? [i] : []));
  };
  const changeTarget = (value: string) => { setTargetOverride(value); setShowAll(false); setVisible({}); setError(""); };
  const toggleMessage = (index: number) => {
    const item = current(index);
    if (item?.complete) setVisible((old) => ({ ...old, [index]: !(old[index] ?? showAll) }));
    else { setVisible((old) => ({ ...old, [index]: true })); void run([index]); }
  };
  return {
    settings, target, changeTarget, showAll, toggleAll, error, busy: pending.size > 0,
    message: (index: number) => ({ item: current(index), showing: visible[index] ?? showAll, pending: pending.has(index), toggle: () => toggleMessage(index) }),
  };
}

export function TranslationToolbar({ translation }: { translation: ReturnType<typeof useConversationTranslation> }) {
  const t = translation;
  return (
    <div style={{ padding: "8px 16px", borderBottom: "1px solid var(--sep)", display: "grid", gap: 6 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
        <Languages size={16} aria-hidden="true" />
        <span className="t-foot">显示译文</span><Switch checked={t.showAll} onChange={t.toggleAll} label="显示这个会话的译文" disabled={t.busy || !t.settings.data} />
        <label className="select" style={{ maxWidth: 180 }}><span className="sr-only">这个会话的阅读语言</span><select value={t.target} disabled={t.busy || !t.settings.data} onChange={(e) => t.changeTarget(e.target.value)}>{(t.settings.data?.languages ?? [{ code: "zh-CN", label: "简体中文" }]).map((l) => <option key={l.code} value={l.code}>{l.label}</option>)}</select></label>
        {t.busy && <span className="t-cap" role="status">正在分批翻译，已完成的会保留…</span>}
      </div>
      {t.error && <p className="t-foot" role="alert" style={{ color: "var(--red)" }}>{t.error} 已完成的译文已保留，可点失败消息旁的「重试翻译」。</p>}
      {t.settings.error && <p className="t-foot" role="alert" style={{ color: "var(--red)" }}>{langError(t.settings.error)} <button type="button" className="btn sm plain" onClick={() => t.settings.refetch()}>重新读取</button></p>}
    </div>
  );
}

export function MessageTranslationControl({ state, busy }: { state: ReturnType<ReturnType<typeof useConversationTranslation>["message"]>; busy: boolean }) {
  const item = state.item;
  return (
    <div style={{ display: "grid", gap: 4, marginTop: 4 }}>
      <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
        <button type="button" className="btn sm plain" style={{ minHeight: 24, padding: "1px 6px" }} disabled={busy} onClick={state.toggle}>{state.pending ? "正在翻译…" : item?.complete ? state.showing ? "看原文" : "看译文" : item?.error ? "重试翻译" : "译"}</button>
        {state.showing && item?.complete && <span className="t-cap">译文 · 请核对专业词{item.cached ? " · 已缓存" : ""}</span>}
      </div>
      {state.showing && item?.source_truncated && <p className="t-foot" style={{ color: "var(--orange)" }}>原文预览仅显示前 4000 字，完整消息共 {item.source_characters ?? "多于 4000"} 字。{item.complete ? "此译文包含完整消息，请到原渠道核对原文。" : "当前译文还未完成，请重试失败部分。"}</p>}
      {state.showing && item?.error && <p role="alert" className="t-foot" style={{ color: "var(--red)" }}>{item.error}{item.chunks?.some((c) => c.text) ? " 已完成的部分已保留，重试只补失败部分。" : ""}</p>}
      {state.showing && item && !item.complete && item.chunks?.some((c) => c.text) && <div className="t-foot" dir="auto" style={{ whiteSpace: "pre-wrap" }}>{item.chunks.filter((c) => c.text).map((c) => c.text).join("\n")}<p style={{ color: "var(--orange)" }}>这是已完成的部分，完整译文还需重试。</p></div>}
    </div>
  );
}

export function NativeComposePanel({ convKey, reply, onAdopt, onGateChange }: {
  convKey: string; reply: string; onAdopt: (text: string) => void; onGateChange: (blocked: boolean) => void;
}) {
  const client = useQueryClient();
  const toast = useToast();
  const settings = useQuery({ queryKey: ["lang-settings"], queryFn: langSettings, retry: false });
  const contact = useQuery({ queryKey: ["lang-contact", convKey], queryFn: () => contactLanguage(convKey), retry: false });
  const [enabledOverride, setEnabledOverride] = useState<boolean | null>(null);
  const enabled = enabledOverride ?? settings.data?.settings.auto_native_send ?? true;
  const [manual, setManual] = useState("");
  const [draft, setDraft] = useState<{ source: string; result: ComposeResult } | null>(null);
  const [adopted, setAdopted] = useState<string | null>(null);
  const known = contact.data?.contact;
  const languages = settings.data?.languages ?? [];
  const fresh = Boolean(draft && (draft.source === reply || adopted === reply));
  const checked = Boolean(draft?.result.validation.ok && draft.result.back_validation?.ok !== false && !draft.result.warnings.some((w) => w.kind === "error") && draft.result.back_translation.trim());
  const ready = Boolean(checked && adopted === reply && draft && known?.native_language === draft.result.native_language && draft.result.my_language === settings.data?.settings.my_language);
  const latestReply = useRef(reply);
  latestReply.current = reply;
  useEffect(() => { onGateChange(enabled && !ready); }, [enabled, ready, onGateChange]);
  const update = () => { client.invalidateQueries({ queryKey: ["lang-contact", convKey] }); client.invalidateQueries({ queryKey: ["lang-contacts"] }); setAdopted(null); setDraft(null); };
  const detect = useMutation({ mutationFn: () => detectLanguage(convKey), onSuccess: (data) => { update(); toast(data.contact.native_language ? "已识别语言，请核对客户母语" : "还无法确定母语，请手动确认"); }, onError: (e: Error) => toast(langError(e), "error") });
  const lock = useMutation({
    mutationFn: () => postForm("/api/v1/lang/contact", { conversation_key: convKey, native_language: manual, locked: "1" }),
    onSuccess: () => { update(); setManual(""); toast("母语已锁定，自动识别不会覆盖"); }, onError: (e: Error) => toast(langError(e), "error"),
  });
  const compose = useMutation({
    mutationFn: (text: string) => postForm<ComposeResult>("/api/v1/lang/compose", { conversation_key: convKey, text }),
    onSuccess: (result, source) => { setDraft({ source, result }); setAdopted(null); if (latestReply.current !== source) toast("起草期间回复已修改，请用当前内容重新生成", "error"); client.invalidateQueries({ queryKey: ["lang-settings"] }); },
    onError: (e: Error) => toast(langError(e), "error"),
  });
  const hasPlaceholder = reply.includes("【待确认");
  return (
    <section aria-label="按客户母语发送" style={{ padding: "10px 0", marginBottom: 8, borderBottom: "1px solid var(--sep)", display: "grid", gap: 8 }}>
      <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}><label style={{ display: "flex", gap: 8, alignItems: "center" }}><input type="checkbox" checked={enabled} disabled={compose.isPending} onChange={(e) => { setEnabledOverride(e.target.checked); setAdopted(null); }} /><b className="t-sub">按客户母语发送</b></label><Link className="t-cap" to="/system/language">语言设置</Link></div>
      {enabled && <>
        <div className="t-foot">客户母语：<b>{languageName(known?.native_language, languages)}</b>{known?.locked && <> <Chip tone="green">手动锁定</Chip></>}{known?.native_language && !known.locked && <span> · 自动识别，需核对</span>}</div>
        {contact.error && <p role="alert" className="t-foot" style={{ color: "var(--red)" }}>{langError(contact.error)} <button type="button" className="btn sm plain" onClick={() => contact.refetch()}>重新读取</button></p>}
        {!known?.locked && <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}><button type="button" className="btn sm" disabled={detect.isPending || compose.isPending} onClick={() => detect.mutate()}>{detect.isPending ? "正在识别…" : "识别母语"}</button>{known?.country_hint && <span className="t-cap">国家提示：{languageName(known.country_hint, languages)}，不能据此确定母语</span>}</div>}
        <div className="form-grid" style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "end" }}><label style={{ flex: "1 1 160px" }}><span>{known?.locked ? "更改锁定语言" : "手动确认母语"}</span><select value={manual} disabled={!settings.data || compose.isPending} onChange={(e) => setManual(e.target.value)}><option value="">请选择语言</option>{languages.map((l) => <option key={l.code} value={l.code}>{l.label}</option>)}</select></label><button type="button" className="btn sm" disabled={!manual || lock.isPending || compose.isPending} onClick={() => lock.mutate()}>锁定母语</button></div>
        {!known?.native_language && <p className="t-foot" style={{ color: "var(--orange)" }}>先识别或手动确认客户母语，再生成母语版。</p>}
        {settings.error && <p role="alert" className="t-foot" style={{ color: "var(--red)" }}>{langError(settings.error)} <button type="button" className="btn sm plain" onClick={() => settings.refetch()}>重新读取</button></p>}
        <div><button type="button" className="btn sm primary" disabled={!reply.trim() || !known?.native_language || compose.isPending || hasPlaceholder || !settings.data} onClick={() => compose.mutate(reply)}>{compose.isPending ? "正在生成母语版和回译稿…" : "生成母语版与回译核对稿"}</button></div>
        {hasPlaceholder && <p className="t-foot" style={{ color: "var(--orange)" }}>原稿还有【待确认】，先补齐内容再翻译。</p>}
        {compose.error && <p className="t-foot" role="alert" style={{ color: "var(--red)" }}>{langError(compose.error)}</p>}
        {draft && <div style={{ display: "grid", gap: 8 }}>
          <div><div className="t-cap">客户母语版 · {languageName(draft.result.native_language, languages)}</div><div className="quote" dir="auto">{draft.result.text}</div></div>
          <div><div className="t-cap">回译核对稿 · {languageName(draft.result.my_language, languages)}</div><div className="quote" dir="auto">{draft.result.back_translation}</div></div>
          {draft.result.warnings.map((w, i) => <div key={i} className="t-foot" role={w.kind === "error" || w.fragments.length ? "alert" : "note"} style={{ color: w.kind === "error" || w.fragments.length ? "var(--red)" : "var(--label-2)" }}>{w.message}{w.fragments.length > 0 && <span>：{w.fragments.join("、")}</span>}</div>)}
          {!draft.result.validation.ok && <p className="t-foot" role="alert" style={{ color: "var(--red)" }}>核对未通过：数字、型号、金额或链接发生变化。请调整原稿后重新生成。{draft.result.validation.missing.join("、")}</p>}
          {draft.result.back_validation?.ok === false && <p className="t-foot" role="alert" style={{ color: "var(--red)" }}>回译核对未通过，请重新生成后再采用。{draft.result.back_validation.missing.join("、")}</p>}
          {(draft.result.native_language !== known?.native_language || draft.result.my_language !== settings.data?.settings.my_language) && <p className="t-foot" style={{ color: "var(--orange)" }}>语言设置已改动，请按当前语言重新生成。</p>}
          {!fresh && <p className="t-foot" style={{ color: "var(--orange)" }}>回复内容已改动，请重新生成并核对。</p>}
          <div><button type="button" className="btn sm primary" disabled={!fresh || !checked || draft.result.my_language !== settings.data?.settings.my_language || compose.isPending || draft.result.native_language !== known?.native_language} onClick={() => { setAdopted(draft.result.text); onAdopt(draft.result.text); toast("已采用母语版，请继续按原流程复制或确认发送"); }}>{ready ? "已采用母语版" : "采用母语版，填入回复框"}</button></div>
        </div>}
        {!ready && <p className="t-foot">先核对母语版和回译稿，再点「采用」。采用只填入回复框，不会发送。</p>}
        {ready && <p className="t-foot" style={{ color: "var(--green)" }}>已采用核对稿，可复制或进入原有人工确认发送流程。修改回复后需要重新核对。</p>}
      </>}
    </section>
  );
}
