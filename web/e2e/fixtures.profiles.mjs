// Fictional profiles only. Every write uses the existing mutable parties fixture.
import { installPartiesFixtures } from './fixtures.parties.mjs';

export async function installProfilesFixtures(page, problems = [], { parties, empty = false, status = 200 } = {}) {
  const ownsParties = !parties;
  parties ||= await installPartiesFixtures(page, problems, { empty });
  const now = Date.now();
  const daysAgo = days => new Date(now - days * 86400_000).toISOString();
  const profile = (key, name, channel, customer_stage, country, country_name, age = 3) => ({
    conversation_key: key, channel, title: name, kind: 'customer', customer_stage,
    org_name: name, people: [{ name: '虚构联系人', role: '采购' }], country, country_name,
    language: 'en', products: ['虚构庭院灯', '虚构壁灯', '虚构台灯', '虚构吊灯'],
    summary: '询问过虚构灯具的规格和采购条件，下一步确认用途。',
    evidence: [{ claim: '问过产品', quote: '请介绍虚构庭院灯的规格，这是虚构验收原话。', message_index: 2, sent_at: daysAgo(age) },
      { claim: '确认采购用途', quote: '虚构项目需要户外灯，请提供资料。', message_index: 5, sent_at: daysAgo(age) }],
    confidence: 0.92, messages_read: 8, messages_total: 8, first_at: daysAgo(age + 10),
    last_at: daysAgo(age), last_inbound_at: daysAgo(age), state: 'ready', updated_at: daysAgo(0),
  });
  const state = { parties, status, detailStatus: 200, reads: [], details: [], items: [], delay: 0, malformed: false, networkError: false };
  if (!empty) {
    const first = parties.conversations.find(c => c.party_id === 1);
    const second = parties.conversations.find(c => c.party_id === 2);
    if (first) state.items.push(profile(first.key, parties.parties.find(p => p.id === 1).name, first.channel, 'customer', 'GB', '英国', 3));
    if (second) state.items.push(profile(second.key, parties.parties.find(p => p.id === 2).name, second.channel, 'customer', 'GB', '英国', 2));
    state.items.push(
      profile('fictional-profile-a', '虚构晨光贸易', 'alibaba', 'potential', 'AE', '阿联酋'),
      { ...profile('fictional-profile-b', '虚构山谷贸易', 'wechat', 'potential', 'BR', '巴西'), state: 'partial', messages_read: 8, messages_total: 20 },
      profile('fictional-profile-c', '虚构远帆贸易', 'whatsapp', 'potential', 'DE', '德国'),
      profile('fictional-profile-quoted', '虚构松林采购', 'alibaba', 'quoted', 'GB', '英国'),
      profile('fictional-profile-aftersales', '虚构银杉采购', 'wechat', 'after_sales', 'US', '美国'),
      profile('fictional-profile-dormant', '虚构冬日贸易', 'whatsapp', 'potential', null, null, 120),
      profile('fictional-profile-multi', '虚构同一客户新询价', 'whatsapp', 'potential', 'GB', '英国', 1),
    );
    for (const p of state.items) {
      let conversation = parties.conversations.find(c => c.key === p.conversation_key);
      if (!conversation) {
        conversation = { key: p.conversation_key, channel: p.channel, name: p.title, party_id: p.conversation_key === 'fictional-profile-multi' ? 1 : null };
        parties.conversations.push(conversation);
      }
      conversation.last_seen = Date.parse(p.last_at) / 1000;
    }
    // Dedicated tests can also click the quote and open an offline conversation.
    if (ownsParties) await page.route('**/api/v1/inbox/conversations/**', async route => {
      if (route.request().method() !== 'GET') return route.fallback();
      const path = new URL(route.request().url()).pathname.slice('/api/v1/inbox/conversations/'.length);
      const key = decodeURIComponent(path.split('/')[0]);
      const p = state.items.find(item => item.conversation_key === key);
      if (!p) return route.fallback();
      if (path.endsWith('/ops/context')) return route.fulfill({ json: { workflow_state: 'done', remind_at: null, reminder_due: false, customer_key: '', customer_name: p.title, customer: null, party: null, deals: [], suppliers: [], outbox: [], dry_run: true } });
      return route.fulfill({ json: { conversation: { key, channel: p.channel, channel_label: '虚构渠道', account: '虚构账号', buyer_name: p.title, country: p.country, n_messages: 8, first_seen: p.first_at, last_seen: p.last_at, source_url: null, can_send: false, copy_hint: '虚构会话，仅用于验收' }, messages: p.evidence.map(e => ({ role: 'buyer', text: e.quote, sent_at: e.sent_at })), draft: null } });
    });
  }
  await page.route('**/api/v1/profiles**', async route => {
    const request = route.request();
    const url = new URL(request.url());
    if (request.method() !== 'GET') { problems.push('Profiles must stay read-only'); return route.abort(); }
    if (state.delay) await new Promise(resolve => setTimeout(resolve, state.delay));
    if (state.networkError) return route.abort('failed');
    if (url.pathname === '/api/v1/profiles') {
      state.reads.push(Object.fromEntries(url.searchParams));
      if (state.status !== 200) return route.fulfill({ status: state.status, json: { error: '虚构接口暂未就绪' } });
      if (state.malformed) return route.fulfill({ contentType: 'application/json', body: '{invalid' });
      const items = state.items.filter(p => (!url.searchParams.get('kind') || p.kind === url.searchParams.get('kind')) && (!url.searchParams.get('stage') || p.customer_stage === url.searchParams.get('stage')) && (!url.searchParams.get('channel') || p.channel === url.searchParams.get('channel')) && (!url.searchParams.get('q') || p.title.includes(url.searchParams.get('q'))));
      const stages = ['potential', 'quoted', 'customer', 'after_sales', 'dormant'];
      return route.fulfill({ json: { items: items.map(p => ({ ...p, evidence: [] })), counts: { kind: { customer: items.length }, stage: Object.fromEntries(stages.map(s => [s, items.filter(p => p.customer_stage === s).length])) }, updated_at: daysAgo(0) } });
    }
    const key = decodeURIComponent(url.pathname.slice('/api/v1/profiles/'.length));
    state.details.push(key);
    const p = state.items.find(p => p.conversation_key === key);
    return route.fulfill({ status: p ? state.detailStatus : 404, json: p && state.detailStatus === 200 ? p : { error: '虚构依据暂未就绪' } });
  });
  return state;
}
