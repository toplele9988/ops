import json
from playwright.sync_api import sync_playwright
BASE='http://127.0.0.1:8799'; OUT='/home/cz/swarm/tasks/t01/browser'
H={'X-Test-User':'u','X-Test-Roles':'manager'}
with sync_playwright() as p:
    b=p.chromium.launch(); ctx=b.new_context(viewport={'width':1440,'height':900},extra_http_headers=H); pg=ctx.new_page()
    errs=[]; resp=[]
    pg.on('response',lambda r: resp.append((r.status,r.request.method,r.url.split('127.0.0.1:8799')[1][:90])) if r.status>=400 else None)
    pg.on('console',lambda m: errs.append(m.text) if m.type=='error' else None)
    pg.on('pageerror',lambda e: errs.append('PAGEERR '+str(e)))
    pg.goto(BASE+'/strobes',wait_until='networkidle')
    mids=[pg.locator('.sg-add').nth(i).get_attribute('data-model-id') for i in range(3)]
    pg.locator('.sg-add').nth(0).click(); pg.wait_for_timeout(300)
    pg.evaluate("m=>document.dispatchEvent(new CustomEvent('xl:add-to-basket',{detail:{model_id:m,qty:40}}))",mids[1])
    pg.wait_for_timeout(400)
    pg.select_option('#cpq-lane','TEST-SEA-LCL'); pg.wait_for_timeout(1500)
    out={'verify':pg.locator('#cpq-verify').inner_text()}
    out['alerts']=[(a.get_attribute('data-code'),a.get_attribute('data-severity')) for a in pg.locator('#cpq-alerts .cpq-alert').all()]
    out['actions']={a.get_attribute('data-action'):(a.is_disabled(),a.get_attribute('title')) for a in pg.locator('#cpq-actions [data-action]').all()}
    out['result']=pg.locator('#cpq-result').inner_text()[:600]
    out['margin_value']=pg.locator('#cpq-margin-value').inner_text()
    out['costbasis']=(pg.locator('#cpq-cost-basis').get_attribute('data-masked'),pg.locator('#cpq-cost-basis').inner_text())
    pg.evaluate("document.querySelector('#cpq-drawer .offcanvas-body').scrollTo(0,99999)"); pg.wait_for_timeout(400)
    pg.screenshot(path=f'{OUT}/desktop_result.png')
    # packing switch to flight case on row1
    pg.locator('#cpq-lines article:not([hidden]) [data-mode="flightcase"]').first.click(); pg.wait_for_timeout(1200)
    out['alerts_fc']=[(a.get_attribute('data-code'),a.get_attribute('data-severity')) for a in pg.locator('#cpq-alerts .cpq-alert').all()]
    out['http_errors']=resp; out['console_errors']=errs
    print(json.dumps(out,ensure_ascii=False,indent=1))
    b.close()
