// Fictional mutable browser fixtures. No requests reach a business service.
import { installFixtures } from './fixtures.mjs';

export async function installPartiesFixtures(page, problems = [], { empty = false } = {}) {
  await installFixtures(page, problems);
  const now = Date.now() / 1000;
  const requests = [];
  const conversations = [
    { key: 'fictional-ali', channel: 'alibaba', name: '虚构灯塔采购', party_id: 1, last_seen: now - 86400 },
    { key: 'fictional-wa', channel: 'whatsapp', name: '虚构灯塔同事', party_id: 2, last_seen: now - 2 * 86400 },
    { key: 'fictional-supplier', channel: 'wechat', name: '虚构星河工厂群', party_id: 3, last_seen: now - 86400 },
    { key: 'fictional-new', channel: 'alibaba', name: '虚构新询盘', party_id: null, last_seen: now - 86400 },
    { key: 'fictional-link', channel: 'whatsapp', name: '虚构新增联系', party_id: null, last_seen: now - 86400 },
  ];
  const base = (id, name, kind, country) => ({ id, name, kind, country, notes: '', tags: '', next_followup: null, last_contact: null, channels: [], todo_count: 0, open_deals: 0, timeline: [], deals: [], drafts: [], terms: [], products: [], pending_questions: [], merges: [], merged_profiles: [] });
  const state = {
    parties: empty ? [] : [base(1, '虚构灯塔采购', 'customer', '虚构国甲'), base(2, '虚构灯塔重复资料', 'customer', '虚构国乙'), base(3, '虚构星河工厂', 'supplier', '虚构国甲'), base(4, '虚构久未联系客户', 'customer', '虚构国甲')],
    conversations: empty ? conversations.map(c => ({ ...c, party_id: null })) : conversations,
    requests, failedRequests: [], results: new Map(), merges: [], failNext: false,
  };
  if (!empty) {
    state.parties[0].deals = [{ id: 11, title: '虚构灯具询盘', status: 'open', stage: 'inquiry' }];
    state.parties[0].todo_count = 2;
    state.parties[2].products = [{ id: 21, name: '虚构演示灯A', model: 'DEMO-A', carton_l_cm: 40, carton_w_cm: 30, carton_h_cm: 20, gross_kg: 5, units_per_carton: 2, packing_ready: true, verified: false, current_offer: { currency: 'CNY', price: '100.00', quoted_on: '2026-10-01', valid_until: '2026-10-30', validity: 'expired' } }, { id: 22, name: '虚构缺资料产品', packing_ready: false, verified: false }];
  }
  const find = id => {
    const p = state.parties.find(p => p.id === Number(id));
    if (!p) throw new Error('未找到虚构资料');
    return p;
  };
  const view = p => {
    const merges = state.merges.filter(m => m.target_id === p.id && !m.undone_at);
    const ids = [p.id, ...merges.map(m => m.source_id)];
    const cs = state.conversations.filter(c => ids.includes(c.party_id));
    const sources = state.parties.filter(s => ids.includes(s.id));
    const deals = sources.flatMap(s => s.deals);
    const allMerges = state.merges.filter(m => m.target_id === p.id);
    return { ...p, last_contact: cs.length ? Math.max(...cs.map(c => c.last_seen)) : null, channels: [...new Set(cs.map(c => c.channel))],
      identities: cs.map(c => ({ key: c.key, name: c.name, channel: c.channel })),
      deals, open_deals: deals.filter(d => ['open', 'paused'].includes(d.status)).length,
      merged_profiles: sources.filter(s => s.id !== p.id).map(s => ({ name: s.name, notes: s.notes, tags: s.tags })), merges: allMerges,
      timeline: [...sources.flatMap(s => s.timeline), ...cs.flatMap(c => [0, 1].map(i => ({ id: `${c.key}-${i}`, at: c.last_seen + i, channel: c.channel, conversation_key: c.key, role: i ? 'seller' : 'buyer', summary: i ? '正在核对虚构采购需求。' : '请提供虚构产品报价。' })))],
      pending_questions: p.products.flatMap(x => [!x.verified ? `${x.name}：核对外箱三边、毛重、每箱数量` : '', !x.current_offer || x.current_offer.validity === 'expired' ? `${x.name}：请报最新价格并注明有效期` : ''].filter(Boolean)),
    };
  };
  const addEvent = (p, summary) => p.timeline.unshift({ id: `event-${requests.length}`, at: new Date().toISOString(), channel: '内部记录', summary });
  await page.route('**/api/v1/parties-ops**', async route => {
    const req = route.request();
    const url = new URL(req.url());
    const tail = url.pathname.slice('/api/v1/parties-ops'.length);
    const method = req.method();
    try {
      if (state.failNext && method === 'POST') { state.failedRequests.push({ tail, fields: Object.fromEntries(new URLSearchParams(req.postData())) }); state.failNext = false; await route.fulfill({ status: 503, json: { error: '虚构保存失败，请重试' } }); return; }
      if (method === 'GET') {
        if (tail === '/conversations') { await route.fulfill({ json: { items: state.conversations } }); return; }
        if (tail) { await route.fulfill({ json: view(find(tail.slice(1))) }); return; }
        const q = url.searchParams;
        let rows = state.parties.filter(p => !state.merges.some(m => m.source_id === p.id && !m.undone_at)).map(view).filter(p => p.kind === q.get('kind'));
        const countries = [...new Set(rows.map(r => r.country).filter(Boolean))].sort();
        const channels = [...new Set(rows.flatMap(r => r.channels))];
        rows = rows.filter(p => (!q.get('q') || `${p.name} ${p.tags} ${p.notes}`.includes(q.get('q'))) && (!q.get('country') || p.country === q.get('country')) && (!q.get('channel') || p.channels.includes(q.get('channel'))) && (!q.get('open') || Boolean(p.open_deals) === (q.get('open') === 'yes')) && (!q.get('contact') || (q.get('contact') === 'never' ? p.last_contact === null : q.get('contact') === 'stale' ? p.last_contact && now - p.last_contact > 30 * 86400 : p.last_contact && now - p.last_contact <= Number(q.get('contact')) * 86400)));
        rows.sort((a, b) => q.get('sort') === 'name' ? a.name.localeCompare(b.name) : q.get('sort') === 'tasks' ? b.todo_count - a.todo_count : q.get('sort') === 'followup' ? (a.next_followup || '9999').localeCompare(b.next_followup || '9999') : q.get('sort') === 'oldest' ? (a.last_contact || 0) - (b.last_contact || 0) : (b.last_contact || 0) - (a.last_contact || 0));
        const n = Number(q.get('page') || 1), size = Number(q.get('page_size') || 40);
        await route.fulfill({ json: { items: rows.slice((n - 1) * size, n * size), total: rows.length, countries, channels, page: n, page_size: size } }); return;
      }
      if (method !== 'POST') throw new Error(`Unregistered method ${method}`);
      const f = Object.fromEntries(new URLSearchParams(req.postData()));
      if (!f.request_id) throw new Error('No idempotency token');
      const fingerprint = JSON.stringify([tail, f]);
      requests.push({ tail, method, fields: f });
      if (state.results.has(f.request_id)) {
        const prior = state.results.get(f.request_id);
        if (prior.fingerprint !== fingerprint) throw new Error('Idempotency conflict');
        await route.fulfill({ json: prior.result }); return;
      }
      const [, id, op] = tail.split('/');
      let p;
      let result;
      if (!tail) {
        p = base(Math.max(0, ...state.parties.map(p => p.id)) + 1, f.name, f.kind, f.country);
        state.parties.push(p);
        if (f.conversation_key) state.conversations.find(c => c.key === f.conversation_key).party_id = p.id;
        result = { id: p.id, ok: true, message: '已新建往来单位，关联会话已归入资料' };
      } else {
        p = find(id);
        let message = '已保存，已记入往来时间线';
        result = { ok: true, id: p.id };
        if (op === 'update') for (const k of ['name', 'country', 'notes', 'tags', 'next_followup']) { if (k in f) p[k] = f[k]; }
        else if (op === 'link') {
          const c = state.conversations.find(c => c.key === f.conversation_key);
          if (c.party_id && c.party_id !== p.id) throw new Error('会话已属于其他单位');
          c.party_id = p.id; message = '已关联会话，全部往来已归入时间线';
        } else if (op === 'merge') {
          if (f.confirm !== '1') throw new Error('Requires confirmation');
          const source = find(f.source_id);
          state.merges.push({ id: state.merges.length + 1, source_id: source.id, source_name: source.name, target_id: p.id, at: new Date().toISOString(), undone_at: null });
          message = '已合并展示身份、往来和案子；原资料保留，可撤销';
        } else if (op === 'undo') {
          if (f.confirm !== '1') throw new Error('Requires confirmation');
          state.merges.find(m => m.id === Number(f.merge_id)).undone_at = new Date().toISOString();
          message = '已撤销合并，两位客户恢复独立展示；原身份和案子归属保留';
        } else if (op === 'draft') {
          if (!view(p).identities.some(i => i.key === f.conversation_key)) throw new Error('Draft requires owned conversation');
          const text = f.purpose === 'followup' ? '您好，请问您的虚构采购需求是否有更新？' : f.purpose === 'inquiry' ? '请提供虚构产品最新价格、交期和报价有效期。' : '请补充虚构产品外箱三边、毛重和每箱数量。';
          const draft = { id: state.parties.flatMap(p => p.drafts).length + 1, party_id: p.id, conversation_key: f.conversation_key, text, reviewed_at: null, dry_run: 1, state: 'draft' };
          p.drafts.push(draft); result = { ...result, draft_id: draft.id, text, dry_run: true, state: 'draft' }; message = '已存入演练发件箱草稿，等待人工核对，尚未发送';
        } else if (op === 'review') {
          if (f.confirm !== '1') throw new Error('Requires confirmation');
          const d = p.drafts.find(d => d.id === Number(f.draft_id)); d.text = f.text; d.reviewed_at = new Date().toISOString(); message = '人工核对已记录；演练草稿仍未发送';
        } else if (op === 'verify') {
          const x = p.products.find(x => x.id === Number(f.product_id));
          if (!x.packing_ready || f.confirm !== '1') throw new Error('Cannot verify incomplete packing');
          x.verified = true; message = '外箱尺寸、毛重和每箱数量已核对，已记入产品资料';
        } else if (op === 'term') {
          p.terms.push({ id: p.terms.length + 1, field: f.field, value: f.value, status: 'confirmed' }); message = '已保存合作条款，已记入往来时间线';
        } else if (op === 'term-status') {
          p.terms.find(t => t.id === Number(f.term_id)).status = f.action === 'confirm' ? 'confirmed' : 'rejected'; message = '合作条款核对结果已保存';
        } else if (op === 'deal') {
          const d = { id: 100 + p.deals.length, title: f.title, stage: 'inquiry', status: 'open', conversations: view(p).identities.map(i => i.key), tasks: ['核对采购需求'] };
          p.deals.push(d); p.todo_count++; result.deal_id = d.id; message = '已建案子并生成询盘待办，客户会话已带入案子';
        } else throw new Error(`Unregistered mutation ${tail}`);
        result.message = message;
      }
      addEvent(p, result.message);
      state.results.set(f.request_id, { fingerprint, result });
      await route.fulfill({ json: result });
    } catch (e) { problems.push(String(e)); await route.fulfill({ status: 501, json: { error: String(e) } }); }
  });
  return state;
}
