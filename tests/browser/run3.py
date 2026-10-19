import json
from playwright.sync_api import sync_playwright
H={'X-Test-User':'u','X-Test-Roles':'manager'}
with sync_playwright() as p:
    b=p.chromium.launch(); pg=b.new_context(viewport={'width':1440,'height':900},extra_http_headers=H).new_page()
    errs=[]; pg.on('console',lambda m: errs.append(m.text) if m.type=='error' else None); pg.on('pageerror',lambda e: errs.append(str(e)))
    pg.goto('http://127.0.0.1:8799/strobes',wait_until='networkidle')
    pg.locator('.sg-add').first.click(); pg.wait_for_timeout(500)
    pg.select_option('#cpq-lane','TEST-FAR-DDP'); pg.wait_for_timeout(1500)
    def snap(tag):
        return {'alerts':[(a.get_attribute('data-code'),a.get_attribute('data-severity')) for a in pg.locator('#cpq-alerts .cpq-alert').all()],
         'acks':[c.get_attribute('data-ack-code') for c in pg.locator('#cpq-ack input[data-ack-code], #cpq-alerts input[data-ack-code]').all()],
         'actions':{a.get_attribute('data-action'):(a.is_disabled(),(a.get_attribute('title') or '')[:40]) for a in pg.locator('#cpq-actions [data-action]').all()},
         'verify':pg.locator('#cpq-verify').inner_text()}
    out={'far':snap('far')}
    for c in pg.locator('#cpq-alerts input[data-ack-code]:visible').all(): c.check()
    pg.wait_for_timeout(1500); out['far_acked']=snap('acked')
    # low margin via slider
    pg.select_option('#cpq-lane','TEST-SEA-LCL'); pg.wait_for_timeout(1200)
    pg.evaluate("(()=>{const s=document.querySelector('#cpq-margin');s.value='0.05';s.dispatchEvent(new Event('input',{bubbles:true}));})()"); pg.wait_for_timeout(1500)
    out['lowmargin']=snap('low'); out['reason_visible']=pg.locator('#cpq-override-reason').is_visible()
    pg.fill('#cpq-override-reason','strategic first order, trial'); pg.wait_for_timeout(1500); out['low_reason']=snap('lowr')
    pg.evaluate("(()=>{const s=document.querySelector('#cpq-margin');s.value='-0.05';s.dispatchEvent(new Event('input',{bubbles:true}));})()"); pg.wait_for_timeout(1500)
    out['fuse']=snap('fuse'); out['errs']=errs
    print(json.dumps(out,ensure_ascii=False,indent=1))
    b.close()
