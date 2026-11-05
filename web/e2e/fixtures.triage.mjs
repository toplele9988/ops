// Fictional OP5 suggestions only. No real group IDs, contacts, or backend.
export async function installTriageFixtures(page) {
  const item = (id, overrides = {}) => ({ id, conversation_key: `fictional-group-${id}`, action: 'identity',
    suggested_deal_id: null, suggested_party_id: id === 1 ? null : id, suggested_party_kind: 'supplier',
    suggested_party_name: `虚构单位${id}`, reason: `档案：虚构工作群（来源 SRC-FICTION-${id}）`,
    confidence: .95, source: 'rules', status: 'pending',
    conversation: { channel: 'wechat', counterpart_name: `虚构群${id}`, last_at: 1894233600, preview: '虚构我方消息' }, ...overrides });
  const state = { items: [item(1, { confidence: .9 }), item(2), item(3),
    item(4, { confidence: .89 }), item(5, { reason: '名称匹配：需要人工核对' }), item(6, { action: 'create' }),
    item(7, { status: 'accepted' })], calls: [], failIds: new Set([2]), active: 0, maxActive: 0,
    holdFirst: true, release: null, reads: 0 };
  await page.route('**/api/v1/triage**', async route => {
    const request = route.request();
    const url = new URL(request.url());
    if (request.method() === 'GET' && url.pathname === '/api/v1/triage') {
      state.reads += 1;
      return route.fulfill({ json: { items: state.items.filter(s => s.status === 'pending') } });
    }
    const match = url.pathname.match(/^\/api\/v1\/triage\/(\d+)\/accept$/);
    if (!match || request.method() !== 'POST') throw new Error(`Unexpected fictional triage request: ${url.pathname}`);
    const id = Number(match[1]);
    state.calls.push({ id, headers: request.headers(), body: request.postData() });
    state.active += 1;
    state.maxActive = Math.max(state.maxActive, state.active);
    if (state.holdFirst) { state.holdFirst = false; await new Promise(resolve => { state.release = resolve; }); }
    const failed = state.failIds.has(id);
    if (!failed) state.items.find(s => s.id === id).status = 'accepted';
    state.active -= 1;
    return route.fulfill({ status: failed ? 409 : 200,
      json: failed ? { error: '虚构会话归属已变化，请人工核对' } : { ok: true, party_id: id, deal_id: null } });
  });
  return state;
}
