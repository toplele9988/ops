// W2 real clicks, with local-server.mjs and mutable browser fixtures. No live APIs.
import assert from 'node:assert/strict';
import { mkdir, writeFile, readFile } from 'node:fs/promises';
import { chromium } from 'playwright-core';
import { startLocalServer } from './local-server.mjs';
import { createInboxState, installInboxFixtures, otherCustomerKey, supplierKey } from './fixtures.inbox.mjs';
const server = await startLocalServer();
const out = new URL('../../tests/inbox/artifacts/', import.meta.url).pathname;
await mkdir(out, { recursive: true });
const browser = await chromium.launch({ ...(process.env.CHROME ? { executablePath: process.env.CHROME } : {}), args: ['--no-sandbox'] });
const results = [], errors = [];
const poll = async (fn, message) => { for (let i=0;i<80;i++) { if (await fn()) return; await new Promise(r=>setTimeout(r,100)); } throw Error(message); };
const button = (page, name) => page.getByRole('button', { name, exact: true });
const dialog = page => page.getByRole('dialog');
async function profile(page) { const toggle=page.locator('.profile-toggle'); await toggle.waitFor({state:'attached'}); if(await toggle.isVisible() && await toggle.getAttribute('aria-expanded')==='false') await toggle.click(); }
async function setup(viewport, state) {
  const context = await browser.newContext({ viewport, timezoneId: 'Asia/Shanghai' });
  const page = await context.newPage(); page.setDefaultTimeout(10000);
  const problems = []; await installInboxFixtures(page,state,problems);
  page.on('pageerror',e=>problems.push(e.message));
  page.on('console',m=>{if(m.type()==='error'&&!/status of (502|503)/.test(m.text())) problems.push(m.text());});
  return {context,page,problems};
}
async function fits(page) { assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'No page overflow'); }
async function supplierPreview(page) {
  await profile(page); await button(page,'问供应商').click();
  await page.getByLabel('供应商会话',{exact:true}).selectOption(supplierKey);
  await page.getByLabel('补充产品',{exact:true}).fill('虚构灯具 X');
  await button(page,'起草供应商询问').click(); await page.getByLabel('供应商询问内容',{exact:true}).waitFor();
}
async function mainFlow(name,viewport) {
  const state=createInboxState(), {context,page,problems}=await setup(viewport,state);
  try {
    await page.goto(server.base+'/app/inbox'); await button(page,'处理询盘').click(); await page.locator('.bubble').last().waitFor();
    await button(page,'放大图片').click(); await page.getByRole('dialog',{name:'图片',exact:true}).waitFor(); await button(page,'关闭').click();
    await button(page,'打开文件').click(); await page.getByText('fictional product information',{exact:true}).waitFor(); const download=page.waitForEvent('download'); await page.getByRole('link',{name:'下载文件',exact:true}).click(); assert.equal(await readFile(await (await download).path(),'utf8'),'fictional product information'); await button(page,'关闭文件').click();
    await page.getByTestId('message-translation').getByText('请确认虚构灯具 X 的外箱尺寸。',{exact:true}).waitFor(); assert.equal(state.translations,1);
    await profile(page); await button(page,'AI 起草回复').click();
    const reply=page.getByRole('textbox',{name:'回复内容（可编辑）'});
    await poll(async()=>(await reply.inputValue()).includes('I will confirm'),'Draft adopted');
    await reply.fill('Edited fictional customer reply, carton size to follow.'); await button(page,'确认发送…').click();
    assert.equal(state.outbox.length,0,'No queue before human confirmation');
    await button(dialog(page),'确认发送').click(); await page.getByTestId('dry-run-record').filter({hasText:'Edited fictional customer reply'}).waitFor();
    assert.equal(state.outbox.length,1); assert.equal(state.outbox[0].dry_run,1); assert.equal(state.outbox[0].state,'queued'); assert.equal(state.conversations[0].workflow_state,'waiting'); assert.equal(await reply.inputValue(),'');
    await profile(page); await button(page,'转为案子').click(); await page.getByRole('textbox',{name:'案子名称',exact:true}).fill('虚构灯具询盘验收'); await button(dialog(page),'保存').click();
    await page.getByRole('link',{name:'虚构灯具询盘验收 · 询盘',exact:true}).waitFor(); assert.equal(state.links.length,1);
    await button(page,'关联已有案子').click(); await page.getByLabel('选择案子',{exact:true}).selectOption('7'); await button(dialog(page),'保存').click();
    await page.getByRole('link',{name:'虚构已有案子 · 询盘',exact:true}).waitFor(); assert.equal(state.links.length,2);
    await button(page,'归到客户').click(); await page.getByLabel('选择客户',{exact:true}).selectOption('2'); await button(page,'确认归属').click();
    await page.getByRole('region',{name:'客户资料',exact:true}).getByText('虚构客户乙',{exact:true}).waitFor(); assert.equal(state.conversations[0].customer_key,otherCustomerKey);
    await supplierPreview(page); assert.equal(state.outbox.length,2); assert.equal(state.outbox[1].state,'draft'); assert.ok(state.outbox[1].text.includes(state.messages.at(-1).text));
    await page.getByLabel('供应商询问内容',{exact:true}).fill(state.outbox[1].text+'\n请提供核对结果。'); await button(page,'确认发送给供应商（演练）').click();
    await poll(()=>state.outbox.length===3,'Supplier queued'); assert.equal(state.outbox[2].conversation_key,supplierKey); assert.equal(state.outbox[2].dry_run,1);
    await button(page,'稍后提醒').click(); await page.getByLabel('提醒时间',{exact:true}).fill(new Date(Date.now()+86400000).toISOString().slice(0,16)); await button(dialog(page),'保存').click();
    await poll(()=>state.conversations[0].remind_at>Date.now()/1000,'Reminder stored'); await page.getByText(/提醒时间：/).waitFor();
    await button(page,'标记已处理').click(); await button(page,'恢复跟进').waitFor(); assert.equal(state.conversations[0].workflow_state,'done');
    await fits(page); await page.screenshot({path:out+name+'-conversation.png',fullPage:true});
    await page.reload(); await profile(page); await button(page,'恢复跟进').waitFor();
    if(viewport.width<=900) { await button(page,'收起客户资料和操作').click(); await button(page,'返回会话列表').click(); }
    await page.getByRole('radio',{name:/^已处理/}).click(); await button(page,'处理询盘').waitFor();
    await page.getByRole('searchbox',{name:'搜索会话'}).fill('不存在的虚构名称'); await page.getByText('没有找到匹配会话',{exact:true}).waitFor();
    await button(page,'查看全部会话').click(); await page.getByRole('radio',{name:/^微信/}).click(); await page.locator('.conv-name').filter({hasText:'虚构供应商群'}).waitFor(); await poll(async()=>await page.locator('.conv').count()===1,'Channel filter applied');
    await page.getByRole('radio',{name:'全部渠道',exact:true}).click(); state.revision++; state.conversations.push({...state.conversations[0], key:Buffer.from(JSON.stringify(['conversation','alibaba','fictional-account','fictional-new'])).toString('base64url'), buyer_name:'虚构新询盘', workflow_state:'waiting', remind_at:null, waiting_hours:0, n_messages:1, last:{role:'buyer',text:'A fictional new message has arrived.',sent_at:new Date().toISOString()}});
    await button(page.getByRole('region',{name:'会话列表',exact:true}),'刷新会话').first().click(); await button(page,'有新消息，查看待回复会话').click(); await page.getByText('A fictional new message has arrived.',{exact:true}).waitFor();
    await fits(page); await page.screenshot({path:out+name+'-list.png',fullPage:true}); assert.deepEqual(problems,[]);
    results.push({name,passed:true,posts:state.requests.filter(r=>r.method==='POST'),outbox:state.outbox,links:state.links});
  } catch(e) { await page.screenshot({path:out+name+'-failure-main.png',fullPage:true}); await writeFile(out+name+'-failure-state.json',JSON.stringify({requests:state.requests,problems},null,2)); throw e; } finally {await context.close();}
}
async function failureFlow(name,viewport) {
  const state=createInboxState(); state.delayList=700; const {context,page,problems}=await setup(viewport,state);
  try {
    await page.goto(server.base+'/app/inbox'); await page.getByLabel('加载中…').waitFor(); await button(page,'处理询盘').click(); await profile(page);
    state.failDraft=true; await button(page,'AI 起草回复').click(); await page.getByText('虚构 AI 暂时不可用',{exact:true}).waitFor();
    state.failDraft=false; await button(page,'AI 起草回复').click(); await poll(async()=>(await page.getByRole('textbox',{name:'回复内容（可编辑）'}).inputValue()).includes('I will confirm'),'Draft retry');
    state.failSend=true; await button(page,'确认发送…').click(); await button(dialog(page),'确认发送').click(); await page.getByText(/草稿保存结果未能确认/).waitFor(); assert.ok(await button(page,'确认发送…').isDisabled());
    await page.waitForTimeout(400); assert.equal(state.requests.filter(r=>r.body.action==='send').length,1); assert.equal(state.outbox.length,0);
    await page.screenshot({path:out+name+'-send-failure.png',fullPage:true});
    state.failContext=true; await button(page,'刷新对话').click(); await profile(page); await page.getByText('没能读到客户与案子',{exact:true}).waitFor();
    state.failContext=false; await button(page,'重新读取').click(); await supplierPreview(page); state.failSupplierSend=true;
    await button(page,'确认发送给供应商（演练）').click(); await page.getByText(/虚构保存结果未确认/).waitFor(); assert.ok(await button(page,'确认发送给供应商（演练）').isDisabled()); assert.equal(state.outbox.filter(r=>r.state==='queued').length,0);
    await button(dialog(page),'取消').click(); state.failList=true; state.delayList=0; await page.goto(server.base+'/app/inbox'); await button(page.getByRole('region',{name:'会话列表',exact:true}),'刷新会话').click(); await page.getByText('没能读到会话',{exact:true}).waitFor();
    state.failList=false; await button(page,'重新读取').click(); await button(page,'处理询盘').waitFor(); await fits(page); assert.deepEqual(problems,[]);
    results.push({name:name+'-failures',passed:true,send_attempts:state.requests.filter(r=>r.body.action==='send').length});
  } catch(e) {await page.screenshot({path:out+name+'-failure-errors.png',fullPage:true}); throw e;} finally {await context.close();}
}
async function translationFlow(name, viewport) {
  const state = createInboxState(); state.nativeLanguage = 'es';
  const {context, page, problems} = await setup(viewport, state);
  try {
    await page.goto(server.base + '/app/inbox'); await button(page, '处理询盘').click();
    const chinese = page.getByTestId('message-translation');
    await chinese.getByText('请确认虚构灯具 X 的外箱尺寸。', {exact:true}).waitFor();
    assert.equal(state.translations, 1);
    assert.equal(await page.locator('.bubble.me [data-testid="message-translation"]').count(), 0);
    await button(page, '只看中文').click();
    assert.ok(!(await page.locator('.bubble.them .bubble-text').innerText()).includes('Please confirm'), 'Only Chinese hides the original prose');
    assert.ok(await button(page, '打开文件').isVisible());
    assert.ok(await button(page, '放大图片').isVisible());
    await page.reload(); await button(page, '看原文').waitFor(); await chinese.waitFor();
    assert.equal(state.translations, 1, 'Reload uses persistent translation cache');
    await page.locator('.conv-snip').filter({hasText:'请确认虚构灯具 X 的外箱尺寸。'}).first().waitFor({state:'attached'});
    await button(page, '看原文').click();
    await page.locator('.bubble.them .bubble-text').getByText('Please confirm the carton size for Fictional Lamp X.', {exact:true}).waitFor();
    const reply = page.getByRole('textbox', {name:'回复内容（可编辑）'});
    await reply.fill('请确认虚构灯具资料。'); await button(page, '生成英文及当地语言稿').click();
    await page.getByRole('region', {name:'英文版', exact:true}).waitFor();
    await page.getByRole('region', {name:'当地语言版', exact:true}).waitFor();
    assert.equal(state.outbox.length, 0, 'Generating never sends');
    await button(page, '采用回复稿，填入回复框').click();
    assert.equal(await reply.inputValue(), 'Please confirm the fictional lamp details.\n\nPor favor, confirme los datos de la lámpara ficticia.');
    assert.equal(state.outbox.length, 0, 'Adopting never sends');
    await button(page, '确认发送…').click(); assert.equal(state.outbox.length, 0);
    await button(dialog(page), '取消').click();
    state.badCompose = true; await reply.fill('请确认虚构灯具资料。'); await button(page, '生成英文及当地语言稿').click();
    await page.getByText('当地语言版的型号有增减，请核对：ZX-999', {exact:true}).waitFor();
    assert.ok(await button(page, '采用回复稿，填入回复框').isDisabled());
    state.badCompose = false;
    state.messages.push({role:'buyer', text:'您好，请确认虚构资料。', sent_at:new Date().toISOString()});
    await button(page, '刷新对话').click(); await page.locator('.bubble').last().getByText('您好，请确认虚构资料。', {exact:true}).waitFor();
    await page.waitForTimeout(200); assert.equal(state.translations, 1, 'Chinese never consumes quota');
    await fits(page); await page.screenshot({path:out + name + '-translation.png',fullPage:true});
    assert.deepEqual(problems, []); results.push({name:name+'-translation',passed:true});
  } finally { await context.close(); }
}
async function quotaAndSettingsFlow(name, viewport) {
  const state=createInboxState(); state.quotaExhausted=true; state.nativeLanguage=null;
  const {context,page,problems}=await setup(viewport,state);
  try {
    await page.goto(server.base+'/app/inbox'); await button(page,'处理询盘').click();
    await page.getByText('本小时翻译额度已用完，2 分钟后继续',{exact:true}).waitFor();
    assert.equal(await page.locator('[role="alert"]').count(),0,'Quota is a quiet status');
    assert.equal(state.translations,0);
    state.quotaExhausted=false;
    const reply=page.getByRole('textbox',{name:'回复内容（可编辑）'});
    await reply.fill('请确认虚构灯具资料。'); await button(page,'生成英文及当地语言稿').click();
    await page.getByRole('region',{name:'英文版',exact:true}).waitFor();
    assert.equal(await page.getByRole('region',{name:'当地语言版',exact:true}).count(),0);
    assert.ok(await button(page,'采用回复稿，填入回复框').isEnabled(),'Unknown native language permits English');
    await page.getByLabel('确认客户母语',{exact:true}).selectOption('en'); await button(page,'锁定母语').click();
    await page.getByText('客户母语：英语。本次只生成英文。',{exact:false}).waitFor();
    await button(page,'生成英文及当地语言稿').click();
    await page.getByRole('region',{name:'英文版',exact:true}).waitFor();
    assert.equal(await page.getByRole('region',{name:'当地语言版',exact:true}).count(),0,'English native language has one version');
    await page.goto(server.base+'/app/system/language');
    const incoming=page.getByRole('checkbox',{name:'收到的消息自动翻成中文',exact:true});
    const local=page.getByRole('checkbox',{name:'回复同时附当地语言',exact:true});
    assert.ok(await incoming.isChecked()); assert.ok(await local.isChecked());
    await incoming.uncheck(); await local.uncheck(); await button(page,'保存设置').click();
    await poll(()=>!state.langSettings.auto_translate_incoming&&!state.langSettings.auto_native_send,'Settings saved');
    await page.reload(); assert.equal(await incoming.isChecked(),false); assert.equal(await local.isChecked(),false);
    await page.goto(server.base+'/app/inbox'); await button(page,'处理询盘').click(); await page.locator('.bubble').last().waitFor();
    await page.waitForTimeout(200); assert.equal(state.translations,0);
    const autoRequests=state.requests.filter(r=>r.path==='/lang/translate'&&r.body.mode==='auto').length;
    assert.equal(autoRequests,1,'Disabled setting does not start automatic translation');
    assert.deepEqual(problems,[]); results.push({name:name+'-quota-settings',passed:true});
  } finally {await context.close();}
}
try {for(const [name,viewport] of [['desktop',{width:1440,height:900}],['mobile',{width:400,height:860}]]) for(const flow of [mainFlow,failureFlow,translationFlow,quotaAndSettingsFlow]) {try{await flow(name,viewport);console.log('PASS',name,flow.name);}catch(e){errors.push({name,flow:flow.name,error:e.stack});console.error('FAIL',name,flow.name,e.stack);}}}
finally {await browser.close();await server.close();await writeFile(out+'results.json',JSON.stringify({results,errors},null,2));}
if(errors.length) process.exitCode=1;
