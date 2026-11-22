import { api, escapeHtml } from './core.js';
import { loadSettings } from './settings.js';

let request = 0;
export function initDataCenter() {
  const page = document.createElement('section');
  page.id = 'data-view'; page.className = 'view data-center'; page.hidden = true;
  page.setAttribute('aria-label', '数据中心');
  page.innerHTML = `<header class="data-center-head"><div><span class="eyebrow">YOUR DATA</span><h2>资料有归处，备份有保障</h2><p>先看概况，再选择要做的事。</p></div><button type="button" id="data-center-refresh" class="text-button">刷新概况</button></header>
    <div id="data-center-summary" role="status">正在读取数据概况…</div>
    <div class="data-center-actions">${[
      ['backup','01','备份与恢复','保存当前资料，或从已有备份恢复副本'],
      ['data','02','数据与导出','导出可读资料，查看数据说明'],
      ['obsidian','03','库与工作区','查看当前库，管理本机工作区'],
      ['migration','04','库结构与迁移','检查目录结构，按步骤迁移资料'],
    ].map(([key,n,title,copy])=>`<button type="button" class="data-action" data-context-settings="${key}" aria-haspopup="dialog"><span class="data-action-number">${n}</span><strong>${title}</strong><span>${copy}</span><span aria-hidden="true">↗</span></button>`).join('')}</div>
    <footer class="data-center-foot"><button type="button" class="text-button" data-context-settings="audit" aria-haspopup="dialog">操作记录与自检</button><button type="button" class="text-button" data-go-view="workbench">返回工作台</button></footer>`;
  document.querySelector('#main-content').append(page);
  document.querySelector('#data-center-refresh').addEventListener('click', loadDataCenter);
  // The retained forms are moved into contextual dialogs, never cloned. This preserves
  // unsaved inputs and existing export/restore safeguards without a second settings UI.
  const legacy = document.querySelector('#settings-view');
  legacy.hidden = true;
  legacy.querySelector('.settings-nav')?.remove();
  legacy.querySelector('.settings-intro')?.remove();
  document.querySelectorAll('[data-view="settings"], [data-view="recycle"]').forEach(node=>node.remove());
}

export async function loadDataCenter() {
  const version = ++request;
  const target = document.querySelector('#data-center-summary');
  const refresh = document.querySelector('#data-center-refresh');
  refresh.disabled = true;
  target.textContent = '正在读取资料与备份概况…';
  try {
    const [, summary] = await Promise.all([loadSettings(), api('/api/diagnostics/summary', {timeoutMs:60000})]);
    if (version !== request) return;
    const counts = summary.counts || {}, backups = summary.backups || {};
    const values = [['Markdown 文件', summary.vault?.markdown_files], ['已确认记忆', counts.memories_active], ['待确认记忆', counts.memories_candidate]];
    const number = value => Number.isFinite(Number(value)) ? Math.max(0, Number(value)) : 0;
    const max = Math.max(1, ...values.map(([,v])=>number(v)));
    target.innerHTML = `<div class="data-metrics"><article><span>资料文件</span><strong>${summary.vault?.markdown_files == null ? '—' : number(summary.vault.markdown_files)}</strong><small>当前库中的 Markdown</small></article><article><span>本地备份</span><strong>${backups.count == null ? '—' : number(backups.count)}</strong><small>${backups.latest?.modified_at ? escapeHtml(new Date(backups.latest.modified_at).toLocaleString()) : '尚无备份记录'}</small></article><article><span>数据库完整性</span><strong>${summary.integrity === 'ok' ? '正常' : '待检查'}</strong><small>按当前自检结果显示</small></article></div>
      <figure class="data-chart"><figcaption>资料与记忆数量</figcaption>${values.map(([label,value])=>`<div class="data-chart-row"><span>${label}</span><div class="data-bar-track"><i></i></div><strong>${value == null ? '—' : number(value)}</strong></div>`).join('')}<p class="field-help">各项独立计数，不代表存储空间占比。更新于 ${escapeHtml(new Date().toLocaleTimeString())}</p></figure>`;
    target.querySelectorAll('.data-bar-track i').forEach((bar,index) => { bar.style.width = `${number(values[index][1])/max*100}%`; });
  } catch (error) {
    if (version === request) target.textContent = `概况暂时不可用：${error.message}。仍可使用下方入口，或点击刷新重试。`;
  } finally { if (version === request) refresh.disabled = false; }
}
