"""Context pack acceptance, called inside the isolated material browser probe."""
from pathlib import Path


async def check_context_pack(cdp, check, click, shot, args, report):
    await cdp.evaluate("document.querySelector('#workbench-banner-mode').focus()")
    await cdp.evaluate("(async()=>{await (await import('/assets/context-pack.js')).openContextPack()})()")
    await cdp.wait_for("document.querySelectorAll('#context-pack-dialog [data-pack-path]').length>0")
    await check('pack_explicit_selection', "document.querySelectorAll('[data-pack-path]:checked').length===0 && document.querySelector('[data-pack-preview]').disabled && document.querySelector('[data-pack-export]').disabled")
    await click('[data-pack-path]')
    await click('[data-pack-preview]')
    await cdp.wait_for("!document.querySelector('[data-pack-export]').disabled")
    await check('pack_real_preview', "!!document.querySelector('[data-pack-result] details pre')")
    await cdp.evaluate("(()=>{const i=document.querySelector('[data-pack-filter]');i.value='no-matching-notes-xyz';i.dispatchEvent(new Event('input',{bubbles:true}));i.dispatchEvent(new Event('change',{bubbles:true}))})()")
    await check('pack_filter_preserves_preview_and_selection', "!document.querySelector('[data-pack-export]').disabled && document.querySelector('[data-pack-count]').textContent.includes('1 篇被筛选隐藏') && !document.querySelector('[data-pack-empty]').hidden")
    await cdp.evaluate("(async()=>{await (await import('/assets/context-pack.js')).openContextPack()})()")
    await check('pack_repeated_open_keeps_draft', "document.querySelectorAll('[data-pack-path]:checked').length===1 && !document.querySelector('[data-pack-export]').disabled")
    await cdp.evaluate("(()=>{const i=document.querySelector('[data-pack-filter]');i.value='';i.dispatchEvent(new Event('input',{bubbles:true}))})()")
    await shot('context-pack-desktop')
    if args.axe:
        await cdp.evaluate(Path(args.axe).read_text(encoding='utf-8'))
        report['axe']['context-pack'] = await cdp.evaluate("(async()=>{const r=await axe.run('#context-pack-dialog',{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}});return r.violations.map(v=>({id:v.id,nodes:v.nodes.map(n=>n.target)}))})()")
        await check('axe_context_pack', str(report['axe']['context-pack'] == []).lower())
    for width in [390, 320]:
        await cdp.call('Emulation.setDeviceMetricsOverride', {'width': width, 'height': 900, 'deviceScaleFactor': 1, 'mobile': False})
        await check(f'pack_no_overflow_{width}', "(()=>{const d=document.querySelector('#context-pack-dialog');return d.scrollWidth<=d.clientWidth+1 && [...d.querySelectorAll('input')].filter(n=>n.getClientRects().length).every(n=>n.getBoundingClientRect().right<=d.getBoundingClientRect().right)})()")
        if width == 390:
            await shot('context-pack-mobile')
    await cdp.call('Emulation.setDeviceMetricsOverride', {'width': 1440, 'height': 1050, 'deviceScaleFactor': 1, 'mobile': False})
    await cdp.evaluate("(()=>{const b=document.querySelector('[data-pack-export]');b.click();b.click()})()")
    await cdp.wait_for("document.querySelector('[data-pack-status]').textContent.startsWith('已导出本地资料包')")
    await check('pack_export_consumes_preview', "document.querySelector('[data-pack-export]').disabled && !!document.querySelector('[data-pack-result] a[download]')")
    await check('pack_real_download', "(async()=>{const r=await fetch(document.querySelector('[data-pack-result] a').href);return r.ok && (await r.text()).includes('## 来源：')})()")
    await click('[data-pack-close]')
    await cdp.wait_for("!document.querySelector('#context-pack-dialog').open && document.activeElement.id==='workbench-banner-mode'")
    await check('pack_close_restores_focus', "document.activeElement.id==='workbench-banner-mode'")

    # Deliberately let old responses complete after abort to verify session isolation too.
    await cdp.evaluate('''window.__packFetch=window.fetch; window.__packRequests=[];
      window.fetch=(url,options)=>{
        if (!String(url).startsWith('/api/knowledge/context-pack/')) return window.__packFetch(url,options);
        if (String(url).endsWith('/status')) return Promise.resolve(new Response(JSON.stringify({exported:false})));
        return new Promise((resolve,reject)=>window.__packRequests.push({url:String(url),signal:options.signal,resolve:value=>resolve(new Response(JSON.stringify(value))),reject}));
      };
      window.__openPack=()=>import('/assets/context-pack.js').then(m=>{window.__packOpening=m.openContextPack()});''')
    await cdp.evaluate('window.__openPack()')
    await cdp.wait_for('window.__packRequests.length===1')
    await cdp.evaluate("document.querySelector('[data-pack-close]').click();window.__openPack()")
    await cdp.wait_for('window.__packRequests.length===2')
    await check('pack_close_aborts_read', 'window.__packRequests[0].signal.aborted')
    await cdp.evaluate('''window.__packRequests[1].resolve({sources:[{path:'知识库/new.md',modified_at:'2026-11-15',bytes:10}]});
      window.__packRequests[0].resolve({sources:[{path:'知识库/old.md',modified_at:'2026-11-15',bytes:10}]});''')
    await cdp.wait_for("document.querySelector('[data-pack-path]')?.dataset.packPath==='知识库/new.md'")
    await check('pack_old_source_cannot_replace_new_session', "document.querySelectorAll('[data-pack-path]').length===1 && document.querySelector('[data-pack-path]').dataset.packPath==='知识库/new.md'")
    await click('[data-pack-path]')
    await click('[data-pack-preview]')
    await cdp.wait_for('window.__packRequests.length===3')
    await click('[data-pack-path]')
    await cdp.evaluate("window.__packRequests[2].resolve({source_hash:'old',files:[],sources:[],bytes:0,warnings:[]})")
    await cdp.wait_for("document.querySelector('[data-pack-preview]').textContent==='预览选中内容'")
    await check('pack_selection_change_rejects_old_preview', "document.querySelector('[data-pack-export]').disabled && document.querySelector('[data-pack-status]').textContent.includes('选择已变化')")
    await click('[data-pack-close]')
    await cdp.evaluate('window.__openPack()')
    await cdp.wait_for('window.__packRequests.length===4')
    await cdp.evaluate("window.__packRequests[3].reject(new TypeError('offline'))")
    await cdp.wait_for("!document.querySelector('[data-pack-retry]').hidden")
    await click('[data-pack-retry]')
    await cdp.wait_for('window.__packRequests.length===5')
    await cdp.evaluate("window.__packRequests[4].resolve({sources:[{path:'知识库/retry.md',modified_at:'2026-11-15',bytes:12}]})")
    await cdp.wait_for("document.querySelector('[data-pack-path]')?.dataset.packPath==='知识库/retry.md'")
    await check('pack_retry_recovers', "document.querySelector('[data-pack-retry]').hidden && document.querySelector('[data-pack-status]').textContent==='请选择笔记，再预览。'")
    await click('[data-pack-path]')
    await click('[data-pack-preview]')
    await cdp.wait_for('window.__packRequests.length===6')
    await cdp.evaluate("window.__packRequests[5].resolve({source_hash:'new',files:[],sources:[{}],bytes:10,warnings:[]})")
    await cdp.wait_for("!document.querySelector('[data-pack-export]').disabled")
    await cdp.evaluate("document.querySelector('[data-pack-export]').click();document.querySelector('[data-pack-export]').click()")
    await cdp.wait_for('window.__packRequests.length===7')
    await check('pack_export_blocks_duplicate_and_close', "(()=>{const e=new Event('cancel',{cancelable:true});document.querySelector('#context-pack-dialog').dispatchEvent(e);return e.defaultPrevented && document.querySelector('[data-pack-close]').disabled && document.querySelector('[data-pack-path]').disabled && window.__packRequests.length===7})()")
    await cdp.evaluate("window.__packRequests[6].reject(new TypeError('offline'))")
    await cdp.wait_for("!document.querySelector('[data-pack-close]').disabled")
    await check('pack_export_failure_recovers', "!document.querySelector('[data-pack-preview]').disabled && document.querySelector('[data-pack-export]').disabled && !document.querySelector('[data-pack-path]').disabled && document.querySelector('[data-pack-status]').textContent.includes('无法连接')")
    await click('[data-pack-close]')
    await cdp.wait_for("!document.querySelector('#context-pack-dialog').open")
    await cdp.evaluate('window.fetch=window.__packFetch')

    # Actual CSP is active on this page; use generated markup and verify painted widths.
    await check('meters_render_under_csp', '''(async()=>{
      const {statsHtml}=await import('/assets/banner.js');
      const {archiveMarkup}=await import('/assets/archive-markup.js');
      const {heatmapBoardBody}=await import('/assets/heatmap-board-model.js');
      const {applyMeters}=await import('/assets/meter.js');
      const host=document.createElement('div');document.body.append(host);
      try {
        host.innerHTML=statsHtml({today_percent:50},{details:true})+archiveMarkup({identity:{name:'测试'}})+heatmapBoardBody({view:'week',days:[],summary:{}});
        const nodes=[...host.querySelectorAll('[data-meter-percent]')];
        if(nodes.length<2 || host.querySelector('[style]')) return false;
        applyMeters(host);
        return nodes.some(n=>Number(n.dataset.meterPercent)>0 && n.getBoundingClientRect().width>0) && nodes.every(n=>{
          const percent=Math.max(0,Math.min(100,Number(n.dataset.meterPercent)||0));
          return n.style.width===`${percent}%` && Math.abs(n.getBoundingClientRect().width-n.parentElement.clientWidth*percent/100)<2;
        });
      } finally {host.remove();}
    })()''')
