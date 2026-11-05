import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link } from 'react-router';
import { get, postForm } from '../api';
import { type Action, type Profile, type Snapshot, kinds, states, typeNames, reasons, beijing } from './operator/types';
import { Rehearsal } from './operator/Rehearsal';
import './operator/operator.css';

const levels = [1, 2, 3];
const finalStates = new Set(['sent', 'done', 'rejected', 'unknown', 'failed', 'duplicate']);

export function Operator() {
  const client = useQueryClient();
  const [feedback, setFeedback] = useState('');
  const [editor, setEditor] = useState<Action | null>(null);
  const [text, setText] = useState('');
  const [rejecting, setRejecting] = useState<Action | null>(null);
  const [reason, setReason] = useState('');
  const [confirming, setConfirming] = useState<Action | null>(null);
  const [contactKey, setContactKey] = useState('');
  const [profile, setProfile] = useState<Profile | null>(null);
  const query = useQuery({ queryKey: ['operator'], queryFn: () => get<Snapshot>('/api/v1/operator'), retry: false, refetchInterval: 15000 });
  const mutation = useMutation({
    mutationFn: ({ path, fields }: { path: string; fields: Record<string, string> }) => postForm<{ action?: Action }>(`/api/v1/operator/${path}`, { ...fields, operation_key: crypto.randomUUID() }),
    onSuccess: (result, input) => {
      setFeedback(input.path.endsWith('/approve') ? (result.action?.state === 'sent' || result.action?.state === 'done' ? '已完成，请查看最近动作。' : `已处理：${states[result.action?.state ?? 'scheduled'] ?? '已排队'}。`) : input.path.endsWith('/reject') ? '已拒绝，并保留原因。' : input.path.endsWith('/edit') ? '草稿已修改，请再次核对后确认。' : '已保存。');
      setEditor(null); setRejecting(null); setConfirming(null); setProfile(null);
      void client.invalidateQueries({ queryKey: ['operator'] });
    },
    onError: (error: Error) => { setFeedback(error.message); },
  });
  const data = query.data;
  const busy = mutation.isPending || query.isFetching || query.isError;
  const change = (scope: string, key: string, enabled: boolean) => mutation.mutate({ path: 'settings', fields: { scope, key, enabled: enabled ? '1' : '0' } });
  const contacts: Record<string, Profile> = {};
  for (const a of [...(data?.actions ?? [])].reverse()) Object.assign(contacts, a.data.context?.contacts ?? {});
  for (const [key, p] of Object.entries(data?.profiles ?? {})) contacts[key] = { ...contacts[key], ...p };
  const waiting = (data?.actions ?? []).filter(a => !finalStates.has(a.state));
  function editProfile(key: string) { setContactKey(key); setProfile({ timezone: '', language: 'en', tone: 'professional', hourly_limit: 6, daily_limit: 20, interval_seconds: 30, ...contacts[key] }); }
  const toggle = (scope: string, key: string, label: string, checked: boolean) => <label className="operator-toggle"><input aria-label={label} type="checkbox" checked={checked} disabled={busy} onChange={e => change(scope, key, e.target.checked)} />{label}</label>;
  const actionCard = (a: Action, review: boolean) => {
    const contact = contacts[a.contact_key];
    const unlinked = a.kind === 'ask_owner' && a.data.payload?.reason === 'identity_required';
    return <article className="operator-card" key={a.id} data-testid={`action-${a.id}`}>
      <div className="operator-card-heading"><strong>{kinds[a.kind] ?? a.kind}</strong><span className="operator-badge">{states[a.state] ?? a.state}</span></div>
      <p>{contact?.name ?? '待核对联系人'} · 第 {a.level === 99 ? '人工' : a.level} 级 · {contact?.channel ?? '渠道待核对'}</p>
      <p className="t-sub">{reasons[a.reason] ?? (a.reason.startsWith('bridge_') ? '发送结果已记录' : a.reason === 'authorized' ? '已获得自动授权' : '请查看决策记录核对原因')}{a.due_at ? ` · 预计北京时间 ${beijing(a.due_at)}` : ''}</p>
      {a.data.parts.length > 0 ? <blockquote>{a.data.parts.join('\n\n')}</blockquote> : unlinked ? <p>这个会话还没确认是哪个客户或供应商，AI 无法起草。确认归属后，新消息会自动处理。</p> : <p>已备好案子操作，核对后执行。</p>}
      {(a.data.repeats ?? 1) > 1 && <p className="t-sub">之后又来了 {(a.data.repeats ?? 1) - 1} 条新消息，已合并到这一条。</p>}
      {contact?.timezone && <p className="t-sub">对方时区：{contact.timezone} · 语言：{contact.language ?? '待核对'} · 语气：{contact.tone === 'friendly' ? '亲切' : '专业'}</p>}
      <div className="operator-actions">
        {review && <>
          <button className="btn primary" disabled={busy || a.data.rehearsal_only || !data?.settings.enabled || data.settings.dry_run || ['blocked','sending'].includes(a.state) || ['confirm_payment','ask_owner'].includes(a.kind)} onClick={() => setConfirming(a)}>确认{a.data.parts.length ? '发送' : '执行'}</button>
          <button className="btn" disabled={busy || a.data.rehearsal_only || a.state === 'sending' || !a.data.parts.length} onClick={() => { setEditor(a); setText(a.data.parts.join('\n\n')); }}>修改</button>
          <button className="btn" disabled={busy || a.state === 'sending'} onClick={() => { setRejecting(a); setReason(''); }}>拒绝</button>
        </>}
        {unlinked && <Link className="btn primary" to={`/triage?${new URLSearchParams({ conversation: a.contact_key })}`}>去确认归属</Link>}
        {a.data.context?.deal?.id && <Link className="btn" to={`/deals/${a.data.context.deal.id}`}>查看案子</Link>}
        <a className="btn" href="#operator-decisions">查看决策记录</a>
      </div>
    </article>;
  };
  return <main className="operator-page">
    <div className="page-head"><div><h1 className="t-large">AI 运营员</h1><p>替你回复、问价和跟进案子；你决定哪些动作可以自动做。</p></div><button className="btn" disabled={query.isFetching} onClick={() => void query.refetch()}>刷新记录</button></div>
    <p role="status" aria-live="polite">{feedback}</p>
    {query.isLoading && <p>正在读取开关与草稿…</p>}
    {query.isError && <div role="alert">暂时无法读取运营员，请刷新重试。为避免误操作，开关和发送暂不可用。</div>}
    {data && <>
      <section className="operator-section" aria-label="运营总开关">
        <div className="operator-card-heading"><h2>运营总开关</h2><span className="operator-badge">{!data.settings.enabled ? '已全停' : data.settings.dry_run ? '演练模式' : '按授权运行'}</span></div>
        {toggle('master', 'enabled', '启用 AI 运营员', data.settings.enabled)}
        {toggle('master', 'dry_run', '演练模式：只保存草稿', data.settings.dry_run)}
        <button className="btn danger" disabled={mutation.isPending || !data.settings.enabled} onClick={() => change('master', 'enabled', false)}>一键全停</button>
        <p className="t-sub">{data.transport === 'fake' ? '当前使用假发送桥，确认后只记录模拟结果。' : '每次发送都会复查授权与资料。'} 停用后所有待发动作暂停，已开始的发送请核对结果。历史演练草稿须逐条确认。</p>
      </section>
      <Rehearsal actions={data.actions} />
      {data.health?.some(h => !h.ok) && <div role="alert">部分事件来源或定时任务读取失败，后台会继续处理其他来源。请核对：{data.health.filter(h => !h.ok).map(h => `${h.component}（${beijing(h.at)}）`).join('、')}。</div>}
      <section className="operator-section" aria-label="自动授权矩阵"><h2>自动授权矩阵</h2><p>只有等级、往来单位类型、联系人三处都打开才会自动执行。收款始终由你到案子页确认。</p>
        <div className="operator-levels">{levels.map(level => <div key={level}>{toggle('levels', String(level), `第 ${level} 级`, data.settings.levels[String(level)] === true)}<p>{level === 1 ? '供应商与货代：资料、问价、运费、交期' : level === 2 ? '客户：已核实资料与常规跟进' : '报价、PI、订单、发货与案子操作'}</p></div>)}</div>
        <div className="operator-table-wrap"><table><caption>按往来单位类型授权</caption><thead><tr><th>往来单位</th>{levels.map(l => <th key={l}>第 {l} 级</th>)}</tr></thead><tbody>{data.types.map(type => <tr key={type}><th scope="row">{typeNames[type] ?? type}</th>{levels.map(l => <td key={l}>{toggle('types', `${l}:${type}`, `${typeNames[type] ?? type} 第 ${l} 级`, data.settings.types[`${l}:${type}`] === true)}</td>)}</tr>)}</tbody></table></div>
        <h3>按联系人授权</h3>
        {Object.keys(contacts).length === 0 && <p>新询盘归属确认后，联系人会出现在这里。<Link to="/inbox">去商机沟通</Link></p>}
        {Object.entries(contacts).map(([key,p]) => <div className="operator-contact" key={key}><strong>{p.name ?? '联系人'} · {typeNames[p.type] ?? p.type}</strong><div className="operator-actions">{levels.map(l => <span key={l}>{toggle('contacts', `${l}:${key}`, `${p.name ?? '联系人'} 第 ${l} 级`, data.settings.contacts[`${l}:${key}`] === true)}</span>)}<button className="btn" disabled={busy || key.startsWith('dossier:')} onClick={() => editProfile(key)}>核对 {p.name ?? '联系人'} 的时间与语言</button></div><p className="t-sub">每小时 {p.hourly_limit ?? 6} 段 / 每天 {p.daily_limit ?? 20} 段 · 间隔至少 {p.interval_seconds ?? 30} 秒 · 时区 {p.timezone ?? '待核对'}</p></div>)}
      </section>
      <section className="operator-section"><h2>AI 待你确认 <span className="operator-badge">{waiting.length}</span></h2>{waiting.length ? waiting.map(a => actionCard(a, true)) : <p>待办已处理。新消息到达后会自动起草。<button className="btn" onClick={() => void query.refetch()}>检查新草稿</button></p>}</section>
      <section className="operator-section"><h2>最近自动动作</h2>{data.actions.filter(a => finalStates.has(a.state)).length ? data.actions.filter(a => finalStates.has(a.state)).map(a => actionCard(a, false)) : <p>还没有已执行动作，先查看演练草稿并核对授权。</p>}</section>
      <section className="operator-section" id="operator-decisions"><h2>决策记录</h2><p className="t-sub">时间为北京时间。展开可核对触发、资料来源、模型输出和策略判定。</p>{data.unresolved_events.map(e => <p role="alert" key={e.id}>事件 {e.id} 处理结果待核对，请勿重新调用模型。</p>)}{!data.decisions.length && <p>收到第一条运营事件后会生成完整记录。</p>}{data.decisions.map(d => <details key={d.id}><summary>{beijing(d.at)} · {d.phase === 'decision' ? 'AI 决策' : d.phase === 'delivery' ? '发送结果' : d.phase === 'owner' ? '人工操作' : '策略判定'}{d.action_id ? ` · 动作 #${d.action_id}` : ''}</summary><pre>{JSON.stringify(d.data, null, 2)}</pre></details>)}</section>
    </>}
    {editor && <div className="operator-modal" role="dialog" aria-modal="true" aria-labelledby="operator-edit-title"><div><h2 id="operator-edit-title">修改草稿</h2><p>可删去不需要的事实段落。新增价格、规格或承诺，先在档案补入核准来源再重新起草。</p><label>草稿正文<textarea value={text} onChange={e => setText(e.target.value)} rows={8} /></label><div className="operator-actions"><button className="btn primary" disabled={busy || !text.trim()} onClick={() => mutation.mutate({ path: `actions/${editor.id}/edit`, fields: { text } })}>保存修改</button><button className="btn" disabled={mutation.isPending} onClick={() => setEditor(null)}>取消</button></div></div></div>}
    {rejecting && <div className="operator-modal" role="dialog" aria-modal="true" aria-labelledby="operator-reject-title"><div><h2 id="operator-reject-title">拒绝这条草稿</h2><label>拒绝原因<textarea value={reason} onChange={e => setReason(e.target.value)} rows={3} maxLength={500} /></label><div className="operator-actions"><button className="btn primary" disabled={busy || !reason.trim()} onClick={() => mutation.mutate({ path: `actions/${rejecting.id}/reject`, fields: { reason } })}>确认拒绝</button><button className="btn" disabled={mutation.isPending} onClick={() => setRejecting(null)}>取消</button></div></div></div>}
    {confirming && <div className="operator-modal" role="dialog" aria-modal="true" aria-labelledby="operator-send-title"><div><h2 id="operator-send-title">核对后确认{confirming.data.parts.length ? '发送' : '执行'}</h2><p>对象：{contacts[confirming.contact_key]?.name ?? '待核对'}</p><blockquote>{confirming.data.parts.join('\n\n') || kinds[confirming.kind]}</blockquote><p>{data?.transport === 'fake' ? '本次走假发送桥，只记录模拟结果。' : '确认后按原渠道发送，仍受当地时间、发送间隔和上限约束。'}</p><div className="operator-actions"><button className="btn primary" disabled={busy} onClick={() => mutation.mutate({ path: `actions/${confirming.id}/approve`, fields: {} })}>确认并继续</button><button className="btn" disabled={mutation.isPending} onClick={() => setConfirming(null)}>取消</button></div></div></div>}
    {profile && <div className="operator-modal" role="dialog" aria-modal="true" aria-labelledby="operator-profile-title"><form onSubmit={e => { e.preventDefault(); mutation.mutate({ path: 'profiles', fields: { conversation_key: contactKey, type: profile.type, timezone: profile.timezone ?? '', language: profile.language ?? 'en', tone: profile.tone ?? 'professional', hourly_limit: String(profile.hourly_limit), daily_limit: String(profile.daily_limit), interval_seconds: String(profile.interval_seconds) } }); }}><h2 id="operator-profile-title">核对时间与语言</h2><label>往来单位类型<select value={profile.type} onChange={e => setProfile({ ...profile, type: e.target.value })}>{Object.entries(typeNames).map(([key,name]) => <option key={key} value={key}>{name}</option>)}</select></label><label>对方 IANA 时区<input required value={profile.timezone ?? ''} placeholder="例如 Asia/Shanghai" onChange={e => setProfile({ ...profile, timezone: e.target.value })} /></label><label>对方语言<select value={profile.language} onChange={e => setProfile({ ...profile, language: e.target.value })}><option value="en">英语</option><option value="zh">中文</option><option value="zh-CN">简体中文</option><option value="en-US">美式英语</option><option value="es">西班牙语</option><option value="fr">法语</option><option value="pt">葡萄牙语</option></select></label><label>语气<select value={profile.tone} onChange={e => setProfile({ ...profile, tone: e.target.value })}><option value="professional">专业</option><option value="friendly">亲切</option></select></label>{([['hourly_limit','每小时上限',60],['daily_limit','每天上限',200],['interval_seconds','分段间隔秒数',3600]] as const).map(([key,label,max]) => <label key={key}>{label}<input type="number" min={1} max={max} required value={profile[key]} onChange={e => setProfile({ ...profile, [key]: Number(e.target.value) })} /></label>)}<div className="operator-actions"><button type="submit" className="btn primary" disabled={busy}>保存联系人资料</button><button type="button" className="btn" disabled={mutation.isPending} onClick={() => setProfile(null)}>取消</button></div></form></div>}
  </main>;
}

export default Operator;
