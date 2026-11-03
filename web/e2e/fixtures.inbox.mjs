// Fictional W2 state machine. Every POST mutates memory; no backend or channel is contacted.
import assert from 'node:assert/strict';
import { installBaseFixtures as installFixtures } from './fixtures.base.mjs';
const at = '2026-11-02T08:00:00+08:00';
const key = (kind, channel, name) => Buffer.from(JSON.stringify([kind, channel, 'fictional-account', name])).toString('base64url');
export const customerKey = key('customer', 'alibaba', '虚构客户甲');
export const otherCustomerKey = key('customer', 'whatsapp', '虚构客户乙');
export const conversationKey = key('conversation', 'alibaba', 'fictional-customer');
export const supplierKey = key('conversation', 'wechat', 'fictional-supplier');
const list = (items) => ({ items, total: items.length, page: 1, page_size: 100, ready: true });
export function createInboxState() {
  const conv = (key, channel, buyer_name, counterpart_role = 'customer') => ({ key, channel, channel_label: { alibaba: '阿里', wechat: '微信', whatsapp: 'WhatsApp' }[channel], account: 'fictional-account', buyer_name, country: '虚构地区', counterpart_role, n_messages: 2, first_seen: at, last_seen: at, last: { role: 'buyer', text: 'Please confirm the carton size for Fictional Lamp X.', sent_at: at }, waiting_reply: true, waiting_hours: 2, workflow_state: 'waiting', customer_key: customerKey, customer_name: '虚构客户甲' });
  return { conversations: [conv(conversationKey, 'alibaba', '虚构买家会话'), conv(supplierKey, 'wechat', '虚构供应商群', 'supplier')],
    messages: [{ role: 'seller', text: 'Which fictional model do you need?', sent_at: at }, { role: 'buyer', text: 'Please confirm the carton size for Fictional Lamp X.', sent_at: at, blocks: [{ type: 'text', text: 'Please confirm the carton size for Fictional Lamp X.' }, { type: 'image', media: 'fictional-image' }, { type: 'file', media: 'fictional-file', name: '虚构产品资料.txt' }] }],
    customers: [{ key: customerKey, buyer_name: '虚构客户甲', country: '虚构地区', channel: 'alibaba', account: 'fictional-account', n_conversations: 1, n_messages: 2, first_seen: at, last_seen: at, waiting_reply: true }, { key: otherCustomerKey, buyer_name: '虚构客户乙', country: '虚构地区乙', channel: 'whatsapp', account: 'fictional-account', n_conversations: 1, n_messages: 3, first_seen: at, last_seen: at, waiting_reply: true }],
    draft: null, deals: [{ id: 7, title: '虚构已有案子', stage_label: '询盘', stage: 'inquiry', status: 'open' }], links: [], outbox: [], requests: [], receipts: new Map(), revision: 1, failSend: false, failList: false, delayList: 0, failContext: false, failDraft: false, failSupplierSend: false, translations: 0 };
}
export async function installInboxFixtures(page, state, problems) {
  await installFixtures(page, problems);
  // Reject any attempt to escape the local static server, including redirects and popup fetches.
  await page.context().route('**/*', async (route) => {
    const url = new URL(route.request().url());
    if (url.hostname !== '127.0.0.1') { problems.push('External request blocked: ' + url.origin); await route.abort(); return; }
    await route.fallback();
  });
  await page.route('**/api/**', async (route) => {
    const req = route.request(); const url = new URL(req.url()); const path = url.pathname.replace('/api/v1', ''); const method = req.method();
    if (path === '/media/fictional-image') { await route.fulfill({ contentType: 'image/png', body: Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=', 'base64') }); return; }
    if (path === '/media/fictional-file') { await route.fulfill({ contentType: 'text/plain', headers: { 'Content-Disposition': 'attachment; filename="fictional.txt"' }, body: 'fictional product information' }); return; }
    const body = Object.fromEntries(new URLSearchParams(req.postData() ?? ''));
    state.requests.push({ method, path, body });
    const ok = async (json, status = 200) => route.fulfill({ status, json: structuredClone(json) });
    try {
      if (method === 'GET' && path === '/inbox/summary') return ok({ ready: true, channels: [], new_24h: 0, draft_new: state.draft ? 1 : 0, revision: String(state.revision), conversation_count: state.conversations.length, waiting_total: state.conversations.filter(c => c.workflow_state === 'waiting' && c.counterpart_role !== 'supplier').length, by_channel: ['alibaba', 'wechat', 'whatsapp'].map(channel => ({ channel, label: { alibaba: '阿里', wechat: '微信', whatsapp: 'WhatsApp' }[channel], waiting: 1 })) });
      if (method === 'GET' && path === '/inbox/conversations') {
        if (state.delayList) await new Promise(resolve => setTimeout(resolve, state.delayList));
        if (state.failList) return ok({ error: '虚构读取失败' }, 503);
        const q = url.searchParams.get('q')?.toLowerCase();
        return ok(list(state.conversations.filter(c => (!url.searchParams.get('channel') || c.channel === url.searchParams.get('channel')) && (url.searchParams.get('role') === 'all' || c.counterpart_role !== 'supplier') && (!q || `${c.buyer_name} ${c.customer_name} ${c.last.text}`.toLowerCase().includes(q)) && (!url.searchParams.has('state') || url.searchParams.get('state') === 'all' || c.workflow_state === url.searchParams.get('state')))));
      }
      if (method === 'GET' && path === '/parties-ops' && !state.partiesOps) return ok(list(state.customers.map((c, i) => ({ id: i + 1, name: c.buyer_name, country: c.country }))));
      if (method === 'GET' && path === '/customers') { const q = url.searchParams.get('q'); return ok(list(state.customers.filter(c => !q || c.buyer_name.includes(q)))); }
      if (method === 'GET' && path === '/deals') { const q = url.searchParams.get('q'); return ok(list(state.deals.filter(d => (!q || d.title.includes(q)) && (!url.searchParams.get('status') || url.searchParams.get('status') === 'all' || d.status === url.searchParams.get('status'))))); }
      if (method === 'POST' && path === '/lang/translate') { state.translations++; return ok({ target_language: body.target_language, items: body.message_indices.split(',').map(raw => ({ index: Number(raw), source_text: state.messages[Number(raw)].text, source_hash: 'fictional-hash', text: '请确认虚构灯具 X 的外箱尺寸。', error: null, complete: true, cached: false, chunks: [] })) }); }
      const match = path.match(/^\/inbox\/conversations\/([^/]+)(.*)$/);
      if (!match) return route.fallback();
      const [, convKey, suffix] = match;
      const conv = state.conversations.find(c => c.key === convKey);
      assert.ok(conv, 'Known fictional conversation');
      if (method === 'GET' && suffix === '') return ok({ conversation: conv, messages: state.messages, draft: state.draft });
      if (method === 'GET' && suffix === '/ops/context') {
        if (state.failContext) return ok({ error: '虚构资料读取失败' }, 503);
        return ok({ workflow_state: conv.workflow_state, remind_at: conv.remind_at ?? null, customer_key: conv.customer_key, customer_name: conv.customer_name, customer: state.customers.find(c => c.key === conv.customer_key) ?? null, deals: state.links.filter(l => l.key === convKey).map(l => state.deals.find(d => d.id === l.id)), party: { id: conv.customer_party_id ?? 1, name: conv.customer_name, country: 'GB', timezone: 'Europe/London' }, suppliers: state.conversations.filter(c => c.counterpart_role === 'supplier'), outbox: state.outbox.filter(r => r.conversation_key === convKey || r.source_key === convKey), dry_run: true });
      }
      if (method === 'POST') {
        assert.ok(body.request_id, 'Every mutation has an intent id');
        const fingerprint = JSON.stringify([path, body]);
        const cached = state.receipts.get(body.request_id);
        if (cached) { assert.equal(cached.fingerprint, fingerprint); return ok(cached.result); }
        let result;
        if (suffix === '/draft') {
          if (state.failDraft) return ok({ error: '虚构 AI 暂时不可用' }, 502);
          state.draft = { id: 3, text: 'I will confirm the carton size for Fictional Lamp X.', language: 'en', needs_confirmation: ['请核对产品型号'], model: 'fictional-ai', sources: [] };
          result = { draft: state.draft };
        } else if (suffix === '/ops/supplier-draft') {
          assert.equal(body.supplier_key, supplierKey);
          assert.ok(body.product, 'Product included');
          const row = { id: state.outbox.length + 1, conversation_key: supplierKey, source_key: convKey, text: `产品：${body.product}\n客户问题：${state.messages.at(-1).text}`, state: 'draft', dry_run: 1, created_at: Date.now()/1000 };
          state.outbox.push(row); result = { message: row, recipient: '虚构供应商群', notice: '已起草到供应商会话，核对后再确认发送（演练）' };
        } else if (suffix === '/ops/action') {
          switch (body.action) {
            case 'send': case 'supplier_send': {
              assert.equal(body.confirm, '1'); assert.equal(body.dry_run, '1');
              if ((body.action === 'send' && state.failSend) || (body.action === 'supplier_send' && state.failSupplierSend)) return ok({ error: '虚构保存结果未确认' }, 503);
              const target = body.action === 'supplier_send' ? body.supplier_key : convKey;
              if (body.action === 'supplier_send') assert.ok(state.outbox.some(r => String(r.id) === body.draft_id && r.state === 'draft'));
              const row = { id: state.outbox.length + 1, conversation_key: target, source_key: convKey, text: body.text, state: 'queued', dry_run: 1, created_at: Date.now()/1000 };
              state.outbox.push(row); result = { message: row, notice: '已进入发送队列（演练）' }; break;
            }
            case 'create_deal': {
              let existing = state.links.find(l => l.key === convKey && l.created);
              if (!existing) { const deal = { id: 8, title: body.title || '虚构新询盘', stage_label: '询盘' }; state.deals.push(deal); existing = { key: convKey, id: deal.id, created: true }; state.links.push(existing); state.onCreate?.(deal, convKey); }
              result = { id: existing.id, notice: '案子已建好，会话已关联' }; break;
            }
            case 'link_deal': if (!state.links.some(l => l.key === convKey && l.id === Number(body.deal_id))) state.links.push({ key: convKey, id: Number(body.deal_id) }); result = { notice: '会话已关联到案子' }; break;
            case 'assign': { assert.equal(body.confirm, '1'); const c = body.customer_party_id ? state.customers[Number(body.customer_party_id) - 1] : state.customers.find(c => c.key === body.customer_key); conv.customer_party_id = Number(body.customer_party_id) || 1; assert.ok(c); conv.customer_key = c.key; conv.customer_name = c.buyer_name; result = { notice: '会话已归到' + c.buyer_name }; break; }
            case 'done': conv.workflow_state = 'done'; result = { notice: '已标记已处理，新消息到达会再次提醒' }; break;
            case 'reopen': conv.workflow_state = 'waiting'; conv.remind_at = null; result = { notice: '已恢复跟进' }; break;
            case 'remind': assert.ok(Date.parse(body.remind_at) > Date.now()); conv.remind_at = Date.parse(body.remind_at)/1000; conv.workflow_state = 'replied'; result = { notice: '提醒已记下，到时会回到要我回复' }; break;
            default: throw new Error('Unknown action ' + body.action);
          }
        } else throw new Error('Unregistered mutation ' + path);
        state.receipts.set(body.request_id, { fingerprint, result });
        return ok(result);
      }
      throw new Error('Unknown inbox request ' + method + path);
    } catch (e) { problems.push(e.stack); return ok({ error: e.message }, 501); }
  });
}
