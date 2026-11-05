// W4/W5: actual clicks, mutable in-browser API fixtures, no business backend.
import assert from 'node:assert/strict';
import { mkdir, writeFile } from 'node:fs/promises';
import { chromium } from 'playwright-core';
import { installPartiesFixtures } from './fixtures.parties.mjs';
import { startLocalServer } from './local-server.mjs';

const out = new URL('../../artifacts/parties/', import.meta.url).pathname;
await mkdir(out, { recursive: true });
const server = await startLocalServer();
const browser = await chromium.launch({ ...(process.env.CHROME ? { executablePath: process.env.CHROME } : {}), args: ['--no-sandbox'] });
let activePage;
const results = [];
await writeFile(out + 'browser-results.json', '[]\n');
const until = async (fn, message) => {
  for (let n = 0; n < 100; n++) { if (await fn()) return; await new Promise(r => setTimeout(r, 50)); }
  assert.fail(message);
};
try {
  for (const [name, viewport] of [['desktop', { width: 1440, height: 900 }], ['mobile', { width: 400, height: 860 }]]) {
    const context = await browser.newContext({ viewport });
    const page = await context.newPage();
    activePage = page;
    page.setDefaultTimeout(10000);
    const problems = [];
    const state = await installPartiesFixtures(page, problems);
    page.on('pageerror', e => problems.push(e.message));
    page.on('console', m => { if (m.type() === 'error' && !/503/.test(m.text())) problems.push(m.text()); });
    await context.route('**/*', async route => {
      const url = new URL(route.request().url());
      if (url.origin !== server.base) { problems.push(`External request blocked: ${url.origin}`); await route.abort(); }
      else await route.fallback();
    });
    let dialogs = 0;
    page.on('dialog', async d => { dialogs++; await d.accept(); });
    const button = text => page.getByRole('button', { name: text, exact: true });
    const open = async (person) => { await button(`处理${person}`).click(); await page.getByRole('heading', { name: person, exact: true }).waitFor(); };
    const status = async text => { await page.getByRole('status').filter({ hasText: text }).first().waitFor(); };
    await page.goto(server.base + '/app/customers?view=records');
    await open('虚构灯塔采购');
    assert.equal(await page.getByRole('region', { name: '往来时间线' }).getByText('请提供虚构产品报价。').count(), 1);
    await button('编辑资料与跟进日期').click();
    const edit = page.getByRole('dialog', { name: '编辑资料与跟进日期' });
    await edit.getByLabel('单位名称', { exact: true }).fill('虚构灯塔重点客户');
    await edit.getByLabel('标签（逗号分隔）').fill('重点,灯具');
    await edit.getByLabel('备注', { exact: true }).fill('虚构验收：下周确认采购数量');
    await edit.getByLabel('下次跟进日期').fill('2026-11-18');
    await edit.getByRole('button', { name: '保存资料', exact: true }).click();
    await status('已保存');
    await page.getByRole('heading', { name: '虚构灯塔重点客户', exact: true }).waitFor();
    assert.equal(state.parties[0].next_followup, '2026-11-18');
    assert.equal(state.parties[0].tags, '重点,灯具');
    assert.equal(state.parties[0].notes, '虚构验收：下周确认采购数量');

    // Linking an existing conversation changes identities and the full timeline.
    await page.getByLabel('待关联会话', { exact: true }).selectOption('fictional-link');
    await button('关联到本单位').click(); await status('已关联会话');
    await until(async () => await page.getByRole('link', { name: '查看会话', exact: true }).count() === 2, 'identity link did not render');
    assert.equal(state.conversations.find(c => c.key === 'fictional-link').party_id, 1);
    await page.getByLabel('草稿发往会话').selectOption('fictional-link');
    await button('一键起草跟进').click();
    const draft = page.getByRole('dialog', { name: '人工核对演练草稿' });
    await draft.waitFor();
    await draft.getByLabel('消息内容').fill('您好，这是虚构跟进内容，请确认采购数量。');
    await draft.getByRole('button', { name: '确认演练草稿' }).click();
    await status('人工核对已记录');
    assert.equal(state.parties[0].drafts[0].conversation_key, 'fictional-link');
    assert.ok(state.parties[0].drafts[0].reviewed_at);
    assert.equal(state.parties[0].drafts[0].text, '您好，这是虚构跟进内容，请确认采购数量。');
    assert.equal(state.parties[0].drafts[0].state, 'draft');
    assert.equal(state.parties[0].drafts[0].dry_run, 1);
    await page.getByText('人工已核对 · 尚未发送', { exact: false }).waitFor();

    await button('转为案子').click();
    const deal = page.getByRole('dialog', { name: '转为案子' });
    await deal.getByLabel('案子名称').fill('虚构灯塔新采购案子');
    await deal.getByRole('button', { name: '创建采购案子' }).click(); await status('已建案子');
    await page.getByText('虚构灯塔新采购案子 · 进行中', { exact: true }).waitFor();
    assert.equal(state.parties[0].deals.at(-1).conversations.length, 2);
    assert.equal(state.parties[0].deals.at(-1).tasks.length, 1);

    // Merge cancellation must produce no mutation; acceptance has explicit confirmation.
    await button('合并重复客户').click();
    const merge = page.getByRole('dialog', { name: '合并重复客户' });
    await merge.getByLabel('要合入的客户').selectOption('2');
    await merge.getByRole('button', { name: '取消', exact: true }).click();
    assert.equal(state.merges.length, 0);
    await button('合并重复客户').click();
    await merge.getByLabel('要合入的客户').selectOption('2');
    await merge.getByRole('button', { name: '确认合并客户' }).click(); await status('已合并展示');
    await until(async () => await page.getByRole('link', { name: '查看会话', exact: true }).count() === 3, 'merged multi-channel identities absent');
    assert.equal(state.merges.length, 1);
    await button('撤销这次合并').click(); await status('已撤销合并');
    assert.ok(state.merges[0].undone_at);
    await page.getByText('虚构灯塔重复资料 ·', { exact: false }).filter({ hasText: '已撤销' }).waitFor();

    await page.evaluate(() => scrollTo(0, 0));
    await page.screenshot({ path: out + name + '-customer.png', fullPage: true });
    await button('返回客户列表').click();
    await page.getByLabel('搜索客户', { exact: true }).fill('不存在的虚构客户');
    await page.getByRole('heading', { name: '没有符合条件的往来单位' }).waitFor();
    await button('清除筛选').click();
    await button('处理虚构灯塔重点客户').waitFor();
    await page.getByLabel('国家', { exact: true }).selectOption('虚构国甲');
    await page.getByLabel('来源渠道').selectOption('alibaba');
    await page.getByLabel('最近联系', { exact: true }).selectOption('7');
    await page.getByLabel('未结案子', { exact: true }).selectOption('yes');
    await page.getByLabel('排序', { exact: true }).selectOption('tasks');
    await until(async () => await page.locator('.parties-list .parties-row').count() === 1 && await button('处理虚构灯塔重点客户').isVisible(), 'combined filters not reflected');
    await button('清除筛选').click();
    await page.getByLabel('最近联系', { exact: true }).selectOption('never');
    await until(async () => await page.locator('.parties-list .parties-row').count() === 1 && await button('处理虚构久未联系客户').isVisible(), 'never-contact filter failed');
    await button('清除筛选').click();
    await page.getByLabel('排序', { exact: true }).selectOption('name');
    await until(() => new URL(page.url()).searchParams.get('sort') === 'name', 'sort not in URL');

    // New customer, linked at creation, then saved state survives a reload.
    await button('新建往来单位').click();
    const create = page.getByRole('dialog', { name: '新建往来单位' });
    await create.getByLabel('单位名称', { exact: true }).fill('虚构新建客户');
    await create.getByLabel('所在国家').fill('虚构国丙');
    await create.getByLabel('关联现有会话').selectOption('fictional-new');
    state.failNext = true;
    await create.getByRole('button', { name: '保存并打开资料' }).click();
    await create.getByRole('alert').filter({ hasText: '虚构保存失败' }).first().waitFor();
    assert.equal(state.parties.filter(p => p.name === '虚构新建客户').length, 0);
    await create.getByRole('button', { name: '保存并打开资料' }).click(); await status('已新建往来单位');
    await page.getByRole('heading', { name: '虚构新建客户', exact: true }).waitFor();
    const newParty = state.parties.find(p => p.name === '虚构新建客户');
    assert.equal(state.conversations.find(c => c.key === 'fictional-new').party_id, newParty.id);
    assert.equal(state.failedRequests[0].fields.request_id, state.requests.find(r => r.tail === '' && r.fields.name === '虚构新建客户').fields.request_id);
    await page.reload(); await page.getByRole('heading', { name: '虚构新建客户', exact: true }).waitFor();
    // Replaying the same write request never creates another party.
    const original = state.requests.find(r => r.tail === '' && r.fields.name === '虚构新建客户');
    await page.evaluate(async fields => { await fetch('/api/v1/parties-ops', { method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded' }, body: new URLSearchParams(fields) }); }, original.fields);
    assert.equal(state.parties.filter(p => p.name === '虚构新建客户').length, 1);

    await page.goto(server.base + '/app/suppliers'); await open('虚构星河工厂');
    const product = page.locator('.parties-product').filter({ has: page.getByRole('heading', { name: '虚构演示灯A DEMO-A' }) });
    await product.getByText('资料齐全，待人工核对', { exact: false }).waitFor();
    assert.equal(await page.locator('.parties-product').filter({ has: page.getByRole('heading', { name: '虚构缺资料产品' }) }).getByRole('button', { name: '标记资料已核' }).isDisabled(), true);
    await product.getByRole('button', { name: '标记资料已核' }).click(); await status('外箱尺寸、毛重');
    await product.getByText('包装核对：已核对', { exact: true }).waitFor();
    assert.equal(state.parties[2].products[0].verified, true);
    await page.getByLabel('条款类型').selectOption('lead_time');
    await page.getByLabel('条款内容').fill('虚构交期：确认后15天');
    await button('记录合作条款').click(); await status('已保存合作条款');
    await page.getByText('虚构交期：确认后15天 · 已核对', { exact: true }).waitFor();
    assert.equal(state.parties[2].terms[0].value, '虚构交期：确认后15天');
    for (const text of ['一键询价', '催资料']) {
      await button(text).click(); await draft.waitFor();
      assert.match(await draft.getByLabel('消息内容').inputValue(), text === '一键询价' ? /报价有效期/ : /毛重/);
      await draft.getByRole('button', { name: '确认演练草稿' }).click(); await status('人工核对已记录');
    }
    assert.equal(state.parties[2].drafts.length, 2);
    assert.ok(state.parties[2].drafts.every(d => d.dry_run === 1 && d.state === 'draft' && d.reviewed_at));
    await page.evaluate(() => scrollTo(0, 0));
    await page.screenshot({ path: out + name + '-supplier.png', fullPage: true });
    await button('返回供应商列表').click();
    for (const [kind, label] of [['supplier', '虚构新工厂'], ['forwarder', '虚构货代'], ['vendor', '虚构包装厂']]) {
      await button('新建往来单位').click();
      await create.getByLabel('往来单位类型').selectOption(kind);
      await create.getByLabel('单位名称', { exact: true }).fill(label);
      await create.getByRole('button', { name: '保存并打开资料' }).click(); await status('已新建往来单位');
      await page.getByRole('heading', { name: label, exact: true }).waitFor();
      assert.equal(state.parties.find(p => p.name === label).kind, kind);
      await button(`返回${kind === 'supplier' ? '供应商' : kind === 'forwarder' ? '货代' : '配套供应商'}列表`).click();
    }
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1);
    assert.equal(overflow, false, `${name}: viewport overflow`);
    assert.ok(dialogs >= 6, 'dangerous operations must be confirmed');
    assert.ok(state.requests.every(r => r.fields.request_id));
    assert.ok(state.requests.every(r => !/send|payment/.test(r.tail)));
    assert.deepEqual(problems, []);
    results.push({ viewport: name, passed: true, mutations: state.requests.length, expectedFailures: state.failedRequests.length, confirmations: dialogs });
    await writeFile(out + 'browser-results.json', JSON.stringify(results, null, 2));
    await context.close();
  }
  // Empty state includes a concrete way out.
  const context = await browser.newContext({ viewport: { width: 400, height: 860 } });
  const page = await context.newPage();
  const problems = [];
  await installPartiesFixtures(page, problems, { empty: true });
  await page.goto(server.base + '/app/customers?view=records');
  await page.getByRole('heading', { name: '这里还没有客户：去商机沟通把会话归到客户' }).waitFor();
  assert.ok(await page.getByRole('link', { name: '去商机沟通', exact: true }).isVisible());
  await page.screenshot({ path: out + 'mobile-empty.png', fullPage: true });
  assert.deepEqual(problems, []);
  await context.close();
  await writeFile(out + 'browser-results.json', JSON.stringify(results, null, 2));
  console.log(JSON.stringify(results, null, 2));
} catch (error) {
  if (activePage && !activePage.isClosed()) {
    await activePage.screenshot({ path: out + 'failure.png', fullPage: true });
    await writeFile(out + 'failure.json', JSON.stringify({ url: activePage.url(), error: String(error), text: await activePage.locator('body').innerText() }, null, 2));
  }
  throw error;
} finally { await browser.close(); await server.close(); }
