// Offline acceptance: real browser clicks against mutable fictional API fixtures.
import assert from 'node:assert/strict';
import { mkdir, writeFile } from 'node:fs/promises';
import { chromium } from 'playwright-core';
import { installProfilesFixtures } from './fixtures.profiles.mjs';
import { startLocalServer } from './local-server.mjs';

const out = new URL('../../artifacts/customers/', import.meta.url).pathname;
await mkdir(out, { recursive: true });
const server = await startLocalServer();
const browser = await chromium.launch({ ...(process.env.CHROME ? { executablePath: process.env.CHROME } : {}), args: ['--no-sandbox'] });
const results = [];
let activePage;
async function until(fn, message) {
  for (let i = 0; i < 120; i++) { if (await fn()) return; await new Promise(resolve => setTimeout(resolve, 50)); }
  assert.fail(message);
}
async function setup(viewport, options = {}) {
  const context = await browser.newContext({ viewport, timezoneId: 'America/Los_Angeles', reducedMotion: 'reduce' });
  const problems = [];
  const page = await context.newPage();
  activePage = page;
  page.setDefaultTimeout(8000);
  page.on('pageerror', e => problems.push(e.message));
  page.on('console', m => { if (m.type() === 'error' && !/Failed to load resource|net::ERR_FAILED/.test(m.text())) problems.push(m.text()); });
  await context.route('**/*', async route => {
    const url = new URL(route.request().url());
    if (url.origin !== server.base) { problems.push(`External request blocked: ${url.origin}`); return route.abort(); }
    return route.fallback();
  });
  const state = await installProfilesFixtures(page, problems, options);
  return { context, page, state, problems };
}
const desktop = { width: 1440, height: 1000 };
const mobile = { width: 390, height: 844 };
const radio = (page, text) => page.getByRole('radio', { name: new RegExp(`^${text}`) });
const card = (page, name) => page.getByRole('article', { name, exact: true });
const loaded = page => page.getByRole('radiogroup', { name: '客户阶段' }).locator('strong').filter({ hasText: /^3$/ }).waitFor();
const noOverflow = async page => assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false, 'whole page overflows');
async function snapshot(page, name) {
  await page.evaluate(() => scrollTo(0, 0));
  await page.screenshot({ path: out + name + '.png', fullPage: true });
}
async function finish(name, run) {
  await noOverflow(run.page);
  assert.deepEqual(run.problems, []);
  results.push({ scenario: name, passed: true, mutations: run.state.parties.requests.length });
  await writeFile(out + 'browser-results.json', JSON.stringify(results, null, 2) + '\n');
  await run.context.close();
}
try {
  for (const [name, viewport] of [['desktop', desktop], ['mobile', mobile]]) {
    const run = await setup(viewport);
    const { page, state } = run;
    await page.goto(server.base + '/app/customers');
    await loaded(page);
    assert.equal(await radio(page, '潜在客户').getAttribute('aria-checked'), 'true');
    for (const [label, count] of [['潜在客户', 3], ['已报价', 1], ['老客户', 2], ['售后中', 1], ['沉睡', 1]]) assert.equal(await radio(page, label).locator('strong').innerText(), String(count));
    assert.equal(await page.locator('.customers-list[aria-label] > article').count(), 3);
    const prospect = card(page, '虚构晨光贸易');
    await prospect.getByText('阿联酋', { exact: false }).waitFor();
    assert.match(await prospect.innerText(), /🇦🇪/);
    assert.equal(await prospect.locator('.customers-channel svg').count(), 1);
    assert.equal(await prospect.locator('.customers-products > span').count(), 3);
    assert.equal(await prospect.getByText('虚构吊灯', { exact: true }).count(), 0);
    assert.match(await prospect.innerText(), /北京时间 · 3 天前/);
    const date = new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false }).format(new Date(state.items.find(p => p.org_name === '虚构晨光贸易').last_at));
    assert.match(await prospect.innerText(), new RegExp(date));
    await card(page, '虚构山谷贸易').getByText('聊天还没读完，判断可能继续补充。').waitFor();
    await page.getByRole('region', { name: '待分层客户' }).getByText('虚构久未联系客户', { exact: true }).waitFor();
    await snapshot(page, name + '-potential');

    // All five stages are clickable and reflect customer rows rather than conversation counts.
    for (const [label, customer] of [['已报价', '虚构松林采购'], ['老客户', '虚构灯塔采购'], ['售后中', '虚构银杉采购'], ['沉睡', '虚构冬日贸易']]) {
      await radio(page, label).click();
      await card(page, customer).waitFor();
      if (label === '老客户') {
        assert.equal(await page.locator('.customers-list[aria-label] > article').count(), 2);
        await card(page, customer).getByText('已建档客户', { exact: true }).waitFor();
        await card(page, customer).getByText('英国', { exact: false }).waitFor();
        assert.equal(await card(page, customer).getByRole('button', { name: '建为客户' }).count(), 0);
        await card(page, customer).getByRole('button', { name: '查看依据' }).click();
        await until(async () => await card(page, customer).locator('.customers-evidence-group').count() === 2, 'owned conversations not grouped');
        await snapshot(page, name + '-existing');
      }
      if (label === '沉睡') {
        assert.match(await card(page, customer).innerText(), /国家待确认/);
        assert.match(await card(page, customer).innerText(), /120 天前/);
      }
    }
    await radio(page, '潜在客户').focus();
    await radio(page, '潜在客户').press('ArrowRight');
    await until(async () => await radio(page, '已报价').getAttribute('aria-checked') === 'true', 'keyboard stage change not rendered');
    await radio(page, '潜在客户').click();

    // Complete evidence is fetched lazily and the quote opens the correct existing inbox route.
    const key = 'fictional-profile-a';
    assert.equal(state.details.includes(key), false);
    await prospect.getByRole('button', { name: '查看依据' }).click();
    await prospect.getByText('确认采购用途', { exact: true }).waitFor();
    assert.ok(state.details.includes(key));
    assert.equal(await prospect.locator('.customers-evidence li').count(), 2);
    const quote = prospect.locator('.customers-quote').first();
    const href = await quote.getAttribute('href');
    assert.equal(new URL(href, server.base).searchParams.get('conversation'), key);
    await snapshot(page, name + '-evidence');
    await quote.click();
    await until(async () => new URL(page.url()).searchParams.get('conversation') === key, 'quote points to wrong conversation');
    await page.getByText('请介绍虚构庭院灯的规格，这是虚构验收原话。', { exact: true }).waitFor();
    await page.goBack();
    await prospect.waitFor();

    // Atomic existing create request contains the actual conversation_key, not a guessed API.
    await prospect.getByRole('button', { name: '建为客户', exact: true }).click();
    await prospect.getByText('已建档客户', { exact: true }).waitFor();
    await page.getByRole('status').filter({ hasText: '已建档 1 位客户' }).waitFor();
    assert.equal(state.parties.requests.length, 1);
    const single = state.parties.requests[0];
    assert.equal(single.tail, '');
    assert.deepEqual(Object.keys(single.fields).sort(), ['conversation_key', 'country', 'kind', 'name', 'request_id']);
    assert.deepEqual({ ...single.fields, request_id: undefined }, { kind: 'customer', name: '虚构晨光贸易', country: 'AE', conversation_key: key, request_id: undefined });
    const created = state.parties.parties.find(p => p.name === '虚构晨光贸易');
    assert.equal(state.parties.conversations.find(c => c.key === key).party_id, created.id);
    assert.ok(single.fields.request_id);

    // Cancelling confirmation sends nothing. Accepting creates precisely the selected two.
    await page.getByRole('checkbox', { name: '全选当前分段未建档客户' }).check();
    const bulk = page.getByRole('button', { name: '全部建档（2）', exact: true });
    const cancel = page.waitForEvent('dialog').then(async d => { assert.match(d.message(), /2 位客户/); await d.dismiss(); });
    await bulk.click(); await cancel;
    assert.equal(state.parties.requests.length, 1);
    const confirm = page.waitForEvent('dialog').then(async d => { assert.match(d.message(), /2 位客户/); await d.accept(); });
    await bulk.click(); await confirm;
    await page.getByRole('status').filter({ hasText: '已建档 2 位客户' }).waitFor();
    await until(async () => await page.getByRole('button', { name: '建为客户', exact: true }).count() === 0, 'bulk rows still unarchived');
    assert.equal(state.parties.requests.length, 3);
    assert.deepEqual(state.parties.requests.slice(1).map(r => r.fields.conversation_key).sort(), ['fictional-profile-b', 'fictional-profile-c']);
    assert.ok(state.parties.requests.every(r => r.tail === '' && r.fields.kind === 'customer' && r.fields.request_id));
    assert.equal(new Set(state.parties.requests.map(r => r.fields.request_id)).size, 3);
    assert.equal(state.parties.parties.filter(p => p.name === '虚构远帆贸易').length, 1);
    await page.reload();
    await loaded(page);
    assert.equal(await page.getByRole('button', { name: '建为客户', exact: true }).count(), 0);
    await snapshot(page, name + '-archived');

    // Existing customer 360° and manual creation remain reachable.
    await card(page, '虚构晨光贸易').getByRole('link', { name: '查看客户资料' }).click();
    await page.getByRole('heading', { name: '虚构晨光贸易', exact: true }).waitFor();
    await page.getByRole('button', { name: '合并重复客户', exact: true }).waitFor();
    await page.getByRole('link', { name: '返回客户分层', exact: true }).click();
    await page.getByRole('link', { name: '管理客户档案' }).click();
    await page.getByRole('button', { name: '新建往来单位', exact: true }).click();
    await page.getByRole('dialog', { name: '新建往来单位' }).waitFor();
    await page.getByRole('dialog').getByRole('button', { name: '取消' }).click();
    await finish(name + ': stages, evidence, single, bulk, preserved details', run);
  }

  for (const [name, viewport] of [['desktop', desktop], ['mobile', mobile]]) {
    const run = await setup(viewport, { status: 404 });
    await run.page.clock.install();
    await run.page.goto(server.base + '/app/customers');
    await run.page.getByRole('heading', { name: 'AI 还在读聊天记录，读完会自动出现' }).waitFor();
    await run.page.getByRole('region', { name: '待分层客户' }).getByText('虚构灯塔采购', { exact: true }).waitFor();
    assert.equal(await run.page.getByRole('alert').count(), 0);
    await snapshot(run.page, name + '-404');
    // New profiles appear on polling, without a page reload or a user refresh.
    run.state.status = 200;
    await run.page.clock.fastForward(21000);
    await loaded(run.page);
    await finish(name + ': 404 waiting and automatic discovery', run);
  }

  // Failure states, recovery, empty results and delayed loading stay usable.
  for (const scenario of ['empty', 'loading', '500', 'malformed', 'network', 'ownership', 'legacy', 'pagination', 'detail404', 'detail500', 'retry', 'partial-batch', 'lost-response', 'conflict', 'filters']) {
    const run = await setup(mobile, { empty: scenario === 'empty', status: scenario === '500' ? 500 : 200 });
    const { page, state } = run;
    if (scenario === 'loading') state.delay = 900;
    if (scenario === 'malformed') state.malformed = true;
    if (scenario === 'network') state.networkError = true;
    if (scenario === 'ownership') await page.route('**/api/v1/parties-ops/conversations', route => route.fulfill({ status: 503, json: { error: '虚构会话归属读取失败' } }));
    if (scenario === 'legacy') await page.route('**/api/v1/parties-ops/conversations', route => route.fulfill({ json: { items: state.parties.conversations.map(c => c.party_id === 1 ? { ...c, party_id: null } : c) } }));
    const pages = [];
    if (scenario === 'pagination') {
      const rows = [...state.parties.parties.filter(p => p.kind === 'customer'), ...Array.from({ length: 102 }, (_, i) => ({ ...structuredClone(state.parties.parties[3]), id: 100 + i, name: `虚构分页客户${i + 1}` }))];
      await page.route('**/api/v1/parties-ops?**', route => {
        if (route.request().method() !== 'GET') return route.fallback();
        const url = new URL(route.request().url());
        const n = Number(url.searchParams.get('page') || 1);
        const size = Number(url.searchParams.get('page_size') || 40);
        pages.push(n);
        return route.fulfill({ json: { items: rows.slice((n - 1) * size, n * size), total: rows.length, countries: [], channels: [], page: n, page_size: size } });
      });
    }
    if (scenario === 'detail404') state.detailStatus = 404;
    if (scenario === 'detail500') state.detailStatus = 500;
    await page.goto(server.base + '/app/customers');
    if (scenario === 'empty') await page.getByRole('heading', { name: 'AI 还在读聊天记录，读完会自动出现' }).waitFor();
    else if (scenario === 'loading') {
      await page.getByRole('status').filter({ hasText: '正在整理客户和聊天记录' }).waitFor();
      await snapshot(page, 'mobile-loading');
      await loaded(page);
    } else if (['500', 'malformed', 'network'].includes(scenario)) {
      await page.getByRole('alert').filter({ hasText: '客户聊天分析暂时没读到' }).waitFor();
      await snapshot(page, 'mobile-' + scenario);
      state.status = 200; state.malformed = false; state.networkError = false;
      await page.getByRole('button', { name: '重读聊天分析' }).click();
      await loaded(page);
    } else if (scenario === 'ownership') {
      await page.getByRole('alert').filter({ hasText: '刷新后再建档，避免重复' }).waitFor();
      assert.equal(await card(page, '虚构晨光贸易').getByRole('button', { name: '建为客户' }).isDisabled(), true);
    } else {
      await loaded(page);
      const prospect = card(page, '虚构晨光贸易');
      if (scenario === 'legacy') {
        await radio(page, '老客户').click();
        await card(page, '虚构灯塔采购').getByText('已建档客户', { exact: true }).waitFor();
        assert.equal(await page.locator('.customers-list[aria-label] > article').count(), 2);
        assert.equal(await page.getByText('虚构同一客户新询价', { exact: true }).count(), 0);
      } else if (scenario === 'pagination') {
        await card(page, '虚构分页客户102').waitFor();
        assert.deepEqual(pages, [1, 2]);
      } else if (scenario.startsWith('detail')) {
        await prospect.getByRole('button', { name: '查看依据' }).click();
        if (scenario === 'detail404') await prospect.getByText('AI 还在读聊天记录，读完会自动出现').waitFor();
        else {
          await prospect.getByRole('alert').waitFor();
          state.detailStatus = 200;
          await prospect.getByRole('button', { name: '重试读取依据' }).click();
          await prospect.getByText('确认采购用途', { exact: true }).waitFor();
        }
      } else if (scenario === 'retry') {
        state.parties.failNext = true;
        await prospect.getByRole('button', { name: '建为客户' }).click();
        await page.getByRole('alert').filter({ hasText: '建档结果暂未确认' }).waitFor();
        const failed = state.parties.failedRequests[0].fields;
        await prospect.getByRole('button', { name: '建为客户' }).click();
        await prospect.getByText('已建档客户', { exact: true }).waitFor();
        assert.deepEqual(state.parties.requests[0].fields, failed);
        assert.equal(state.parties.parties.filter(p => p.name === '虚构晨光贸易').length, 1);
      } else if (scenario === 'partial-batch') {
        state.parties.failNext = true;
        await page.getByRole('checkbox', { name: '全选当前分段未建档客户' }).check();
        page.once('dialog', d => d.accept());
        await page.getByRole('button', { name: '全部建档（3）' }).click();
        await page.getByRole('status').filter({ hasText: '已建档 2 位客户' }).waitFor();
        await page.getByRole('alert').filter({ hasText: '建档结果暂未确认' }).waitFor();
        assert.equal(state.parties.requests.length, 2);
        assert.equal(await page.getByRole('checkbox', { name: '选择虚构晨光贸易' }).isChecked(), true);
        page.once('dialog', d => d.accept());
        await page.getByRole('button', { name: '全部建档（1）' }).click();
        await prospect.getByText('已建档客户', { exact: true }).waitFor();
        assert.equal(state.parties.requests.length, 3);
        assert.equal(state.parties.failedRequests[0].fields.request_id, state.parties.requests[2].fields.request_id);
      } else if (scenario === 'lost-response') {
        state.parties.loseNextResponse = true;
        await prospect.getByRole('button', { name: '建为客户' }).click();
        await page.getByRole('alert').filter({ hasText: '建档结果暂未确认' }).waitFor();
        await prospect.getByText('已建档客户', { exact: true }).waitFor();
        assert.equal(state.parties.parties.filter(p => p.name === '虚构晨光贸易').length, 1);
        assert.equal(state.parties.requests.length, 1);
      } else if (scenario === 'conflict') {
        // Another operator archives this chat after our list was loaded.
        state.parties.conversations.find(c => c.key === 'fictional-profile-a').party_id = 2;
        await prospect.getByRole('button', { name: '建为客户' }).click();
        await page.getByRole('alert').filter({ hasText: '请刷新客户后再核对' }).waitFor();
        assert.equal(state.parties.parties.some(p => p.name === '虚构晨光贸易'), false);
        assert.equal(state.parties.conversations.find(c => c.key === 'fictional-profile-a').party_id, 2);
      } else if (scenario === 'filters') {
        await page.getByLabel('客户来源渠道').selectOption('wechat');
        await until(async () => await page.locator('.customers-list[aria-label] > article').count() === 1, 'channel filter not rendered');
        await card(page, '虚构山谷贸易').waitFor();
        await page.getByLabel('搜索客户').fill('不存在的虚构产品');
        await page.getByRole('heading', { name: '没有找到符合条件的客户' }).waitFor();
        await snapshot(page, 'mobile-no-match');
        await page.getByRole('button', { name: '清除筛选', exact: true }).first().click();
        await loaded(page);
      }
    }
    assert.ok(state.reads.every(r => r.kind === 'customer'));
    await finish(scenario, run);
  }
  console.log(JSON.stringify(results, null, 2));
} catch (error) {
  if (activePage && !activePage.isClosed()) {
    await activePage.screenshot({ path: out + 'failure.png', fullPage: true });
    await writeFile(out + 'failure.json', JSON.stringify({ error: String(error), url: activePage.url(), text: await activePage.locator('body').innerText() }, null, 2));
  }
  throw error;
} finally { await browser.close(); await server.close(); }
