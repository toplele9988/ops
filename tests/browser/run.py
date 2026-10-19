import json, sys
from playwright.sync_api import sync_playwright
BASE='http://127.0.0.1:8799'; OUT='/home/cz/swarm/tasks/t01/browser'
H={'X-Test-User':'u','X-Test-Roles':'manager'}
log=[]
with sync_playwright() as p:
    b=p.chromium.launch()
    for name,(w,h) in {'desktop':(1440,900),'mobile':(390,844)}.items():
        ctx=b.new_context(viewport={'width':w,'height':h},extra_http_headers=H)
        pg=ctx.new_page()
        errs=[]
        pg.on('console',lambda m,errs=errs: errs.append(('console.'+m.type,m.text)) if m.type in('error','warning') else None)
        pg.on('pageerror',lambda e,errs=errs: errs.append(('pageerror',str(e))))
        pg.on('requestfailed',lambda r,errs=errs: errs.append(('requestfailed',r.url)))
        pg.add_init_script("document.addEventListener('securitypolicyviolation',e=>console.error('CSPV '+e.violatedDirective+' '+e.blockedURI));")
        pg.goto(BASE+'/strobes',wait_until='networkidle')
        res={'cards':pg.locator('.sg-card').count(),'scrollW':pg.evaluate('document.documentElement.scrollWidth'),'innerW':pg.evaluate('window.innerWidth')}
        pg.screenshot(path=f'{OUT}/{name}_grid.png')
        # add first two cards to basket
        mid2=pg.locator('.sg-add').nth(1).get_attribute('data-model-id')
        pg.locator('.sg-add').nth(0).click(); pg.wait_for_timeout(300)
        pg.evaluate("m=>document.dispatchEvent(new CustomEvent('xl:add-to-basket',{detail:{model_id:m,qty:3}}))",mid2)
        pg.wait_for_timeout(500)
        res['drawer_open']=pg.locator('#cpq-drawer.show').count()
        pg.screenshot(path=f'{OUT}/{name}_drawer.png')
        res['alerts']=[(a.get_attribute('data-code'),a.get_attribute('data-severity')) for a in pg.locator('#cpq-alerts .cpq-alert').all()]
        res['verify']=pg.locator('#cpq-verify').inner_text() if pg.locator('#cpq-verify').count() else None
        res['lanes']=pg.locator('#cpq-lane option').all_inner_texts() if pg.locator('#cpq-lane').count() else None
        res['errors']=errs
        log.append((name,res))
        ctx.close()
    b.close()
print(json.dumps(log,ensure_ascii=False,indent=1))
