import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Languages, Plus } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router";
import { get, postForm, qs } from "../api";
import { ago } from "../format";
import { detectLanguage, langError, langSettings, languageName, type ContactLanguage, type LangSettings, type Language, type Term } from "../lang";
import { Chip, Empty, Sheet, Skeleton } from "../ui/bits";
import { useToast } from "../ui/Toast";

function Preferences({ settings, languages }: { settings: LangSettings; languages: Language[] }) {
  const toast = useToast();
  const client = useQueryClient();
  const [mine, setMine] = useState(settings.my_language);
  const [native, setNative] = useState(settings.auto_native_send);
  const save = useMutation({
    mutationFn: () => postForm("/api/v1/lang/settings", { my_language: mine, auto_native_send: native ? "1" : "0" }),
    onSuccess: () => { client.invalidateQueries({ queryKey: ["lang-settings"] }); toast("语言设置已保存，会话阅读和回复会使用这个默认设置"); },
    onError: (e: Error) => toast(langError(e), "error"),
  });
  return (
    <form onSubmit={(e) => { e.preventDefault(); save.mutate(); }} style={{ display: "grid", gap: 12 }}>
      <div className="form-grid"><label><span>我的阅读语言</span><select value={mine} onChange={(e) => setMine(e.target.value)}>{languages.map((l) => <option key={l.code} value={l.code}>{l.label}</option>)}</select></label></div>
      <label style={{ display: "flex", gap: 8, alignItems: "flex-start" }}><input type="checkbox" checked={native} onChange={(e) => setNative(e.target.checked)} /><span>回复时，默认开启「按客户母语发送」</span></label>
      <p className="t-foot">母语版和回译稿都要先核对，再点「采用」。阿里仍需复制到 Win10 发送，WhatsApp 仍需你确认发送。</p>
      <div><button type="submit" className="btn primary" disabled={save.isPending}>{save.isPending ? "正在保存…" : "保存设置"}</button></div>
      {save.error && <p role="alert" className="t-foot" style={{ color: "var(--red)" }}>{langError(save.error)}</p>}
    </form>
  );
}

function TermEditor({ term, onClose }: { term: Term | null; onClose: () => void }) {
  const toast = useToast();
  const client = useQueryClient();
  const [word, setWord] = useState(term?.term ?? "");
  const [mode, setMode] = useState<"keep" | "translate">(term?.mode ?? "keep");
  const [translation, setTranslation] = useState(term?.translation ?? "");
  const save = useMutation({
    mutationFn: () => postForm("/api/v1/lang/glossary/save", { ...(term ? { id: String(term.id) } : {}), term: word, mode, translation: mode === "keep" ? "" : translation }),
    onSuccess: () => { client.invalidateQueries({ queryKey: ["lang-glossary"] }); toast("术语已保存，后续翻译会使用新规则"); onClose(); },
    onError: (e: Error) => toast(langError(e), "error"),
  });
  return (
    <form onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
      <div className="form-grid" style={{ gridTemplateColumns: "1fr" }}>
        <label><span>术语或品牌名</span><input autoComplete="off" required maxLength={120} value={word} onChange={(e) => setWord(e.target.value)} /></label>
        <label><span>翻译规则</span><select value={mode} onChange={(e) => setMode(e.target.value as "keep" | "translate")}><option value="keep">保持原样</option><option value="translate">使用指定译法</option></select></label>
        {mode === "translate" && <label><span>指定译法</span><input autoComplete="off" required maxLength={200} value={translation} onChange={(e) => setTranslation(e.target.value)} /></label>}
      </div>
      <p className="t-foot" style={{ marginTop: 12 }}>型号、数字、金额、链接和带单位的数字会保留原样。指定译法会用于所有目标语言，请填写适合客户使用的词。</p>
      {save.error && <p role="alert" className="t-foot" style={{ color: "var(--red)" }}>{langError(save.error)}</p>}
      <div className="sheet-actions"><button type="button" className="btn" onClick={onClose}>取消</button><button type="submit" className="btn primary" disabled={!word.trim() || (mode === "translate" && !translation.trim()) || save.isPending}>保存术语</button></div>
    </form>
  );
}

