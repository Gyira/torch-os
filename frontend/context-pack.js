import { api, escapeHtml, formatDate } from "./core.js";

let activeSession = null;

export async function openContextPack() {
  let dialog = document.getElementById("context-pack-dialog");
  if (dialog?.open) { dialog.querySelector("[data-pack-filter]").focus(); return; }
  activeSession?.controller.abort();
  const session = { controller: new AbortController(), trigger: document.activeElement };
  activeSession = session;
  if (!dialog) {
    dialog = document.createElement("dialog");
    dialog.id = "context-pack-dialog";
    dialog.className = "share-link-dialog context-pack-dialog";
    dialog.setAttribute("aria-labelledby", "context-pack-heading");
    document.body.append(dialog);
  }
  dialog.innerHTML = `<header class="note-editor-head"><div><h2 id="context-pack-heading">ChatGPT 资料包</h2><p>挑选笔记，预览正文，再导出到本机。</p></div><button type="button" class="button secondary" data-pack-close>关闭</button></header>
    <div class="context-pack-body">
      <p class="field-help">默认不选中任何内容；附件不包含。导出后需自行上传或替换 ChatGPT 项目文件。</p>
      <div class="context-pack-filters"><label>目录 / 主题 <input data-pack-filter type="search" placeholder="搜索路径，如日记、自我档案" /></label>
      <label>修改日期从 <input data-pack-from type="date" /></label><label>到 <input data-pack-to type="date" /></label></div>
      <p data-pack-previous class="field-help"></p>
      <p data-pack-status role="status">正在读取可选笔记…</p><button type="button" class="button secondary" data-pack-retry hidden>重新读取</button>
      <div class="context-pack-selection"><p data-pack-count role="status">已选 0 篇</p><button type="button" class="text-button" data-pack-clear disabled>清除选择</button></div>
      <div data-pack-sources class="context-pack-sources" aria-label="可选笔记"></div>
      <p data-pack-empty class="field-help" hidden>没有匹配的笔记，试试缩短关键词或清空日期。</p>
      <p class="field-help">筛选不会取消已勾选的笔记；导出内容以预览清单为准。</p>
      <div data-pack-result class="context-pack-result"></div>
    </div><footer class="context-pack-actions"><button type="button" class="button secondary" data-pack-preview disabled>预览选中内容</button><button type="button" class="button primary" data-pack-export disabled>确认导出到本机</button></footer>`;
  const find = selector => dialog.querySelector(selector);
  const status = find("[data-pack-status]"), output = find("[data-pack-result]");
  const previewButton = find("[data-pack-preview]"), exportButton = find("[data-pack-export]");
  const closeButton = find("[data-pack-close]"), clearButton = find("[data-pack-clear]");
  const current = () => activeSession === session && dialog.open;
  const readOptions = { signal: session.controller.signal };
  const selected = () => [...dialog.querySelectorAll("input[data-pack-path]:checked")];
  let snapshot = null, busy = false, exporting = false, revision = 0;

  const syncControls = () => {
    const picked = selected();
    const rows = [...dialog.querySelectorAll("[data-pack-row]")];
    const hidden = picked.filter(input => input.closest("[data-pack-row]").hidden).length;
    find("[data-pack-count]").textContent = `已选 ${picked.length} 篇${hidden ? `（其中 ${hidden} 篇被筛选隐藏）` : ""} · 当前显示 ${rows.filter(row => !row.hidden).length} 篇`;
    previewButton.disabled = busy || !picked.length;
    previewButton.textContent = busy && !exporting ? "正在预览…" : "预览选中内容";
    exportButton.disabled = busy || !snapshot;
    exportButton.textContent = exporting ? "正在导出…" : "确认导出到本机";
    closeButton.disabled = exporting;
    clearButton.disabled = exporting || !picked.length;
    dialog.querySelectorAll("input[data-pack-path]").forEach(input => { input.disabled = exporting; });
  };
  const selectionChanged = () => {
    revision += 1; snapshot = null;
    output.textContent = "选择已变化，请重新预览。";
    syncControls();
  };
  dialog.onchange = event => { if (event.target.matches("[data-pack-path]")) selectionChanged(); };
  clearButton.onclick = () => { selected().forEach(input => { input.checked = false; }); selectionChanged(); };
  closeButton.onclick = () => { if (!exporting) dialog.close(); };
  dialog.oncancel = event => { if (exporting) { event.preventDefault(); status.textContent = "正在完成本地导出，请稍候。"; } };
  dialog.onclose = () => {
    if (dialog.open) return;
    session.controller.abort();
    if (activeSession === session) activeSession = null;
    if (session.trigger?.isConnected) session.trigger.focus({ preventScroll: true });
  };
  const filter = () => {
    const query = find("[data-pack-filter]").value.trim().toLowerCase();
    const from = find("[data-pack-from]").value, to = find("[data-pack-to]").value;
    const rows = [...dialog.querySelectorAll("[data-pack-row]")];
    rows.forEach(row => { row.hidden = !row.dataset.path.toLowerCase().includes(query) || Boolean(from && row.dataset.date < from) || Boolean(to && row.dataset.date > to); });
    find("[data-pack-empty]").hidden = rows.some(row => !row.hidden);
    syncControls();
  };
  for (const key of ["filter", "from", "to"]) find(`[data-pack-${key}]`).oninput = filter;

  previewButton.onclick = async () => {
    if (busy || !selected().length) return;
    busy = true; snapshot = null;
    const requestedRevision = revision;
    const paths = selected().map(input => input.dataset.packPath);
    status.textContent = "正在生成正文预览…";
    syncControls();
    try {
      const result = await api("/api/knowledge/context-pack/preview", { ...readOptions, method: "POST", body: JSON.stringify({ paths }) });
      if (!current() || revision !== requestedRevision) return;
      snapshot = { paths, expected_hash: result.source_hash };
      output.innerHTML = result.files.map(file => `<details><summary>${escapeHtml(file.name)}</summary><pre>${escapeHtml(file.markdown)}</pre></details>`).join("");
      status.textContent = `${result.sources.length} 篇 · ${result.bytes} 字节。${result.warnings.join(" ")}`;
    } catch (error) { if (current() && revision === requestedRevision && error.name !== "AbortError") status.textContent = error.message; }
    finally { if (current()) { busy = false; if (revision !== requestedRevision) status.textContent = "选择已变化，请重新预览。"; syncControls(); } }
  };
  exportButton.onclick = async () => {
    if (busy || !snapshot) return;
    busy = true; exporting = true; syncControls();
    status.textContent = "正在导出到本机…";
    try {
      const result = await api("/api/knowledge/context-pack/export", { method: "POST", body: JSON.stringify(snapshot) });
      if (!current()) return;
      status.textContent = `已导出本地资料包；尚未上传 ChatGPT。与上次导出相比 ${result.changed.length} 篇有变化。`;
      output.innerHTML = result.files.map(file => `<p><a href="${escapeHtml(file.url)}" download>${escapeHtml(file.name)}</a></p>`).join("");
      snapshot = null;
    } catch (error) { if (current()) { status.textContent = error.message; snapshot = null; } }
    finally { if (current()) { busy = false; exporting = false; syncControls(); } }
  };

  const loadSources = async () => {
    const retry = find("[data-pack-retry]");
    retry.hidden = true;
    status.textContent = "正在读取可选笔记…";
    try {
      const result = await api("/api/knowledge/context-pack/sources", readOptions);
      if (!current()) return;
      find("[data-pack-sources]").innerHTML = result.sources.map(source =>
        `<label class="context-pack-row" data-pack-row data-path="${escapeHtml(source.path)}" data-date="${escapeHtml(source.modified_at.slice(0, 10))}"><input type="checkbox" data-pack-path="${escapeHtml(source.path)}" /><span>${escapeHtml(source.path)}<small>${escapeHtml(source.modified_at.slice(0, 10))} · ${source.bytes} B</small></span></label>`).join("");
      filter();
      status.textContent = result.sources.length ? "请选择笔记，再预览。" : "当前没有可导出的普通 Markdown 笔记。";
    } catch (error) { if (current() && error.name !== "AbortError") { status.textContent = error.message; retry.hidden = false; } }
  };
  find("[data-pack-retry]").onclick = loadSources;
  dialog.showModal();
  find("[data-pack-filter]").focus();
  await Promise.allSettled([loadSources(), (async () => {
    try {
      const previous = await api("/api/knowledge/context-pack/status", readOptions);
      if (current() && previous.exported) find("[data-pack-previous]").textContent = `上次导出：${formatDate(previous.exported_at)}；${previous.stale ? "选源已变化，建议重新导出" : "原选源未变化"}。${previous.note}`;
    } catch (error) { if (current() && error.name !== "AbortError") find("[data-pack-previous]").textContent = `上次导出记录暂不可用：${error.message}`; }
  })()]);
}
