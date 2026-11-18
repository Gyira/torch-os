"""Focused conversation navigation check in the isolated QA runtime."""
import asyncio, base64, json, subprocess, tempfile, urllib.request
from pathlib import Path
import websockets
from browser_smoke import CDP, find_edge, debug_targets

async def run():
    assert json.load(urllib.request.urlopen('http://127.0.0.1:8785/api/health'))['runtime_label']=='qa'
    output=Path('artifacts/agent-focused'); output.mkdir(exist_ok=True)
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
                await cdp.wait_for('document.querySelector("[data-agent-provider=chatgpt]")')
                await cdp.evaluate('document.querySelector("[data-agent-provider=chatgpt]").click()')
                await asyncio.sleep(1)
                checks={}
                checks['chatgpt_selected']=await cdp.evaluate('document.querySelector("#dsh-shell").hidden && !document.querySelector("#chatgpt-pane").hidden')
                checks['no_remote_iframe']=await cdp.evaluate('!document.querySelector("iframe[src*=chatgpt]")')
                checks['no_overflow']=await cdp.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
                result=await cdp.call('Page.captureScreenshot',{'format':'png'}); (output/'chatgpt.png').write_bytes(base64.b64decode(result['data']))
                await cdp.evaluate('document.querySelector("[data-agent-provider=dsh]").click()')
                checks['dsh_selected']=await cdp.evaluate('!document.querySelector("#dsh-shell").hidden && document.querySelector("#chatgpt-pane").hidden')
                await cdp.evaluate('document.querySelector(".panel-nav-item[data-view=workbench]").click()')
                await cdp.wait_for('document.querySelector("#agent-view").hidden')
                checks['left_page']=True
                (output/'report.json').write_text(json.dumps(checks,indent=2),encoding='utf-8')
                print(checks); assert all(checks.values())
        finally:
            subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW)

asyncio.run(run())