function ContactEditor({ contact, languages, onClose }: { contact: ContactLanguage; languages: Language[]; onClose: () => void }) {
  const client = useQueryClient();
  const toast = useToast();
  const [language, setLanguage] = useState(contact.native_language ?? "");
  const save = useMutation({
    mutationFn: (locked: boolean) => postForm("/api/v1/lang/contact", { conversation_key: contact.conversation_key, native_language: locked ? language : "", locked: locked ? "1" : "0" }),
    onSuccess: (_r, locked) => { client.invalidateQueries({ queryKey: ["lang-contacts"] }); client.invalidateQueries({ queryKey: ["lang-contact", contact.conversation_key] }); toast(locked ? "母语已锁定，自动识别不会覆盖" : "已解除锁定，可重新识别语言"); onClose(); },
    onError: (e: Error) => toast(langError(e), "error"),
  });
  return (
    <>
      <div className="form-grid"><label><span>确认这位联系人的母语</span><select value={language} onChange={(e) => setLanguage(e.target.value)}><option value="">请选择语言</option>{languages.map((l) => <option key={l.code} value={l.code}>{l.label}</option>)}</select></label></div>
      <p className="t-foot" style={{ marginTop: 12 }}>手动锁定优先于自动识别。国家只能作提示，不能据此确定母语。</p>
      {save.error && <p role="alert" className="t-foot" style={{ color: "var(--red)" }}>{langError(save.error)}</p>}
      <div className="sheet-actions">{contact.locked && <button type="button" className="btn" disabled={save.isPending} onClick={() => save.mutate(false)}>解除锁定</button>}<button type="button" className="btn" onClick={onClose}>取消</button><button type="button" className="btn primary" disabled={!language || save.isPending} onClick={() => save.mutate(true)}>锁定母语</button></div>
    </>
  );
}

