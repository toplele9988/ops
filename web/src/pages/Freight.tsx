import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus } from "lucide-react";
import { useState } from "react";
import { useSearchParams } from "react-router";
import { get, postForm, qs } from "../api";
import { Freight as Quote, MODE, money, Page, Party, todayIso, UNIT, VALIDITY } from "../archive";
import { Chip, Empty, Pick, Sheet, Skeleton } from "../ui/bits";
import { useToast } from "../ui/Toast";

const EMPTY = { forwarder_id: "", origin: "广州", destination_country: "", destination_detail: "", mode: "air", service: "DDP 门到门", unit: "kg", price: "", min_charge: "", transit_days_min: "", transit_days_max: "", quoted_on: todayIso(), valid_until: "", channel: "forwarder" };

export function Freight() {
  const toast = useToast();
  const client = useQueryClient();
  const [params, setParams] = useSearchParams();
  const [page, setPage] = useState(1);
  const [adding, setAdding] = useState(false);
  const [form, setForm] = useState(EMPTY);
  const [newName, setNewName] = useState("");
  const forwarder = params.get("forwarder") ?? "";
  const country = params.get("country") ?? "";
  const mode = params.get("mode") ?? "";
  const state = params.get("state") ?? "";
  const set = (changes: Record<string, string>) => {
    const next = new URLSearchParams(params);
    for (const [key, value] of Object.entries(changes)) (value ? next.set(key, value) : next.delete(key));
    setParams(next, { replace: true });
    setPage(1);
  };
  const forwarders = useQuery({ queryKey: ["parties", "forwarder"], queryFn: () => get<Page<Party>>("/api/v1/archive/parties?kind=forwarder&page_size=100"), retry: false });
  const list = useQuery({
    queryKey: ["freight", forwarder, country, mode, state, page],
    queryFn: () => get<Page<Quote>>(`/api/v1/archive/freight${qs({ forwarder, country, mode, state, page, page_size: 30 })}`),
    placeholderData: keepPreviousData, retry: false,
  });
  const names = new Map((forwarders.data?.items ?? []).map((x) => [x.id, x.short_name || x.name]));
  const refresh = () => { client.invalidateQueries({ queryKey: ["freight"] }); client.invalidateQueries({ queryKey: ["archive-summary"] }); };
  const createParty = useMutation({
    mutationFn: () => postForm<{ id: number }>("/api/v1/archive/parties", { kind: "forwarder", name: newName }),
    onSuccess: (r) => { toast("货代已添加"); setNewName(""); client.invalidateQueries({ queryKey: ["parties", "forwarder"] }); if (r?.id) setForm((f) => ({ ...f, forwarder_id: String(r.id) })); },
    onError: (e: Error) => toast(e.message, "error"),
  });
  const add = useMutation({
    mutationFn: () => postForm("/api/v1/archive/freight", Object.fromEntries(Object.entries(form).filter(([, v]) => v !== ""))),
    onSuccess: () => { toast("运价已记录"); setAdding(false); setForm({ ...EMPTY, forwarder_id: form.forwarder_id }); refresh(); },
    onError: (e: Error) => toast(e.message, "error"),
  });
  const act = useMutation({
    mutationFn: ({ id, action }: { id: number; action: string }) => postForm(`/api/v1/archive/freight/${id}/status`, { action }),
    onSuccess: () => { toast("已保存"); refresh(); }, onError: (e: Error) => toast(e.message, "error"),
  });
  const pages = list.data ? Math.max(1, Math.ceil(list.data.total / 30)) : 1;
  const fwdOptions = forwarders.data?.items ?? [];
  const set1 = (key: keyof typeof EMPTY) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => setForm({ ...form, [key]: e.target.value });

  return (
    <>
      <div className="page-head">
        <div>
          <h1 className="t-large">货代运价</h1>
          <p>每条运价都有有效期，价格一直在变。过期或没写有效期的运价不会用来报价。</p>
        </div>
        <button type="button" className="btn primary" onClick={() => setAdding(true)}><Plus size={16} aria-hidden="true" />记录运价</button>
      </div>
      <div className="toolbar">
        <Pick label="货代" value={forwarder} onChange={(value) => set({ forwarder: value })} options={[{ value: "", label: "全部货代" }, ...fwdOptions.map((x) => ({ value: String(x.id), label: x.short_name || x.name }))]} />
        <Pick label="方式" value={mode} onChange={(value) => set({ mode: value })} options={[{ value: "", label: "全部方式" }, ...Object.entries(MODE).map(([value, label]) => ({ value, label }))]} />
        <Pick label="有效状态" value={state} onChange={(value) => set({ state: value })} options={[{ value: "", label: "全部" }, { value: "valid", label: "有效" }, { value: "expiring", label: "快到期" }, { value: "expired", label: "已过期" }, { value: "none", label: "没写有效期" }]} />
        <label className="field" style={{ width: 160 }}><span className="sr-only">目的国</span><input name="country" autoComplete="off" placeholder="目的国…" defaultValue={country} onChange={(e) => { const v = e.target.value.trim(); window.clearTimeout((window as unknown as { __fc?: number }).__fc); (window as unknown as { __fc?: number }).__fc = window.setTimeout(() => set({ country: v }), 350); }} style={{ paddingLeft: 12 }} /></label>
        {list.data && <span className="t-foot num">共 {list.data.total} 条</span>}
      </div>

      {list.isLoading && <Skeleton lines={4} />}
      {list.isError && <div className="group"><Empty title="没能读到运价">{(list.error as Error).message}</Empty></div>}
      {list.data && list.data.items.length === 0 && (
        <div className="group"><Empty title="还没有运价" action={<button type="button" className="btn primary" onClick={() => setAdding(true)}>记录第一条运价</button>}>
          从货代发来的报价里挑一条记下来：线路、方式、价格、有效期。以后算到手价时只用有效的。</Empty></div>
      )}
      {list.data && list.data.items.length > 0 && (
        <div className="group" style={{ opacity: list.isPlaceholderData ? 0.6 : 1 }}>
          {list.data.items.map((x) => (
            <div className="row" key={x.id} style={{ alignItems: "flex-start" }}>
              <div className="row-main">
                <div className="row-title num">{money(x.price, x.currency)} {UNIT[x.unit] ?? x.unit}　<span className="t-cap">{MODE[x.mode] ?? x.mode} · {x.service}</span>{x.channel === "platform_alibaba" && <> <Chip tone="orange">阿里国际站物流</Chip></>}</div>
                <div className="row-sub">{names.get(x.forwarder_id) ?? "货代"} · {x.origin} → {x.destination_country}{x.destination_detail ? ` ${x.destination_detail}` : ""}{x.transit_days_min != null ? ` · ${x.transit_days_min}${x.transit_days_max && x.transit_days_max !== x.transit_days_min ? `–${x.transit_days_max}` : ""} 天` : ""}{x.min_charge ? ` · 最低收费 ${money(x.min_charge, x.currency)}` : ""}</div>
                <div className="row-sub num">报价日 {x.quoted_on}{x.valid_until ? ` · 有效至 ${x.valid_until}` : ""}{x.validity_notice && x.status === "confirmed" ? `　${x.validity_notice}` : ""}</div>
              </div>
              {x.status === "suggested" ? (
                <span style={{ display: "flex", gap: 6 }}>
                  <button type="button" className="btn sm primary" onClick={() => act.mutate({ id: x.id, action: "confirm" })}>确认</button>
                  <button type="button" className="btn sm" onClick={() => act.mutate({ id: x.id, action: "reject" })}>不对</button>
                </span>
              ) : x.status === "rejected" ? <Chip tone="plain">已否决</Chip> : <Chip tone={VALIDITY[x.validity]?.tone}>{VALIDITY[x.validity]?.text}</Chip>}
            </div>
          ))}
        </div>
      )}
      {list.data && pages > 1 && (
        <div className="pager">
          <button type="button" className="btn sm" disabled={page <= 1} onClick={() => setPage(page - 1)}>上一页</button><span className="num">{page} / {pages}</span>
          <button type="button" className="btn sm" disabled={page >= pages} onClick={() => setPage(page + 1)}>下一页</button>
        </div>
      )}

      <Sheet open={adding} wide title="记录一条运价" onClose={() => setAdding(false)}>
        <div className="form-grid">
          <label className="span2"><span>货代</span>
            <select name="forwarder_id" value={form.forwarder_id} onChange={set1("forwarder_id")}><option value="">请选择…</option>{fwdOptions.map((x) => <option key={x.id} value={x.id}>{x.short_name || x.name}</option>)}</select></label>
          <label className="span2"><span>没有这家？直接添加</span><span style={{ display: "flex", gap: 6 }}><input name="newforwarder" style={{ flex: 1 }} autoComplete="off" placeholder="货代名称…" value={newName} onChange={(e) => setNewName(e.target.value)} /><button type="button" className="btn sm" disabled={!newName.trim() || createParty.isPending} onClick={() => createParty.mutate()}>添加</button></span></label>
          <label><span>起运地</span><input name="origin" autoComplete="off" value={form.origin} onChange={set1("origin")} /></label>
          <label><span>目的国</span><input name="destination_country" autoComplete="off" value={form.destination_country} onChange={set1("destination_country")} placeholder="如 美国…" /></label>
          <label><span>目的地细分（可空）</span><input name="destination_detail" autoComplete="off" value={form.destination_detail} onChange={set1("destination_detail")} placeholder="如 洛杉矶…" /></label>
          <label><span>来源</span><select name="channel" value={form.channel} onChange={set1("channel")}><option value="forwarder">外部货代</option><option value="platform_alibaba">阿里国际站物流</option><option value="other">其他</option></select></label>
          <label><span>运输方式</span><select name="mode" value={form.mode} onChange={set1("mode")}>{Object.entries(MODE).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></label>
          <label><span>服务</span><input name="service" autoComplete="off" value={form.service} onChange={set1("service")} /></label>
          <label><span>计价单位</span><select name="unit" value={form.unit} onChange={set1("unit")}>{Object.entries(UNIT).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></label>
          <label><span>价格（元）</span><input name="price" inputMode="decimal" autoComplete="off" value={form.price} onChange={set1("price")} placeholder="例如 38.50…" /></label>
          <label><span>最低收费（元，可空）</span><input name="min_charge" inputMode="decimal" autoComplete="off" value={form.min_charge} onChange={set1("min_charge")} /></label>
          <label><span>时效最短（天）</span><input name="transit_days_min" inputMode="numeric" autoComplete="off" value={form.transit_days_min} onChange={set1("transit_days_min")} /></label>
          <label><span>时效最长（天）</span><input name="transit_days_max" inputMode="numeric" autoComplete="off" value={form.transit_days_max} onChange={set1("transit_days_max")} /></label>
          <label><span>报价日期</span><input name="quoted_on" type="date" value={form.quoted_on} onChange={set1("quoted_on")} /></label>
          <label><span>有效期至（必填才能用）</span><input name="valid_until" type="date" value={form.valid_until} onChange={set1("valid_until")} /></label>
        </div>
        <div className="sheet-actions"><button type="button" className="btn" onClick={() => setAdding(false)}>取消</button><button type="button" className="btn primary" disabled={!form.forwarder_id || !form.destination_country || !form.price || add.isPending} onClick={() => add.mutate()}>保存运价</button></div>
      </Sheet>
    </>
  );
}
