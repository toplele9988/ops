// Actual private sidecar projection, GET only. Screenshots must remain private.
import assert from 'node:assert/strict';
import { readFile, mkdir, writeFile } from 'node:fs/promises';
import { resolve, sep } from 'node:path';
import { chromium } from 'playwright-core';

const state=resolve(process.env.DOSSIER_PRIVATE_STATE||'../private_out/dossiers-acceptance/real');
assert.ok(state.split(sep).includes('private_out'));
const out=resolve(state,'screenshots');await mkdir(out,{recursive:true});
const base=process.env.DOSSIER_PRIVATE_BASE||'http://127.0.0.1:18936';
assert.equal(new URL(base).hostname,'127.0.0.1');
const token=await readFile(resolve(state,'rehearsal-token'),'utf8');
let browser;
try{
  browser=await chromium.launch({...(process.env.CHROME?{executablePath:process.env.CHROME}:{}),args:['--no-sandbox']});
  const results=[];
  for(const viewport of [{width:1440,height:1000},{width:390,height:844}]){
    const context=await browser.newContext({viewport,extraHTTPHeaders:{'x-dossier-rehearsal':token}}),page=await context.newPage(),errors=[];
    page.on('pageerror',()=>errors.push('browser script failed'));
    await context.route('**/*',route=>{
      const r=route.request();
      if(new URL(r.url()).origin!==base||r.method()!=='GET'){errors.push('non-read request blocked');return route.abort();}
      return route.continue();
    });
    const shot=async name=>{
      await page.locator('.ds-error').waitFor({state:'hidden'});
      assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2),'private layout overflow');
      await page.screenshot({path:resolve(out,`${name}-${viewport.width}.png`),fullPage:true});
    };
    const suppliers=await context.request.get(base+'/api/v1/dossiers/suppliers');assert.equal(suppliers.status(),200);
    const s=await suppliers.json();assert.ok(s.total>0);
    const families=await context.request.get(base+'/api/v1/dossiers/families');assert.equal(families.status(),200);
    const f=await families.json();assert.ok(f.total>0);
    await page.goto(base+'/app/supplier-dossiers');await page.locator('.ds-supplier-card').first().waitFor();await shot('suppliers');
    // Select a supplier with configurations, without logging its identity.
    const sid=s.items.find(x=>x.configuration_count>0).supplier_id;
    await page.goto(base+'/app/supplier-dossiers/'+encodeURIComponent(sid));await page.locator('.ds-table tbody tr').first().waitFor();await shot('supplier-detail');
    const supplierDetail=await (await context.request.get(base+'/api/v1/dossiers/suppliers/'+encodeURIComponent(sid))).json();
    const quote=supplierDetail.products.flatMap(p=>p.offers)[0];
    const candidate=quote?.evidence.some(e=>e.evidence_state==='ocr_candidate');
    if(candidate)assert.ok(quote.evidence.filter(e=>e.evidence_state==='ocr_candidate').every(e=>e.method==='ocr'));
    await page.locator('.ds-quote .ds-source').first().click();
    await page.getByRole('dialog').getByRole('heading',{name:'逐字段证据',exact:true}).waitFor();
    if(candidate){
      await page.getByRole('dialog').getByText('图片识别，待核对',{exact:true}).first().waitFor();
      const source=await (await context.request.get(base+'/api/v1/dossiers/sources/'+encodeURIComponent(quote.source_id))).json();
      if(source.available&&source.mime?.startsWith('image/'))await page.waitForFunction(()=>document.querySelector('.ds-original')?.naturalWidth>0);
    }
    await shot('source');
    await page.getByRole('button',{name:'关闭来源',exact:true}).click();await page.getByRole('dialog').waitFor({state:'hidden'});
    await page.goto(base+'/app/product-dossiers');await page.locator('.ds-product-card').first().waitFor();
    const imageFamily=f.items.find(x=>x.thumbnail_source_id);
    if(imageFamily){
      const imageSource=await context.request.get(base+'/api/v1/dossiers/sources/'+encodeURIComponent(imageFamily.thumbnail_source_id));
      const image=await imageSource.json();
      if(image.available&&image.mime?.startsWith('image/')){
        await page.getByRole('img',{name:'产品原图缩略',exact:true}).first().waitFor();
        await page.waitForFunction(()=>[...document.querySelectorAll('.ds-thumb img')].some(i=>i.complete&&i.naturalWidth>0));
      }
    }
    await shot('products');
    await page.locator('.ds-product-card').first().click();await page.locator('.ds-comparison tbody tr').first().waitFor();await shot('comparison');
    await page.goto(base+'/app/dossier-gaps');await page.getByTestId('dossier-gap').first().waitFor();await shot('gaps');
    assert.deepEqual(errors,[]);
    results.push({viewport,passed:true,supplier_count:s.total,family_count:f.total,readOnly:true,screenshots:6});
    await context.close();
  }
  await writeFile(resolve(state,'browser-result.json'),JSON.stringify(results,null,2));
  console.log(JSON.stringify({passed:true,viewports:2,readOnly:true,screenshots:12}));
}catch{
  // Assertions and browser error messages may contain private DOM text/URLs.
  console.error('Private rehearsal failed; inspect private screenshots locally.');process.exitCode=1;
}finally{if(browser)await browser.close();}
