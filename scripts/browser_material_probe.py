"""Real Chromium acceptance of materials and contextual settings, using a QA vault only.

Optional axe source is a local axe-core/axe.min.js; no CDN loaded by the product.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import urllib.request

import websockets

from browser_smoke import CDP, debug_targets, find_edge
from browser_theme import DSH_LIGHT, DSH_DARK


async def run(args):
    with urllib.request.urlopen(args.base_url + '/api/health', timeout=10) as response:
        assert json.load(response)['runtime_label'] == 'qa', 'Only a disposable QA workspace is allowed'
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    profile = Path(tempfile.mkdtemp(prefix='torch-material-')).resolve()
    process = None
    report = {'checks': {}, 'screenshots': [], 'axe': {}}
    checks = report['checks']
    try:
        process = subprocess.Popen([find_edge(), '--headless=new', '--disable-gpu',
            '--no-first-run', '--no-default-browser-check', '--remote-allow-origins=*',
            f'--remote-debugging-port={args.port}', f'--user-data-dir={profile}', 'about:blank'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW)
        target = None
        for _ in range(60):
            try:
                target = next(x for x in debug_targets(args.port) if x['type'] == 'page')
                break
            except Exception:
                await asyncio.sleep(.2)
        assert target, 'Browser did not start'
        async with websockets.connect(target['webSocketDebuggerUrl'], max_size=20_000_000) as socket:
            cdp = CDP(socket)
            await cdp.call('Page.enable')
            await cdp.call('Runtime.enable')
            await cdp.call('Emulation.setDeviceMetricsOverride', {'width': 1440, 'height': 1050, 'deviceScaleFactor': 1, 'mobile': False})
            await cdp.call('Emulation.setEmulatedMedia', {'features': [{'name': 'prefers-color-scheme', 'value': 'light'}]})
            await cdp.call('Page.addScriptToEvaluateOnNewDocument', {'source': '''
              window.__materialErrors=[];
              addEventListener('error', e=>window.__materialErrors.push(e.message));
              addEventListener('unhandledrejection', e=>window.__materialErrors.push(String(e.reason)));
            '''})
            await cdp.call('Page.navigate', {'url': args.base_url})
            await cdp.wait_for("document.querySelector('.panel-nav-scroll.material-ready') && document.querySelector('#workbench-hello').textContent !== '你好'")
            async def check(name, expression):
                result = await cdp.evaluate(expression)
                checks[name] = result is True
                if result is not True:
                    print(f'FAIL {name}: {result}', flush=True)
                return result
            async def shot(name):
                await asyncio.sleep(.4)
                result = await cdp.call('Page.captureScreenshot', {'format': 'png'})
                path = output / (name + '.png')
                path.write_bytes(base64.b64decode(result['data']))
                report['screenshots'].append(str(path))
            async def click(selector):
                await cdp.evaluate(f'document.querySelector({json.dumps(selector)}).click()')
            async def key(key_name, code=None, modifiers=0):
                values = {'key': key_name, 'code': code or key_name, 'modifiers': modifiers,
                          'windowsVirtualKeyCode': {'Tab': 9, 'Escape': 27, ' ': 32}.get(key_name, 0)}
                await cdp.call('Input.dispatchKeyEvent', {'type': 'keyDown', **values})
                await cdp.call('Input.dispatchKeyEvent', {'type': 'keyUp', **values})
            async def audit(name):
                if not args.axe:
                    return
                await cdp.evaluate("Promise.all(document.querySelector('#context-settings-dialog').getAnimations({subtree:true}).filter(a=>a.effect?.getTiming().iterations!==Infinity).map(a=>a.finished.catch(()=>{})))")
                await cdp.evaluate(Path(args.axe).read_text(encoding='utf-8'))
                result = await cdp.evaluate('''(async()=>{
                  const r=await axe.run('#context-settings-dialog', {runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}});
                  return r.violations.map(x=>({id:x.id,impact:x.impact,nodes:x.nodes.map(n=>({target:n.target,summary:n.failureSummary}))}));
                })()''')
                report['axe'][name] = result
                checks['axe_' + name] = result == []
            await shot('workbench-light')
            report['fonts'] = await cdp.evaluate("({body:getComputedStyle(document.body).fontFamily, dialog:getComputedStyle(document.querySelector('#context-settings-dialog')).fontFamily})")
            await click('[data-context-settings="widgets"]')
            await cdp.wait_for("document.querySelector('#context-settings-dialog').open && !document.querySelector('#context-settings-content').inert")
            await check('context_keeps_page', "location.hash==='#workbench' && !document.querySelector('#workbench-view').hidden")
            await check('single_live_widget_form', "document.querySelectorAll('#widget-settings-list').length===1 && !!document.querySelector('#context-settings-dialog #widget-settings-list')")
            await cdp.evaluate("window.__widgetForm=document.querySelector('#widget-settings-list'); window.__oldWeather=document.querySelector('[data-widget-toggle=weather]').checked; document.querySelector('[data-widget-toggle=weather]').focus()")
            await key(' ', 'Space')
            await check('toggle_focus_retained', "document.activeElement.dataset.widgetToggle==='weather'")
            await check('widget_applies_in_place', "document.querySelector('[data-widget=weather]').hidden===window.__oldWeather")
            await key(' ', 'Space')
            await audit('widgets-light')
            await shot('widgets-light')
            await cdp.evaluate("document.querySelector('#context-settings-close').focus()")
            await key('Tab', modifiers=8)
            await check('dialog_focus_trapped', "document.querySelector('#context-settings-dialog').contains(document.activeElement)")
            await key('Escape')
            await cdp.wait_for("!document.querySelector('#context-settings-dialog').open")
            await check('escape_returns_focus', "document.activeElement.dataset.contextSettings==='widgets'")
            await check('original_form_restored', "document.querySelector('#settings-view #widget-settings-list')===window.__widgetForm")
            await click('[data-context-settings="schedule"]')
            await cdp.wait_for("document.querySelector('#context-settings-dialog').open && !document.querySelector('#context-settings-content').inert")
            await cdp.evaluate("const i=document.querySelector('#schedule-morning-time'); i.value='07:43'; i.dispatchEvent(new Event('input',{bubbles:true}))")
            await key('Escape')
            await cdp.wait_for("!document.querySelector('#context-settings-dialog').open")
            await click('[data-context-settings="schedule"]')
            await check('unsaved_draft_survives', "document.querySelector('#schedule-morning-time').value==='07:43'")
            await audit('schedule-light')
            await shot('schedule-light')
            await click('#schedule-form button[type=submit]')
            await cdp.wait_for("document.querySelector('#schedule-feedback').classList.contains('success')")
            await check('save_uses_existing_api', "(async()=>{const r=await fetch('/api/schedule').then(r=>r.json());return r.settings.morning_time==='07:43'})()")
            await key('Escape')
            await cdp.wait_for("!document.querySelector('#context-settings-dialog').open")
            # Fast nav changes must settle at committed state without waiting for decoration.
            for view in ['settings', 'workbench', 'ops', 'settings']:
                await click(f'.panel-nav-item[data-view="{view}"]')
                await asyncio.sleep(.045)
            await cdp.wait_for("!document.querySelector('#settings-view').hidden")
            await asyncio.sleep(.45)
            await check('indicator_matches_last_selection', '''(()=>{const n=document.querySelector('.panel-nav-scroll');const a=n.querySelector('.active').getBoundingClientRect(); const b=n.querySelector('.material-indicator').getBoundingClientRect();return Math.abs(a.left-b.left)<1 && Math.abs(a.width-b.width)<1})()''')
            await click('[data-settings-nav="appearance"]')
            await shot('settings-light')
            for width in [768, 390, 320]:
                await cdp.call('Emulation.setDeviceMetricsOverride', {'width': width, 'height': 900, 'deviceScaleFactor': 1, 'mobile': False})
                await asyncio.sleep(.3)
                await check(f'settings_no_overflow_{width}', 'document.documentElement.scrollWidth<=innerWidth')
                await click('.panel-nav-item[data-view="workbench"]')
                await click('[data-context-settings="widgets"]')
                await cdp.wait_for("document.querySelector('#context-settings-dialog').open && !document.querySelector('#context-settings-content').inert")
                await check(f'dialog_no_overflow_{width}', "(()=>{const d=document.querySelector('#context-settings-dialog');return d.scrollWidth<=d.clientWidth+1 && d.getBoundingClientRect().right<=innerWidth})()")
                if width == 390:
                    await audit('widgets-mobile')
                    await shot('widgets-mobile')
                await key('Escape')
                await cdp.wait_for("!document.querySelector('#context-settings-dialog').open")
                await check(f'workbench_no_overflow_{width}', 'document.documentElement.scrollWidth<=innerWidth')
                if width == 390:
                    await shot('workbench-mobile')
                await click('.panel-nav-item[data-view="settings"]')
            await cdp.call('Emulation.setDeviceMetricsOverride', {'width': 1440, 'height': 1050, 'deviceScaleFactor': 1, 'mobile': False})
            await cdp.call('Emulation.setEmulatedMedia', {'features': [{'name': 'prefers-color-scheme', 'value': 'dark'}, {'name': 'prefers-reduced-motion', 'value': 'reduce'}]})
            await cdp.wait_for("document.documentElement.dataset.torchTheme==='dark'")
            await shot('settings-dark')
            await click('.panel-nav-item[data-view="workbench"]')
            await click('[data-context-settings="widgets"]')
            await cdp.wait_for("document.querySelector('#context-settings-dialog').open && !document.querySelector('#context-settings-content').inert")
            await check('reduced_motion_dialog_static', "document.querySelector('#context-settings-dialog').getAnimations().length===0")
            await audit('widgets-dark')
            await shot('widgets-dark')
            await key('Escape')
            await cdp.wait_for("!document.querySelector('#context-settings-dialog').open")
            await click('.panel-nav-item[data-view="settings"]')
            await asyncio.sleep(.05)
            await check('reduced_motion_indicator_static', "document.querySelector('.panel-nav-scroll .material-indicator').getAnimations().length===0")
            # Check the existing module routes too; no AI invocation or real third-party sync.
            for view in ['life', 'self', 'ops', 'knowledge', 'roles']:
                await click(f'.panel-nav-item[data-view="{view}"]')
                await cdp.wait_for(f"!document.querySelector('#{view}-view').hidden")
                await check(f'module_{view}_layout', 'document.documentElement.scrollWidth<=innerWidth')
            await click('.panel-nav-item[data-view="workbench"]')
            await cdp.evaluate('''(async()=>{
              const {state}=await import('/assets/core.js');
              window.__originalDidaCapability=state.config.capabilities.dida_sync;
              window.__materialFetch=window.fetch;
              window.__materialDidaFail=true;
              window.fetch=(input, options)=>String(input)==='/api/sync/dida/status'
                ? window.__materialDidaFail ? Promise.resolve(new Response(JSON.stringify({detail:'QA unavailable'}),{status:503}))
                : Promise.resolve(new Response(JSON.stringify({supported:true,configured:false,cookie_set:false,csrf_set:false,
                    enabled:false,read_only:false,habits_enabled:true,region:'dida365',interval_minutes:15,completed_days:30}),{headers:{'Content-Type':'application/json'}}))
                : window.__materialFetch(input,options);
              state.config.capabilities.dida_sync=true;
              document.dispatchEvent(new CustomEvent('torch:config'));
            })()''')
            await click('[data-context-settings="dida"]')
            await cdp.wait_for("document.querySelector('#context-settings-dialog').open && !document.querySelector('#context-settings-content').hasAttribute('aria-busy')")
            await check('failed_load_does_not_enable_empty_form', "document.querySelector('#context-settings-content').inert && document.querySelector('#dida-feedback').classList.contains('error')")
            await key('Escape')
            await cdp.wait_for("!document.querySelector('#context-settings-dialog').open")
            await cdp.evaluate('window.__materialDidaFail=false')
            await click('[data-context-settings="dida"]')
            await cdp.wait_for("document.querySelector('#context-settings-dialog').open && !document.querySelector('#context-settings-content').inert")
            await check('dida_mock_form_visible', "!!document.querySelector('#context-settings-dialog #dida-sync-form') && document.querySelector('#dida-account-details').open")
            await audit('dida-mock-dark')
            await shot('dida-mock-dark')
            await cdp.call('Emulation.setDeviceMetricsOverride', {'width': 390, 'height': 900, 'deviceScaleFactor': 1, 'mobile': False})
            await check('dida_mobile_no_overflow', "(()=>{const d=document.querySelector('#context-settings-dialog');return d.scrollWidth<=d.clientWidth+1})()")
            await key('Escape')
            await cdp.wait_for("!document.querySelector('#context-settings-dialog').open")
            await cdp.evaluate('''(async()=>{const {state}=await import('/assets/core.js'); window.fetch=window.__materialFetch;
              state.config.capabilities.dida_sync=window.__originalDidaCapability;
              document.dispatchEvent(new CustomEvent('torch:config'));
            })()''')
            await cdp.call('Emulation.setDeviceMetricsOverride', {'width': 1440, 'height': 1050, 'deviceScaleFactor': 1, 'mobile': False})
            await click('.panel-nav-item[data-view="settings"]')
            for scheme, tokens, colour in [('light', DSH_LIGHT, 'rgb(255, 255, 255)'), ('dark', DSH_DARK, 'rgb(35, 35, 36)')]:
                payload = json.dumps({'type': 'torch:theme', 'scheme': scheme, 'tokens': tokens})
                await cdp.evaluate(f"window.postMessage({payload}, location.origin)")
                await cdp.wait_for(f"document.documentElement.dataset.torchHost==='dsh' && document.documentElement.dataset.torchTheme==='{scheme}'")
                await check('host_palette_' + scheme, f"getComputedStyle(document.querySelector('.settings-card')).backgroundColor==={json.dumps(colour)}")
                await shot('host-' + scheme)
            report['errors'] = await cdp.evaluate('window.__materialErrors')
            checks['no_uncaught_errors'] = report['errors'] == []
            report['ok'] = all(checks.values())
    except Exception as error:
        report['fatal'] = str(error)
        report['ok'] = False
        raise
    finally:
        (output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        if process:
            subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20, creationflags=subprocess.CREATE_NO_WINDOW)
        # Verify the only recursive removal remains inside our fresh temp profile parent.
        if profile.parent == Path(tempfile.gettempdir()).resolve() and profile.name.startswith('torch-material-'):
            shutil.rmtree(profile, ignore_errors=True)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-url', default='http://127.0.0.1:8784')
    parser.add_argument('--port', type=int, default=9346)
    parser.add_argument('--output', default='artifacts/material-ui')
    parser.add_argument('--axe')
    args = parser.parse_args()
    result = asyncio.run(run(args))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result['ok'] else 1)
