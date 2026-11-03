// Real local sources through the fake-login rehearsal. Responses/screenshots are private.
import assert from 'node:assert/strict';
import { mkdir, writeFile } from 'node:fs/promises';
import { chromium } from 'playwright-core';

export async function runRehearsal() {
  assert.equal(process.env.IMGW_REHEARSAL, '1');
  const url = new URL(process.env.BASE);
  assert.equal(url.hostname, '127.0.0.1');
  assert.equal(url.protocol, 'http:');
  assert.ok(Number(url.port) >= 1024 && Number(url.port) !== 8791);
  const base = url.origin;
  const marker = await fetch(base + '/__rehearsal__').then(r => r.json());
  assert.ok(marker.rehearsal && marker.dry_run_writes, 'explicit rehearsal marker required');
  const out = process.env.OUT;
  assert.ok(out && out.includes('/private_out/'), 'real-data results must stay in ignored private_out');
  await mkdir(out, { recursive: true });
  const read = async path => { const r = await fetch(base + '/api/v1/' + path); assert.equal(r.status, 200, path); return r.json(); };
  const [customers, suppliers, conversations, deals] = await Promise.all([
    read('parties-ops?kind=customer&page_size=1'), read('parties-ops?kind=supplier&page_size=1'),
    read('inbox/conversations?state=all&role=all&page_size=5'), read('deals?status=all&page_size=1'),
  ]);
  const paths = ['/', '/workbench', '/inbox', '/deals', '/customers', '/suppliers', '/products', '/fulfilment', '/outbox', '/search', '/roles', '/guide', '/system', '/system/channels', '/system/pipeline', '/system/alibaba-open', '/system/knowledge', '/system/language', '/triage', '/classify', '/directory', '/audit', '/freight', '/prices', '/packing', '/requirements'];
  if (customers.items[0]) paths.push('/customers/' + customers.items[0].id);
  if (suppliers.items[0]) paths.push('/suppliers/' + suppliers.items[0].id);
  if (conversations.items[0]) paths.push('/inbox?role=all&conversation=' + encodeURIComponent(conversations.items[0].key));
  if (deals.items[0]) {
    paths.push('/deals/' + deals.items[0].id);
    const rounds = await read('deals/' + deals.items[0].id + '/quotes');
    if (rounds.items[0]) paths.push('/quotes/' + rounds.items[0].id);
  }
  const browser = await chromium.launch({ executablePath: process.env.CHROME, args: ['--no-sandbox'] });
  const results = [];
  try {
    for (const viewport of [{ width: 1440, height: 1000 }, { width: 400, height: 860 }]) {
      for (const [index, path] of paths.entries()) {
        const context = await browser.newContext({ viewport });
        const p = await context.newPage();
        const problems = [];
        const unavailable = [];
        p.on('pageerror', e => problems.push(e.message));
        p.on('console', m => { if (m.type() === 'error' && /Content Security Policy|Refused to/.test(m.text())) problems.push(m.text()); });
        p.on('response', r => { const u = new URL(r.url()); if (u.origin === base && r.status() === 503 && u.pathname === '/api/v1/system/whatsapp/status') { unavailable.push('WhatsApp remote login status unavailable'); return; } if (u.origin === base && r.status() >= 400 && !((u.pathname.startsWith('/api/v1/media/') || /\/(brief\/today|deals\/\d+\/secretary)$/.test(u.pathname)) && r.status() === 404)) problems.push(`HTTP ${r.status()} ${u.pathname}`); });
        await context.route('**/*', async route => {
          const r = route.request(), u = new URL(r.url());
          if (u.origin !== base) { problems.push('external request refused'); return route.abort(); }
          // Classify auto-scan is a local projection write into the disposable DB.
          if (!['GET', 'HEAD'].includes(r.method()) && !(r.method() === 'POST' && u.pathname === '/api/v1/classify/scan')) { problems.push('unexpected mutation ' + u.pathname); return route.abort(); }
          return route.continue();
        });
        try {
          await p.goto(base + '/app' + path, { waitUntil: 'load' });
          await p.waitForLoadState('networkidle', { timeout: 25000 });
          assert.ok(await p.locator('h1,.pane-head').count(), 'page heading');
          if (unavailable.length) assert.equal(await p.getByText('没能读到 WhatsApp 的登录状态', { exact: true }).isVisible(), true, 'the existing remote login failure must remain explicit');
          const overflow = await p.evaluate(() => document.documentElement.scrollWidth - innerWidth);
          if (overflow > 2) problems.push(`overflow ${overflow}`);
          await p.screenshot({ path: `${out}/${viewport.width}-${index}.png`, fullPage: true });
        } catch (e) { problems.push(String(e)); }
        results.push({ width: viewport.width, index, path, problems, unavailable });
        console.log(`${problems.length ? 'FAIL' : 'PASS'} rehearsal ${viewport.width} view ${index}`);
        await context.close();
      }
    }
  } finally { await browser.close(); }
  await writeFile(`${out}/results.json`, JSON.stringify(results, null, 2));
  const failures = results.filter(r => r.problems.length);
  console.log(`rehearsal checked=${results.length} failures=${failures.length}; remote-unavailable=${results.filter(r => r.unavailable.length).length}; details private`);
  return failures.length ? 1 : 0;
}