export function LanguageSettings() {
  const client = useQueryClient();
  const toast = useToast();
  const settings = useQuery({ queryKey: ["lang-settings"], queryFn: langSettings, retry: false });
  const glossary = useQuery({ queryKey: ["lang-glossary"], queryFn: () => get<{ items: Term[] }>("/api/v1/lang/glossary"), retry: false });
  const contacts = useQuery({ queryKey: ["lang-contacts"], queryFn: () => get<{ items: ContactLanguage[] }>("/api/v1/lang/contacts"), retry: false });
  const [editingTerm, setEditingTerm] = useState<Term | null | undefined>(undefined);
  const [editingContact, setEditingContact] = useState<ContactLanguage | null>(null);
  const [filter, setFilter] = useState("");
  const remove = useMutation({
    mutationFn: (id: number) => postForm("/api/v1/lang/glossary/delete", { id: String(id) }),
    onSuccess: () => { client.invalidateQueries({ queryKey: ["lang-glossary"] }); toast("术语已删除"); }, onError: (e: Error) => toast(langError(e), "error"),
  });
  const detect = useMutation({
    mutationFn: detectLanguage,
    onSuccess: (data, key) => { client.invalidateQueries({ queryKey: ["lang-contacts"] }); client.invalidateQueries({ queryKey: ["lang-contact", key] }); toast(data.contact.native_language ? "语言识别已更新，请核对是否符合客户母语" : "还无法确定母语，请手动确认语言"); },
    onError: (e: Error) => toast(langError(e), "error"),
  });
  const languages = settings.data?.languages ?? [];
  const rows = (contacts.data?.items ?? []).filter((c) => !filter || `${c.display_name ?? ""} ${languageName(c.native_language, languages)}`.toLowerCase().includes(filter.toLowerCase()));
  return (
    <>
      <div className="page-head"><div><h1 className="t-large"><Languages size={28} aria-hidden="true" style={{ verticalAlign: "text-bottom", marginRight: 8 }} />语言设置</h1><p>客户消息译成你看得懂的语言；你的回复先生成客户母语版和回译稿，核对后再采用。</p></div></div>
      <section className="block"><h2 className="group-title">我的语言与回复习惯</h2><div className="group" style={{ padding: 16 }}>
        {settings.isLoading && <Skeleton lines={2} />}
        {settings.error && <Empty title="语言设置暂时无法读取" action={<button type="button" className="btn" onClick={() => settings.refetch()}>重新读取</button>}>{langError(settings.error)}</Empty>}
        {settings.data && <><Preferences key={`${settings.data.settings.my_language}-${settings.data.settings.auto_native_send}`} settings={settings.data.settings} languages={languages} /><p className="t-cap num" style={{ marginTop: 12 }}>语言功能独立额度：本小时已用 {settings.data.quota.used} 次，还可用 {settings.data.quota.remaining} 次，上限 {settings.data.quota.hourly_limit} 次。缓存命中不占额度。</p></>}
      </div></section>
      <section className="block"><h2 className="group-title" style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>灯光行业术语<button type="button" className="btn sm" onClick={() => setEditingTerm(null)}><Plus size={14} aria-hidden="true" />添加术语</button></h2>
        <p className="t-foot" style={{ marginBottom: 8 }}>DMX、RDM、Art-Net 等预置为保持原样。品牌名可加入术语表，固定专业译法可选「使用指定译法」。</p>
        <div className="group">
          {glossary.isLoading && <Skeleton lines={3} />}
          {glossary.error && <Empty title="术语表暂时无法读取" action={<button type="button" className="btn" onClick={() => glossary.refetch()}>重新读取</button>}>{langError(glossary.error)}</Empty>}
          {glossary.data?.items.length === 0 && <Empty title="还没有术语">添加常用品牌名、专业词或固定译法。</Empty>}
          {glossary.data?.items.map((t) => <div className="row" key={t.id}><div className="row-main"><div className="row-title">{t.term}</div><div className="row-sub">{t.mode === "keep" ? "保持原样" : `指定译法：${t.translation}`}</div></div><button type="button" className="btn sm" onClick={() => setEditingTerm(t)}>修改</button><button type="button" className="btn sm plain" disabled={remove.isPending} onClick={() => remove.mutate(t.id)}>删除</button></div>)}
        </div>
      </section>
      <section className="block"><h2 className="group-title">联系人母语</h2><p className="t-foot" style={{ marginBottom: 10 }}>先在会话中识别语言，或在这里手动锁定。多条客户消息共同判断，一句问候不会直接改变已确认语言。</p>
        <div className="form-grid" style={{ marginBottom: 10 }}><label><span>筛选联系人或语言</span><input autoComplete="off" value={filter} onChange={(e) => setFilter(e.target.value)} placeholder="输入名称或语言" /></label></div>
        <div className="group">
          {contacts.isLoading && <Skeleton lines={3} />}
          {contacts.error && <Empty title="联系人语言暂时无法读取" action={<button type="button" className="btn" onClick={() => contacts.refetch()}>重新读取</button>}>{langError(contacts.error)}</Empty>}
          {contacts.data && rows.length === 0 && <Empty title={filter ? "没有找到联系人" : "还没有联系人语言记录"}>{filter ? "换个名称或语言试试。" : "到商机沟通里打开会话，点击「识别母语」，再来这里核对或锁定。"}</Empty>}
          {rows.map((c) => <div className="row" key={`${c.channel}-${c.conversation_key}`} style={{ flexWrap: "wrap", alignItems: "flex-start" }}><div className="row-main" style={{ whiteSpace: "normal", minWidth: 160 }}><div className="row-title"><Link to={`/inbox${qs({ c: c.conversation_key })}`}>{c.display_name || "未命名联系人"}</Link> <Chip tone={c.locked ? "green" : c.native_language ? "blue" : "orange"}>{c.locked ? "手动锁定" : c.native_language ? "自动识别" : "尚未确认"}</Chip></div><div className="row-sub" style={{ whiteSpace: "normal" }}>{languageName(c.native_language, languages)} · {({ alibaba: "阿里国际站", wechat: "微信", whatsapp: "WhatsApp", website: "独立站" } as Record<string, string>)[c.channel] ?? "其他渠道"}{c.confidence ? ` · 把握 ${Math.round(c.confidence * 100)}%` : ""}{c.updated_at ? ` · ${ago(c.updated_at)}更新` : ""}</div>{c.country_hint && <div className="row-sub">国家提示：{languageName(c.country_hint, languages)}，需人工确认</div>}</div><button type="button" className="btn sm" disabled={detect.isPending || c.locked} onClick={() => detect.mutate(c.conversation_key)}>重新识别</button><button type="button" className="btn sm" disabled={!settings.data} onClick={() => setEditingContact(c)}>{c.locked ? "修改母语" : "手动锁定"}</button></div>)}
        </div>
      </section>
      <Sheet open={editingTerm !== undefined} title={editingTerm ? "修改术语" : "添加术语"} onClose={() => setEditingTerm(undefined)}>{editingTerm !== undefined && <TermEditor key={editingTerm?.id ?? "new"} term={editingTerm} onClose={() => setEditingTerm(undefined)} />}</Sheet>
      <Sheet open={Boolean(editingContact)} title={`确认母语：${editingContact?.display_name || "联系人"}`} onClose={() => setEditingContact(null)}>{editingContact && <ContactEditor key={editingContact.conversation_key} contact={editingContact} languages={languages} onClose={() => setEditingContact(null)} />}</Sheet>
    </>
  );
}
