// Shared, stateful fictional fixtures. Base handler fails every unknown API closed.
import { installBaseFixtures, demoDeal, demoQuote } from './fixtures.base.mjs';
import { installInboxFixtures, createInboxState, conversationKey, supplierKey } from './fixtures.inbox.mjs';
import { installPartiesFixtures } from './fixtures.parties.mjs';
import { installDealFixtures } from './fixtures.deals.mjs';
import { installQueueFixtures, queueResponse } from './fixtures.queue.mjs';
import { installDossierFixtures } from './fixtures.dossiers.mjs';
import { installProfilesFixtures } from './fixtures.profiles.mjs';
export { conversationKey, supplierKey };

export async function installFixtures(page, problems = []) {
  await installBaseFixtures(page, problems);
  const deals = await installDealFixtures(page, problems);
  const parties = await installPartiesFixtures(page, problems);
  const inbox = createInboxState();
  inbox.partiesOps = parties;
  inbox.deals = deals.deals;
  const canonical = id => parties.merges.find(m => m.source_id === id && !m.undone_at)?.target_id ?? id;
  parties.conversations[0].key = conversationKey;
  parties.conversations[2].key = supplierKey;
  inbox.onCreate = (d, key) => {
    const row = deals.deals.find(r => r.id === d.id);
    Object.assign(row, structuredClone(demoDeal), { id: d.id, title: d.title, customer: { id: 1, name: parties.parties[0].name, country: 'GB', timezone: 'Europe/London' }, items: [], tasks: {}, conversations: [{ key, conversation_key: key, party_id: 1, channel_label: '阿里国际站' }] });
    parties.parties[0].deals.push({ id: row.id, title: row.title, status: row.status, stage: row.stage });
  };
  deals.deals.push(structuredClone(demoDeal));
  deals.quotes.push({ ...structuredClone(demoQuote), terms: { confirmed: true }, valid_until: '2030-02-01', warnings: [], customer_conversations: [] });
  await installInboxFixtures(page, inbox, problems);
  const queue = await installQueueFixtures(page, problems, { queueOnly: true });
  queue.items = queue.items.map(i => ({ ...i, to: i.to.replaceAll('demo-alibaba', conversationKey).replaceAll('demo-wechat', supplierKey) }));
  await page.route('**/api/v1/ops/drafts', route => {
    if (route.request().method() !== 'GET') return route.fallback();
    return route.fulfill({ json: { dry_run: true, items: [...inbox.outbox.map(d => ({ ...d, source: 'inbox', reviewed_at: null })), ...parties.parties.flatMap(p => p.drafts.map(d => ({ ...d, source: 'parties' }))) ] } });
  });
  // Project current canonical party after every merge / undo without moving raw ownership.
  for (const conv of inbox.conversations) Object.defineProperty(conv, 'customer_name', { configurable: true, enumerable: true, get: () => parties.parties.find(p => p.id === canonical(conv.customer_party_id ?? 1))?.name ?? '', set: () => {} });
  await installDossierFixtures(page);
  await installProfilesFixtures(page, problems, { parties });
  // Registered after the dossier fixtures: the newest route wins, and the day's queue needs real items.
  await page.route('**/api/v1/ops/queue', route => {
    if (route.request().method() !== 'GET') return route.fallback();
    const items = queue.items.filter(i => i.kind !== 'buyer_reply' || inbox.conversations[0].workflow_state !== 'done');
    return route.fulfill({ json: queueResponse(items) });
  });
  // Minimal read-only AI operator snapshot (fictional): everything off, rehearsal only.
  await page.route('**/api/v1/operator**', route => {
    if (route.request().method() !== 'GET') return route.fallback();
    return route.fulfill({ json: { settings: { enabled: false, dry_run: true, levels: {}, types: {}, contacts: {} }, profiles: {}, actions: [], decisions: [],
      types: ['customer_project', 'customer_wholesale', 'customer_end', 'customer_other', 'supplier_product', 'supplier_logistics', 'supplier_support'],
      transport: 'fake', health: [], unresolved_events: [] } });
  });
  return { inbox, parties, deals, queue };
}
