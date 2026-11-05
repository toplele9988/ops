// Built workspace + fictional API fixtures only. Run after npm run build.
import assert from 'node:assert/strict';
import { chromium } from 'playwright-core';
import { installBaseFixtures } from './fixtures.base.mjs';
import { startLocalServer } from './local-server.mjs';

const server = await startLocalServer();
const browser = await chromium.launch({ ...(process.env.CHROME ? { executablePath: process.env.CHROME } : {}), args: ['--no-sandbox'] });
try {
  for (const width of [1440, 400]) {
    const page = await browser.newPage({ viewport: { width, height: 900 } });
    const problems = [];
    await installBaseFixtures(page, problems);
    page.on('pageerror', error => problems.push(String(error)));
    await page.goto(server.base + '/app/directory');
    const hint = page.getByTestId('missing-names');
    await hint.waitFor();
    assert.match(await hint.innerText(), /微信会话共 6 个，还有 2 个没名字。接口没返回群名：2 个。/);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    assert.deepEqual(problems, []);
    await page.close();
  }
  console.log('名字缺失提示：桌面、手机通过。');
} finally {
  await browser.close();
  await server.close();
}
