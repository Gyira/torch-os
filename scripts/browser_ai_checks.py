from pathlib import Path


async def check_ai(cdp, check, click, shot, args, report):
    await click('.panel-nav-item[data-view="settings"]')
    await click('[data-settings-nav="roles"]')
    await cdp.wait_for("document.querySelector('#ai-model-status').textContent.includes('测试环境')")
    await cdp.evaluate("document.querySelector('.ai-model-panel').open=true")
    await check('model_settings_disabled_in_qa', "document.querySelector('#ai-model-form button[type=submit]').disabled")
    await shot('ai-model-settings')
    # No credential, no paid request: the settings form must preserve new edits while loading.
    await cdp.evaluate('''window.__modelFetch=window.fetch;
      window.fetch=(url,options)=>String(url)==='/api/ai/models'
        ? new Promise(resolve=>{window.__resolveModel=()=>resolve(new Response(JSON.stringify({enabled:true,provider:'deepseek',model:'',reasoning_effort:'',max_output_tokens:8192,label:'DeepSeek',key_configured:false})))})
        : window.__modelFetch(url,options);
      import('/assets/ai-model-settings.js').then(m=>{window.__modelLoading=m.loadModelSettings()});''')
    await cdp.wait_for("typeof window.__resolveModel==='function'")
    await cdp.evaluate("(()=>{const i=document.querySelector('#ai-model-form [name=model]');i.value='gpt-synthetic';i.dispatchEvent(new Event('input',{bubbles:true}));window.__resolveModel()})()")
    await cdp.evaluate('window.__modelLoading')
    await check('model_loading_keeps_draft', "document.querySelector('#ai-model-form [name=model]').value==='gpt-synthetic' && !document.querySelector('#ai-model-form button[type=submit]').disabled")
    await cdp.evaluate("window.fetch=window.__modelFetch;document.querySelector('#ai-model-form').dataset.dirty='false'")
    await cdp.evaluate("(async()=>await(await import('/assets/ai-model-settings.js')).loadModelSettings())()")
    await cdp.evaluate("(async()=>await(await import('/assets/ai-handoff.js')).openAIHandoff({role_id:'mentor',mode:'converse',entry:'self.mentor',message:'请帮我梳理下一步'}))()")
    await check('handoff_ready_without_model', "!document.querySelector('[data-handoff-copy]').disabled && document.querySelector('#ai-handoff-text').value.includes('请帮我梳理下一步')")
    await check('handoff_history_is_opt_in', "!document.querySelector('[data-handoff-history]').checked && document.querySelector('[data-handoff-history]').disabled")
    if args.axe:
        await cdp.evaluate(Path(args.axe).read_text(encoding='utf-8'))
        result = await cdp.evaluate("(async()=>{const r=await axe.run('#ai-handoff-dialog',{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}});return r.violations.map(v=>({id:v.id,nodes:v.nodes.map(n=>n.target)}))})()")
        report['axe']['ai-handoff'] = result
        await check('axe_ai_handoff', str(result == []).lower())
    await shot('ai-handoff-desktop')
    for width in [390, 320]:
        await cdp.call('Emulation.setDeviceMetricsOverride', {'width':width,'height':900,'deviceScaleFactor':1,'mobile':False})
        await check(f'handoff_no_overflow_{width}', "(()=>{const d=document.querySelector('#ai-handoff-dialog');return d.scrollWidth<=d.clientWidth+1})()")
        if width == 390:
            await shot('ai-handoff-mobile')
    await click('[data-handoff-close]')
    await cdp.wait_for("!document.querySelector('#ai-handoff-dialog').open")
    await cdp.call('Emulation.setDeviceMetricsOverride', {'width':1440,'height':1050,'deviceScaleFactor':1,'mobile':False})
    await cdp.evaluate("(async()=>await(await import('/assets/roles.js')).openRoleDrawer({roleId:'academic_mentor',mode:'discuss',entry:'knowledge.editor'}))()")
    await check('offline_role_supports_handoff', "!document.querySelector('#role-drawer').hidden && document.querySelector('#role-drawer-send').disabled && !!document.querySelector('#role-drawer-handoff')")
    await click('#role-drawer-close')
    await click('[data-view="workbench"]')
