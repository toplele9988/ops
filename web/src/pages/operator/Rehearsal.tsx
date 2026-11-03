import { Link } from 'react-router';
import { type Action, beijing, typeNames, states } from './types';

const timing: Record<string, string> = {
  contact_binding_required: '先核对原渠道联系人、语言与时区，才能计算发送时间',
  channel_unavailable: '原渠道暂无可用发送通道，交人工处理',
  timezone_required: '核对对方时区后才能计算发送时间',
  language_required: '核对对方语言后才能计算发送时间',
};

export function Rehearsal({ actions }: { actions: Action[] }) {
  const drafts = actions.filter(a => a.dry_run || a.state === 'dry_run' || a.data.rehearsal_only);
  return <section className="operator-section" id="operator-rehearsal" aria-label="演练记录">
    <h2>演练记录 <span className="operator-badge">{drafts.length}</span></h2>
    <p>每条都是拟议动作。时间按当前资料与发送节奏估算；打开第 1 级后，历史演练草稿仍须逐条核对。</p>
    {!drafts.length && <p>收到真实新消息或高优先级资料缺口后，会在这里备好演练记录。</p>}
    <div className="operator-timeline">{drafts.map(a => {
      const view = a.rehearsal;
      const profile = a.data.context?.contacts?.[a.contact_key];
      return <article className="operator-card" data-testid={`rehearsal-${a.id}`} key={a.id}>
        <div className="operator-card-heading"><strong>{view?.who ?? profile?.name ?? '待核对联系人'}</strong><span>{typeNames[view?.type ?? profile?.type ?? ''] ?? '往来单位待核对'} · 第 {a.level} 级 · {states[a.state] ?? a.state}</span></div>
        <p className="t-sub">{beijing(a.created_at)} · {view?.trigger ?? '运营事件'}</p>
        <p><strong>为什么：</strong>{view?.why ?? '请核对触发消息与相关案子。'}</p>
        {Array.isArray(a.data.payload?.gaps) && <ul>{(a.data.payload!.gaps as {id: string; kind: string; impact: string}[]).map(g => <li key={g.id}>{g.kind}：{g.impact}</li>)}</ul>}
        <p><strong>拟发正文</strong></p>
        {a.data.parts.length ? <blockquote>{a.data.parts.join('\n\n')}</blockquote> : <p>需要先核对归属或资料，再起草正文。</p>}
        <p><strong>引用来源</strong></p>
        {view?.sources.length ? <ul>{view.sources.map((s, i) => <li key={i}>{s.label} · {s.ref}{s.conversation_key && <> <Link to={`/inbox?role=all&conversation=${encodeURIComponent(s.conversation_key)}`}>查看原会话</Link></>}{s.at && ` · ${s.at}`}</li>)}</ul> : <p>暂无可核准来源，请先补齐资料。</p>}
        <p><strong>如果打开第 1 级：</strong>{a.level !== 1 ? '本条属于其他等级，开启第 1 级不会发送。' : view?.would_send_at ? `预计北京时间 ${beijing(view.would_send_at)}，仍需通过联系人、来源与额度核对。` : timing[view?.timing_reason ?? ''] ?? '资料核对后计算发送时间。'}</p>
        {a.data.rehearsal_only && <p className="t-sub">档案缺口草稿仅供演练。请先核对联系人与档案，再重新起草；本条不能批准发送。</p>}
      </article>;
    })}</div>
  </section>;
}
