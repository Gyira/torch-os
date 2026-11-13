import { api, escapeHtml } from "./core.js";

export async function openContextPack() {
  let dialog = document.getElementById("context-pack-dialog");
  if (!dialog) {
    dialog = document.createElement("dialog");
    dialog.id = "context-pack-dialog";
    dialog.className = "share-link-dialog";
    document.body.append(dialog);
  }
  dialog.innerHTML = `<h2>ChatGPT 资料包</h2><p>选择允许提供给 ChatGPT 的笔记。默认不选中任何内容；附件不包含。导出后需自行上传或替换项目文件。</p>
    <button type="button" data-pack-close>关闭</button><p data-pack-status role="status">正在读取选源…</p>
    <label>目录 / 主题路径筛选 <input data-pack-filter type="search" placeholder="如 01_自我档案 或 日记" /></label>
    <label>修改日期从 <input data-pack-from type="date" /></label><label>到 <input data-pack-to type="date" /></label>
    <p>筛选只帮助查找，隐藏的已勾选笔记仍会包含；以预览清单为准。</p><div data-pack-sources></div>
    <button type="button" data-pack-preview>预览选中内容</button><button type="button" data-pack-export disabled>确认导出到本机</button><div data-pack-result></div>`;
  let snapshot = null;
  let busy = false;
  let revision = 0;
  const status = dialog.querySelector("[data-pack-status]");
  const output = dialog.querySelector("[data-pack-result]");
  const exportButton = dialog.querySelector("[data-pack-export]");
  dialog.querySelector("[data-pack-close]").onclick = () => dialog.close();
  dialog.onchange = () => { revision += 1; snapshot = null; exportButton.disabled = true; output.textContent = "选择已变化，请重新预览。"; };
  dialog.querySelector("[data-pack-preview]").onclick = async () => {
    if (busy) return;
    busy = true;
    snapshot = null;
    exportButton.disabled = true;
    const requestedRevision = revision;
    try {
      const paths = [...dialog.querySelectorAll("input[data-pack-path]:checked")].map(el => el.dataset.packPath);
      const result = await api("/api/knowledge/context-pack/preview", { method: "POST", body: JSON.stringify({ paths }) });
      if (revision !== requestedRevision) return;
      snapshot = { paths, expected_hash: result.source_hash };
      output.innerHTML = result.files.map(f => `<details><summary>${escapeHtml(f.name)}</summary><pre>${escapeHtml(f.markdown)}</pre></details>`).join("");
      status.textContent = `${result.sources.length} 篇 · ${result.bytes} 字节。${result.warnings.join(" ")}`;
      exportButton.disabled = false;
    } catch (error) { status.textContent = error.message; }
    finally { busy = false; }
  };
  exportButton.onclick = async () => {
    if (busy || !snapshot) return;
    busy = true;
    exportButton.disabled = true;
    try {
      const result = await api("/api/knowledge/context-pack/export", { method: "POST", body: JSON.stringify(snapshot) });
      status.textContent = `已导出本地资料包；尚未上传 ChatGPT。与上次导出相比 ${result.changed.length} 篇有变化。`;
      output.innerHTML = result.files.map(f => `<p><a href="${escapeHtml(f.url)}" download>${escapeHtml(f.name)}</a></p>`).join("");
    } catch (error) { status.textContent = error.message; }
    finally { busy = false; exportButton.disabled = !snapshot; }
  };
  if (!dialog.open) dialog.showModal();
  try {
    const result = await api("/api/knowledge/context-pack/sources");
    dialog.querySelector("[data-pack-sources]").innerHTML = result.sources.map(s =>
      `<label style="display:block" data-pack-row data-path="${escapeHtml(s.path)}" data-date="${escapeHtml(s.modified_at.slice(0, 10))}"><input type="checkbox" data-pack-path="${escapeHtml(s.path)}" /> ${escapeHtml(s.path)} (${s.bytes} B)</label>`).join("");
    const filter = () => {
      const query = dialog.querySelector("[data-pack-filter]").value.toLowerCase();
      const from = dialog.querySelector("[data-pack-from]").value;
      const to = dialog.querySelector("[data-pack-to]").value;
      dialog.querySelectorAll("[data-pack-row]").forEach(row => {
        row.hidden = !row.dataset.path.toLowerCase().includes(query) || Boolean(from && row.dataset.date < from) || Boolean(to && row.dataset.date > to);
        row.style.display = row.hidden ? "none" : "block";
      });
    };
    for (const key of ["filter", "from", "to"]) dialog.querySelector(`[data-pack-${key}]`).oninput = filter;
    status.textContent = "请选择笔记，再预览。";
    const previous = await api("/api/knowledge/context-pack/status");
    if (previous.exported) status.textContent = `上次导出：${previous.exported_at}；${previous.stale ? "选源已变化，建议重新导出" : "原选源未变化"}。${previous.note}`;
  } catch (error) { status.textContent = error.message; }
}
