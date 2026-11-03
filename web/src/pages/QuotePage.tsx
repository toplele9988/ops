import { PageIntro } from "./overview/PageIntro";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Copy, Plus, TriangleAlert } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router";
import { copyText, get, qs } from "../api";
import { MODE, Page, Product, UNIT, VALIDITY, todayIso } from "../archive";
import { ago, formatTime } from "../format";
import { CHANNEL_TEXT, COST_KIND, FreightOption, FreightOptions, INCOTERMS, QSTATUS, QuoteDetail, QuoteText, REC_TEXT, SIDE } from "../quotes";
import { operationForm as postForm } from "./deals/actions";
import { uuid } from "../lib_uuid";
import { Chip, Empty, SearchField, Sheet, Skeleton } from "../ui/bits";
import { useToast } from "../ui/Toast";

const yuan = (v: string | null | undefined) => (v == null ? "—" : `¥${v}`);
const EMPTY_COST = { kind: "freight", party_id: "", freight_quote_id: "", amount: "", valid_until: "", source_note: "", description: "", source_offer_id: "", quantity_line_id: "", unit_price: "", quantity: "", unit: "", destination_country: "", mode: "sea", segment: "" };

export function QuotePage() {
  const { id } = useParams();
  const navigate = useNavigate();
  const toast = useToast();
  const client = useQueryClient();
  const q = useQuery({ queryKey: ["quote", id], queryFn: () => get<QuoteDetail>(`/api/v1/quotes/${id}`), retry: false });
  const [edit, setEdit] = useState<Record<string, string>>({});
  const [text, setText] = useState<QuoteText | null>(null);
  const [textBusy, setTextBusy] = useState(false);
  const [termsEdit, setTermsEdit] = useState<Record<string, string>>({});
  const [conversationKey, setConversationKey] = useState("");
  const [selection, setSelection] = useState<FreightOption | null>(null);
  const [freightChoice, setFreightChoice] = useState({ selection_mode: "replace", replace_cost_id: "", reason_type: "other", reason: "", segment: "", request_key: "" });
  const [sending, setSending] = useState(false);
  const [pickFor, setPickFor] = useState<number | "new" | null>(null);
  const [pickQ, setPickQ] = useState("");
  const [addingCost, setAddingCost] = useState(false);
  const [cost, setCost] = useState(EMPTY_COST);
  const [nego, setNego] = useState({ side: "customer", party_id: "", note: "", price: "" });
  const [sell, setSell] = useState<Record<number, string>>({});
  const [quantities, setQuantities] = useState<Record<number, string>>({});
  const [units, setUnits] = useState<Record<number, string>>({});
  const [newQty, setNewQty] = useState("");
  const [newUnit, setNewUnit] = useState("");
  const products = useQuery({ queryKey: ["products-pick", pickQ], queryFn: () => get<Page<Product>>(`/api/v1/archive/products${qs({ q: pickQ, page_size: 20 })}`), enabled: pickFor != null, retry: false });
  const options = useQuery({ queryKey: ["freight-options", id], queryFn: () => get<FreightOptions>(`/api/v1/quotes/${id}/freight-options`), enabled: addingCost && cost.kind === "freight", retry: false });
  const refresh = () => { client.invalidateQueries({ queryKey: ["quote", id] }); client.invalidateQueries({ queryKey: ["quotes"] }); client.invalidateQueries({ queryKey: ["deal"] }); client.invalidateQueries({ queryKey: ["freight-options"] }); };
  const act = useMutation({
    mutationFn: ({ path, fields }: { path: string; fields: Record<string, string> }) => postForm(`/api/v1/${path}`, fields),
    retry: false, onError: (e: Error) => toast(e.message + "；请先刷新核对，再决定是否重试", "error"),
  });
  const run = (path: string, fields: Record<string, string>, ok = "已保存", after?: () => void) =>
    act.mutate({ path, fields }, { onSuccess: () => { toast(ok); refresh(); after?.(); } });

  if (q.isLoading) return <><h1 className="t-large">报价</h1><Skeleton lines={6} /></>;
  if (q.isError || !q.data) return <div className="group"><Empty title="没能打开这轮报价" action={<button className="btn" onClick={() => q.refetch()}>重新读取报价</button>}>{(q.error as Error | null)?.message}</Empty></div>;
  const d = q.data;
  const locked = d.status !== "draft";
  const field = (key: string, fallback: string) => edit[key] ?? fallback;
  const dirty = Object.keys(edit).length > 0;
  const minPrice = new Map(d.per_line.map((p) => [p.line_id, p.min_price_for_target]));
  const verdict = d.inverted ? { text: "倒挂：卖价低于成本", tone: "red" as const } : d.below_target ? { text: `利润率低于目标 ${d.target_margin}`, tone: "orange" as const } : d.totals.margin ? { text: "利润正常", tone: "green" as const } : null;
  const showText = async (lang: string) => { setTextBusy(true); try { const r = await get<QuoteText>(`/api/v1/quotes/${d.id}/${lang === "pi" ? "pi" : `text?lang=${lang}`}`); setText({ ...r, lang }); } catch (e) { toast((e as Error).message, "error"); } finally { setTextBusy(false); } };
  const nativeText = async () => { setTextBusy(true); try { const r = await postForm<QuoteText>(`/api/v1/quotes/${d.id}/native`, { conversation_key: conversationKey }); setText(r); } catch (e) { toast((e as Error).message, "error"); } finally { setTextBusy(false); } };

  return (
    <>
      <button type="button" className="btn plain" style={{ marginLeft: -8, marginBottom: 6 }} onClick={() => navigate(`/deals/${d.deal_id}`)}><ArrowLeft size={16} aria-hidden="true" />回到案子</button>
      <div className="page-head" style={{ marginBottom: 12 }}>
        <div><h1 className="t-large">第 {d.round_no} 轮报价</h1><p>成本只用「已确认且在有效期内」的价格；有问题的地方会在下面直接说明怎么办。</p></div>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
          <Chip tone={QSTATUS[d.status]?.tone}>{QSTATUS[d.status]?.text}</Chip>
          <button type="button" className="btn primary" disabled={act.isPending || d.blocking.length > 0} onClick={() => run(`quotes/${d.id}/drafts`, { kind: "quote", lang: "zh" }, "报价单已保存到案子演练草稿箱，未发送")}>保存报价草稿</button>
          <button type="button" className="btn" disabled={act.isPending || d.blocking.length > 0} onClick={() => run(`quotes/${d.id}/drafts`, { kind: "pi", lang: "zh" }, "PI 已保存到案子演练草稿箱，未发送")}>保存 PI 草稿</button>
          <button type="button" className="btn" onClick={() => showText("en")}><Copy size={16} aria-hidden="true" />报价文本（英文）</button>
          <button type="button" className="btn" onClick={() => showText("zh")}>中文</button>
          <button type="button" className="btn" disabled={textBusy} onClick={() => showText("pi")}>中文 PI 预览</button>
          {d.status === "draft" && <button type="button" className="btn primary" disabled={d.blocking.length > 0} onClick={() => setSending(true)}>标记为已发出…</button>}
          {d.status === "sent" && <><button type="button" className="btn primary" onClick={() => run(`quotes/${d.id}/status`, { action: "accept" }, "已记录：客户接受")}>客户接受</button><button type="button" className="btn" onClick={() => run(`quotes/${d.id}/status`, { action: "reject" }, "已记录：客户拒绝")}>客户拒绝</button></>}
        </div>
      </div>
      <PageIntro text="核对这笔生意的往来、报价和待办，再推进下一步。" />

      <div className="kpis" style={{ marginBottom: 16 }}>
        <div className="kpi"><span className="t-foot">已登记成本小计</span><span className="kpi-n num">{yuan(d.totals.cost_cny)}</span><span className="t-cap">{d.totals.cost_complete ? "按已登记项目计算，税费与附加费仍须核对" : "成本还不完整"}</span></div>
        <div className="kpi"><span className="t-foot">销售额</span><span className="kpi-n num">{d.totals.revenue ? `${d.currency} ${d.totals.revenue}` : "—"}</span><span className="t-cap">折合 {yuan(d.totals.revenue_cny)}</span></div>
        <div className="kpi"><span className="t-foot">利润</span><span className="kpi-n num" style={{ color: d.inverted ? "var(--red)" : undefined }}>{yuan(d.totals.profit_cny)}</span><span className="t-cap">{d.totals.margin ? `利润率 ${d.totals.margin}` : "成本完整后才能算"}</span></div>
        <div className="kpi"><span className="t-foot">判断</span>{verdict ? <span style={{ marginTop: 6 }}><Chip tone={verdict.tone}>{verdict.text}</Chip></span> : <span className="kpi-n">—</span>}<span className="t-cap">目标利润率 {d.target_margin}</span></div>
      </div>

      <div className="block">
        <h2 className="group-title">{d.blocking.length ? `发出前必须解决 ${d.blocking.length} 件事` : "核价检查通过，发出前仍须人工审阅"}{d.warnings.length > 0 && <span className="count">另有 {d.warnings.length} 条提醒</span>}</h2>
        <div className="group">
          {d.blocking.length > 0 && <div className="row"><Link className="btn" to="/products">去产品核准成本或要新价</Link><Link className="btn" to="/freight">去货代要有效运价</Link></div>}
          {d.blocking.map((b, i) => <div className="row" key={`b${i}`}><TriangleAlert size={18} aria-hidden="true" style={{ color: "var(--red)", flex: "none" }} /><div className="row-main">{b.line_no ? <span className="t-cap">第 {b.line_no} 行　</span> : null}{b.message}</div></div>)}
          {d.warnings.map((w, i) => <div className="row" key={`w${i}`}><TriangleAlert size={18} aria-hidden="true" style={{ color: "var(--orange)", flex: "none" }} /><div className="row-main">{w.message}</div></div>)}
          {d.blocking.length === 0 && d.warnings.length === 0 && <div className="row"><div className="row-main t-sub">已登记项目的核价检查通过。请人工核对交付范围和费用是否有遗漏。</div></div>}
        </div>
      </div>

      <div className="cols">
        <div>
          <div className="block">
            <h2 className="group-title">本轮付款与交付条件</h2>
            <div className="group" style={{ padding: 16 }}>
              <p className="t-foot">30% 定金，70% 余款在发货前付清。请核对交付范围，再确认本轮条件。</p>
              <div className="form-grid">
                <label className="span2"><span>交付范围（必填，对客户可见）</span><textarea disabled={locked || act.isPending} value={termsEdit.delivery_scope ?? d.terms.delivery_scope ?? ""} onChange={(e) => setTermsEdit({ ...termsEdit, delivery_scope: e.target.value })} /></label>
                <label><span>公司开票名称（PI 预览）</span><input disabled={locked || act.isPending} value={termsEdit.seller_name ?? d.terms.seller_name ?? ""} onChange={(e) => setTermsEdit({ ...termsEdit, seller_name: e.target.value })} /></label>
                <label><span>公司开票地址（PI 预览）</span><input disabled={locked || act.isPending} value={termsEdit.seller_address ?? d.terms.seller_address ?? ""} onChange={(e) => setTermsEdit({ ...termsEdit, seller_address: e.target.value })} /></label>
              </div>
              {d.terms.confirmed && <p className="t-cap">本轮条件由 {d.terms.confirmed_by} 确认，时间 {formatTime(d.terms.confirmed_at ?? "")}</p>}
              {!locked && <button type="button" className="btn" disabled={act.isPending || !(termsEdit.delivery_scope ?? d.terms.delivery_scope)?.trim()} onClick={() => run(`quotes/${d.id}/terms`, { confirm: "1", payment: "30_70", delivery_scope: termsEdit.delivery_scope ?? d.terms.delivery_scope ?? "", ...(termsEdit.seller_name ?? d.terms.seller_name ? { seller_name: termsEdit.seller_name ?? d.terms.seller_name ?? "" } : {}), ...(termsEdit.seller_address ?? d.terms.seller_address ? { seller_address: termsEdit.seller_address ?? d.terms.seller_address ?? "" } : {}) }, "本轮条款已确认，请重新生成并审阅文本", () => { setTermsEdit({}); setText(null); })}>确认本轮 30%/70% 和交付范围</button>}
            </div>
          </div>
          <div className="block">
            <h2 className="group-title">客户母语报价</h2>
            <div className="group" style={{ padding: 16 }}>
              <label><span>选择本案客户会话</span><select value={conversationKey} onChange={(e) => setConversationKey(e.target.value)}><option value="">请明确选择客户会话…</option>{d.customer_conversations.map((c, i) => <option key={c.conversation_key} value={c.conversation_key}>客户会话 {i + 1}（单位 #{c.party_id}）</option>)}</select></label>
              <p className="t-foot">先到收件箱核对并锁定客户母语。母语不明确时不能生成；生成后请核对中文回译。</p>
              <button type="button" className="btn" disabled={locked || !conversationKey || textBusy} onClick={nativeText}>{textBusy ? "正在准备文本…" : "生成母语版和中文回译"}</button>
            </div>
          </div>
          <div className="block">
            <h2 className="group-title">产品<span className="count num">{d.lines.length} 行</span></h2>
            <div className="group">
              {d.lines.map((l, i) => (
                <div className="row" key={l.id} style={{ alignItems: "flex-start" }}>
                  <div className="row-main">
                    <div className="row-title">{l.name} <span className="t-cap">× {l.qty}{l.unit ? ` ${l.unit}` : ""}</span></div>
                    <div className="row-sub num">
                      {l.cost_unit ? <>成本 ¥{l.cost_unit} <Chip tone={VALIDITY[l.cost_validity ?? ""]?.tone ?? "plain"}>{VALIDITY[l.cost_validity ?? ""]?.text ?? "未知"}</Chip>{l.cost_valid_until ? ` 至 ${l.cost_valid_until}` : ""}</> : <span style={{ color: "var(--red)" }}>还没选供应商报价</span>}
                    </div>
                    {minPrice.get(l.id) && <div className="row-sub num">要达到目标利润率，至少卖 {d.currency} {minPrice.get(l.id)}</div>}
                    {!locked && (
                      <div style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 8, flexWrap: "wrap" }}>
                        <label>数量<input style={{ width: 80 }} inputMode="numeric" aria-label={`第 ${i + 1} 行数量`} value={quantities[l.id] ?? String(l.qty)} onChange={(e) => setQuantities({ ...quantities, [l.id]: e.target.value })} /></label>
                        <label>单位<input style={{ width: 80 }} aria-label={`第 ${i + 1} 行单位`} value={units[l.id] ?? l.unit ?? ""} onChange={(e) => setUnits({ ...units, [l.id]: e.target.value })} /></label>
                        <button type="button" className="btn sm" disabled={act.isPending || (quantities[l.id] == null && units[l.id] == null)} onClick={() => run(`quotes/${d.id}/lines/${l.id}/update`, { qty: quantities[l.id] ?? String(l.qty), unit: units[l.id] ?? l.unit ?? "" }, "数量与单位已保存，自动费用已重算；人工总费用请重新核对", () => { setQuantities({}); setUnits({}); })}>保存数量和单位</button>
                        <label className="t-foot" style={{ display: "flex", gap: 6, alignItems: "center" }}>卖价 {d.currency}
                          <input className="textarea" style={{ minHeight: 32, width: 96, padding: "0 8px" }} name={`sell${l.id}`} inputMode="decimal" autoComplete="off" disabled={act.isPending} aria-label={`第 ${i + 1} 行销售单价`} value={sell[l.id] ?? l.sell_unit_price ?? ""} onChange={(e) => setSell({ ...sell, [l.id]: e.target.value })} /></label>
                        <button type="button" className="btn sm" disabled={act.isPending || sell[l.id] == null || sell[l.id] === (l.sell_unit_price ?? "")} onClick={() => run(`quotes/${d.id}/lines/${l.id}/update`, { sell_unit_price: sell[l.id] }, "已保存", () => setSell((s) => { if (s[l.id] !== sell[l.id]) return s; const n = { ...s }; delete n[l.id]; return n; }))}>保存价格</button>
                        <button type="button" className="btn sm" onClick={() => { setPickFor(l.id); setPickQ(""); }}>{l.product_id ? "换产品/报价" : "选择产品和报价"}</button>
                        <button type="button" className="btn sm plain" onClick={() => run(`quotes/${d.id}/lines/${l.id}/remove`, {}, "已移除")}>移除</button>
                      </div>
                    )}
                    {locked && l.sell_unit_price && <div className="row-sub num">卖价 {d.currency} {l.sell_unit_price}</div>}
                  </div>
                </div>
              ))}
              {!locked && <div className="row"><button type="button" className="btn" onClick={() => { setPickFor("new"); setPickQ(""); }}><Plus size={16} aria-hidden="true" />添加产品</button></div>}
            </div>
          </div>

          <div className="block">
            <h2 className="group-title">费用（运费、配套等）</h2>
            <div className="group">
              {d.costs.length === 0 && <div className="row"><div className="row-main t-sub">还没有费用。DDP 报价至少要有一条国际运费。</div></div>}
              {d.costs.map((c) => (
                <div className="row" key={c.id} style={{ alignItems: "flex-start" }}>
                  <div className="row-main">
                    <div className="row-title">{COST_KIND[c.kind] ?? c.kind}{c.description ? `：${c.description}` : ""} <span className="num">{c.amount ? yuan(c.amount) : ""}</span></div>
                    <div className="row-sub">{c.calc?.mode ? `${MODE[c.calc.mode] ?? c.calc.mode} · ${UNIT[c.calc.unit ?? ""] ?? ""} ¥${c.calc.unit_price ?? ""}` : c.source_note ?? ""}{c.valid_until ? ` · 有效至 ${c.valid_until}` : ""}</div>
                    {c.calc?.party && <div className="row-sub">单位：{c.calc.party.name} · {c.source_note}</div>}
                    {c.calc?.source_offer && <div className="row-sub">来源快照：{c.calc.source_offer.name} · {c.calc.source_offer.source_ref ?? "档案询价"}</div>}
                    {c.calc?.source_offer?.conversation_key && <Link className="btn sm" to={`/inbox?conversation=${encodeURIComponent(c.calc.source_offer.conversation_key)}`}>回看询价会话</Link>}
                    {c.calc?.quantity != null && <div className="row-sub num">按 {c.calc.quantity} {c.calc.unit} 计价</div>}
                    {!locked && c.calc?.needs_review && <button type="button" className="btn sm" disabled={act.isPending} onClick={() => run(`quotes/${d.id}/costs/${c.id}/review`, { confirm: "1" }, "已记录人工复核")}>已向该单位核对，总费用仍有效</button>}
                    {c.calc?.blocking?.map((b) => <div className="row-sub" key={b.code} style={{ color: "var(--red)" }}>{b.message}</div>)}
                    {!locked && c.kind === "freight" && !c.manual && <button type="button" className="btn sm" style={{ marginTop: 6 }} onClick={() => run(`quotes/${d.id}/costs/${c.id}/recalc`, {}, "已按最新包装资料重算")}>重新计算</button>}
                  </div>
                  {c.validity && <Chip tone={VALIDITY[c.validity]?.tone ?? "plain"}>{VALIDITY[c.validity]?.text ?? c.validity}</Chip>}
                  {!locked && <button type="button" className="btn sm plain" onClick={() => run(`quotes/${d.id}/costs/${c.id}/remove`, {}, "已移除")}>移除</button>}
                </div>
              ))}
              {!locked && <div className="row"><button type="button" className="btn" onClick={() => setAddingCost(true)}><Plus size={16} aria-hidden="true" />添加费用</button></div>}
            </div>
          </div>
        </div>

        <div>
          <div className="block">
            <h2 className="group-title">报价条件</h2>
            <div className="group" style={{ padding: 16 }}>
              <div className="form-grid" style={{ gridTemplateColumns: "1fr 1fr" }}>
                <label><span>贸易术语</span><select aria-label="贸易术语" name="incoterm" disabled={locked || act.isPending} value={field("incoterm", d.incoterm)} onChange={(e) => setEdit({ ...edit, incoterm: e.target.value })}>{INCOTERMS.map((x) => <option key={x}>{x}</option>)}</select></label>
                <label><span>币种</span><input name="currency" disabled={locked || act.isPending} autoComplete="off" value={field("currency", d.currency)} onChange={(e) => setEdit({ ...edit, currency: e.target.value.toUpperCase() })} /></label>
                <label><span>汇率（1 {d.currency} = ? 元）</span><input name="fx_rate" disabled={locked || act.isPending} inputMode="decimal" autoComplete="off" value={field("fx_rate", d.fx_rate ?? "")} onChange={(e) => setEdit({ ...edit, fx_rate: e.target.value })} /></label>
                <label><span>目的国</span><input name="destination_country" disabled={locked || act.isPending} autoComplete="off" value={field("destination_country", d.destination_country ?? "")} onChange={(e) => setEdit({ ...edit, destination_country: e.target.value })} /></label>
                <label><span>目标利润率 %</span><input name="target_margin" disabled={locked || act.isPending} inputMode="decimal" autoComplete="off" value={field("target_margin", d.target_margin.replace("%", ""))} onChange={(e) => setEdit({ ...edit, target_margin: e.target.value })} /></label>
                <label><span>报价有效期至</span><input name="valid_until" type="date" disabled={locked || act.isPending} value={field("valid_until", d.valid_until ?? "")} onChange={(e) => setEdit({ ...edit, valid_until: e.target.value })} /></label>
              </div>
              {d.fx && <p className="t-cap" style={{ marginTop: 8 }}>今日参考汇率 {d.fx.usd_cny}（{d.fx.source}{d.fx.as_of ? `，${ago(d.fx.as_of)}` : ""}）</p>}
              {!locked && <div style={{ textAlign: "right", marginTop: 8 }}><button type="button" className="btn" disabled={!dirty || act.isPending} onClick={() => run(`quotes/${d.id}/update`, edit, "已保存", () => setEdit({}))}>保存条件</button></div>}
            </div>
          </div>

          <div className="block">
            <h2 className="group-title">讨价还价的记录</h2>
            <div className="group">
              {d.negotiations.length === 0 && <div className="row"><div className="row-main t-sub">客户还价、供应商让价、货代调价，都记在这里，以后能回看。</div></div>}
              {d.negotiations.map((n) => (
                <div className="row" key={n.id} style={{ alignItems: "flex-start" }}>
                  <div className="row-main"><div className="row-title">{SIDE[n.side] ?? n.side}{n.party_id ? ` · ${d.parties.find((p) => p.id === n.party_id)?.name ?? `单位 #${n.party_id}`}` : ""}{n.price ? <span className="num">　{n.currency ?? ""} {n.price}</span> : null}</div><div className="row-sub">{n.note.replace(/〔请求:[^〕]*〕/g, "")}</div></div>
                  <span className="t-cap num">{formatTime(n.at)}</span>
                </div>
              ))}
              <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
                <select className="textarea" style={{ minHeight: 36, width: 104, padding: "0 10px" }} aria-label="谁说的" value={nego.side} onChange={(e) => setNego({ ...nego, side: e.target.value, party_id: "" })}>{Object.entries(SIDE).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select>
                {nego.side !== "us" && <select aria-label="具体单位" value={nego.party_id} onChange={(e) => setNego({ ...nego, party_id: e.target.value })}><option value="">选择具体单位…</option>{d.parties.filter((p) => p.kind === nego.side).map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</select>}
                <input className="textarea" style={{ minHeight: 36, flex: 1, minWidth: 140, padding: "0 10px" }} aria-label="说了什么" name="note" autoComplete="off" placeholder="例如：嫌贵，要求 36 美元…" value={nego.note} onChange={(e) => setNego({ ...nego, note: e.target.value })} />
                <input className="textarea" style={{ minHeight: 36, width: 84, padding: "0 10px" }} aria-label="价格" name="price" inputMode="decimal" autoComplete="off" placeholder="价格…" value={nego.price} onChange={(e) => setNego({ ...nego, price: e.target.value })} />
                <button type="button" className="btn" disabled={!nego.note.trim() || act.isPending || (nego.side !== "us" && !nego.party_id)} onClick={() => run(`quotes/${d.id}/negotiations`, { side: nego.side, note: nego.note, ...(nego.party_id ? { party_id: nego.party_id } : {}), ...(nego.price ? { price: nego.price, currency: d.currency } : {}) }, "已记录", () => setNego({ ...nego, note: "", price: "" }))}>记录</button>
              </div>
            </div>
          </div>
        </div>
      </div>

      <Sheet open={text != null} wide title={text?.lang === "pi" ? "中文 PI 预览" : text?.lang === "native" ? "客户母语报价与中文回译" : text?.lang === "zh" ? "报价文本（中文）" : "报价文本（英文）"} onClose={() => setText(null)}>
        {text && !text.ready && <div className="callout" role="note"><b>这份报价还没准备好，文本里有【待确认】的地方。</b>先解决页面上方列出的问题。</div>}
        {text?.notice && <p className="callout">{text.notice}</p>}{text?.missing?.length ? <p>请补齐：{text.missing.join("、")}</p> : null}
        {text?.contact && <p>母语：{text.native_language} · {text.contact.locked ? "人工锁定" : "自动识别，请人工核对"}</p>}
        <label style={{ display: "block", marginTop: 10 }}><span className="sr-only">报价文本</span><textarea className="textarea" name="quotetext" rows={14} value={text?.text ?? ""} readOnly spellCheck={false} /></label>
        {text?.back_translation && <label><span>中文回译（逐项核对后再采用）</span><textarea className="textarea" rows={12} readOnly value={text.back_translation} /></label>}
        <p className="t-foot" style={{ marginTop: 8 }}>需要修改时，回到本轮产品或条件表单保存，再重新生成并审阅。这里不会发送消息；复制不代表已送达。</p>
        <div className="sheet-actions"><button type="button" className="btn" onClick={() => setText(null)}>关闭</button>{text?.lang !== "pi" && <><button type="button" className="btn" disabled={!text?.ready || locked || act.isPending} onClick={() => text && run(`quotes/${d.id}/review`, { confirm: "1", source_hash: text.source_hash, lang: text.lang, text: text.text }, "本轮文本与审阅人、时间已留档")}>确认审阅并留档</button><button type="button" className="btn primary" disabled={!text?.ready} onClick={async () => toast((await copyText(text?.text ?? "")) ? "已复制，请到正确客户会话人工发送" : "复制没成功，请手动选择复制", "ok")}><Copy size={16} aria-hidden="true" />复制</button></>}</div>
      </Sheet>

      <Sheet open={sending} title="确认：这轮报价已经发给客户了？" onClose={() => setSending(false)}>
        <p>这里只是**记录**这轮报价已发出，并让案子进入报价阶段；真正发给客户要在渠道里完成。发出后这一轮不能再改，要改请新建下一轮。</p>
        <div className="sheet-actions"><button type="button" className="btn" onClick={() => setSending(false)}>再想想</button>
          <button type="button" className="btn primary" disabled={act.isPending} onClick={() => run(`quotes/${d.id}/status`, { action: "send", confirm: "1" }, "已记录为已发出", () => setSending(false))}>确认已发出</button></div>
      </Sheet>

      <Sheet open={pickFor != null} wide title="选一个产品" onClose={() => setPickFor(null)}>
        {pickFor === "new" && <div className="form-grid"><label><span>采购数量（必填）</span><input inputMode="numeric" value={newQty} onChange={(e) => setNewQty(e.target.value)} /></label><label><span>单位（留空沿用产品报价）</span><input value={newUnit} onChange={(e) => setNewUnit(e.target.value)} /></label></div>}
        <SearchField label="搜索产品" placeholder="搜名称、型号…" value={pickQ} onSearch={setPickQ} />
        <div className="group" style={{ marginTop: 12, maxHeight: 340, overflowY: "auto" }}>
          {products.isLoading && <div className="row"><div className="row-main t-sub">读取中…</div></div>}
          {(products.data?.items ?? []).map((p) => (
            <button type="button" className="row" key={p.id} disabled={act.isPending || (pickFor === "new" && !/^[1-9]\d{0,6}$/.test(newQty))} onClick={() => {
              const line = pickFor === "new" ? null : d.lines.find((l) => l.id === pickFor);
              if (pickFor === "new") run(`quotes/${d.id}/lines`, { product_id: String(p.id), name: p.name, qty: newQty, ...(newUnit ? { unit: newUnit } : {}) }, "已添加", () => { setPickFor(null); setNewQty(""); setNewUnit(""); });
              else run(`quotes/${d.id}/lines/${line?.id}/update`, { product_id: String(p.id), name: p.name }, "已更换", () => setPickFor(null));
            }}>
              <div className="row-main"><div className="row-title">{p.name}</div><div className="row-sub">{p.packing_ready ? "包装资料齐全" : "缺包装资料"} · {p.current_offer ? `报价${VALIDITY[p.current_offer.validity]?.text}` : "没有可用报价"}</div></div>
            </button>
          ))}
          {products.data && products.data.items.length === 0 && <div className="row"><div className="row-main t-sub">没有找到。</div></div>}
        </div>
        <p className="t-foot" style={{ marginTop: 8 }}>选好后系统会自动带入这个产品「当前可用」的供应商报价；没有可用报价的产品，请先到「产品」里确认报价并写上有效期。</p>
      </Sheet>

      <Sheet open={addingCost} wide title="添加费用" onClose={() => setAddingCost(false)}>
        <div className="form-grid" style={{ gridTemplateColumns: "1fr 1fr" }}>
          <label><span>类型</span><select name="kind" value={cost.kind} onChange={(e) => setCost({ ...EMPTY_COST, kind: e.target.value })}>{Object.entries(COST_KIND).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></label>
          {cost.kind === "freight" && (
            <div className="span2">
              <div className="t-foot" style={{ marginBottom: 6 }}>这一票货各家运价对比（按这张报价里的产品和包装资料算）</div>
              {options.isLoading && <p className="t-sub">正在计算…</p>}
              {options.isError && <p className="callout">运价没有读到，请关闭后重新打开核对，暂时不要重复选择。</p>}
              {options.data?.destination_notice && <p className="t-foot" style={{ color: "var(--orange)" }}>{options.data.destination_notice}</p>}
              {options.data && options.data.items.length === 0 && <p className="t-sub">没有可用的运价。先到“货代运价”录入，并写上有效期。</p>}
              <div className="group">
                {(options.data?.items ?? []).map((o) => (
                  <div className="row" key={o.id} style={{ alignItems: "flex-start" }}>
                    <div className="row-main" style={{ whiteSpace: "normal" }}>
                      <div className="row-title" style={{ whiteSpace: "normal" }}>{o.forwarder_name} <Chip tone={o.channel === "platform_alibaba" ? "orange" : "plain"}>{CHANNEL_TEXT[o.channel]}</Chip> {o.recommendations.map((r) => <Chip key={r} tone={REC_TEXT[r].tone}>{REC_TEXT[r].text}</Chip>)}</div>
                      <div className="row-sub num">{MODE[o.mode] ?? o.mode} · {o.service} · {o.currency} {o.unit_price}/{UNIT[o.unit] ?? o.unit}{o.min_charge ? ` · 最低 ${o.min_charge}` : ""}</div>
                      <div className="row-sub">{o.origin} → {o.destination_country} · 请人工核对起运地和服务覆盖范围</div>
                      <div className="row-sub num">{o.transit_days_min != null ? `${o.transit_days_min}${o.transit_days_max && o.transit_days_max !== o.transit_days_min ? `–${o.transit_days_max}` : ""} 天到` : "时效没写"} · {o.valid_until ? `有效至 ${o.valid_until}${o.remaining_validity_days != null ? `（还剩 ${o.remaining_validity_days} 天）` : ""}` : "没写有效期"}</div>
                      {!o.calculable && <div className="row-sub" style={{ color: "var(--red)", whiteSpace: "normal" }}>算不出总价：{o.calculation_reasons.join("；") || "资料不全"}</div>}
                    </div>
                    <div style={{ textAlign: "right", flex: "none" }}>
                      <div className="num" style={{ fontWeight: 600 }}>{o.amount_in_quote_currency ? `${o.currency} ${o.amount_in_quote_currency}` : "—"}</div>
                      <button type="button" className="btn sm primary" style={{ marginTop: 6 }} disabled={act.isPending || !o.calculable || o.currency !== "CNY" || !d.destination_country} onClick={() => { setSelection(o); setFreightChoice({ selection_mode: d.costs.some((c) => c.kind === "freight" && !c.manual) ? "replace" : "append", replace_cost_id: "", reason_type: "other", reason: "", segment: "", request_key: uuid() }); }}>核对并选择…</button>
                    </div>
                  </div>
                ))}
              </div>
              <p className="t-foot" style={{ marginTop: 8 }}>系统只标出最便宜、最快，选哪家由你定；选定后会记在谈判记录里，以后能回看当时为什么选它。不满意的话，也可以在下面手填。</p>
            </div>
          )}
          <label><span>具体单位（配套、拖车必选）</span><select value={cost.party_id} onChange={(e) => setCost({ ...cost, party_id: e.target.value, source_offer_id: "" })}><option value="">请明确选择单位…</option>{d.parties.filter((p) => cost.kind !== "vendor" || p.kind === "vendor").map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</select></label>
          <label><span>费用内容</span><input value={cost.description} onChange={(e) => setCost({ ...cost, description: e.target.value })} /></label>
          {cost.kind === "vendor" && <label className="span2"><span>已确认询价来源（保留快照，不重复计算产品成本）</span><select value={cost.source_offer_id} onChange={(e) => { const source = d.cost_sources.find((s) => String(s.id) === e.target.value); setCost({ ...cost, source_offer_id: e.target.value, amount: "", unit_price: "", unit: source?.unit ?? "", valid_until: source?.valid_until ?? cost.valid_until }); }}><option value="">人工登记来源与人民币费用</option>{d.cost_sources.filter((s) => String(s.party_id) === cost.party_id).map((s) => <option key={s.id} value={s.id}>{s.name} · {s.currency} {s.unit_price}/{s.unit} · 至 {s.valid_until}</option>)}</select></label>}
          {(cost.source_offer_id || cost.unit_price) && <><label><span>随哪个产品行的数量重算（单位须一致）</span><select value={cost.quantity_line_id} onChange={(e) => setCost({ ...cost, quantity_line_id: e.target.value })}><option value="">使用人工确认的独立数量</option>{d.lines.map((l) => <option key={l.id} value={l.id}>{l.name} · {l.qty} {l.unit}</option>)}</select></label>{!cost.quantity_line_id && <label><span>人工确认数量</span><input inputMode="numeric" value={cost.quantity} onChange={(e) => setCost({ ...cost, quantity: e.target.value })} /></label>}</>}
          {!cost.source_offer_id && <><label><span>或：按人民币单价计费</span><input inputMode="decimal" value={cost.unit_price} onChange={(e) => setCost({ ...cost, unit_price: e.target.value, amount: "" })} /></label><label><span>计价单位</span><input value={cost.unit} onChange={(e) => setCost({ ...cost, unit: e.target.value })} /></label></>}
          {cost.kind === "freight" && <><label><span>人工运费目的国（须与本轮一致）</span><input value={cost.destination_country} onChange={(e) => setCost({ ...cost, destination_country: e.target.value })} /></label><label><span>运输方式</span><select value={cost.mode} onChange={(e) => setCost({ ...cost, mode: e.target.value })}>{Object.entries(MODE).map(([key, name]) => <option key={key} value={key}>{name}</option>)}</select></label></>}
          {!(cost.kind === "freight" && cost.freight_quote_id) && (<>
            {!cost.source_offer_id && !cost.unit_price && <label><span>人工总金额（元，数量改变后须复核）</span><input name="amount" inputMode="decimal" autoComplete="off" value={cost.amount} onChange={(e) => setCost({ ...cost, amount: e.target.value })} /></label>}
            <label><span>这个价格有效期至</span><input name="valid_until" type="date" value={cost.valid_until} onChange={(e) => setCost({ ...cost, valid_until: e.target.value })} /></label>
            <label className="span2"><span>价格是谁报的、怎么来的</span><input name="source_note" autoComplete="off" placeholder="例如：货代小王 10/29 微信报价…" value={cost.source_note} onChange={(e) => setCost({ ...cost, source_note: e.target.value })} /></label>
          </>)}
        </div>
        <div className="sheet-actions"><button type="button" className="btn" onClick={() => setAddingCost(false)}>取消</button>
          <button type="button" className="btn primary" disabled={act.isPending || (!cost.amount && !cost.unit_price && !cost.source_offer_id) || !cost.valid_until || !cost.source_note.trim() || (["vendor", "domestic"].includes(cost.kind) && !cost.party_id)}
            onClick={() => run(`quotes/${d.id}/costs`, Object.fromEntries(Object.entries({ ...cost, manual: "1", mode: cost.kind === "freight" ? cost.mode : "" }).filter(([, value]) => value !== "")), "已添加并保留来源快照", () => { setAddingCost(false); setCost(EMPTY_COST); })}>确认数量与人民币费用后添加</button></div>
      </Sheet>
      <Sheet open={selection != null} title="确认运输方案与选择原因" onClose={() => setSelection(null)}>
        <p>{selection?.forwarder_name} · {selection?.origin} → {selection?.destination_country} · {selection?.service} · CNY {selection?.amount_cny}</p>
        <div className="form-grid">
          <label><span>如何计入费用</span><select value={freightChoice.selection_mode} onChange={(e) => setFreightChoice({ ...freightChoice, selection_mode: e.target.value })}><option value="replace">替换同一运输方案</option><option value="append">追加运输段（首次选择也用此项）</option></select></label>
          {freightChoice.selection_mode === "replace" ? <label><span>明确选择被替换的费用</span><select value={freightChoice.replace_cost_id} onChange={(e) => setFreightChoice({ ...freightChoice, replace_cost_id: e.target.value })}><option value="">请选择原方案…</option>{d.costs.filter((c) => c.kind === "freight" && !c.manual && c.calc?.mode === selection?.mode).map((c) => <option key={c.id} value={c.id}>费用 #{c.id} · {c.amount} 元 · {c.source_note}</option>)}</select></label> : <label><span>这笔费用覆盖哪个运输段</span><input value={freightChoice.segment} onChange={(e) => setFreightChoice({ ...freightChoice, segment: e.target.value })} placeholder="例如：虚构港口至虚构目的港海运" /></label>}
          <label><span>人工选择原因</span><select value={freightChoice.reason_type} onChange={(e) => setFreightChoice({ ...freightChoice, reason_type: e.target.value })}><option value="other">其他取舍</option><option value="cheapest">核对同线路后选最低价</option><option value="fastest">核对后优先时效</option></select></label>
          <label className="span2"><span>具体原因（必填）</span><textarea value={freightChoice.reason} onChange={(e) => setFreightChoice({ ...freightChoice, reason: e.target.value })} /></label>
        </div>
        <p className="t-foot">替换会在一次保存中移除原方案。追加会与已有费用相加，请核对没有重复覆盖同一段。结果未知时先刷新本轮核对，不要重复提交。</p>
        <div className="sheet-actions"><button className="btn" onClick={() => setSelection(null)}>取消</button><button className="btn primary" disabled={act.isPending || !freightChoice.reason.trim() || (freightChoice.selection_mode === "replace" ? !freightChoice.replace_cost_id : !freightChoice.segment.trim())} onClick={() => selection && run(`quotes/${d.id}/freight-options/choose`, Object.fromEntries(Object.entries({ confirm: "1", freight_quote_id: String(selection.id), ...freightChoice, ...(freightChoice.selection_mode === "append" ? { replace_cost_id: "" } : { segment: "" }) }).filter(([, value]) => value !== "")), "运输方案已保存", () => { setSelection(null); setAddingCost(false); })}>确认计入</button></div>
      </Sheet>
      <p className="t-cap" style={{ marginTop: 24 }}>案子：<Link to={`/deals/${d.deal_id}`}>回到案子页</Link>　·　今天 {todayIso()}</p>
    </>
  );
}
