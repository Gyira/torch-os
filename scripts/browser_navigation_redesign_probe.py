"""Focused conversation navigation check in the isolated QA runtime."""
import asyncio, base64, json, subprocess, tempfile, urllib.request
from pathlib import Path
import websockets
from browser_smoke import CDP, find_edge, debug_targets

async def run():
    assert json.load(urllib.request.urlopen('http://127.0.0.1:8785/api/health'))['runtime_label']=='qa'
    output=Path('artifacts/navigation-redesign'); output.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='torch-agent-') as profile:
        process=subprocess.Popen([find_edge(),'--headless=new','--disable-gpu','--no-first-run','--remote-allow-origins=*','--remote-debugging-port=9358',f'--user-data-dir={profile}','about:blank'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            for _ in range(50):
                try: target=next(x for x in debug_targets(9358) if x['type']=='page'); break
                except Exception: await asyncio.sleep(.2)
            async with websockets.connect(target['webSocketDebuggerUrl'],max_size=20_000_000) as socket:
                cdp=CDP(socket)
                await cdp.call('Page.enable'); await cdp.call('Runtime.enable')
                await cdp.call('Emulation.setDeviceMetricsOverride',{'width':1440,'height':1000,'deviceScaleFactor':1,'mobile':False})
                await cdp.call('Page.navigate',{'url':'http://127.0.0.1:8785/#agent'})
                await cdp.wait_for('document.querySelector("#chatgpt-stage")')
                await asyncio.sleep(1)
                checks={}
                async def check(name, expression):
                    checks[name]=bool(await cdp.evaluate(expression))
                    assert checks[name], name
                await cdp.evaluate("window.__errors=[];addEventListener('error',e=>__errors.push(e.message));addEventListener('unhandledrejection',e=>__errors.push(String(e.reason)))")
                await check('six_primary_items', "document.querySelectorAll('.panel-nav-scroll [data-view]').length===6")
                await check('no_settings_recycle_nav', "!document.querySelector('.panel-nav [data-view=settings],.panel-nav [data-view=recycle]')")
                await cdp.evaluate("document.querySelector('.panel-nav-help').click()")
                await cdp.wait_for("document.querySelector('#context-settings-dialog').open && !document.querySelector('#context-settings-content').inert")
                await check('navigation_cards_in_gear', "document.querySelectorAll('#context-settings-dialog [data-nav-card]').length===6")
                await cdp.evaluate("document.querySelector('#context-settings-dialog').close()")
                await check('chatgpt_direct', "document.querySelector('.panel-nav-item[data-view=agent]').textContent==='ChatGPT' && !document.querySelector('#ai-model-form')")
                await cdp.evaluate("document.querySelector('.panel-nav-item[data-view=self]').dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowDown',bubbles:true}))")
                await cdp.wait_for("document.querySelector('[data-nav-notebook=mentor]')")
                await check('self_dropdown', "!!document.querySelector('[data-nav-notebook=bazi]')")
                await cdp.evaluate("document.querySelector('[data-nav-notebook=mentor]').click()")
                await cdp.wait_for("!document.querySelector('#self-view').hidden")
                await cdp.evaluate("document.querySelector('.panel-nav-item[data-view=workbench]').click()")
                await cdp.wait_for("!document.querySelector('#workbench-view').hidden")
                await check('dida_default', "document.querySelector('#workbench-list-module').value === 'dida' && !document.querySelector('#time-dida-page').hidden")

                await check('reports_removed', "!document.querySelector('[data-context-settings=schedule]')")
                await cdp.evaluate("document.querySelector('#workbench-banner').click()")
                await cdp.wait_for("document.querySelector('#heatmap-dialog').open")
                checks['banner_opens_board']=True
                await cdp.evaluate("document.querySelector('#heatmap-dialog').close()")
                await cdp.evaluate("document.querySelector('#workbench-banner-collapse').click()")
                await check('collapse_animated', "document.querySelector('#workbench-banner').getAnimations().length>0")
                await cdp.evaluate("document.querySelector('[data-go-view=data]').click()")
                await cdp.wait_for("document.querySelector('.data-metrics')", timeout=90)
                await check('chart_matches_counts', "[...document.querySelectorAll('.data-chart-row')].every(row=> {const count=Number(row.querySelector('strong').textContent);const width=row.querySelector('i').getBoundingClientRect().width;return count===0 ? width===0 : width>0;})")
                await check('four_data_actions', "document.querySelectorAll('.data-action').length===4")
                for section in ['backup','data','obsidian','migration']:
                    await cdp.evaluate(f"document.querySelector('.data-action[data-context-settings={section}]').click()")
                    await cdp.wait_for("document.querySelector('#context-settings-dialog').open && !document.querySelector('#context-settings-content').inert", timeout=90)
                    await check(section+'_form', f"!!document.querySelector('#context-settings-content [data-settings-section={section}]')")
                    await cdp.evaluate("document.querySelector('#context-settings-dialog').close()")
                    await asyncio.sleep(.15)
                await cdp.call('Emulation.setDeviceMetricsOverride',{'width':390,'height':844,'deviceScaleFactor':1,'mobile':False})
                await check('narrow_no_overflow', "document.documentElement.scrollWidth<=innerWidth+1")
                await cdp.call('Emulation.setDeviceMetricsOverride',{'width':1440,'height':1200,'deviceScaleFactor':1,'mobile':False})
                await check('no_js_errors', "__errors.length===0")
                result=await cdp.call('Page.captureScreenshot',{'format':'png'}); (output/'data-center.png').write_bytes(base64.b64decode(result['data']))
                (output/'report.json').write_text(json.dumps(checks,indent=2),encoding='utf-8')
                print(checks)
        finally:
            subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW)

asyncio.run(run())
