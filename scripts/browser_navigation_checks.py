"""User workflow regression. Only run through the QA-guarded material probe."""
import json
from pathlib import Path


async def check_navigation(cdp, check, click, shot, args, report):
    async def unhover():
        await cdp.call('Input.dispatchMouseEvent', {'type': 'mouseMoved', 'x': 2, 'y': 2})

    async def hover(selector):
        # 先移开再进入：鼠标停在原地不会重复触发 pointerenter，菜单也不会再展开。
        await unhover()
        box = await cdp.evaluate(
            f"(()=>{{const n=document.querySelector({json.dumps(selector)});"
            "if(!n)return null;const r=n.getBoundingClientRect();"
            "return {x:r.left+r.width/2,y:r.top+r.height/2}})()")
        assert box, f'找不到悬停目标：{selector}'
        await cdp.call('Input.dispatchMouseEvent', {'type': 'mouseMoved', 'x': box['x'], 'y': box['y']})

    await click('.panel-nav-item[data-view="workbench"]')
    await check('workbench_has_no_duplicate_menu', "document.querySelector('#module-nav').hidden && !document.querySelector('.panel-nav-item[data-view=roles]')")
    await check('ops_dropdown_closed_by_default', "document.getElementById('panel-nav-ops-menu').hidden && document.querySelector('.panel-nav-trigger[data-view=ops]').getAttribute('aria-expanded')==='false' && [...document.querySelectorAll('#panel-nav-ops-menu .panel-nav-system')].length===2 && [...document.querySelectorAll('#panel-nav-ops-menu .panel-nav-system')].every(b=>!b.getClientRects().length)")
    await check('navigation_buttons_share_baseline_and_height', "(()=>{const boxes=[...document.querySelectorAll('.panel-nav-scroll > button, .panel-nav-scroll > .panel-nav-family > button')].map(b=>b.getBoundingClientRect());return boxes.length>=8 && boxes.every(b=>Math.abs(b.top-boxes[0].top)<1 && Math.abs(b.height-boxes[0].height)<1)})()")
    await hover('.panel-nav-trigger[data-view=ops]')
    await cdp.wait_for("!document.getElementById('panel-nav-ops-menu').hidden")
    await check('ops_dropdown_opens_on_hover', "document.querySelector('.panel-nav-trigger[data-view=ops]').getAttribute('aria-expanded')==='true' && [...document.querySelectorAll('#panel-nav-ops-menu .panel-nav-system')].every(b=>b.getClientRects().length)")
    # 下拉层不能被横向滚动的标签条裁掉：菜单中心点必须命中菜单本身。
    await check('ops_dropdown_not_clipped', "(()=>{const m=document.getElementById('panel-nav-ops-menu');const r=m.getBoundingClientRect();const hit=document.elementFromPoint(r.left+r.width/2,r.top+r.height/2);return r.height>0 && r.top>=0 && hit && hit.closest('#panel-nav-ops-menu')!==null})()")
    if args.axe:
        await cdp.evaluate(Path(args.axe).read_text(encoding='utf-8'))
        violations = await cdp.evaluate("(async()=>{const r=await axe.run('#panel-nav-ops-menu',{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}});return r.violations.map(x=>({id:x.id,nodes:x.nodes.map(n=>n.target)}))})()")
        report['axe']['ops-dropdown'] = violations
        await check('axe_ops_dropdown', str(violations == []).lower())
    await shot('navigation-ops-dropdown-open')
    await click('#panel-nav-ops-menu .panel-nav-system[data-system="health"]')
    await check('health_one_click', "!document.querySelector('#health-view').hidden && document.querySelector('#workbench-view').hidden")
    await check('health_highlight_is_on_ops_entry', "(()=>{const t=document.querySelector('.panel-nav-trigger[data-view=ops]');const h=document.querySelector('.panel-nav-system[data-system=health]');return t.classList.contains('active') && t.getAttribute('aria-current')===null && h.getAttribute('aria-current')==='page' && document.getElementById('panel-nav-ops-menu').hidden && [...document.querySelectorAll('.panel-nav-item.active')].filter(b=>b.getClientRects().length).length===1})()")
    await click('.panel-nav-item[data-view="ops"]')
    await check('ops_returns_home_from_health', "!document.querySelector('#ops-view').hidden && document.querySelector('#health-view').hidden")
    await hover('.panel-nav-trigger[data-view=ops]')
    await cdp.wait_for("!document.getElementById('panel-nav-ops-menu').hidden")
    await click('#panel-nav-ops-menu .panel-nav-system[data-system="love"]')
    await check('love_one_click', "!document.querySelector('#love-view').hidden")
    await click('.panel-nav-item[data-view="ops"]')
    await check('ops_returns_home_from_love', "!document.querySelector('#ops-view').hidden && document.querySelector('#love-view').hidden && document.querySelector('.panel-nav-trigger[data-view=ops]').getAttribute('aria-current')==='page'")
    await check('ops_has_no_duplicate_second_row', "document.querySelector('#module-nav').hidden")
    # 键盘也能开合：方向键展开并把焦点送进菜单，Esc 收起并回到按钮。
    await cdp.evaluate("document.querySelector('.panel-nav-trigger[data-view=ops]').focus()")
    await cdp.evaluate("document.querySelector('.panel-nav-trigger[data-view=ops]').dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowDown',bubbles:true,cancelable:true}))")
    await check('ops_dropdown_opens_with_arrow_down', "!document.getElementById('panel-nav-ops-menu').hidden && document.activeElement?.dataset.system==='health'")
    await cdp.evaluate("document.activeElement.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true,cancelable:true}))")
    await check('ops_dropdown_escape_restores_focus', "document.getElementById('panel-nav-ops-menu').hidden && document.activeElement?.classList.contains('panel-nav-trigger')")
    # 鼠标移开就收起（保留短暂缓冲，便于从按钮挪进菜单）。
    await hover('.panel-nav-trigger[data-view=ops]')
    await cdp.wait_for("!document.getElementById('panel-nav-ops-menu').hidden")
    await unhover()
    await cdp.wait_for("document.getElementById('panel-nav-ops-menu').hidden", timeout=5)
    await check('ops_dropdown_closes_when_pointer_leaves', "document.querySelector('.panel-nav-trigger[data-view=ops]').getAttribute('aria-expanded')==='false'")
    await shot('navigation-ops-home')
    await cdp.call('Emulation.setDeviceMetricsOverride', {'width':390,'height':900,'deviceScaleFactor':1,'mobile':False})
    await check('mobile_navigation_is_one_aligned_row', "(()=>{const boxes=[...document.querySelectorAll('.panel-nav-scroll > button, .panel-nav-scroll > .panel-nav-family > button')].map(b=>b.getBoundingClientRect());return boxes.length>=8 && boxes.every(b=>Math.abs(b.top-boxes[0].top)<1 && Math.abs(b.height-boxes[0].height)<1) && document.documentElement.scrollWidth<=innerWidth})()")
    await hover('.panel-nav-trigger[data-view=ops]')
    await cdp.wait_for("!document.getElementById('panel-nav-ops-menu').hidden")
    await check('mobile_ops_dropdown_fits_viewport', "(()=>{const r=document.getElementById('panel-nav-ops-menu').getBoundingClientRect();return r.left>=0 && r.right<=innerWidth && r.bottom<=innerHeight && document.documentElement.scrollWidth<=innerWidth})()")
    await shot('navigation-ops-mobile-dropdown')
    await unhover()
    await shot('navigation-ops-mobile')
    await cdp.call('Emulation.setDeviceMetricsOverride', {'width':1440,'height':1050,'deviceScaleFactor':1,'mobile':False})
    if args.axe:
        await cdp.evaluate(Path(args.axe).read_text(encoding='utf-8'))
        violations = await cdp.evaluate("(async()=>{const r=await axe.run('.panel-nav',{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}});return r.violations.map(x=>({id:x.id,nodes:x.nodes.map(n=>n.target)}))})()")
        report['axe']['top-navigation'] = violations
        await check('axe_top_navigation', str(violations == []).lower())
    await click('.panel-nav-item[data-view="agent"]')
    await check('conversation_is_independent_page', "!document.querySelector('#agent-view').hidden && document.querySelector('#agent-view').parentElement.id==='main-content' && document.querySelector('#workbench-view').hidden")
    await shot('conversation-standalone')

    await cdp.evaluate('''(async()=>{
      const {api,jsonBody}=await import('/assets/core.js');
      const name='QA 导航回收 '+Date.now();
      window.__workflowBook=await api('/api/notebooks',jsonBody('POST',{pillar:'life',name}));
      window.__workflowNote=await api('/api/writing/notes',jsonBody('POST',{chapter:window.__workflowBook.key,title:'QA 打开速度',body:'正文必须先出现。'}));
    })()''')
    await click('.panel-nav-item[data-view="life"]')
    await cdp.wait_for("[...document.querySelectorAll('#life-view [data-notebook]')].some(b=>b.dataset.notebook===window.__workflowBook.key)")
    await cdp.evaluate('''window.__workflowFetch=window.fetch;window.__workflowCalls=[];
      window.fetch=(url,options)=>{window.__workflowCalls.push(String(url));return window.__workflowFetch(url,options)};
      [...document.querySelectorAll('#life-view [data-notebook]')].find(b=>b.dataset.notebook===window.__workflowBook.key).click();''')
    await cdp.wait_for("document.querySelector('#life-view .notebook-page')?.textContent.includes('QA 打开速度')")
    await check('notebook_navigation_avoids_overview_scan', "!window.__workflowCalls.some(url=>url.includes('/overview'))")
    await cdp.evaluate('''window.fetch=(url,options)=>String(url).startsWith('/api/notes/backlinks?')
      ?new Promise(resolve=>{window.__releaseBacklinks=()=>resolve(new Response(JSON.stringify({items:[{path:'知识库/QA.md',title:'QA 关联资料'}]}),{headers:{'Content-Type':'application/json'}}));})
      :window.__workflowFetch(url,options);
      import('/assets/pillars.js').then(m=>m.openNote(window.__workflowNote.path));''')
    try:
        await cdp.wait_for("document.querySelector('#life-view .pillar-reader .md-body')?.textContent.includes('正文必须先出现')")
    except TimeoutError:
        print(await cdp.evaluate("({note:window.__workflowNote,errors:window.__materialErrors,reader:document.querySelector('#life-view .pillar-shell')?.innerText.slice(-1500),toasts:document.querySelector('#toast')?.textContent})"), flush=True)
        await shot('note-open-failure')
        raise
    await check('note_opens_before_backlinks_finish', "typeof window.__releaseBacklinks==='function'")
    await cdp.evaluate("window.__readerBody=document.querySelector('#life-view .pillar-reader .md-body');window.__releaseBacklinks()")
    await cdp.wait_for("document.querySelector('#life-view [data-reader-related]')?.textContent.includes('QA 关联资料')")
    await check('backlink_refresh_keeps_document_dom', "window.__readerBody===document.querySelector('#life-view .pillar-reader .md-body')")
    await cdp.evaluate('''(async()=>{window.fetch=window.__workflowFetch;
      const {api,jsonBody}=await import('/assets/core.js');
      await api('/api/notebooks/delete',jsonBody('POST',{pillar:'life',key:window.__workflowBook.key,confirm:true}));
    })()''')
    await click('.panel-nav-item[data-view="recycle"]')
    await cdp.wait_for("document.querySelector('#recycle-groups').textContent.includes(window.__workflowBook.title)")
    await cdp.evaluate("document.querySelector('#recycle-search').value=window.__workflowBook.title;document.querySelector('#recycle-search').dispatchEvent(new Event('input',{bubbles:true}))")
    await check('deleted_notebook_is_searchable', "document.querySelectorAll('#recycle-groups .recycle-item').length===1 && document.querySelector('#recycle-groups .recycle-kind').textContent==='本子'")
    await click('#recycle-select-visible')
    await check('batch_selection_enabled', "!document.querySelector('#recycle-restore-selected').disabled && !document.querySelector('#recycle-purge-selected').disabled")
    if args.axe:
        await cdp.evaluate(Path(args.axe).read_text(encoding='utf-8'))
        violations = await cdp.evaluate("(async()=>{const r=await axe.run('#recycle-view',{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}});return r.violations.map(x=>({id:x.id,nodes:x.nodes.map(n=>n.target)}))})()")
        report['axe']['recycle'] = violations
        if violations:
            print({'recycle_axe': violations}, flush=True)
        await check('axe_recycle_center', str(violations == []).lower())
    await shot('recycle-notebook-desktop')
    await cdp.call('Emulation.setDeviceMetricsOverride', {'width':390,'height':900,'deviceScaleFactor':1,'mobile':False})
    await check('recycle_mobile_no_overflow', "document.documentElement.scrollWidth<=innerWidth")
    await shot('recycle-notebook-mobile')
    await cdp.call('Emulation.setDeviceMetricsOverride', {'width':1440,'height':1050,'deviceScaleFactor':1,'mobile':False})
    await click('#recycle-restore-selected')
    await cdp.wait_for("document.querySelector('#recycle-groups').textContent.includes('没有匹配') || document.querySelector('#recycle-groups').textContent.includes('是空的')")
    await check('whole_notebook_restored', "(async()=>{const r=await(await fetch('/api/notes?path='+encodeURIComponent(window.__workflowNote.path))).json();return r.body.includes('正文必须先出现')})()")

    # Mock schedule and timetables, no third-party requests and no changes to real duty data.
    await cdp.evaluate('''(async()=>{
      const {shanghaiDay}=await import('/assets/journal-model.js');window.__dutyToday=shanghaiDay();window.__qaRest=false;window.__dutySynced=new Set();window.__dutySyncCalls=[];
      window.__beforeDutyFetch=window.fetch;
      window.fetch=(url,options)=>{
        const text=String(url);let result;
        if(text.startsWith('/api/love/schedule/upcoming'))result={today:window.__dutyToday,found:true,days:[{date:window.__dutyToday,in_range:true,work:!window.__qaRest,rest:window.__qaRest,duty:'G1234/3'}]};
        else if(text==='/api/train-tracker/status')result={sync_enabled:true,trains:['G1234','G1233']};
        else if(text==='/api/train-tracker/sync'){const body=JSON.parse(options.body);window.__dutySyncCalls.push(body);window.__dutySynced.add(body.code);result={success:true};}
        else if(text.startsWith('/api/train-tracker/train?')){const code=new URL(text,location.origin).searchParams.get('code');result={found:true,code,train:{train_code:code,from_station:'甲站',to_station:'乙站',timetable_date:window.__dutySynced.has(code)?window.__dutyToday:'2020-01-01',stops:[]},realtime:{status:code==='G1233'?'running':'arrived',status_text:'按图运行',progress_pct:30}};}
        return result?Promise.resolve(new Response(JSON.stringify(result),{headers:{'Content-Type':'application/json'}})):window.__beforeDutyFetch(url,options);
      };
      (await import('/assets/duty-service.js')).invalidateDutySchedule();
      await(await import('/assets/train-tracker-widget.js')).loadTrainTrackerWidget();
    })()''')
    await click('.panel-nav-item[data-view="workbench"]')
    await cdp.wait_for("document.querySelector('#train-tracker-code').textContent==='G1233'")
    await check('duty_selects_running_train', "document.querySelector('#train-tracker-code').textContent==='G1233' && document.querySelector('#workbench-train-tracker').dataset.resting==='false'")
    await check('duty_refreshes_today_once_per_train', "window.__dutySyncCalls.length===2 && window.__dutySyncCalls.every(row=>row.date===window.__dutyToday)")
    await shot('navigation-duty-desktop')
    await cdp.evaluate("window.__qaRest=true;document.dispatchEvent(new CustomEvent('torch:duty-changed'))")
    await cdp.wait_for("document.querySelector('#workbench-train-tracker').dataset.resting==='true'")
    await check('rest_day_keeps_train_card', "getComputedStyle(document.querySelector('#workbench-train-tracker')).display!=='none' && (document.querySelector('#train-tracker-status').textContent==='今日休息' || document.querySelector('#train-tracker-meta').textContent.includes('休息日'))")
    await cdp.evaluate("window.fetch=window.__beforeDutyFetch;document.dispatchEvent(new CustomEvent('torch:duty-changed'))")
