"""Library file management acceptance; caller enforces the disposable QA vault."""
import json
from pathlib import Path


async def check_library(cdp, check, click, shot, args, report):
    async def audit(name, selector):
        if not args.axe:
            return
        await cdp.evaluate(Path(args.axe).read_text(encoding='utf-8'))
        violations = await cdp.evaluate(f"(async()=>{{const r=await axe.run({json.dumps(selector)},{{runOnly:{{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}}}});return r.violations.map(x=>({{id:x.id,nodes:x.nodes.map(n=>n.target)}}))}})()")
        report['axe'][name] = violations
        await check('axe_' + name, str(violations == []).lower())
    await click('.panel-nav-item[data-view="settings"]')
    await click('[data-settings-nav="appearance"]')
    await cdp.wait_for('document.querySelectorAll("[data-nav-card]").length===8')
    await cdp.evaluate('''(()=>{window.__orderBefore=[...document.querySelectorAll('[data-nav-card]')].map(x=>x.dataset.navCard);
      const cards=document.querySelectorAll('[data-nav-card]'), d=new DataTransfer();
      cards[0].dispatchEvent(new DragEvent('dragstart',{bubbles:true,dataTransfer:d}));
      cards[2].dispatchEvent(new DragEvent('dragover',{bubbles:true,cancelable:true,dataTransfer:d}));
      cards[2].dispatchEvent(new DragEvent('drop',{bubbles:true,cancelable:true,dataTransfer:d}));})()''')
    await check('nav_drag_changes_cards_and_top_order', "(()=>{const keys=[...document.querySelectorAll('[data-nav-card]')].map(x=>x.dataset.navCard);const top=[...document.querySelectorAll('.panel-nav-scroll [data-view]')].map(x=>x.dataset.view);return keys[2]===window.__orderBefore[0] && JSON.stringify(keys)===JSON.stringify(top)})()")
    await shot('navigation-order-cards')
    await audit('navigation-cards', '#nav-order-list')
    await click('#nav-order-reset')
    await cdp.evaluate('''(async()=>{const {api,jsonBody}=await import('/assets/core.js');
      window.__libBooks=[];window.__libNotes=[];
      for(const name of ['甲','乙'])window.__libBooks.push(await api('/api/notebooks',jsonBody('POST',{pillar:'knowledge',name:'QA 文件管理 '+name+' '+Date.now()})));
      for(const title of ['文档甲','文档乙'])window.__libNotes.push(await api('/api/writing/notes',jsonBody('POST',{chapter:window.__libBooks[0].key,title,body:'用于验证批量操作的正文'})));
      document.dispatchEvent(new CustomEvent('data-changed'));
    })()''')
    await click('.panel-nav-item[data-view="knowledge"]')
    await cdp.wait_for('[...document.querySelectorAll("#knowledge-view [data-library-kind=book]")].some(b=>b.dataset.libraryId===window.__libBooks[0].key)')
    await cdp.evaluate('document.querySelector(".panel-nav-trigger[data-view=knowledge]").dispatchEvent(new KeyboardEvent("keydown",{key:"ArrowDown",bubbles:true,cancelable:true}))')
    await cdp.wait_for('!document.querySelector("#panel-nav-knowledge-menu").hidden && document.querySelectorAll("#panel-nav-knowledge-menu button").length>2')
    await check('knowledge_dropdown_includes_custom_notebooks', '[...document.querySelectorAll("#panel-nav-knowledge-menu button")].some(b=>b.dataset.navNotebook===window.__libBooks[0].key)')
    await shot('knowledge-notebook-dropdown')
    await audit('notebook-dropdown', '#panel-nav-knowledge-menu')
    await cdp.evaluate('[...document.querySelectorAll("#panel-nav-knowledge-menu button")].find(b=>b.dataset.navNotebook===window.__libBooks[0].key).click()')
    await cdp.wait_for('document.querySelector("#knowledge-view .notebook-page h3")?.textContent===window.__libBooks[0].title')
    await check('notebook_dropdown_opens_selected_book', 'document.querySelectorAll("#knowledge-view [data-library-kind=note]").length===2 && document.querySelector("#panel-nav-knowledge-menu").hidden')
    await cdp.evaluate('document.querySelector("#knowledge-view .note-card").dispatchEvent(new MouseEvent("contextmenu",{bubbles:true,cancelable:true}))')
    await cdp.wait_for('document.querySelector("[data-library-action=title]")')
    await click('[data-library-action="title"]')
    await cdp.wait_for('document.querySelector("#library-action-form input")')
    await audit('library-title-form', '#detail-dialog')
    await cdp.evaluate('document.querySelector("#library-action-form input").value="新的文档标题";document.querySelector("#library-action-form").requestSubmit()')
    await cdp.wait_for('document.querySelector("#knowledge-view").textContent.includes("新的文档标题") && !document.querySelector("#detail-dialog").open')
    await check('context_rename_updates_actual_markdown_title', "(async()=>{const {api}=await import('/assets/core.js');const notes=await Promise.all(window.__libNotes.map(n=>api('/api/notes?path='+encodeURIComponent(n.path))));return notes.some(n=>n.title==='新的文档标题') && notes.every(n=>n.body.includes('用于验证批量操作'))})()")
    await cdp.evaluate('document.querySelector("#knowledge-view .library-more[data-library-kind=note]").click()')
    await click('[data-library-action="select"]')
    await click('[data-selection="all"]')
    await check('notes_multiselect_shows_two_selected', 'document.querySelectorAll("#knowledge-view .library-selected").length===2 && document.querySelector("[data-selection=title]").disabled')
    await audit('library-multiselect', '.library-selection-bar')
    await click('[data-selection="move"]')
    await cdp.wait_for('document.querySelector("#library-action-form select")')
    await cdp.evaluate('document.querySelector("#library-action-form select").value=window.__libBooks[1].key;document.querySelector("#library-action-form").requestSubmit()')
    await cdp.wait_for('document.querySelectorAll("#knowledge-view [data-library-kind=note]").length===0')
    await check('batch_move_updates_file_locations', "(async()=>{const {api}=await import('/assets/core.js');const result=await api('/api/pillars/knowledge/notes?chapter='+encodeURIComponent(window.__libBooks[1].key));return result.items.length===2 && result.items.every(n=>n.path.startsWith(window.__libBooks[1].path+'/'))})()")
    await click('#knowledge-view [data-notebook-back]')
    await cdp.wait_for('document.querySelector("#knowledge-view [data-library-kind=book]")')
    await cdp.evaluate('[...document.querySelectorAll("#knowledge-view [data-notebook]")].find(b=>b.dataset.notebook===window.__libBooks[1].key).click()')
    await cdp.wait_for('document.querySelectorAll("#knowledge-view [data-library-kind=note]").length===2')
    await click('#knowledge-view .library-more[data-library-kind="note"]')
    await click('[data-library-action="select"]')
    await click('[data-selection="all"]')
    await click('[data-selection="delete"]')
    await cdp.wait_for('document.querySelector("#confirm-dialog").open')
    await cdp.evaluate('''(async()=>{const {api,jsonBody}=await import('/assets/core.js');
      const path=document.querySelector('#knowledge-view [data-library-kind=note]').dataset.libraryId;
      window.__conflictNote=await api('/api/notes?path='+encodeURIComponent(path));
      await api('/api/writing/notes',jsonBody('PUT',{path,body:'外部刚刚修改的正文',expected_revision:window.__conflictNote.revision}));
    })()''')
    await click('#confirm-accept')
    await cdp.wait_for('document.querySelector("#detail-dialog").open && document.querySelector("#dialog-title").textContent==="部分项目未完成"')
    await check('batch_delete_reports_revision_conflict_without_losing_selection', 'document.querySelector("#dialog-content").textContent.includes("已完成 1 项，未完成 1 项") && document.querySelectorAll("#knowledge-view .library-selected").length===1')
    await check('batch_delete_preserves_external_edit', "(async()=>{const {api}=await import('/assets/core.js');const note=await api('/api/notes?path='+encodeURIComponent(window.__conflictNote.path));return note.body.includes('外部刚刚修改的正文')})()")
    await shot('library-partial-failure')
    await click('#dialog-close')
    await click('[data-selection="done"]')
    await click('#knowledge-view [data-notebook-back]')
    await cdp.wait_for('document.querySelector("#knowledge-view [data-library-kind=book]")')
    await cdp.evaluate('[...document.querySelectorAll("#knowledge-view [data-library-kind=book]")].find(b=>b.dataset.libraryId===window.__libBooks[1].key).click()')
    await check('notebook_menu_has_no_move', '!document.querySelector("[data-library-action=move]")')
    await click('[data-library-action="title"]')
    await cdp.wait_for('document.querySelector("#library-action-form input")')
    await cdp.evaluate('document.querySelector("#library-action-form input").value="资料整理本";document.querySelector("#library-action-form").requestSubmit()')
    await cdp.wait_for('[...document.querySelectorAll("#knowledge-view .notebook-title")].some(n=>n.textContent==="资料整理本")')
    await check('notebook_title_persists_in_vault', "(async()=>{const {api}=await import('/assets/core.js');return (await api('/api/pillars/knowledge/overview')).notebook_titles[window.__libBooks[1].key]==='资料整理本'})()")
    await shot('knowledge-file-management')
    await cdp.evaluate('[...document.querySelectorAll("#knowledge-view [data-library-kind=book]")].find(b=>b.dataset.libraryId===window.__libBooks[1].key).click()')
    await click('[data-library-action="delete"]')
    await cdp.wait_for('document.querySelector("#confirm-dialog").open')
    await click('#confirm-accept')
    await cdp.wait_for('![...document.querySelectorAll("#knowledge-view [data-library-kind=book]")].some(b=>b.dataset.libraryId===window.__libBooks[1].key)')
    await check('context_book_delete_is_recoverable', "(async()=>{const {api}=await import('/assets/core.js');const r=await api('/api/recycle');return r.groups.flatMap(g=>g.items).some(n=>n.kind==='notebook' && n.count===1 && n.title==='资料整理本')})()")
    await click('.panel-nav-item[data-view="life"]')
    await cdp.evaluate('document.querySelector(".panel-nav-trigger[data-view=life]").dispatchEvent(new KeyboardEvent("keydown",{key:"ArrowDown",bubbles:true,cancelable:true}))')
    await cdp.wait_for('!document.querySelector("#panel-nav-life-menu").hidden && document.querySelectorAll("#panel-nav-life-menu button").length>0')
    await check('life_dropdown_has_notebooks', 'document.querySelectorAll("#panel-nav-life-menu [data-nav-notebook]").length>0')
    await click('.panel-nav-item[data-view="workbench"]')
    await check('task_sidebar_readable_density', '(()=>{const row=document.querySelector(".time-side-item");return row && parseFloat(getComputedStyle(row).fontSize)>=15 && row.getBoundingClientRect().height>=40})()')
    await shot('task-sidebar-comfortable')
    await cdp.call('Emulation.setDeviceMetricsOverride', {'width': 390, 'height': 844, 'deviceScaleFactor': 1, 'mobile': False})
    await click('.panel-nav-item[data-view="knowledge"]')
    await check('library_mobile_no_page_overflow', 'document.documentElement.scrollWidth<=innerWidth+1')
    await shot('knowledge-mobile-controls')
    await cdp.call('Emulation.setDeviceMetricsOverride', {'width': 1440, 'height': 1050, 'deviceScaleFactor': 1, 'mobile': False})
    await click('.panel-nav-item[data-view="workbench"]')
