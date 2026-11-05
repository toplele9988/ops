// Real components and clicks. API requests are intercepted with fictional data.
import assert from 'node:assert/strict';
import { mkdir, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { chromium } from 'playwright-core';
import { startLocalServer } from './local-server.mjs';
import { installFixtures } from './fixtures.mjs';
import { installDossierFixtures } from './fixtures.dossiers.mjs';

const out=resolve(process.env.DOSSIER_ARTIFACTS||'../private_out/dossiers-acceptance/synthetic');
await mkdir(out,{recursive:true});
const server=await startLocalServer();let browser;
try{
  browser=await chromium.launch({...(process.env.CHROME?{executablePath:process.env.CHROME}:{}),args:['--no-sandbox']});
  const results=[];
  for(const viewport of [{width:1440,height:1000},{width:390,height:844}]){
    const context=await browser.newContext({viewport}),page=await context.newPage(),errors=[],problems=[];
    page.on('pageerror',e=>errors.push(e.message));
    page.on('console',m=>{if(m.type()==='error'&&/Content Security Policy|Refused to/.test(m.text()))errors.push(m.text());});
    await context.route('**/*',r=>new URL(r.request().url()).origin===server.base?r.fallback():(errors.push('external request'),r.abort()));
    await installFixtures(page,problems);const state=await installDossierFixtures(page);
    const visit=async path=>{await page.goto(server.base+'/app/'+path);await page.getByRole('heading',{level:1}).waitFor();};
    const shot=async name=>{assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2),'no horizontal page overflow');await page.screenshot({path:resolve(out,`${name}-${viewport.width}.png`),fullPage:true});};
    await visit('supplier-dossiers');
    await page.getByRole('link',{name:/虚构甲灯具.*产品族/}).waitFor();await shot('suppliers');
    await page.getByRole('combobox',{name:'供应商类型',exact:true}).selectOption('物流');
    await page.getByRole('link',{name:/虚构运达物流.*产品族/}).waitFor();
    assert.equal(await page.getByRole('link',{name:/虚构甲灯具.*产品族/}).count(),0);
    await page.getByRole('combobox',{name:'供应商类型',exact:true}).selectOption('');
    await page.getByRole('searchbox',{name:'搜索供应商档案',exact:true}).fill('虚构甲');
    await page.waitForFunction(()=>document.querySelectorAll('.ds-supplier-card').length===1);
    await page.locator('.ds-supplier-card').click();
    await page.getByRole('heading',{name:'虚构甲灯具',exact:true}).waitFor();
    await page.getByText('123.4500',{exact:true}).waitFor();assert.equal(await page.getByText('120.00000001',{exact:true}).count(),0);
    await page.locator('.ds-quote').filter({has:page.getByText('123.4500',{exact:true})}).getByText('图片识别，待核对',{exact:true}).first().waitFor();
    await page.getByRole('button',{name:'查看来源 attachment:ocr',exact:true}).first().click();
    await page.getByRole('dialog').getByText('虚构报价图.png',{exact:true}).waitFor();
    await page.getByRole('img',{name:'来源原图',exact:true}).waitFor();
    await page.getByRole('dialog').getByText('图片识别，待核对',{exact:true}).first().waitFor();
    await page.waitForFunction(()=>document.querySelector('.ds-original')?.naturalWidth>0);
    await shot('source');await page.getByRole('button',{name:'关闭来源',exact:true}).click();
    await page.getByRole('dialog').waitFor({state:'hidden'});await shot('supplier-detail');
    await visit('product-dossiers');await page.locator('.ds-product-card').waitFor();
    await page.getByRole('img',{name:'产品原图缩略',exact:true}).waitFor();await shot('products');
    await page.locator('.ds-product-card').click();
    await page.getByText('120.00000001',{exact:true}).waitFor();
    await page.getByText('123.4500',{exact:true}).waitFor();
    assert.equal(await page.getByText('未确认供货',{exact:true}).count(),1);
    assert.ok(await page.getByText('未报价',{exact:true}).count()>0);
    assert.ok(await page.getByText(/币种 未知 · 税制 未知/).count()>0);
    const plain=page.locator('.ds-quote').filter({has:page.getByText('120.00000001',{exact:true})});
    assert.equal(await plain.getByText('图片识别，待核对',{exact:true}).count(),0);
    await plain.getByRole('button',{name:'查看来源 page:2:cell:B7',exact:true}).click();
    await page.getByRole('dialog').getByText('虚构资料.png',{exact:true}).waitFor();
    assert.equal(await page.getByRole('dialog').getByText('图片识别，待核对',{exact:true}).count(),0);
    await page.getByRole('button',{name:'关闭来源',exact:true}).click();
    await page.getByRole('dialog').waitFor({state:'hidden'});
    await shot('comparison');
    await visit('dossier-gaps');await page.getByTestId('dossier-gap').first().waitFor();
    await page.getByRole('combobox',{name:'该问哪家',exact:true}).selectOption('S1');
    await page.getByText('当前筛选 34 项',{exact:false}).waitFor();
    await page.getByRole('button',{name:'下一页',exact:true}).click();
    await page.getByText('2 / 2 · 共 34 项',{exact:true}).waitFor();
    await page.getByRole('button',{name:'生成问话草稿',exact:true}).click();
    await page.getByText('草稿已存档，未发送。',{exact:true}).waitFor();
    assert.equal(JSON.parse(state.drafts[0].gap_ids_json).length,34);
    await shot('draft');
    await page.getByRole('combobox',{name:'缺口优先级',exact:true}).selectOption('1');
    await page.getByRole('combobox',{name:'缺口类型',exact:true}).selectOption('power_conflict');
    await page.getByText('当前筛选 1 项',{exact:false}).waitFor();
    await page.getByRole('button',{name:'生成问话草稿',exact:true}).click();
    await page.getByText('草稿已存档，未发送。',{exact:true}).waitFor();
    assert.equal(JSON.parse(state.drafts[0].gap_ids_json).length,1);
    await page.getByText('查看已存档草稿',{exact:true}).click();
    await page.locator('.ds-archive-draft').first().locator('summary').click();
    await shot('gaps');
    assert.equal(state.posts.length,2);
    await visit('');await page.getByRole('region',{name:'供应商缺资料',exact:true}).waitFor();await shot('today-queue');
    await page.getByRole('link',{name:'查看缺资料 / 生成问话草稿',exact:true}).click();
    await page.getByRole('heading',{name:'缺资料',exact:true}).waitFor();
    for(const [mode,text] of [['missing','档案接口尚未部署或记录不存在'],['error','读取失败'],['network','读取失败'],['empty','没有符合条件的供应商']]){
      state.mode=mode;await visit('supplier-dossiers');await page.getByText(text,{exact:true}).waitFor();await shot(mode);
    }
    assert.deepEqual(errors,[]);assert.deepEqual(problems,[]);
    results.push({viewport,passed:true,draftPosts:state.posts.length,networkSends:0,faultStates:4});
    await context.close();
  }
  await writeFile(resolve(out,'results.json'),JSON.stringify(results,null,2));console.log(JSON.stringify({passed:true,viewports:results.length,artifacts:out}));
}finally{if(browser)await browser.close();await server.close();}
