// Real browser clicks on the real Operator component in a disposable routed build.
// The shared App/nav stay untouched. APIs are intercepted; all sending uses a fake.
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, readFile, writeFile, cp, symlink, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { resolve, extname, sep } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { spawnSync } from 'node:child_process';
import { createServer } from 'node:http';
import { installOperatorFixtures } from './fixtures.operator.mjs';

const web = fileURLToPath(new URL('../',import.meta.url));
const deps = resolve(process.env.OPERATOR_NODE_MODULES ?? resolve(web,'node_modules'));
const { chromium } = await import(pathToFileURL(resolve(deps,'playwright-core/index.mjs')).href);
const root = await mkdtemp(resolve(tmpdir(),'ops-operator-'));
const out = process.env.OPERATOR_ARTIFACTS ?? resolve(root,'artifacts');
await mkdir(out,{recursive:true});
let browser, server;
try {
  await cp(resolve(web,'src'),resolve(root,'src'),{recursive:true});
  for(const file of ['index.html','package.json','tsconfig.json','vite.config.ts']) await cp(resolve(web,file),resolve(root,file));
  await symlink(deps,resolve(root,'node_modules'),'dir');
  const appPath=resolve(root,'src/App.tsx');
  let app=await readFile(appPath,'utf8');
  app='import { Operator } from "./pages/Operator";\n'+app;
  app=app.replace('<Route path="roles" element={<RolesPage />} />','<Route path="roles" element={<RolesPage />} />\n<Route path="operator" element={<Operator />} />');
  await writeFile(appPath,app);
  const navPath=resolve(root,'src/nav.ts');let nav=await readFile(navPath,'utf8');
  nav=nav.replace('const PAGES: NavItem[] = [','const PAGES: NavItem[] = [\n{ key: "operator", label: "AI 运营员", path: "/operator", kind: "live", area: "front", group: "日常工作", icon: Bot },');
  nav=nav.replace('"search", "roles",','"search", "roles", "operator",');await writeFile(navPath,nav);
  for(const [tool,args] of [['typescript/bin/tsc',['--noEmit']],['vite/bin/vite.js',['build']]]) {
    const built=spawnSync(process.execPath,[resolve(deps,tool),...args],{cwd:root,encoding:'utf8'});
    assert.equal(built.status,0,built.stdout+built.stderr);console.log(built.stdout.trim());
  }
  const dist=resolve(root,'dist');
  server=createServer(async(req,res)=>{
    res.setHeader('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; frame-ancestors 'none'");
    try {
      const path=new URL(req.url,'http://localhost').pathname;
      if(req.method!=='GET'||!path.startsWith('/app/'))return res.writeHead(404).end();
      const file=path.startsWith('/app/assets/')?resolve(dist,decodeURIComponent(path.slice(5))):resolve(dist,'index.html');
      if(!file.startsWith(dist+sep))throw Error('invalid path');
      res.setHeader('Content-Type',({'.html':'text/html','.js':'text/javascript','.css':'text/css'})[extname(file)]??'application/octet-stream');
      res.end(await readFile(file));
    } catch {res.writeHead(404).end();}
  });
  await new Promise(r=>server.listen(0,'127.0.0.1',r));
  const base=`http://127.0.0.1:${server.address().port}`;
  browser=await chromium.launch({...(process.env.CHROME?{executablePath:process.env.CHROME}:{}),args:['--no-sandbox']});
  const results=[];
  for(const viewport of [{width:1440,height:1000},{width:390,height:844}]) {
    const context=await browser.newContext({viewport});
    const page=await context.newPage();const errors=[];
    page.on('pageerror',e=>errors.push(e.message));
    await context.route('**/*',route=>new URL(route.request().url()).origin===base?route.fallback():(errors.push('external request'),route.abort()));
    const state=await installOperatorFixtures(page);
    await page.goto(base+'/app/operator');
    await page.getByRole('heading',{name:'AI 运营员',exact:true}).waitFor();
    const loaded=()=>page.getByRole('checkbox',{name:'启用 AI 运营员',exact:true}).waitFor({state:'visible'});
    await loaded();
    assert.equal(await page.getByTestId('action-1').getByRole('button',{name:'确认发送',exact:true}).isDisabled(),true);
    const tick=async name=>{
      const item=page.getByRole('checkbox',{name,exact:true}), expected=!(await item.isChecked());
      await item.click();
      await page.waitForFunction(({name,expected})=>[...document.querySelectorAll('input[type=checkbox]')].some(i=>i.getAttribute('aria-label')===name&&i.checked===expected&&!i.disabled),{name,expected});
    };
    await tick('启用 AI 运营员');await tick('演练模式：只保存草稿');
    await tick('第 1 级');await tick('产品供应商 第 1 级');await tick('虚构供应商 第 1 级');
    assert.equal(state.settings.levels['1'],true);assert.equal(state.settings.types['1:supplier_product'],true);assert.equal(state.settings.contacts['1:fictional-supplier'],true);
    await page.getByTestId('action-1').getByRole('button',{name:'修改',exact:true}).click();
    await page.getByRole('textbox',{name:'草稿正文',exact:true}).fill('您好，请核对本次询盘的产品规格、装箱资料、现价、报价有效期与交期。');
    await page.getByRole('button',{name:'保存修改',exact:true}).click();
    await page.getByRole('dialog').waitFor({state:'hidden'});
    await page.getByTestId('action-1').getByRole('button',{name:'确认发送',exact:true}).click();
    await page.getByRole('button',{name:'确认并继续',exact:true}).click();
    await page.getByRole('dialog').waitFor({state:'hidden'});
    await page.getByTestId('action-1').getByText('已发送',{exact:true}).waitFor();
    assert.equal(state.fakeBridge.length,1);assert.equal(state.fakeBridge[0].channel,'wechat');
    await page.getByTestId('action-2').getByRole('button',{name:'拒绝',exact:true}).click();
    assert.equal(await page.getByRole('button',{name:'确认拒绝',exact:true}).isDisabled(),true);
    await page.getByRole('textbox',{name:'拒绝原因',exact:true}).fill('虚构验收：已由老板处理');
    await page.getByRole('button',{name:'确认拒绝',exact:true}).click();
    await page.getByRole('dialog').waitFor({state:'hidden'});
    await page.getByTestId('action-2').getByText('已拒绝',{exact:true}).waitFor();
    assert.equal(await page.getByTestId('action-3').getByRole('button',{name:'确认发送',exact:true}).count(),0);
    await page.getByRole('button',{name:'核对 虚构供应商 的时间与语言',exact:true}).click();
    await page.getByRole('textbox',{name:'对方 IANA 时区',exact:true}).fill('UTC');
    await page.getByRole('button',{name:'保存联系人资料',exact:true}).click();
    await page.getByRole('dialog').waitFor({state:'hidden'});
    assert.equal(state.profiles['fictional-supplier'].timezone,'UTC');
    await page.getByRole('button',{name:'一键全停',exact:true}).click();
    await page.getByText('已全停',{exact:true}).waitFor();assert.equal(state.settings.enabled,false);
    await page.locator('#operator-decisions details').last().locator('summary').click();
    await page.getByText('fictional-qwen',{exact:false}).waitFor();
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2),'no horizontal page overflow');
    await page.screenshot({path:resolve(out,`operator-${viewport.width}.png`),fullPage:true});
    state.fail=true;await page.getByRole('button',{name:'刷新记录',exact:true}).click();
    await page.getByRole('alert').waitFor();
    assert.equal(await page.getByRole('checkbox',{name:'启用 AI 运营员',exact:true}).isDisabled(),true);
    assert.ok(state.posts.every(p=>p.fields.operation_key));assert.deepEqual(errors,[]);
    results.push({viewport,passed:true,posts:state.posts.length,fakeBridgeCalls:state.fakeBridge.length,unknownRetried:false});
    await context.close();
  }
  await writeFile(resolve(out,'results.json'),JSON.stringify(results,null,2));console.log(JSON.stringify({out,results},null,2));
} finally {
  if(browser)await browser.close();if(server)await new Promise(r=>server.close(r));
  // Keep reports at OPERATOR_ARTIFACTS (or the printed temporary artifact path).
  if(process.env.OPERATOR_ARTIFACTS)await rm(root,{recursive:true,force:true});
}
