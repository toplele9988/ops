// Real desktop/mobile clicks; every API is intercepted with fictional data.
import assert from 'node:assert/strict';
import { chromium } from 'playwright-core';
import { installBaseFixtures } from './fixtures.base.mjs';
import { installTriageFixtures } from './fixtures.triage.mjs';
import { startLocalServer } from './local-server.mjs';

const server = await startLocalServer();
const browser = await chromium.launch({ ...(process.env.CHROME ? { executablePath: process.env.CHROME } : {}), args: ['--no-sandbox'] });
const results = [];
try {
  for (const width of [1440, 400]) {
    const context = await browser.newContext({ viewport: { width, height: 900 } });
    const page = await context.newPage();
    const problems = [];
    page.on('pageerror', error => problems.push(error.message));
    await installBaseFixtures(page, problems);
    const state = await installTriageFixtures(page);
    await context.route('**/*', async route => {
      assert.equal(new URL(route.request().url()).origin, server.base, 'No external requests');
      return route.fallback();
    });
    await page.goto(server.base + '/app/triage');
    await page.getByText('档案匹配 3 条', { exact: true }).waitFor();
    await page.evaluate(() => document.querySelector('meta[name="csrf-token"]').setAttribute('content', 'fictional-op5-csrf'));
    const bulk = page.getByRole('button', { name: '全部采用档案匹配', exact: true });
    const cancelled = page.waitForEvent('dialog').then(dialog => dialog.dismiss());
    await bulk.click(); await cancelled;
    assert.equal(state.calls.length, 0, 'Cancelling confirmation must never bind');
    const confirmed = page.waitForEvent('dialog').then(async dialog => {
      for (const id of [1, 2, 3]) assert.ok(dialog.message().includes(`虚构群${id} → 虚构单位${id}`));
      for (const id of [4, 5, 6, 7]) assert.ok(!dialog.message().includes(`虚构群${id}`));
      await dialog.accept();
    });
    await bulk.click(); await confirmed;
    const report = page.getByLabel('档案匹配采用结果');
    await report.getByText('虚构群1 → 虚构单位1：正在采用…', { exact: true }).waitFor();
    assert.equal(await page.getByRole('button', { name: '让分拣员扫描一遍', exact: true }).isDisabled(), true);
    state.release();
    await report.getByText('虚构群3 → 虚构单位3：已采用', { exact: true }).waitFor();
    await report.getByText('虚构群2 → 虚构单位2：失败：虚构会话归属已变化，请人工核对', { exact: true }).waitFor();
    assert.deepEqual(state.calls.map(c => c.id), [1, 2, 3]);
    assert.equal(state.maxActive, 1, 'Accepts run sequentially');
    for (const call of state.calls) {
      assert.equal(call.headers['x-csrf-token'], 'fictional-op5-csrf');
      assert.equal(call.headers['content-type'], 'application/x-www-form-urlencoded');
      assert.ok(!call.body, 'Reuse the existing empty acceptance form');
    }
    await page.getByText('档案匹配 1 条', { exact: true }).waitFor();
    assert.equal(await page.locator('#triage-1').count(), 0);
    assert.equal(await page.locator('#triage-2').count(), 1, 'Failed suggestion stays pending');
    assert.equal(state.items.find(s => s.id === 2).status, 'pending');
    state.failIds.clear();
    const retried = page.waitForEvent('dialog').then(async dialog => {
      assert.ok(dialog.message().includes('虚构群2 → 虚构单位2'));
      assert.ok(!dialog.message().includes('虚构群1'));
      await dialog.accept();
    });
    await bulk.click(); await retried;
    await report.getByText('虚构群2 → 虚构单位2：已采用', { exact: true }).waitFor();
    await page.getByText('档案匹配 0 条', { exact: true }).waitFor();
    assert.equal(await bulk.isDisabled(), true);
    assert.deepEqual(state.calls.map(c => c.id), [1, 2, 3, 2]);
    assert.deepEqual(problems, []);
    results.push({ width, passed: true, acceptedRequests: state.calls.length });
    await context.close();
  }
  process.stdout.write(JSON.stringify(results, null, 2) + '\n');
} finally {
  await browser.close(); await server.close();
}
